"""Account-scoped contract, renewal and opportunity evidence."""

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from ..models import Evidence, Metric, Source, ToolContext, ToolResult, source

CONTRACT_ANALYSES = {"contract_position", "renewal_calendar", "contract_reconciliation", "contract_changes",
                     "proposal_comparison"}
CRM_ANALYSES = {"opportunity_snapshot", "opportunity_age", "proposal_comparison"}
OPEN_STAGES = {"Qualification", "Proposal", "Negotiation"}


def _date(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def _nonnegative_int(value: str | None) -> int | None:
    try:
        number = int(value)
        return number if number >= 0 else None
    except (TypeError, ValueError):
        return None


def _groups(rows, table: str, id_column: str) -> dict[str, tuple[Source, ...]]:
    groups = defaultdict(list)
    for row in rows:
        reference = source(table, id_column, row)
        groups[reference.key].append(reference)
    return {key: tuple(refs) for key, refs in groups.items()}


def _deduplicate(
    references: tuple[Source, ...], result: ToolResult, analysis: str
) -> dict | None:
    values = references[0].raw
    if len(references) == 1:
        return values
    table = references[0].table
    key = references[0].key
    equivalent = all(ref.raw == values for ref in references)
    issue = "Equivalent physical rows were counted once." if equivalent else (
        "Conflicting rows share the same source ID. The record was excluded from calculations."
    )
    result.evidence.append(Evidence(
        f"commercial.rows.{table}.{key}", analysis, f"{table} record {key}. {issue}",
        references, caveats=(issue,), priority=22,
    ))
    result.data["row_decisions"].append({
        "table": table, "key": key, "rowids": [ref.rowid for ref in references],
        "decision": "counted_once" if equivalent else "excluded_conflict",
    })
    return values if equivalent else None


def _state(row: dict, as_of: date) -> tuple[str, date | None, date | None, str]:
    start, end, signed = (_date(row[field]) for field in ("start_date", "end_date", "signed_date"))
    status = row["status"]
    if status == "pending_signature":
        caveat = "The pending signature label conflicts with a populated signature date." if row["signed_date"] else ""
        if not start or not end or start > end:
            caveat = "The pending record has missing or invalid term dates."
        return "pending", start, end, caveat
    if not start or not end or not signed or start > end:
        return "unknown", start, end, "Signed term dates are missing or invalid."
    if status not in {"active", "expired"}:
        return "unknown", start, end, "The contract's recorded status is unrecognised."
    if signed > as_of or signed > end:
        return "unknown", start, end, "The signature date cannot establish execution for this snapshot and term."
    if end < as_of:
        conflict = "The active status label conflicts with the elapsed end date." if status == "active" else ""
        return "expired", start, end, conflict
    if start > as_of:
        return "future", start, end, "The status label differs from the signed term's future start date."
    if status == "expired":
        return "unknown", start, end, "The expired status label conflicts with dates spanning the snapshot."
    return "current", start, end, ""


def _notice(
    contract_id: str, refs: tuple[Source, ...], end: date, row: dict, as_of: date
) -> Evidence:
    notice_days = _nonnegative_int(row["notice_period_days"])
    term_months = _nonnegative_int(row["term_months"])
    auto = {"Y": "The recorded terms provide for auto-renewal.",
            "N": "The recorded terms have no auto-renewal."}.get(row["auto_renew"], "Auto-renewal is unknown.")
    period = f"snapshot {as_of}"
    metrics = [Metric("term_end", str(end), "date", str(end), period)]
    caveats = ["Notice delivery and waivers are absent from the available records. The recorded rule does not establish renewal execution."]
    sentences = [f"Contract {contract_id} ends on {end}."]
    if term_months:
        sentences.append(f"The recorded term is {term_months} months.")
        metrics.append(Metric("term_months", term_months, "months", str(term_months), period))
    else:
        caveats.append("The recorded term duration is unavailable.")
    if notice_days is None or notice_days > (end - date.min).days:
        sentences.append("The recorded notice deadline cannot be calculated.")
        caveats.append("The notice period is missing or invalid.")
    else:
        deadline = end - timedelta(days=notice_days)
        remaining = (deadline - as_of).days
        if remaining < 0:
            timing = f"{abs(remaining)} days before the snapshot"
        elif remaining == 0:
            timing = "on the snapshot date"
        else:
            timing = f"{remaining} days after the snapshot"
        sentences.append(f"The {notice_days}-day notice period puts the recorded deadline on {deadline}, {timing}.")
        metrics.extend((
            Metric("notice_period", notice_days, "days", str(notice_days), period),
            Metric("notice_deadline", str(deadline), "date", str(deadline), period),
            Metric("days_to_notice_deadline", remaining, "signed days from snapshot", str(abs(remaining)), period),
        ))
    sentences.append(auto)
    return Evidence(f"commercial.notice.{contract_id}", "renewal_calendar", " ".join(sentences), refs,
                    tuple(metrics), period=period, caveats=tuple(caveats), priority=5)


def _contract_sources(result: ToolResult) -> dict[str, tuple[Source, ...]]:
    return {
        item.key.removeprefix("commercial.contract."): item.sources
        for item in result.evidence if item.key.startswith("commercial.contract.")
    }


def _unique_product_lines(refs: tuple[Source, ...], products: dict) -> tuple[dict[str, dict], str]:
    line_groups = _groups_by_key(ref for ref in refs if ref.table == "contract_lines")
    if not line_groups:
        return {}, "No product-line records were found."
    if any(any(ref.raw != group[0].raw for ref in group) for group in line_groups):
        return {}, "Conflicting physical records share a contract-line ID."
    rows = [group[0].raw for group in line_groups]
    by_product = {row["product_id"]: row for row in rows}
    if len(by_product) != len(rows):
        return {}, "Multiple distinct contract lines share a product without a component breakdown."
    if by_product.keys() - products.keys():
        return {}, "A product line does not match the product catalogue."
    return by_product, ""


def _groups_by_key(refs) -> list[list[Source]]:
    groups = defaultdict(list)
    for ref in refs:
        groups[ref.key].append(ref)
    return list(groups.values())


def _signed_money(value: int) -> str:
    return f"{'−' if value < 0 else '+' if value > 0 else ''}€{abs(value):,}"


def _term_period(row: dict) -> str:
    return f"{row['start_date']} through {row['end_date']}"


def _reconcile_contracts(result: ToolResult, products: dict) -> None:
    sources = _contract_sources(result)
    summaries = []
    for duplicate in result.evidence.copy():
        if not duplicate.key.startswith("commercial.rows.contracts.") or not any(
            ref.raw != duplicate.sources[0].raw for ref in duplicate.sources
        ):
            continue
        key = duplicate.sources[0].key
        reason = "Conflicting header records prevent confirmation of a signed contract and its ARR."
        result.evidence.append(Evidence(
            f"commercial.reconciliation.{key}", "contract_reconciliation", f"Contract {key}. {reason}",
            duplicate.sources, caveats=(reason,), priority=40,
        ))
        summaries.append({"contract_id": key, "header_arr_eur": None, "line_fee_total_eur": None,
                          "line_minus_header_eur": None, "problem": reason})
    for row in result.data["contracts"]:
        if row["position"] not in {"current", "expired"}:
            continue
        key, header = row["contract_id"], row["arr_eur"]
        refs = sources[key]
        lines, problem = _unique_product_lines(refs, products)
        fees = [_nonnegative_int(line["annual_fee_eur"]) for line in lines.values()]
        if not problem and (header is None or any(fee is None for fee in fees)):
            problem = "The signed header or a line lacks a valid annual EUR amount."
        period = _term_period(row)
        caveats, metrics = (), ()
        line_total, difference = None, None
        if problem:
            text = f"The signed ARR and product-line fees for {key} cannot be reconciled. {problem}"
            caveats = (problem,)
        else:
            line_total = sum(fees)
            difference = line_total - header
            amount_header, amount_lines = f"€{header:,}", f"€{line_total:,}"
            variance = _signed_money(difference)
            text = (f"Signed contract {key} records {amount_header} in header ARR and {amount_lines} in annual "
                    f"product-line fees for {period}. Line fees minus header ARR equal {variance}.")
            metrics = (
                Metric("signed_header_arr", header, "EUR/year", amount_header, period),
                Metric("signed_line_fee_total", line_total, "EUR/year", amount_lines, period),
                Metric("line_fees_minus_header_arr", difference, "EUR/year", variance, period),
            )
            if difference:
                caveats = ("The signed header and line fees disagree. Billing and amendment records were unavailable for reconciliation.",)
                text += f" {caveats[0]}"
        result.evidence.append(Evidence(
            f"commercial.reconciliation.{key}", "contract_reconciliation", text, refs, metrics,
            period=period, caveats=caveats, priority=40 if problem or difference else 65,
            model_visible=bool(problem or difference),
        ))
        summaries.append({"contract_id": key, "header_arr_eur": header, "line_fee_total_eur": line_total,
                          "line_minus_header_eur": difference, "problem": problem})
    result.data["contract_reconciliation"] = summaries


def _compare_product_lines(
    previous: dict, recent: dict, previous_lines: dict, recent_lines: dict,
    refs: tuple[Source, ...], products: dict, result: ToolResult,
) -> None:
    prior_term, recent_term = previous["contract_id"], recent["contract_id"]
    period = f"{_term_period(previous)} compared with {_term_period(recent)}"
    for product_id in sorted(previous_lines.keys() & recent_lines.keys()):
        old, new = previous_lines[product_id], recent_lines[product_id]
        product_sources = products[product_id]
        product = product_sources[0].raw
        text, metrics, caveats = [], [], []
        for field, label, unit in (
            ("annual_fee_eur", "annual fee", "EUR/year"),
            ("included_units_monthly", "monthly allowance", f"{product['usage_unit']} per month"),
        ):
            before, after = _nonnegative_int(old[field]), _nonnegative_int(new[field])
            if before is None or after is None:
                caveats.append(f"The recorded {label} is missing or invalid for a term.")
                continue
            if before == after:
                continue
            money = field == "annual_fee_eur"
            first, second = (f"€{v:,}" if money else f"{v:,}" for v in (before, after))
            suffix = "" if money else f" {unit}"
            text.append(f"The {label} changed from {first} to {second}{suffix}.")
            metrics.extend((Metric(f"previous_{field}", before, unit, first, _term_period(previous), product_id),
                            Metric(f"later_{field}", after, unit, second, _term_period(recent), product_id)))
        if not text and not caveats:
            continue
        claim = " ".join([f"{product['product_name']} in signed contracts {prior_term} and {recent_term}.",
                          *text, *caveats])
        product_refs = tuple(ref for ref in refs if ref.table == "contracts" or ref.raw["product_id"] == product_id)
        result.evidence.append(Evidence(
            f"commercial.change.{prior_term}.{recent_term}.{product_id}", "contract_changes", claim,
            product_refs + product_sources, tuple(metrics), product_id, period, tuple(caveats), priority=60,
        ))


def _compare_signed_terms(result: ToolResult, products: dict) -> None:
    sources = _contract_sources(result)
    signed = sorted((row for row in result.data["contracts"] if row["position"] in {"current", "expired"}),
                    key=lambda row: (_date(row["start_date"]), row["contract_id"]))
    unresolved = any(row["position"] == "unknown" for row in result.data["contracts"]) or any(
        entry["table"] == "contracts" and entry["decision"] == "excluded_conflict"
        for entry in result.data["row_decisions"]
    )
    dates = {row["contract_id"]: (_date(row["start_date"]), _date(row["end_date"])) for row in signed}
    overlapping = {key for key, (start, end) in dates.items() if any(
        key != other and start <= other_end and other_start <= end
        for other, (other_start, other_end) in dates.items()
    )}
    if len(signed) < 2:
        holdings = next(item for item in result.evidence if item.key == "commercial.holdings")
        reason = "The signed history is too short to compare consecutive executed terms."
        result.evidence.append(Evidence("commercial.change.history", "contract_changes", reason, holdings.sources,
                                        caveats=(reason,), priority=67))
    for previous, recent in zip(signed, signed[1:]):
        prior_term, recent_term = previous["contract_id"], recent["contract_id"]
        pair_key = f"commercial.change.{prior_term}.{recent_term}"
        refs = sources[prior_term] + sources[recent_term]
        period = f"{_term_period(previous)} compared with {_term_period(recent)}"
        if unresolved or {prior_term, recent_term} & overlapping:
            holdings = next(item for item in result.evidence if item.key == "commercial.holdings")
            reason = ("Other overlapping or unresolved contract records prevent a reliable consecutive-term comparison.")
            result.evidence.append(Evidence(pair_key, "contract_changes", f"{prior_term} and {recent_term}. {reason}",
                                            holdings.sources, period=period, caveats=(reason,), priority=51))
            continue
        previous_lines, prior_problem = _unique_product_lines(sources[prior_term], products)
        recent_lines, recent_problem = _unique_product_lines(sources[recent_term], products)
        statements = [f"Signed contract {prior_term} ({_term_period(previous)}) compared with "
                      f"{recent_term} ({_term_period(recent)})."]
        caveats, metrics = [], []
        for row in (previous, recent):
            term = _term_period(row)
            metrics.extend(Metric(field, row[field], "date", row[field], term) for field in ("start_date", "end_date"))
        gap_days = (dates[recent_term][0] - dates[prior_term][1]).days - 1
        if gap_days:
            caveats.append(f"The recorded terms leave {gap_days} days of unconfirmed coverage between them.")
            statements.append(caveats[-1])
            metrics.append(Metric("days_between_signed_terms", gap_days, "days", str(gap_days), period))
        product_refs = ()
        if prior_problem or recent_problem:
            caveats.append("Product scope cannot be compared because a term lacks reliable line coverage.")
            statements.append(caveats[-1])
        else:
            added = sorted(recent_lines.keys() - previous_lines.keys())
            removed = sorted(previous_lines.keys() - recent_lines.keys())
            product_refs = tuple(ref for product_id in added + removed for ref in products[product_id])
            for ids, phrase in ((added, "adds {} to"), (removed, "removes {} from")):
                if ids:
                    names = ", ".join(products[product_id][0].raw["product_name"] for product_id in ids)
                    statements.append(f"The later term {phrase.format(names)} the recorded product scope.")
            if not added and not removed:
                statements.append("The recorded product set is unchanged.")
            _compare_product_lines(previous, recent, previous_lines, recent_lines, refs, products, result)
        before, after = previous["arr_eur"], recent["arr_eur"]
        if before is not None and after is not None:
            delta = after - before
            old_amount, new_amount, delta_amount = f"€{before:,}", f"€{after:,}", _signed_money(delta)
            statements.append(f"Signed header ARR changed from {old_amount} to {new_amount}, a difference of {delta_amount}.")
            metrics.extend((Metric("previous_header_arr", before, "EUR/year", old_amount, _term_period(previous)),
                            Metric("later_header_arr", after, "EUR/year", new_amount, _term_period(recent)),
                            Metric("signed_header_arr_difference", delta, "EUR/year", delta_amount, period)))
        else:
            caveats.append("A recorded EUR header ARR amount is unavailable for a term.")
        result.evidence.append(Evidence(pair_key, "contract_changes", " ".join(statements), refs + product_refs,
                                        tuple(metrics), period=period, caveats=tuple(caveats), priority=49))


def _contracts(ctx: ToolContext, analyses: tuple[str, ...], result: ToolResult) -> None:
    db = ctx.db
    as_of = ctx.as_of
    period = f"snapshot {as_of}"
    rows = db.execute("SELECT rowid, * FROM contracts WHERE client_id = ? ORDER BY contract_id, rowid", (ctx.client_id,))
    groups = _groups(rows, "contracts", "contract_id")
    product_rows = db.execute("SELECT rowid, * FROM products ORDER BY product_id, rowid")
    products = _groups(product_rows, "products", "product_id")
    current: list[dict] = []
    held, history, proposed = set(), set(), set()
    holding_refs: list[Source] = []
    incomplete_holdings = False
    contracts = []
    expired = []
    pending = []
    for key, refs in groups.items():
        holding_refs.extend(refs)
        row = _deduplicate(refs, result, "contract_position")
        if row is None:
            incomplete_holdings = True
            continue
        state, start, end, conflict = _state(row, as_of)
        if state == "unknown":
            incomplete_holdings = True
        line_rows = db.execute("SELECT rowid, * FROM contract_lines WHERE contract_id = ? ORDER BY line_id, rowid", (key,))
        lines = _groups(line_rows, "contract_lines", "line_id")
        line_refs, line_product_ids = [], set()
        if state == "current" and not lines:
            incomplete_holdings = True
        for group in lines.values():
            line_refs.extend(group)
            line = _deduplicate(group, result, "contract_position")
            if line and line["product_id"] in products:
                line_product_ids.add(line["product_id"])
            elif state == "current":
                incomplete_holdings = True
        holding_refs.extend(line_refs)
        arr = _nonnegative_int(row["arr_eur"]) if row["currency"] == "EUR" else None
        arr_text = f"€{arr:,}" if arr is not None else "an unavailable amount"
        product_ids = sorted(line_product_ids)
        contract = dict(row, position=state, arr_eur=arr, product_ids=product_ids)
        contracts.append(contract)
        dates = f" from {start} through {end}" if start and end and start <= end else " with unconfirmed term dates"
        caveats = [conflict] if conflict else []
        if arr is None:
            caveats.append("An EUR ARR amount is unavailable for this record.")
        if state == "current":
            current.append(contract)
            held.update(product_ids)
            text = f"Signed contract {key} is in force{dates} and records {arr_text} in ARR."
            priority = 9
            if "renewal_calendar" in analyses:
                result.evidence.append(_notice(key, refs, end, row, as_of))
        elif state == "expired":
            expired.append((end, key, row, refs))
            history.update(product_ids)
            text = f"Expired signed contract {key} covered the term{dates} and recorded {arr_text} in ARR during that term."
            priority = 27
        elif state == "pending":
            pending.append(key)
            proposed.update(product_ids)
            text = f"Contract {key} is marked pending signature, with a proposed term{dates} and proposed ARR of {arr_text}. The draft is outside signed coverage."
            priority = 7
        elif state == "future":
            text = f"Signed contract {key} records a future term{dates} and {arr_text} in ARR for that term."
            priority = 15
        else:
            text = f"The snapshot cannot confirm whether contract {key} is in force."
            priority = 10
        metrics = []
        if start and end and start <= end and state != "unknown":
            metrics.extend((Metric("term_start", str(start), "date", str(start), period),
                            Metric("term_end", str(end), "date", str(end), period)))
        if arr is not None and state != "unknown":
            metrics.append(Metric(f"{state}_arr", arr, "EUR annual recurring revenue", arr_text, period))
        if conflict:
            text += f" {conflict}"
        result.evidence.append(Evidence(
            f"commercial.contract.{key}", "contract_position", text, refs + tuple(line_refs), tuple(metrics),
            period=period, caveats=tuple(caveats), priority=priority,
        ))

    if not current and expired and "renewal_calendar" in analyses:
        end, key, row, refs = max(expired, key=lambda item: (item[0], item[1]))
        days = (as_of - end).days
        position = "The snapshot cannot confirm a signed current term." if incomplete_holdings else (
            "The available records show no signed term covering the snapshot."
        )
        former_notice = _notice(key, refs, end, row, as_of)
        deadline = next((metric for metric in former_notice.metrics if metric.name == "notice_deadline"), None)
        notice_text = (f" That term's recorded notice deadline was {deadline.display}." if deadline else
                       " The historical notice deadline is unavailable.")
        result.evidence.append(Evidence(
            f"commercial.elapsed.{key}", "renewal_calendar",
            f"The most recent confirmed signed term, {key}, ended on {end}, {days} days before the snapshot."
            f"{notice_text} {position}",
            refs + tuple(ref for ref in holding_refs if ref.table == "contracts" and ref.key != key),
            (Metric("last_signed_term_end", str(end), "date", str(end), period),
             Metric("days_since_expiry", days, "days", str(days), period)) + ((deadline,) if deadline else ()),
            period=period, caveats=former_notice.caveats, priority=4,
        ))

    overlapping = len(current) > 1
    conclusive = bool(current) and not (overlapping or incomplete_holdings)
    names, product_refs = [], []
    for product_id in sorted(held):
        product_refs.extend(products[product_id])
        names.append(products[product_id][0].raw["product_name"])
    caveats = []
    if not current:
        wording = "The snapshot does not confirm any signed in-force product holdings or current ARR."
        caveats.append("Current product scope and signed fees remain uncertain until executed terms are confirmed.")
        if not groups:
            holding_refs.append(source("clients", "client_id", ctx.client))
    elif conclusive:
        wording = f"Recorded signed in-force holdings are {', '.join(names)}."
    else:
        wording = f"Signed records show these current product lines: {', '.join(names)}." if names else (
            "The snapshot cannot establish a complete list of current product holdings."
        )
        caveats.append("Unresolved contract or line records make the current product list incomplete.")
    if overlapping:
        caveats.append("Overlapping signed terms make account-level ARR ambiguous. Contract amounts are reported separately.")
    if caveats and current:
        wording += " " + " ".join(caveats)
    result.evidence.append(Evidence(
        "commercial.holdings", "contract_position", wording, tuple(holding_refs + product_refs),
        period=period, caveats=tuple(caveats), priority=13 if not conclusive else 30,
    ))
    result.data.update(
        contracts=contracts,
        current_contract_ids=[row["contract_id"] for row in current],
        pending_contract_ids=pending,
        current_arr_eur=current[0]["arr_eur"] if len(current) == 1 and not incomplete_holdings else None,
        current_product_ids=sorted(held),
        historical_product_ids=sorted(history),
        pending_product_ids=sorted(proposed),
        holdings_conclusive=conclusive,
    )
    if "contract_reconciliation" in analyses:
        _reconcile_contracts(result, products)
    if "contract_changes" in analyses:
        _compare_signed_terms(result, products)


def _opportunity_age(
    row: dict, refs: tuple[Source, ...], created: date, expected_close: date | None, as_of: date,
) -> Evidence:
    key = row["opp_id"]
    product_id = row["product_id"] or None
    period = f"snapshot {as_of}"
    age = (as_of - created).days
    text = f"CRM opportunity {key} was created on {created}, {age} calendar days before the snapshot."
    caveats = ["The export has no stage-transition dates or actual closing date for these calculations."]
    metrics = [Metric("crm_created_date", str(created), "date", str(created), period, product_id),
               Metric("days_since_crm_creation", age, "days", str(age), period, product_id)]
    if row["stage"] in OPEN_STAGES:
        if not expected_close or expected_close < created:
            reason = "The CRM expected close date is missing or precedes creation."
            text += f" {reason}"
            caveats.append(reason)
        else:
            remaining = (expected_close - as_of).days
            timing = (f"{abs(remaining)} calendar days {'before' if remaining < 0 else 'after'} the snapshot"
                      if remaining else "on the snapshot date")
            text += f" The recorded expected close is {expected_close}, {timing}."
            metrics.extend((Metric("crm_expected_close_date", str(expected_close), "date", str(expected_close),
                                   period, product_id),
                            Metric("days_to_expected_close", remaining, "signed days from snapshot",
                                   str(abs(remaining)), period, product_id)))
    return Evidence(f"commercial.age.{key}", "opportunity_age", text, refs, tuple(metrics), product_id,
                    period, tuple(caveats), priority=44)


def _proposal_comparison(
    row: dict, amount: int | None, refs: tuple[Source, ...], as_of: date, result: ToolResult,
) -> Evidence:
    key = row["opp_id"]
    period = f"snapshot {as_of}"
    signed = result.data["current_arr_eur"]
    metrics = ()
    if amount is None or signed is None or row["product_id"]:
        signed_position = next(item for item in result.evidence if item.key == "commercial.holdings")
        refs += signed_position.sources
        reason = "The snapshot does not establish a comparable current signed ARR for the recorded proposal."
        if amount is None:
            reason = "The renewal opportunity lacks a valid proposed ARR amount."
        elif row["product_id"]:
            reason = "The opportunity is product-scoped and cannot be compared with signed whole-contract ARR."
        text = f"Renewal opportunity {key}. {reason}"
        caveats = (reason,)
    else:
        contract_id = result.data["current_contract_ids"][0]
        signed_position = next(item for item in result.evidence if item.key == f"commercial.contract.{contract_id}")
        refs += signed_position.sources
        delta = amount - signed
        amount_text, signed_text, delta_text = f"€{amount:,}", f"€{signed:,}", _signed_money(delta)
        text = (f"CRM renewal opportunity {key} records {amount_text} proposed ARR against {signed_text} signed ARR "
                f"in current contract {contract_id}. Proposed ARR minus current signed ARR is {delta_text}.")
        metrics = (Metric("proposed_renewal_arr", amount, "EUR/year", amount_text, period),
                   Metric("current_signed_arr", signed, "EUR/year", signed_text, period),
                   Metric("proposal_minus_current_arr", delta, "EUR/year", delta_text, period))
        if signed > 0:
            percent = (Decimal(delta) * 100 / signed).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
            if percent == 0:
                percent = abs(percent)
            pct = f"{percent:+.1f}%" if percent else "0.0%"
            text += f" That difference is {pct} of current signed ARR."
            metrics += (Metric("proposal_arr_difference_pct", str(percent), "percent of current signed ARR", pct,
                               period),)
        caveats = ("The proposal lacks a comparable signed scope breakdown or evidence of execution.",)
        text += " The proposal scope and execution remain unconfirmed."
    return Evidence(f"commercial.proposal.{key}", "proposal_comparison", text, refs, metrics,
                    period=period, caveats=caveats, priority=39)


def _opportunities(ctx: ToolContext, analyses: tuple[str, ...], result: ToolResult) -> None:
    db = ctx.db
    rows = db.execute("SELECT rowid, * FROM crm_opportunities WHERE client_id = ? ORDER BY opp_id, rowid", (ctx.client_id,))
    groups = _groups(rows, "crm_opportunities", "opp_id")
    rows = db.execute("SELECT rowid, * FROM products ORDER BY product_id, rowid")
    products = _groups(rows, "products", "product_id")
    period = f"snapshot {ctx.as_of}"
    opportunities, active_products = [], set()
    for key, refs in groups.items():
        row = _deduplicate(refs, result, "opportunity_snapshot")
        if row is None:
            continue
        created = _date(row["created_date"])
        if not created or created > ctx.as_of:
            reason = "The opportunity creation date is invalid or after the reporting snapshot."
            result.evidence.append(Evidence(f"commercial.opportunity.{key}", "opportunity_snapshot", reason,
                                            refs, period=period, caveats=(reason,), priority=25))
            continue
        expected_close = _date(row["expected_close_date"])
        stage = row["stage"]
        active = stage in OPEN_STAGES
        amount = _nonnegative_int(row["amount_eur"])
        opportunity = dict(row, amount_eur=amount)
        opportunities.append(opportunity)
        product_id = row["product_id"] or None
        product_refs = products.get(product_id, ())
        product = product_refs[0].raw["product_name"] if product_refs else product_id
        if product_id and active:
            active_products.add(product_id)
        description = row["opp_type"].replace("_", "-")
        subject = f"{description} opportunity for {product}" if product else f"{description} opportunity"
        statements = [f"CRM lists the {subject} {key} at {stage}."]
        metrics, caveats = [], []
        priority = 26 if row["opp_type"] == "renewal" and active else 36 if active else 42
        if amount is not None:
            label = "proposed ARR" if row["opp_type"] == "renewal" else "recorded annual opportunity value"
            display = f"€{amount:,}"
            statements.append(f"The {label} is {display}.")
            metrics.append(Metric("opportunity_amount", amount, f"EUR {label}", display, period, product_id))
        if expected_close and active:
            date_phrase = f"The recorded expected close was {expected_close} and has passed." if expected_close < ctx.as_of else (
                f"The recorded expected close is {expected_close}."
            )
            statements.append(date_phrase)
            metrics.append(Metric("expected_close", str(expected_close), "date", str(expected_close), period, product_id))
        if row["next_step"]:
            wording = "next step" if active else "context"
            statements.append(f"Recorded CRM {wording} is ‘{row['next_step'].rstrip('.')}’.")
        if stage == "Closed Won":
            caveats.append("The CRM Closed Won stage does not establish signed terms or incremental ARR.")
        result.evidence.append(Evidence(
            f"commercial.opportunity.{key}", "opportunity_snapshot", " ".join(statements), refs + product_refs,
            tuple(metrics), product_id, period, tuple(caveats), priority,
        ))
        if "opportunity_age" in analyses:
            result.evidence.append(_opportunity_age(row, refs, created, expected_close, ctx.as_of))
        if "proposal_comparison" in analyses and row["opp_type"] == "renewal" and active:
            result.evidence.append(_proposal_comparison(row, amount, refs, ctx.as_of, result))
    result.data.update(opportunities=opportunities, active_opportunity_product_ids=sorted(active_products))


def run(ctx: ToolContext, analyses: tuple[str, ...], prior: dict[str, ToolResult]) -> ToolResult:
    result = ToolResult(data={"row_decisions": []}, inputs={
        "client_id": ctx.client_id, "as_of": str(ctx.as_of), "contract_dates": "inclusive",
        "notice_date": "recorded term end minus notice days",
        "opportunity_scope": "recorded CRM snapshot, not contract execution",
    })
    if CONTRACT_ANALYSES.intersection(analyses):
        _contracts(ctx, analyses, result)
    if CRM_ANALYSES.intersection(analyses):
        _opportunities(ctx, analyses, result)
    # Foundations used internally by the extension tools should not add an unselected analysis.
    if set(analyses) - {"contract_position", "renewal_calendar", "opportunity_snapshot"}:
        selected = set(analyses)
        result.evidence = [item for item in result.evidence if item.analysis in selected]
    if any(item.caveats for item in result.evidence):
        result.status = "qualified"
    elif not result.evidence:
        result.status = "empty"
    return result
