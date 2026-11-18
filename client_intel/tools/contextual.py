"""Retrieve attributed meeting statements and qualify catalogue gaps against holdings."""

from collections import defaultdict
from datetime import date, datetime
from hashlib import sha256
import re

from ..models import Evidence, Metric, Source, ToolContext, ToolResult, source
from . import commercial


def _unique_sources(sources: list[Source]) -> tuple[Source, ...]:
    return tuple({(item.table, item.rowid): item for item in sources}.values())


def _meeting_evidence(ctx: ToolContext, result: ToolResult, analyses: tuple[str, ...]) -> None:
    note_analysis = "meeting_context" if "meeting_context" in analyses else "recorded_commitments"
    rows = ctx.db.execute(
        "SELECT rowid, * FROM meeting_notes WHERE client_id = ? ORDER BY meeting_date DESC, note_id, rowid",
        (ctx.client_id,),
    ).fetchall()
    physical_notes = defaultdict(list)
    for row in rows:
        physical_notes[row["note_id"]].append(source("meeting_notes", "note_id", row))

    notes = []
    issues = []
    for note_id, records in physical_notes.items():
        variants = defaultdict(list)
        for record in records:
            variants[tuple(record.raw.items())].append(record)
        if len(records) > 1:
            conflict = len(variants) > 1
            explanation = (
                "The snapshot contains conflicting versions of this meeting note. Confirm which account is accurate."
                if conflict else
                "Repeated copies of this meeting note were counted once."
            )
            issues.append({"note_id": note_id, "conflict": conflict, "rowids": [r.rowid for r in records]})
            result.evidence.append(Evidence(
                f"contextual.note_issue.{note_id}", note_analysis, explanation,
                tuple(records), caveats=(explanation,), priority=12,
            ))

        for variant in variants.values():
            record = variant[0]
            note = record.raw
            try:
                meeting_date = date.fromisoformat(note["meeting_date"] or "")
            except ValueError:
                meeting_date = None
            if meeting_date is None or meeting_date > ctx.as_of or not (note["body"] or "").strip():
                reason = "A meeting note was withheld because its date or text cannot support an as-of statement."
                issues.append({"note_id": note_id, "rowid": record.rowid, "reason": reason})
                result.evidence.append(Evidence(
                    f"contextual.note_invalid.{note_id}.{record.rowid}", note_analysis, reason,
                    tuple(variant), caveats=(reason,), priority=13,
                ))
                continue

            raw_body = note["body"]
            body = raw_body.replace("\r\n", "\n").replace("\r", "\n")
            recorded_at = meeting_date.isoformat()
            author = note["author"] or "the note author"
            spans = []
            # Each export line is a statement or an action. Headers and attendee lists
            # remain in the saved raw row and are not sent as independent claim options.
            matches = list(re.finditer(r"^(?:[-*]\s+)([^\n]+)|^(Actions:[^\n]+)", body, re.MULTILINE))
            if not matches:
                matches = list(re.finditer(r"^(?!#|Date:|Author:|Attendees:)(\S[^\n]*)", body, re.MULTILINE))
            for match in matches:
                group = 1 if match.group(1) is not None else 2
                start, end = match.span(group)
                quote = body[start:end].rstrip()
                if not quote or quote.casefold() == "actions: none.":
                    continue
                key = f"contextual.note.{note_id}.{record.rowid}.{start}"
                if "meeting_context" in analyses:
                    result.evidence.append(Evidence(
                        key, "meeting_context", f"In the {recorded_at} meeting note, {author} recorded “{quote}”",
                        tuple(variant), (Metric("meeting_date", recorded_at, "date", recorded_at, recorded_at),),
                        period=recorded_at, priority=20 + min(len(notes), 8),
                    ))
                spans.append({
                    "key": key, "type": "recorded_action" if quote.startswith("Actions:") else "statement",
                    "start": start, "end": start + len(quote), "quote": quote,
                })
            notes.append({
                "note_id": note_id, "date": recorded_at, "author": note["author"], "title": note["title"],
                "rowids": [item.rowid for item in variant], "spans": spans,
                "raw_body_sha256": sha256(raw_body.encode("utf-8")).hexdigest(),
                "normalised_body_sha256": sha256(body.encode("utf-8")).hexdigest(),
            })

    if not any(note["spans"] for note in notes):
        message = "The snapshot contains no usable meeting statements for this account. Client priorities and commitments need direct confirmation."
        result.evidence.append(Evidence(
            "contextual.notes_unavailable", note_analysis, message,
            tuple(record for records in physical_notes.values() for record in records)
            or (source("clients", "client_id", ctx.client),),
            caveats=(message,), priority=14,
        ))
        result.status = "qualified" if rows else "empty"
    elif issues:
        result.status = "qualified"

    result.inputs["notes"] = {
        "table": "meeting_notes", "client_id": ctx.client_id, "through": ctx.as_of.isoformat(),
        "span_offsets": "zero-based Unicode offsets, end exclusive, with CRLF and CR normalised to LF",
    }
    result.data.update(notes=notes, note_issues=issues)
    if "recorded_commitments" in analyses:
        records_by_rowid = {record.rowid: record for records in physical_notes.values() for record in records}
        _recorded_commitments(ctx, result, notes, records_by_rowid)


_ACTION_OWNER = re.compile(r"^(?P<owner>.+?)\s+to\s+(?P<action>\S.*)$", re.IGNORECASE)
_ACTION_DATE = re.compile(
    r"\b(?:by|before|due(?: on)?)\s+(\d{4}-\d{2}-\d{2}|\d{1,2} [A-Za-z]+ \d{4}|"
    r"[A-Za-z]+ \d{1,2}, \d{4})\.?$", re.IGNORECASE,
)


def _due_date(action: str) -> tuple[str | None, str | None]:
    valid = {}
    for match in _ACTION_DATE.finditer(action):
        label = match.group(1)
        for fmt in ("%Y-%m-%d", "%d %B %Y", "%B %d, %Y"):
            try:
                parsed = datetime.strptime(label, fmt).date().isoformat()
            except ValueError:
                continue
            valid.setdefault(parsed, label)
            break
    return next(iter(valid.items())) if len(valid) == 1 else (None, None)


def _recorded_commitments(ctx, result, notes, records_by_rowid):
    commitments = []
    for note in notes:
        refs = tuple(records_by_rowid[rowid] for rowid in note["rowids"])
        body = refs[0].raw["body"]
        author = note["author"] or "the note author"
        attendees = re.search(r"^Attendees:[ \t]*([^\r\n]*)", body, re.MULTILINE)
        labels = {author.casefold()}
        if attendees:
            labels.update(re.sub(r"\s*\([^)]*\)", "", item).strip(" .").casefold()
                          for item in attendees.group(1).split(","))
        if note["author"]:
            labels.add(note["author"].split()[0].casefold())

        for span in note["spans"]:
            if span["type"] != "recorded_action":
                continue
            for match in re.finditer(r"[^;]+", span["quote"].removeprefix("Actions:")):
                fragment = match.group()
                quote = fragment.strip()
                if not quote or quote.rstrip(".").casefold() == "none":
                    continue
                start = span["start"] + len("Actions:") + match.start() + len(fragment) - len(fragment.lstrip())
                owner = None
                action = quote
                assignment = _ACTION_OWNER.fullmatch(quote)
                if assignment and assignment["owner"].casefold() in labels:
                    owner = assignment["owner"]
                    action = assignment["action"]
                due_date, due_text = _due_date(action)
                meeting_date = note["date"]
                metrics = [Metric("meeting_date", meeting_date, "date", meeting_date, meeting_date)]
                if due_date:
                    metrics.append(Metric("recorded_due_date", due_date, "date", due_text, meeting_date))
                key = f"contextual.commitment.{note['note_id']}.{refs[0].rowid}.{start}"
                ending = "" if quote.endswith((".", "?", "!")) else "."
                result.evidence.append(Evidence(
                    key, "recorded_commitments",
                    f"In the {meeting_date} meeting note, {author} recorded the action “{quote}”{ending} Completion remains unconfirmed.",
                    refs, tuple(metrics), period=meeting_date, priority=31,
                ))
                commitments.append({
                    "key": key, "note_id": note["note_id"], "note_date": meeting_date, "rowids": note["rowids"],
                    "start": start, "end": start + len(quote), "quote": quote, "action": action,
                    "owner_as_recorded": owner, "due_date": due_date, "due_text_as_recorded": due_text,
                    "completion": "unknown",
                })

    if not commitments:
        result.evidence.append(Evidence(
            "contextual.commitments_unavailable", "recorded_commitments",
            "The meeting-note export contains no explicit action for this account. Confirm owners and timing directly.",
            tuple(records_by_rowid.values()) or (source("clients", "client_id", ctx.client),),
            period=ctx.as_of.isoformat(), priority=43,
        ))
    result.data["recorded_commitments"] = commitments
    result.inputs["recorded_commitments"] = {"action_source": "explicit Actions passages in dated notes",
                                              "completion_source": "no completion tracking in this export"}


def _split(field):
    return [item.strip() for item in (field or "").split(";") if item.strip()]


def _product_catalogue(
    ctx: ToolContext, result: ToolResult, assessments: tuple[str, ...], prior: dict[str, ToolResult],
) -> None:
    records = defaultdict(list)
    for row in ctx.db.execute("SELECT rowid, * FROM products ORDER BY product_id, rowid"):
        records[row["product_id"]].append(source("products", "product_id", row))

    catalogue = {}
    for product_id, sources in records.items():
        first = sources[0].raw
        conflicting = any(item.raw != first for item in sources[1:])
        if len(sources) > 1:
            message = (
                "The catalogue has conflicting descriptions for a product. Confirm its definition before using it as a fit signal."
                if conflicting else
                "Repeated copies of this catalogue entry were counted once."
            )
            result.evidence.append(Evidence(
                f"contextual.catalogue_issue.{product_id}", assessments[0], message,
                tuple(sources), product_id=product_id, caveats=(message,), priority=15,
            ))
            result.status = "qualified"
        if conflicting:
            continue
        catalogue[product_id] = first

    if not catalogue:
        message = "The snapshot has no usable catalogue rows. Product fit cannot be assessed."
        result.evidence.append(Evidence(
            "contextual.catalogue_unavailable", assessments[0], message,
            (source("clients", "client_id", ctx.client),), caveats=(message,), priority=15,
        ))
        result.status = "unavailable"

    definitions = {}
    for product_id, row in catalogue.items():
        description = row["description"] or ""
        requires = [other_id for other_id, other in catalogue.items()
                    if other_id != product_id and re.search(
                        rf"\brequires\s+(?:the\s+)?{re.escape(other['product_name'])}\b", description, re.IGNORECASE
                    )]
        definitions[product_id] = {
            "name": row["product_name"], "description": description,
            "typical_segments": _split(row["typical_segments"]),
            "often_bought_with": _split(row["often_bought_with"]), "requires": requires,
        }
        if "catalogue_relationships" in assessments:
            result.evidence.append(Evidence(
                f"contextual.catalogue.{product_id}", "catalogue_relationships",
                f"The catalogue describes {row['product_name']} as “{description}”",
                tuple(records[product_id]), product_id=product_id,
                model_visible="product_gaps" not in assessments, priority=80,
            ))

    result.inputs["catalogue"] = {"table": "products", "client_segment": ctx.client["segment"]}
    result.data["catalogue"] = definitions
    if catalogue and "product_gaps" in assessments:
        _product_gaps(ctx, result, definitions, records, prior)
    if "peer_holdings" in assessments:
        _peer_holdings(ctx, result, definitions, records)


def _product_gaps(ctx, result, definitions, catalogue_sources, prior):
    commercial_result = prior.get("commercial", ToolResult(status="unavailable"))
    telemetry = prior.get("telemetry", ToolResult(status="unavailable"))
    data = commercial_result.data
    current = set(data.get("current_product_ids", ()))
    historical = set(data.get("historical_product_ids", ()))
    pending = set(data.get("pending_product_ids", ()))
    conclusive = data.get("holdings_conclusive", False)
    holdings = [fact for fact in commercial_result.evidence if fact.key == "commercial.holdings"]
    timeline = [fact for fact in commercial_result.evidence if fact.key.startswith("commercial.contract.")]
    offers = defaultdict(list)
    for offer in data.get("opportunities", ()):
        if offer["product_id"]:
            offers[offer["product_id"]].append(offer)
    offer_facts = {fact.key: fact for fact in commercial_result.evidence if fact.analysis == "opportunity_snapshot"}
    usage_facts = {fact.product_id: fact for fact in telemetry.evidence if fact.analysis == "usage_history"}
    client_profile = source("clients", "client_id", ctx.client)
    segment = ctx.client["segment"]
    gaps = []

    if not conclusive:
        message = "Signed-current holdings are uncertain. Confirm entitlement before treating any catalogue candidate as an addition."
        result.evidence.append(Evidence(
            "contextual.gaps_uncertain", "product_gaps", message,
            _unique_sources([client_profile, *(row for fact in holdings for row in fact.sources)]),
            period=ctx.as_of.isoformat(), caveats=(message,), priority=14,
        ))
        result.status = "qualified"

    for product_id, product in definitions.items():
        if product_id in current:
            continue
        product_offers = offers[product_id]
        related = sorted(held for held in current if held in definitions and (
            held in product["often_bought_with"] or product_id in definitions[held]["often_bought_with"]
        ))
        missing_requirements = sorted(set(product["requires"]) - current)
        segment_fit = segment in product["typical_segments"]
        observations = telemetry.data.get("usage", {}).get(product_id, {}).get("history", ())
        latest_observation = max((row for row in observations if row["status"] == "observed"),
                                 key=lambda row: row["month"], default=None)
        sources = [*catalogue_sources[product_id], client_profile]
        sources.extend(row for fact in holdings for row in fact.sources)
        sources.extend(row for held in related for row in catalogue_sources[held])
        detail = [f"The catalogue describes {product['name']} as “{product['description']}”"]
        if conclusive:
            detail.append("It sits outside the snapshot’s confirmed signed-current holdings.")
        if segment_fit:
            detail.append(f"The catalogue lists {segment} as a typical segment.")
        if related:
            related_names = ", ".join(definitions[held]["name"] for held in related)
            detail.append(f"The catalogue lists an often-bought-with relationship to {related_names}, among the recorded current holdings.")
        if product_id in historical:
            detail.append("It appears in a prior signed term.")
        if product_id in pending:
            detail.append("It also appears on a pending contract.")
        if product_id in historical or product_id in pending:
            sources.extend(row for fact in timeline for row in fact.sources)
        metrics = ()
        if latest_observation and product_id in usage_facts:
            month = latest_observation["month"]
            rowids = {item["rowid"] for item in latest_observation["source_refs"]}
            sources.extend(row for row in usage_facts[product_id].sources
                           if row.table == "usage_monthly" and row.rowid in rowids)
            detail.append(f"Reported consumption includes a usable observation for {month}.")
            metrics = (Metric("latest_usage_observation_month", month, "month", month, month, product_id),)
        if product_offers:
            stages = ", ".join(sorted({offer["stage"] for offer in product_offers}))
            detail.append(f"Recorded CRM opportunity stages include {stages}.")
            for offer in product_offers:
                fact = offer_facts.get(f"commercial.opportunity.{offer['opp_id']}")
                if fact:
                    sources.extend(fact.sources)
        if missing_requirements:
            names = ", ".join(definitions[item]["name"] for item in missing_requirements)
            detail.append(f"Confirm prerequisite coverage for {names} before discussion.")
        priority = 28 if segment_fit or related or product_offers or latest_observation else 80
        result.evidence.append(Evidence(
            f"contextual.gap.{product_id}", "product_gaps", " ".join(detail), _unique_sources(sources),
            metrics=metrics, product_id=product_id, period=ctx.as_of.isoformat(), priority=priority,
        ))
        gaps.append({
            "product_id": product_id, "holding_status": "outside_confirmed_holdings" if conclusive else "unknown",
            "typical_segment_match": segment_fit, "related_current_product_ids": related,
            "prior_signed_term": product_id in historical, "pending_term": product_id in pending,
            "latest_reported_usage_month": latest_observation["month"] if latest_observation else None,
            "opportunity_ids": [offer["opp_id"] for offer in product_offers],
            "missing_confirmed_prerequisites": missing_requirements,
        })

    result.data["product_gaps"] = gaps
    result.data["holdings_conclusive"] = conclusive
    if not gaps and conclusive:
        result.evidence.append(Evidence(
            "contextual.no_catalogue_gap", "product_gaps",
            "All products in the available catalogue appear in recorded signed-current holdings.",
            _unique_sources([*(row for fact in holdings for row in fact.sources),
                             *(row for items in catalogue_sources.values() for row in items)]),
            period=ctx.as_of.isoformat(), priority=15,
        ))
    if any(gap["typical_segment_match"] or gap["related_current_product_ids"] for gap in gaps):
        result.evidence.append(Evidence(
            "contextual.fit_qualification", "data_quality",
            "Catalogue fit identifies discussion candidates. Confirm current budget, delivery requirements and buying intent before proposing terms.",
            _unique_sources([client_profile, *(row for items in catalogue_sources.values() for row in items)]),
            period=ctx.as_of.isoformat(), priority=32,
        ))
    result.inputs["product_gaps"] = {
        "depends_on": [fact.key for fact in holdings], "holdings_as_of": ctx.as_of.isoformat(),
        "fit_basis": "catalogue capability, typical segment, held-product relationships, CRM, terms and observed usage",
    }


def _peer_holdings(ctx, result, definitions, catalogue_sources):
    segment = ctx.client["segment"]
    period = ctx.as_of.isoformat()
    scope_key = "contextual.peer.cohort"
    inputs = {
        "segment": segment, "target_excluded": ctx.client_id, "as_of": period,
        "eligibility": "exact same segment and conclusive holdings from signed terms active on the snapshot date",
        "denominator": "same-segment accounts with conclusive signed-current holdings, excluding target",
    }
    profile_sources = defaultdict(list)
    if segment:
        rows = ctx.db.execute(
            "SELECT rowid, * FROM clients WHERE segment = ? AND client_id <> ? ORDER BY client_id, rowid",
            (segment, ctx.client_id),
        )
        for row in rows:
            profile_sources[row["client_id"]].append(source("clients", "client_id", row))

    sources = [source("clients", "client_id", ctx.client)]
    eligible = {}
    excluded = {}
    for peer_id, refs in profile_sources.items():
        sources.extend(refs)
        if any(ref.raw != refs[0].raw for ref in refs[1:]):
            excluded[peer_id] = "conflicting_client_rows"
            continue
        record = refs[0]
        peer = ToolContext(ctx.db, dict(record.raw, rowid=record.rowid), ctx.as_of)
        position = commercial.run(peer, ("contract_position",), {})
        holdings = next(item for item in position.evidence if item.key == "commercial.holdings")
        sources.extend(holdings.sources)
        if position.data["holdings_conclusive"]:
            eligible[peer_id] = position.data["current_product_ids"]
        else:
            excluded[peer_id] = "signed_current_holdings_inconclusive"

    sources = _unique_sources(sources)
    cohort_size = len(profile_sources)
    denominator = len(eligible)
    caveat = "The limited peer sample provides context for discovery. Confirm this client's needs separately."
    caveats = [caveat]
    if excluded:
        caveats.append("Some peer holdings are unresolved, so the observed distribution may differ from the full same-segment cohort.")
    metrics = (Metric("same_segment_cohort_size", cohort_size, "peer accounts", str(cohort_size), period),
               Metric("eligible_peer_count", denominator, "peer accounts", str(denominator), period))
    if not segment:
        text = "The snapshot leaves this account's segment unspecified. A same-segment peer cohort is unavailable."
    elif not cohort_size:
        text = f"The snapshot contains no other accounts in the {segment} segment. Peer holdings are unavailable."
    else:
        text = (f"The snapshot contains {cohort_size} other {segment} accounts. Their signed-current product holdings "
                f"are conclusive for {denominator}.")
    text += f" {caveat}"
    result.evidence.append(Evidence(scope_key, "peer_holdings", text, sources, metrics,
                                    period=period, caveats=tuple(caveats), priority=49))

    by_product = {}
    for product_id, product in definitions.items():
        numerator = sum(product_id in held for held in eligible.values()) if denominator else None
        by_product[product_id] = {"holding_peers": numerator, "eligible_peers": denominator}
        if numerator is None:
            continue
        text = (f"{numerator} of {denominator} other {segment} accounts with conclusive signed-current holdings "
                f"hold {product['name']} in this snapshot.")
        metrics = (
            Metric("holding_peer_count", numerator, "peer accounts", str(numerator), period, product_id),
            Metric("eligible_peer_count", denominator, "peer accounts", str(denominator), period, product_id),
        )
        result.evidence.append(Evidence(
            f"contextual.peer.{product_id}", "peer_holdings", text,
            _unique_sources([*sources, *catalogue_sources[product_id]]), metrics, product_id,
            period, priority=50 if numerator else 68,
        ))

    result.inputs["peer_holdings"] = inputs
    result.data["peer_holdings"] = {
        "cohort_evidence_key": scope_key, "same_segment_cohort_size": cohort_size, "eligible_peers": eligible,
        "excluded_peers": excluded, "products": by_product,
    }
    if not denominator or excluded:
        result.status = "qualified" if result.status != "unavailable" else result.status


def run(ctx: ToolContext, analyses: tuple[str, ...], prior: dict[str, ToolResult]) -> ToolResult:
    result = ToolResult()
    if {"meeting_context", "recorded_commitments"}.intersection(analyses):
        _meeting_evidence(ctx, result, analyses)
    catalogue_analyses = tuple(name for name in analyses if name in {"catalogue_relationships", "product_gaps", "peer_holdings"})
    if catalogue_analyses:
        _product_catalogue(ctx, result, catalogue_analyses, prior)
        if result.status == "empty" and result.data["catalogue"]:
            result.status = "qualified"
    return result
