"""Bounded evidence-led inquiries that consume already collected descriptive results."""

from decimal import Decimal
import re

from ..models import Evidence, ToolContext, ToolResult


QUESTIONS = {
    "decline_breakdown": "Which products show a decline in comparable periods?",
    "notes_record_discrepancies": "Do the meeting account and measured trend cover the same population, dates and units?",
    "contract_usage_reconciliation": "What recorded agreement covers observed consumption?",
    "need_product_alignment": "Which documented need is relevant to a catalogue candidate?",
    "prior_offer_context": "Does a recorded offer or objection affect the next discussion?",
    "renewal_execution_gaps": "What approval, signature or interim coverage remains unconfirmed?",
    "commercial_change_context": "What change can be explained from recorded terms and proposals?",
    "value_evidence_inquiry": "Which account outcome, if any, can measure the reported benefit?",
    "coverage_inquiry": "How does an observed coverage defect constrain interpretation?",
}


def _assess(result, analysis, status, supporting=(), counter=(), *, missing="", fact="", decision="",
            product_id=None, period=""):
    result.data["inquiries"][analysis] = {
        "question": QUESTIONS[analysis], "conclusion": status, "missing": missing,
        "supporting_evidence_keys": [item.key for item in supporting],
        "counterevidence_keys": [item.key for item in counter], "decision_basis": decision,
        "product_id": product_id, "period": period,
    }
    if status in {"insufficient_evidence", "not_testable"}:
        result.status = "qualified"
    if not fact:
        return
    refs = {(row.table, row.rowid): row for item in (*supporting, *counter) for row in item.sources}
    result.evidence.append(Evidence(
        f"inquiry.{analysis}", analysis, fact, tuple(refs.values()),
        tuple(metric for item in (*supporting, *counter) for metric in item.metrics),
        product_id=product_id, period=period, caveats=(missing,) if missing else (), priority=17,
    ))


def _note_statement(item):
    return item.text.partition(" recorded “")[2].removesuffix("”").casefold()


def _number(item, metric):
    value = next((entry.value for entry in item.metrics if entry.name == metric), None)
    return Decimal(value) if value is not None else None


def _notes_discrepancy(result, notes, trends):
    analysis = "notes_record_discrepancies"
    usage_words = re.compile(r"\b(?:use|usage|consumption|hours|calls|markets|impressions|users|viewers)\b")
    increase = re.compile(r"\b(?:up|increas\w*|grow\w*|rose|higher)\b")
    decrease = re.compile(r"\b(?:down|declin\w*|fell|lower|decreas\w*)\b")
    for note in notes:
        statement = _note_statement(note)
        if not usage_words.search(statement):
            continue
        directions = (bool(increase.search(statement)), bool(decrease.search(statement)))
        if directions[0] == directions[1]:
            continue
        for trend in trends:
            count = _number(trend, "percentage_change")
            products = [record.raw["product_name"].casefold() for record in trend.sources if record.table == "products"]
            # A catalogue product name or its distinctive tail word identifies the subject.
            names = {name for name in products} | {name.split()[-1] for name in products if len(name.split()[-1]) > 4}
            if count is None or not any(re.search(rf"\b{re.escape(name)}\b", statement) for name in names):
                continue
            if not ((directions[0] and count < 0) or (directions[1] and count > 0)):
                continue
            missing = "The note does not establish the same comparison population, months and units as the export."
            _assess(result, analysis, "insufficient_evidence", (note,), (trend,), missing=missing,
                    fact=f"{note.text} {trend.text} {missing}",
                    decision="Opposite directions on a named product; note scope remains unverified.",
                    product_id=trend.product_id, period=trend.period)
            return
    _assess(result, analysis, "insufficient_evidence", missing="No comparable directional statement and record pair was found.",
            decision="Checked dated note statements and usable product comparisons.")


def _decline(result, trends):
    analysis = "decline_breakdown"
    comparable = [(item, _number(item, "percentage_change")) for item in trends]
    usable = [(item, amount) for item, amount in comparable if amount is not None]
    ranked = sorted(((amount, item.key, item) for item, amount in usable if amount < 0), key=lambda row: row[:2])
    declines = [item for _, _, item in ranked]
    if not usable:
        _assess(result, analysis, "insufficient_evidence", tuple(trends), missing="Comparable product history is unavailable.")
    elif not declines:
        _assess(result, analysis, "contradicted", tuple(item for item, _ in usable),
                decision="No decline in the product periods with a valid percentage comparison.")
    else:
        _assess(result, analysis, "supported", tuple(declines), tuple(item for item, amount in usable if amount >= 0),
                decision="Ranked product-specific changes by percentage without combining consumption units.")


def run(ctx: ToolContext, analyses: tuple[str, ...], prior: dict[str, ToolResult]) -> ToolResult:
    result = ToolResult(data={"inquiries": {}}, inputs={"client_id": ctx.client_id, "as_of": str(ctx.as_of),
                                                      "follow_up_rounds": 1, "strategy": "one pass over descriptive evidence"})
    evidence = [item for tool in prior.values() for item in tool.evidence]
    notes = [item for item in evidence if item.key.startswith("contextual.note.")]
    trends = [item for item in evidence if item.analysis == "usage_trends"]
    commercial = prior.get("commercial", ToolResult())
    for analysis in analyses:
        if analysis == "notes_record_discrepancies":
            _notes_discrepancy(result, notes, trends)
        elif analysis == "decline_breakdown":
            _decline(result, trends)
        elif analysis == "contract_usage_reconciliation":
            unmatched = tuple(item for item in evidence if item.key.endswith(".unconfirmed_term"))
            _assess(result, analysis, "supported" if unmatched else "insufficient_evidence", unmatched,
                    missing="Interim terms and authorization require independent confirmation." if unmatched else "No eligible mismatch identified.",
                    decision="Observed usage was checked against exported signed-term coverage.")
        elif analysis == "need_product_alignment":
            _need_product(result, evidence, notes)
        elif analysis == "prior_offer_context":
            offers = tuple(item for item in evidence if item.analysis == "opportunity_snapshot" and
                           any(ref.table == "crm_opportunities" and ref.raw["opp_type"] in {"cross_sell", "upsell"}
                               for ref in item.sources))
            _assess(result, analysis, "supported" if offers else "insufficient_evidence", offers,
                    missing="Later client clearance or current appetite requires confirmation.",
                    decision="Inspected recorded expansion offers and their stages or objections.")
        elif analysis == "renewal_execution_gaps":
            _renewal_execution(result, commercial, notes)
        elif analysis == "commercial_change_context":
            records = tuple(item for item in evidence if item.analysis in {"contract_changes", "proposal_comparison"}
                            and item.metrics and not item.caveats)
            _assess(result, analysis, "supported" if records else "insufficient_evidence", records,
                    missing="Commercial intent and acceptance require direct confirmation.",
                    decision="Used amounts or scope changes from supported signed or proposed terms.")
        elif analysis == "value_evidence_inquiry":
            statements = tuple(item for item in notes if re.search(r"\b(?:success|delighted|happy|valued)\b", _note_statement(item)))
            _assess(result, analysis, "not_testable", statements, missing="No independently measured client-outcome series is present.",
                    decision="Attributed benefits, product usage and measured outcomes have separate meanings.")
        elif analysis == "coverage_inquiry":
            defects = tuple(item for item in evidence if item.key.endswith(".coverage_gap") or
                            (item.analysis == "usage_trends" and item.caveats))
            _assess(result, analysis, "supported" if defects else "insufficient_evidence", defects,
                    missing=("The cause of the observed coverage defect requires confirmation." if defects else
                             "No coverage defect in the selected product windows was identified for further assessment."),
                    decision="Checked product windows and their eligibility limitations.")
    return result


def _need_product(result, evidence, notes):
    analysis = "need_product_alignment"
    gaps = [item for item in evidence if item.key.startswith("contextual.gap.")]
    requests = [item for item in notes if re.search(r"\b(?:ask\w*|want\w*|need\w*|interest\w*)\b", _note_statement(item))]
    for gap in gaps:
        products = [ref.raw for ref in gap.sources if ref.table == "products" and ref.key == gap.product_id]
        if not products:
            continue
        product = products[0]
        description = " ".join((product["product_name"], product["description"])).casefold()
        for request in requests:
            statement = _note_statement(request)
            named = product["product_name"].casefold() in statement
            features = set(re.findall(r"\b(?:embeddable|integrity|latency|line-ups|dashboards)\b", description))
            matched = any(re.search(rf"\b{word}\b", statement) for word in features)
            if named or matched:
                _assess(result, analysis, "supported", (request, gap),
                        missing="The match supports discovery. Budget, feasibility and purchase intent remain unconfirmed.",
                        decision="An attributed request uses the product name or a distinctive catalogue capability.")
                return
    _assess(result, analysis, "insufficient_evidence", missing="A documented need could not be linked to a catalogue gap.",
            decision="Checked client requests for a direct product or capability match.")


def _renewal_execution(result, commercial, notes):
    analysis = "renewal_execution_gaps"
    pending = set(commercial.data.get("pending_contract_ids", ()))
    drafts = [item for item in commercial.evidence if item.key.startswith("commercial.contract.") and
              item.key.rsplit(".", 1)[-1] in pending and
              any(ref.table == "contracts" and ref.raw["contract_type"] == "renewal" for ref in item.sources)]
    renewals = [item for item in commercial.evidence if item.analysis == "opportunity_snapshot" and
                any(ref.table == "crm_opportunities" and ref.raw["opp_type"] == "renewal" for ref in item.sources)]
    reported = next((item for item in notes if re.search(r"\bagreed commercially\b|\bbasically agreed\b", _note_statement(item))), None)
    if reported and (drafts or renewals):
        counterpart = (drafts or renewals)[0]
        _assess(result, analysis, "supported" if drafts else "insufficient_evidence", (reported,), (counterpart,),
                missing="Confirm the remaining approvals and whether later execution or interim authority is documented.",
                decision="Compared an attributed discussion with the recorded draft or CRM renewal state.")
    else:
        refs = tuple((drafts or renewals)[:1])
        _assess(result, analysis, "supported" if drafts else "insufficient_evidence", refs,
                missing="The available records do not confirm later approval, signature or interim authority.",
                decision="Checked renewal stage and draft state as of the snapshot.")
