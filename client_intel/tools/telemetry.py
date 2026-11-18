"""Client-scoped usage and support calculations over the read-only snapshot."""

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Iterable

from ..models import Evidence, Metric, Source, SUPPORT_DAYS, ToolContext, ToolResult, USAGE_MONTHS, source


USAGE_HISTORY_MONTHS = 24
ENTITLEMENT_ANALYSES = {"entitlement_utilisation", "overage_exposure"}
USAGE_ANALYSES = ENTITLEMENT_ANALYSES | {"usage_history", "usage_trends"}


@dataclass(frozen=True)
class _TermCandidate:
    contracts: tuple[Source, ...]
    lines: tuple[Source, ...]


def _date(raw: str | None) -> date | None:
    try:
        parsed = date.fromisoformat(raw or "")
    except ValueError:
        return None
    return parsed if parsed.isoformat() == raw else None


def _month(raw: str) -> date | None:
    return _date(f"{raw}-01")


def _shift_month(first: date, offset: int) -> date:
    year, month = divmod(first.year * 12 + first.month - 1 + offset, 12)
    return date(year, month + 1, 1)


def _month_key(first: date) -> str:
    return first.isoformat()[:7]


def _value(raw: str | None) -> Decimal | None:
    try:
        number = Decimal(raw or "")
    except InvalidOperation:
        return None
    return number if number.is_finite() and number >= 0 else None


def _display(value: Decimal) -> str:
    formatted = format(value, ",f")
    return formatted.rstrip("0").rstrip(".") if "." in formatted else formatted


def _rollup_display(value: Decimal, average: bool) -> str:
    return _display(value.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP) if average else value)


def _count(value: int, singular: str) -> str:
    return f"{value} {singular}" if value == 1 else f"{value} {singular}s"


def _day_window(first: date, last: date) -> str:
    return f"{first.isoformat()} through {last.isoformat()}"


def _month_window(months: tuple[date, ...]) -> str:
    return f"{months[0]:%b %Y} through {months[-1]:%b %Y}"


def _references(records: Iterable[Source]) -> list[dict]:
    return [{"table": item.table, "id": item.key, "rowid": item.rowid} for item in records]


def _unique_sources(*groups: Iterable[Source]) -> tuple[Source, ...]:
    return tuple({(item.table, item.rowid): item for group in groups for item in group}.values())


def _usage_inputs(ctx: ToolContext) -> tuple[dict, dict, list[date]]:
    history = [
        _shift_month(ctx.as_of, offset)
        for offset in range(-USAGE_HISTORY_MONTHS, 0)
    ]
    catalogue = {
        row["product_id"]: source("products", "product_id", row)
        for row in ctx.db.execute("SELECT rowid, * FROM products ORDER BY rowid")
    }
    used = defaultdict(list)
    for row in ctx.db.execute(
        "SELECT rowid, * FROM usage_monthly WHERE client_id = ? ORDER BY product_id, month, rowid",
        (ctx.client_id,),
    ):
        used[row["product_id"]].append(source("usage_monthly", "usage_id", row))
    return catalogue, used, history


def _signed_usage_terms(ctx: ToolContext) -> tuple[dict, list[Source]]:
    contracts = defaultdict(list)
    for row in ctx.db.execute(
        "SELECT rowid, * FROM contracts WHERE client_id = ? ORDER BY rowid", (ctx.client_id,)
    ):
        contracts[row["contract_id"]].append(source("contracts", "contract_id", row))

    terms = defaultdict(list)
    for key, occurrences in contracts.items():
        if any(item.raw != occurrences[0].raw for item in occurrences[1:]):
            continue
        contract = occurrences[0].raw
        signed = _date(contract["signed_date"])
        start = _date(contract["start_date"])
        end = _date(contract["end_date"])
        if contract["status"] not in {"active", "expired"} or not (signed and start and end):
            continue
        effective_start = max(start, signed)
        if effective_start > end or effective_start > ctx.as_of:
            continue
        for row in ctx.db.execute(
            "SELECT rowid, * FROM contract_lines WHERE contract_id = ? ORDER BY rowid", (key,)
        ):
            line = source("contract_lines", "line_id", row)
            for record in occurrences:
                terms[row["product_id"]].append((effective_start, end, record, line))
    return terms, [record for occurrences in contracts.values() for record in occurrences]


def _entitlement_candidates(ctx: ToolContext, contracts: list[Source]) -> dict[str, list[_TermCandidate]]:
    by_contract = defaultdict(list)
    for contract in contracts:
        by_contract[contract.key].append(contract)
    lines = defaultdict(list)
    for row in ctx.db.execute(
        """SELECT l.rowid AS rowid, l.* FROM contract_lines AS l
           WHERE EXISTS (SELECT 1 FROM contracts AS c WHERE c.contract_id = l.contract_id AND c.client_id = ?)
           ORDER BY l.contract_id, l.product_id, l.rowid""",
        (ctx.client_id,),
    ):
        lines[row["contract_id"], row["product_id"]].append(source("contract_lines", "line_id", row))
    candidates = defaultdict(list)
    for (contract_id, product_id), records in lines.items():
        candidates[product_id].append(_TermCandidate(tuple(by_contract[contract_id]), tuple(records)))
    return candidates


def _overlaps_recorded_month(contract: Source, month: date, last_day: date) -> bool:
    start = _date(contract.raw["start_date"])
    end = _date(contract.raw["end_date"])
    if not (start and end) or start > end:
        return True  # An unplaceable candidate cannot rule out conflicting coverage.
    return start <= last_day and end >= month


def _effective_line(
    month: date, candidates: list[_TermCandidate],
) -> tuple[Source | None, str | None, tuple[Source, ...]]:
    last_day = _shift_month(month, 1) - timedelta(days=1)
    considered = []
    eligible = []
    conflict = False
    for candidate in candidates:
        if not any(_overlaps_recorded_month(contract, month, last_day) for contract in candidate.contracts):
            continue
        considered.extend((*candidate.contracts, *candidate.lines))
        header = candidate.contracts[0].raw
        if any(record.raw != header for record in candidate.contracts[1:]):
            conflict = True
            continue
        if header["status"] == "pending_signature" and not header["signed_date"]:
            continue
        start, end, signed = (_date(header[key]) for key in ("start_date", "end_date", "signed_date"))
        if (header["status"] not in {"active", "expired"} or not (start and end and signed)
                or start > end or signed > end):
            conflict = True
            continue
        if signed > last_day:
            continue
        line = candidate.lines[0]
        if any(record.raw != line.raw for record in candidate.lines[1:]):
            conflict = True
            continue
        eligible.append((max(start, signed), end, line))

    refs = _unique_sources(considered)
    if conflict:
        return None, "recorded terms or line items are conflicting or incomplete", refs
    if len(eligible) > 1:
        return None, "multiple signed terms overlap the month", refs
    if not eligible:
        return None, "no matching signed term covers the month", refs
    start, end, line = eligible[0]
    if start > month or end < last_day:
        return None, "signed coverage changes within the month and proration is unknown", refs
    return line, None, refs


def _observations(rows: list[Source], conflicting_ids: dict[str, list[Source]]) -> tuple[dict, list[Source]]:
    by_month = defaultdict(list)
    malformed = []
    for record in rows:
        when = _month(record.raw["month"])
        if when is None:
            malformed.append(record)
            continue
        by_month[when].append(record)
    observations = {}
    for when, records in by_month.items():
        values = {
            (_value(item.raw["units_used"]), _value(item.raw["included_units"]))
            for item in records
        }
        invalid_measurement = any(
            used is None or (item.raw["included_units"] and included is None)
            for item in records
            for used, included in [(_value(item.raw["units_used"]), _value(item.raw["included_units"]))]
        )
        cross_records = [other for record in records for other in conflicting_ids.get(record.key, [])]
        conflicted = len(values) > 1 or bool(cross_records)
        used, included = next(iter(values))
        if conflicted or invalid_measurement:
            status = "conflicting" if conflicted else "invalid"
            observations[when] = (status, None, None, _unique_sources(records, cross_records))
        else:
            observations[when] = ("observed", used, included, records)
    return observations, malformed


def _month_history(history: list[date], observations: dict, terms: list) -> tuple[list[dict], dict]:
    actual_months = [month for month in history if month in observations]
    first, last = (min(actual_months), max(actual_months)) if actual_months else (None, None)
    series = []
    term_rows = {}
    for month in history:
        contracts = _unique_sources(
            item
            for start, end, contract, line in terms
            if start < _shift_month(month, 1) and end >= month
            for item in (contract, line)
        )
        term_rows[month] = contracts
        expected = bool(contracts or (first and first <= month <= last))
        status, used, included, records = observations.get(
            month, ("missing" if expected else "outside_known_history", None, None, [])
        )
        series.append({
            "month": _month_key(month),
            "status": status,
            "expected": expected,
            "used": str(used) if used is not None else None,
            "recorded_included_units": str(included) if included is not None else None,
            "has_signed_term_overlap": bool(contracts),
            "source_refs": _references(records),
            "expectation_refs": _references(contracts),
        })
    return series, term_rows


def _money(value: Decimal) -> str:
    return f"EUR {_display(value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))}"


def _entitlement_month(
    month: date, candidates: list[_TermCandidate], observations: dict, product: Source, calculate_charge: bool,
) -> tuple[dict, tuple[Source, ...]]:
    line, term_issue, term_sources = _effective_line(month, candidates)
    status, observed, exported_allowance, usage_sources = observations.get(month, ("missing", None, None, ()))
    allowance = _value(line.raw["included_units_monthly"]) if line else None
    rate = _value(line.raw["overage_rate_eur_per_unit"]) if line else None
    reason = term_issue
    if line and allowance is None:
        reason = "the signed line has no usable monthly allowance"
    elif line and status != "observed":
        reason = f"usage is {status}"
    elif line and exported_allowance is None:
        reason = "the meter export omits the recorded allowance despite a signed line"
    elif line and exported_allowance != allowance:
        reason = "the metered allowance differs from the signed line"

    utilisation = excess = headroom = charge = None
    charge_reason = reason
    if reason is None:
        excess = max(observed - allowance, Decimal(0))
        headroom = max(allowance - observed, Decimal(0))
        if allowance:
            utilisation = 100 * observed / allowance
    if reason is None and calculate_charge:
        header = next(item for item in term_sources
                      if item.table == "contracts" and item.key == line.raw["contract_id"])
        if header.raw["currency"] != "EUR":
            charge_reason = "the signed line has no confirmed EUR currency"
        elif rate is None:
            charge_reason = "the signed line has no usable excess-unit rate"
        else:
            charge = excess * rate

    refs = _unique_sources(usage_sources, term_sources, [product])
    calculated = {
        "observed_usage": observed, "metered_allowance": exported_allowance, "signed_allowance": allowance,
        "signed_rate_per_unit": rate, "utilisation_percent": utilisation, "headroom_units": headroom,
        "excess_units": excess, "estimated_exposure_eur": charge,
    }
    details = {key: str(value) if value is not None else None for key, value in calculated.items()}
    details.update({
        "month": _month_key(month), "month_label": f"{month:%b %Y}", "usage_status": status,
        "entitlement_issue": reason, "exposure_issue": charge_reason,
        "source_refs": _references(refs), "selected_line_id": line.key if line else None,
    })
    return details, refs


def _allowance_metrics(details: dict, product_id: str, unit: str) -> tuple[Metric, ...]:
    fields = ("observed_usage", "metered_allowance", "signed_allowance", "headroom_units", "excess_units")
    period = details["month"]
    metrics = tuple(Metric(key, details[key], unit, _display(Decimal(details[key])), period, product_id)
                    for key in fields if details[key] is not None)
    if details["utilisation_percent"] is not None:
        value = details["utilisation_percent"]
        display = Decimal(value).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
        metrics += (Metric("utilisation_percent", value, "percent", f"{display:.1f}%", period, product_id),)
    return metrics


def _monthly_entitlement(
    result: ToolResult, analyses: tuple[str, ...], base: str, account: str, label: str, unit: str,
    product_id: str, values: dict, refs: tuple[Source, ...],
) -> None:
    month = values["month"]
    month_label = values["month_label"]
    context = f"For {account}'s {label} in {month_label}"
    if "entitlement_utilisation" in analyses:
        issue = values["entitlement_issue"]
        metrics = _allowance_metrics(values, product_id, unit)
        if issue:
            text = f"{context}, allowance utilisation was withheld because {issue}."
        else:
            figures = {metric.name: metric.display for metric in metrics}
            remaining = "excess" if Decimal(values["excess_units"]) > 0 else "headroom"
            ratio = figures.get("utilisation_percent")
            text = (f"{context}, usage was {figures['observed_usage']} {unit} against a signed allowance of "
                    f"{figures['signed_allowance']} {unit}, with {figures[f'{remaining}_units']} {unit} of {remaining}.")
            if ratio:
                text += f" Utilisation was {ratio}."
            else:
                text += " The signed allowance was zero, so a utilisation percentage is undefined."
        result.evidence.append(Evidence(f"{base}.utilisation.{month}", "entitlement_utilisation", text,
                                        refs, metrics, product_id, month, (issue,) if issue else (),
                                        priority=65, model_visible=False))
    if "overage_exposure" in analyses:
        issue = values["exposure_issue"]
        metrics = ()
        if issue:
            text = f"{context}, excess charge exposure was withheld because {issue}."
        else:
            rate, exposure = (Decimal(values[key]) for key in ("signed_rate_per_unit", "estimated_exposure_eur"))
            excess = _display(Decimal(values["excess_units"]))
            exact_rate = f"EUR {_display(rate)}"
            metrics = (
                Metric("excess_units", values["excess_units"], unit, excess, month, product_id),
                Metric("signed_rate", str(rate), f"EUR per {unit}", exact_rate, month, product_id),
                Metric("estimated_exposure", str(exposure), "EUR", _money(exposure), month, product_id),
            )
            text = (f"{context}, {excess} excess {unit} at a signed rate of {exact_rate} per recorded usage unit "
                    f"produce {_money(exposure)} in calculated exposure. Billing is unverified.")
        result.evidence.append(Evidence(f"{base}.exposure.{month}", "overage_exposure", text, refs,
                                        metrics, product_id, month, (issue,) if issue else (),
                                        priority=65, model_visible=False))


def _coverage_caveat(months: list[dict], issue_key: str) -> tuple[str, ...]:
    problems = defaultdict(list)
    for month in months:
        issue = month[issue_key]
        if issue:
            problems[issue].append(month["month_label"])
    return tuple(f"For {', '.join(labels)}, {issue}." for issue, labels in problems.items())


def _summarise_entitlements(
    result: ToolResult, analyses: tuple[str, ...], base: str, account: str, label: str, unit: str,
    product_id: str, period: str, months: list[dict], refs: tuple[Source, ...],
) -> dict:
    covered = [month for month in months if month["entitlement_issue"] is None]
    exceeded = sum(Decimal(month["excess_units"]) > 0 for month in covered)
    assessed = len(covered)
    total = len(months)
    summary = {"period": period, "months": months, "window_months": total}
    if "entitlement_utilisation" in analyses:
        caveats = _coverage_caveat(months, "entitlement_issue")
        metrics = [Metric("assessed_months", assessed, "months", str(assessed), period, product_id),
                   Metric("window_months", total, "months", str(total), period, product_id)]
        if assessed:
            latest = covered[-1]
            latest_usage, allowance = (_display(Decimal(latest[key])) for key in ("observed_usage", "signed_allowance"))
            metrics.append(Metric("months_over_allowance", exceeded, "months", str(exceeded), period, product_id))
            metrics.extend(_allowance_metrics(latest, product_id, unit))
            text = (f"For {account}, {label} exceeded its recorded signed allowance in {exceeded} of {assessed} "
                    f"assessable months in {period}. In {latest['month_label']}, usage was {latest_usage} {unit} "
                    f"against {allowance} {unit} included.")
        else:
            text = f"Allowance utilisation for {account}'s {label} was unavailable in {period}."
        if caveats:
            text = f"{text} {' '.join(caveats)}"
        result.evidence.append(Evidence(f"{base}.utilisation.summary", "entitlement_utilisation", text,
                                        refs, tuple(metrics), product_id, period, caveats,
                                        priority=33 if exceeded or caveats else 62))
        summary.update({"entitlement_assessed_months": assessed, "months_over_allowance": exceeded if covered else None})
    if "overage_exposure" in analyses:
        chargeable = [month for month in months if month["estimated_exposure_eur"] is not None]
        exposure = sum(Decimal(month["estimated_exposure_eur"]) for month in chargeable) if chargeable else None
        charge_months = len(chargeable)
        caveats = _coverage_caveat(months, "exposure_issue")
        metrics = [Metric("charge_assessed_months", charge_months, "months", str(charge_months), period, product_id),
                   Metric("window_months", total, "months", str(total), period, product_id)]
        if exposure is None:
            text = f"Excess charge exposure for {account}'s {label} was unavailable in {period}."
        elif charge_months == total:
            text = (f"Calculated excess charge exposure for {account}'s {label} in {period} totals {_money(exposure)}. "
                    "Billing is unverified.")
        else:
            text = (f"Calculated excess charge exposure for {account}'s {label} in {period} totals {_money(exposure)} "
                    f"across {charge_months} of {total} months. The full-period exposure and actual billing are unverified.")
        if exposure is not None:
            metrics.append(Metric("eligible_month_exposure", str(exposure), "EUR", _money(exposure), period, product_id))
        if caveats:
            text = f"{text} {' '.join(caveats)}"
        result.evidence.append(Evidence(f"{base}.exposure.summary", "overage_exposure", text, refs,
                                        tuple(metrics), product_id, period, caveats,
                                        priority=34 if exposure or caveats else 63))
        summary.update({
            "exposure_assessed_months": charge_months,
            "eligible_months_exposure_eur": str(exposure) if exposure is not None else None,
            "full_period_exposure_eur": str(exposure) if charge_months == total else None,
        })
    return summary


def _entitlements(
    ctx: ToolContext, analyses: tuple[str, ...], result: ToolResult, product: Source,
    recent: tuple[date, ...], candidates: list[_TermCandidate], observations: dict,
) -> dict:
    base = f"telemetry.usage.{product.key}"
    account = ctx.client["client_name"]
    label, unit = product.raw["product_name"], product.raw["usage_unit"]
    months = []
    groups = []
    calculate_charge = "overage_exposure" in analyses
    for month in recent:
        values, refs = _entitlement_month(month, candidates, observations, product, calculate_charge)
        months.append(values)
        groups.append(refs)
        _monthly_entitlement(result, analyses, base, account, label, unit, product.key, values, refs)
    return _summarise_entitlements(result, analyses, base, account, label, unit, product.key,
                                   _month_window(recent), months, _unique_sources(*groups))


def _rollup(values: list[Decimal], is_average: bool) -> Decimal:
    value = sum(values, Decimal(0))
    return value / len(values) if is_average else value


def _usage_comparison(
    ctx: ToolContext, product_id: str, product_name: str, unit: str, series: list[dict],
    observations: dict, term_rows: dict, product: Source, recent: tuple[date, ...],
    previous: tuple[date, ...],
) -> tuple[Evidence, dict]:
    indexed = {entry["month"]: entry for entry in series}
    periods = {"recent": recent, "year_earlier": previous}
    average = product_id == "FAN_INSIGHTS"
    aggregation = "monthly average" if average else "total"
    result = {"aggregation": aggregation, "windows": {}, "percentage_change": None, "absolute_change": None}
    metrics = []
    for name, months in periods.items():
        records = [indexed[_month_key(month)] for month in months]
        values = [Decimal(row["used"]) for row in records if row["status"] == "observed"]
        rollup = _rollup(values, average) if values else None
        result["windows"][name] = {
            "period": _month_window(months),
            "observed_months": len(values),
            "expected_months": sum(row["expected"] for row in records),
            "months": records,
            "observed_rollup": str(rollup) if rollup is not None else None,
            "observed_sum": str(sum(values)) if values else None,
        }
        if rollup is not None:
            metrics.append(Metric(f"{name}_observed_{aggregation}", str(rollup), unit,
                                  _rollup_display(rollup, average), _month_window(months), product_id))
        metrics.append(Metric(f"{name}_observed_months", len(values), "months", str(len(values)),
                              _month_window(months), product_id))
        expected = result["windows"][name]["expected_months"]
        metrics.append(Metric(f"{name}_expected_months", expected, "months", str(expected),
                              _month_window(months), product_id))

    start_text = f"For {ctx.client['client_name']}, {product_name}"
    recent_text, previous_text = _month_window(recent), _month_window(previous)
    comparison_period = f"{recent_text} vs {previous_text}"
    usable_months = sum(window["observed_months"] for window in result["windows"].values())
    caveats = ()
    priority = 50
    if usable_months == len(recent) + len(previous):
        new = Decimal(result["windows"]["recent"]["observed_rollup"])
        old = Decimal(result["windows"]["year_earlier"]["observed_rollup"])
        difference = new - old
        result["absolute_change"] = str(difference)
        metrics.append(Metric("absolute_change", str(difference), unit, _rollup_display(difference, average),
                              comparison_period, product_id))
        if old == 0:
            caveats = ("The comparison baseline is zero, so the percentage change is undefined.",)
            text = (f"{start_text} logged a {aggregation} of {_rollup_display(new, average)} {unit} in {recent_text} "
                    f"against 0 {unit} in {previous_text}. The absolute change was {_rollup_display(difference, average)} "
                    f"{unit}. The zero baseline prevents a year-over-year percentage.")
        else:
            percentage = (difference * 100 / old).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
            result["percentage_change"] = str(percentage)
            metrics.append(Metric("percentage_change", str(percentage), "percent", f"{percentage:+.1f}%",
                                  comparison_period, product_id))
            direction = "rose" if difference > 0 else "fell" if difference < 0 else "was unchanged"
            change = f"{direction} by {abs(percentage):.1f}%" if difference else direction
            if percentage == 0 and difference:
                change = f"{direction} slightly"
            text = (f"{start_text} usage {change} year over year. Its {aggregation} was "
                    f"{_rollup_display(new, average)} {unit} in {recent_text}, compared with {_rollup_display(old, average)} {unit} "
                    f"in {previous_text}.")
            priority = 24 if difference < 0 else 42
        result["status"] = "comparable" if old else "zero_baseline"
    else:
        fragments = []
        for name, months in periods.items():
            window = result["windows"][name]
            observed = window["observed_months"]
            if observed:
                rollup = _rollup_display(Decimal(window["observed_rollup"]), average)
                fragments.append(
                    f"{rollup} {unit} ({aggregation} across {observed} of {len(months)} months in {window['period']})"
                )
            else:
                fragments.append(f"no usable observations for {window['period']}")
        caveats = ("A complete same-month year-over-year comparison cannot be calculated from the usable observations.",)
        text = f"{start_text} has {' and '.join(fragments)}. The year-over-year comparison is unavailable."
        if observations and previous[0] < min(observations):
            text += f" Recorded usage history begins in {min(observations):%b %Y}."
        priority = 22
        result["status"] = "incomplete_coverage"

    period_months = (*previous, *recent)
    raw = [item for month in period_months for item in observations.get(month, (None, None, None, []))[3]]
    terms = [item for month in period_months for item in term_rows[month]]
    evidence = Evidence(f"telemetry.usage.{product_id}.yoy", "usage_trends", text,
                        _unique_sources(raw, [product], terms), tuple(metrics), product_id,
                        comparison_period, caveats, priority)
    return evidence, result


def _usage(ctx: ToolContext, analyses: tuple[str, ...], result: ToolResult) -> None:
    catalogue, used, history = _usage_inputs(ctx)
    terms, contract_records = _signed_usage_terms(ctx)
    measures_entitlement = bool(ENTITLEMENT_ANALYSES.intersection(analyses))
    candidates = _entitlement_candidates(ctx, contract_records) if measures_entitlement else {}
    usage_ids = defaultdict(list)
    for rows in used.values():
        for row in rows:
            usage_ids[row.key].append(row)
    conflicting_ids = {
        key: rows for key, rows in usage_ids.items()
        if len({(row.raw["product_id"], row.raw["month"]) for row in rows}) > 1
    }
    recent = tuple(history[-USAGE_MONTHS:])
    previous = tuple(_shift_month(month, -12) for month in recent)
    result.inputs["usage"] = {
        "history_first_month": _month_key(history[0]),
        "complete_through": _month_key(history[-1]),
        "recent": [_month_key(month) for month in recent],
        "year_earlier": [_month_key(month) for month in previous],
        "expected_rule": "signed-term overlap in month or between first and last recorded usage observation",
        "yoy_rule": "both full same-month windows observed; positive prior denominator for percentage",
        "yoy_rounding": "percentage change to nearest 0.1 percentage point using half-up rounding",
    }
    if measures_entitlement:
        result.inputs["entitlement"] = {
            "months": [_month_key(month) for month in recent],
            "coverage": "one unambiguous signed line must cover the full calendar month",
            "allowance": "use the signed line when the recorded meter allowance agrees",
            "overage_formula": "max(observed usage minus signed monthly allowance, 0) times the effective EUR rate",
            "money_display": "round to the nearest EUR cent after aggregating exact Decimal monthly estimates",
            "billing_scope": "contract-rate calculation without invoice, credit or waiver evidence",
        }
    result.data["usage"] = {}
    profile = source("clients", "client_id", ctx.client)
    account = ctx.client["client_name"]
    product_ids = set(used) | set(terms)
    if measures_entitlement:
        product_ids.update(candidates)
    if not product_ids:
        text = f"The snapshot has no observed usage or signed usage history for {account}."
        selected = "usage_history" if "usage_history" in analyses else next(
            analysis for analysis in analyses if analysis in USAGE_ANALYSES
        )
        result.evidence.append(Evidence("telemetry.usage.none", selected, text, (profile,),
                                        caveats=(text,), priority=16))

    for product_id in sorted(product_ids):
        product = catalogue.get(product_id)
        raw = used.get(product_id, [])
        if product is None:
            text = f"Usage for product {product_id} has no catalogue unit. Its totals and changes are unavailable."
            selected = "usage_history" if "usage_history" in analyses else next(
                analysis for analysis in analyses if analysis in USAGE_ANALYSES
            )
            result.evidence.append(Evidence(f"telemetry.usage.{product_id}.unknown_unit", selected, text,
                                            tuple(raw), product_id=product_id, caveats=(text,), priority=16))
            continue
        label, unit = product.raw["product_name"], product.raw["usage_unit"]
        observations, malformed = _observations(raw, conflicting_ids)
        series, term_rows = _month_history(history, observations, terms.get(product_id, []))
        expected = sum(row["expected"] for row in series)
        observed = sum(row["status"] == "observed" for row in series)
        period = _month_window(tuple(history))
        details = {"product_name": label, "unit": unit, "history": series}
        result.data["usage"][product_id] = details
        if "usage_history" in analyses:
            text = f"{account} has {observed} usable monthly observations for {label} out of {expected} expected in {period}."
            counts = (Metric("observed_months", observed, "months", str(observed), period, product_id),
                      Metric("expected_months", expected, "months", str(expected), period, product_id))
            observed_months = tuple(
                Metric("observed_month_usage", row["used"], unit, _display(Decimal(row["used"])), row["month"], product_id)
                for row in series if row["status"] == "observed"
            )
            result.evidence.append(Evidence(f"telemetry.usage.{product_id}.history", "usage_history", text,
                                            _unique_sources(raw, [product, profile], *term_rows.values()),
                                            counts + observed_months,
                                            product_id, period, model_visible=False))
        if "usage_trends" in analyses:
            evidence, details["yoy"] = _usage_comparison(ctx, product_id, label, unit, series,
                                                         observations, term_rows, product, recent, previous)
            result.evidence.append(evidence)

        if measures_entitlement:
            details["entitlement"] = _entitlements(ctx, analyses, result, product, recent,
                                                   candidates.get(product_id, []), observations)

        _usage_quality(ctx, result, product_id, product, series, observations, malformed, term_rows, recent,
                       contract_records)


def _usage_quality(
    ctx: ToolContext, result: ToolResult, product_id: str, product: Source, series: list[dict],
    observations: dict, malformed: list[Source], term_rows: dict, recent: tuple[date, ...],
    contract_records: list[Source],
) -> None:
    base = f"telemetry.usage.{product_id}"
    label = product.raw["product_name"]
    account = ctx.client["client_name"]
    duplicates = [
        (month, records) for month, (status, _, _, records) in observations.items()
        if status == "observed" and len(records) > 1
    ]
    if duplicates:
        months = ", ".join(f"{month:%b %Y}" for month, _ in duplicates)
        text = f"Equivalent usage rows for {account}'s {label} in {months} were counted once. All source occurrences are retained."
        result.evidence.append(Evidence(f"{base}.duplicates", "data_quality", text,
                                        _unique_sources(*(rows for _, rows in duplicates)), product_id=product_id,
                                        period=months, caveats=(text,), priority=36))
    gaps = [row for row in series if row["status"] in {"missing", "invalid", "conflicting"}]
    if gaps or malformed:
        groups = []
        gaps_terms = []
        gaps_usage = malformed.copy()
        for status in ("missing", "invalid", "conflicting"):
            months = [_month(row["month"]) for row in gaps if row["status"] == status]
            if months:
                groups.append(f"{status} in {', '.join(f'{month:%b %Y}' for month in months)}")
            for month in months:
                gaps_terms.extend(term_rows[month])
                gaps_usage.extend(observations.get(month, (None, None, None, []))[3])
        if malformed:
            groups.append("malformed month dates in the usage export")
        text = (f"{account}'s {label} usage has coverage defects ({', '.join(groups)}). "
                "Affected usage is unknown and excluded from calculations.")
        compared = {_month_key(month) for month in (*recent, *(_shift_month(month, -12) for month in recent))}
        if gaps and not malformed and all(row["month"] not in compared for row in gaps):
            text += " The affected months fall outside the year-over-year comparison windows."
        remaining = [item for _, _, _, rows in observations.values() for item in rows]
        result.evidence.append(Evidence(f"{base}.coverage_gap", "data_quality", text,
                                        _unique_sources(gaps_usage, gaps_terms, remaining, [product]),
                                        product_id=product_id, caveats=(text,), priority=16))

    no_terms = [month for month in recent if month in observations and observations[month][0] == "observed"
                and not term_rows[month]]
    if no_terms:
        when = ", ".join(f"{month:%b %Y}" for month in no_terms)
        raw = [item for month in no_terms for item in observations[month][3]]
        text = (f"The snapshot shows {account} using {label} in {when} without a matching signed term in the export. "
                "The usage is observed and commercial coverage remains unconfirmed.")
        result.evidence.append(Evidence(f"{base}.unconfirmed_term", "data_quality", text,
                                        _unique_sources(raw, [product], contract_records, *term_rows.values()),
                                        product_id=product_id,
                                        period=when, caveats=(text,), priority=17))


def _ticket_records(ctx: ToolContext, result: ToolResult) -> list[tuple[tuple[Source, ...], date, date | None]]:
    records = defaultdict(list)
    for row in ctx.db.execute(
        "SELECT rowid, * FROM support_tickets WHERE client_id = ? ORDER BY opened_date, rowid",
        (ctx.client_id,),
    ):
        item = source("support_tickets", "ticket_id", row)
        records[item.key].append(item)

    normalized = []
    account = ctx.client["client_name"]
    for key, occurrences in records.items():
        case = occurrences[0].raw
        if any(other.raw != case for other in occurrences[1:]):
            text = f"Conflicting versions of {account}'s ticket {key} were excluded from support and satisfaction calculations."
            result.evidence.append(Evidence(f"telemetry.support.{key}.conflict", "data_quality", text,
                                            tuple(occurrences), caveats=(text,), priority=16))
            continue
        if len(occurrences) > 1:
            text = f"The support export repeats {account}'s ticket {key}. It was counted once and both source occurrences are retained."
            result.evidence.append(Evidence(f"telemetry.support.{key}.duplicate", "data_quality", text,
                                            tuple(occurrences), caveats=(text,), priority=36))
        opened = _date(case["opened_date"])
        closed = _date(case["closed_date"])
        is_open = case["status"] == "open" and not case["closed_date"]
        is_closed = case["status"] == "closed" and closed is not None
        if not (opened and (is_open or is_closed)) or (closed and (closed < opened or closed > ctx.as_of)):
            text = f"Ticket {key} has invalid dates or inconsistent status for {account}. It was excluded from support calculations."
            result.evidence.append(Evidence(f"telemetry.support.{key}.invalid_dates", "data_quality", text,
                                            tuple(occurrences), caveats=(text,), priority=16))
            continue
        if opened <= ctx.as_of:
            normalized.append((tuple(occurrences), opened, closed))
    return normalized


def _support(ctx: ToolContext, analyses: tuple[str, ...], result: ToolResult) -> None:
    recent_end = ctx.as_of
    recent_start = recent_end - timedelta(days=SUPPORT_DAYS - 1)
    previous_start = recent_start - timedelta(days=SUPPORT_DAYS)
    previous_end = recent_start - timedelta(days=1)
    periods = {"recent": (recent_start, recent_end), "previous": (previous_start, previous_end)}
    records = _ticket_records(ctx, result)
    windows = {}
    selected_rows = {}
    for name, (start, end) in periods.items():
        arrivals = [rows for rows, opened, _ in records if start <= opened <= end]
        closures = [rows for rows, _, closed in records if closed and start <= closed <= end]
        as_of_open = [rows for rows, opened, closed in records if opened <= end and (closed is None or closed > end)]
        observed_priorities = Counter(rows[0].raw["priority"] or "unknown" for rows in arrivals)
        observed_categories = Counter(rows[0].raw["category"] or "unknown" for rows in arrivals)
        observed_products = Counter(rows[0].raw["product_id"] or "unknown" for rows in arrivals)
        windows[name] = {
            "start": start.isoformat(), "end": end.isoformat(),
            "arrivals": len(arrivals), "closures": len(closures), "open_at_end": len(as_of_open),
            "arrivals_by_priority": dict(observed_priorities), "arrivals_by_category": dict(observed_categories),
            "arrivals_by_product": dict(observed_products),
            "arrivals_refs": _references(record for rows in arrivals for record in rows),
            "closures_refs": _references(record for rows in closures for record in rows),
            "open_at_end_refs": _references(record for rows in as_of_open for record in rows),
        }
        selected_rows[name] = (arrivals, closures, as_of_open)

    result.inputs["support"] = {
        "window_days": SUPPORT_DAYS, "recent": {
            "start": recent_start.isoformat(), "end": recent_end.isoformat(),
        }, "previous": {"start": previous_start.isoformat(), "end": previous_end.isoformat()},
        "arrival_basis": "opened date in an inclusive window",
        "closure_and_csat_basis": "closed date in an inclusive window",
        "open_basis": "opened by period end with no closure by period end; historical states are reconstructed from dates",
    }
    result.data["support"] = windows
    if "support_summary" in analyses:
        _support_summary(ctx, result, periods, windows, selected_rows)
    if "support_csat" in analyses:
        _support_csat(ctx, result, periods, windows, selected_rows)
    if "support_resolution" in analyses:
        result.inputs["support"]["resolution_basis"] = (
            "calendar-date difference from opening to recorded closure, or to the reporting date if open"
        )
        _support_resolution(ctx, result, periods, windows, selected_rows)


def _support_summary(ctx: ToolContext, result: ToolResult, periods: dict, windows: dict, selected_rows: dict) -> None:
    profile = source("clients", "client_id", ctx.client)
    account = ctx.client["client_name"]
    current = windows["recent"]
    prior = windows["previous"]
    recent_period, previous_period = (_day_window(*periods[name]) for name in ("recent", "previous"))
    recent_rows, recent_closures, open_rows = selected_rows["recent"]
    prior_rows, _, _ = selected_rows["previous"]
    counts = (
        Metric("recent_arrivals", current["arrivals"], "tickets", str(current["arrivals"]), recent_period),
        Metric("previous_arrivals", prior["arrivals"], "tickets", str(prior["arrivals"]), previous_period),
        Metric("recent_closures", current["closures"], "tickets", str(current["closures"]), recent_period),
        Metric("open_at_snapshot", current["open_at_end"], "tickets", str(current["open_at_end"]), ctx.as_of.isoformat()),
    )
    text = (f"The snapshot records {_count(current['arrivals'], 'ticket')} opened for {account} from {recent_period}, compared with "
            f"{prior['arrivals']} from {previous_period}. It records {current['open_at_end']} open at {ctx.as_of.isoformat()}.")
    evidence_rows = (item for group in (recent_rows, prior_rows, recent_closures, open_rows)
                     for occurrences in group for item in occurrences)
    result.evidence.append(Evidence("telemetry.support.summary", "support_summary", text,
                                    _unique_sources(evidence_rows, [profile]), counts, period=recent_period, priority=34))

    products = {
        row["product_id"]: source("products", "product_id", row)
        for row in ctx.db.execute("SELECT rowid, * FROM products ORDER BY rowid")
    }
    critical_by_product = defaultdict(list)
    for rows in recent_rows:
        if rows[0].raw["priority"] == "P1":
            critical_by_product[rows[0].raw["product_id"]].append(rows)
    for product_id, cases in critical_by_product.items():
        product = products.get(product_id)
        label = product.raw["product_name"] if product else product_id or "unspecified product"
        categories = Counter(rows[0].raw["category"] or "unclassified" for rows in cases)
        breakdown = ", ".join(f"{count} {category.lower()}" for category, count in sorted(categories.items()))
        counts = (Metric("critical_recent_arrivals", len(cases), "P1 tickets", str(len(cases)), recent_period, product_id),)
        counts += tuple(Metric(f"critical_recent_{category}", count, "P1 tickets", str(count), recent_period, product_id)
                        for category, count in sorted(categories.items()))
        text = (f"The snapshot records {_count(len(cases), 'P1 ticket')} opened for {account} about {label} "
                f"from {recent_period} ({breakdown}).")
        rows = _unique_sources((item for case in cases for item in case), [product] if product else [])
        result.evidence.append(Evidence(f"telemetry.support.critical.{product_id}", "support_summary", text, rows,
                                        counts, product_id, recent_period, priority=18))

    for occurrences in open_rows:
        case = occurrences[0].raw
        product_id = case["product_id"]
        product = products.get(product_id)
        label = product.raw["product_name"] if product else product_id or "unspecified product"
        text = (f"{account}'s {case['priority']} {case['category'].lower()} ticket for {label}, opened {case['opened_date']}, "
                f"was still open at {ctx.as_of.isoformat()}. The ticket subject reads \u201c{case['subject']}\u201d.")
        rows = _unique_sources(occurrences, [product] if product else [])
        metrics = (Metric("opened_date", case["opened_date"], "date", case["opened_date"], product_id=product_id),)
        priority = {"P1": 10, "P2": 20}.get(case["priority"], 42)
        result.evidence.append(Evidence(f"telemetry.support.open.{case['ticket_id']}", "support_summary", text, rows,
                                        metrics, product_id, ctx.as_of.isoformat(), priority=priority))

    closed_arrivals = [rows for rows in recent_rows if rows[0].raw["status"] == "closed"]
    requests = [rows for rows in closed_arrivals if rows[0].raw["category"] == "Feature request"]
    if closed_arrivals:
        _support_topic(result, closed_arrivals[-1], products, account, 54)
    if requests and requests[-1] != closed_arrivals[-1]:
        _support_topic(result, requests[-1], products, account, 48)


def _support_topic(result: ToolResult, occurrences: tuple[Source, ...], products: dict, account: str, priority: int) -> None:
    case = occurrences[0].raw
    product_id = case["product_id"]
    product = products.get(product_id)
    label = product.raw["product_name"] if product else product_id or "unspecified product"
    opened = case["opened_date"]
    text = (f"The {case['category'].lower()} ticket for {account}'s {label}, opened {opened}, is recorded as closed. "
            f"The ticket subject reads \u201c{case['subject']}\u201d.")
    rows = _unique_sources(occurrences, [product] if product else [])
    metrics = (Metric("opened_date", opened, "date", opened, product_id=product_id),)
    result.evidence.append(Evidence(f"telemetry.support.topic.{case['ticket_id']}", "support_summary",
                                    text, rows, metrics, product_id, opened, priority=priority))


def _support_csat(ctx: ToolContext, result: ToolResult, periods: dict, windows: dict, selected_rows: dict) -> None:
    account = ctx.client["client_name"]
    profile = source("clients", "client_id", ctx.client)
    for name in ("recent", "previous"):
        _, closures, _ = selected_rows[name]
        period = _day_window(*periods[name])
        scores = []
        invalid = []
        for occurrences in closures:
            raw = occurrences[0].raw["csat_score"]
            if raw in {"1", "2", "3", "4", "5"}:
                scores.append(int(raw))
            elif raw:
                invalid.extend(occurrences)

        average = ((Decimal(sum(scores)) / len(scores)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                   if scores else None)
        windows[name]["csat"] = {
            "mean": str(average) if average is not None else None,
            "respondents": len(scores), "closed_tickets": len(closures),
            "distribution": dict(sorted(Counter(scores).items())),
            "invalid_response_refs": _references(invalid),
        }
        metrics = (
            Metric("csat_response_count", len(scores), "responses", str(len(scores)), period),
            Metric("csat_closed_count", len(closures), "closed tickets", str(len(closures)), period),
        )
        caveats = ()
        if average is not None:
            metrics += (Metric("mean_csat", str(average), "score out of 5", f"{average}/5", period),)
            text = (f"For {account} tickets closed from {period}, CSAT averaged {average}/5 across "
                    f"{_count(len(scores), 'response')} from {_count(len(closures), 'closed ticket')}.")
            if len(scores) < len(closures):
                text += " Satisfaction among nonrespondents is unknown."
                caveats += ("The satisfaction mean describes respondents only.",)
        else:
            text = (f"There were 0 valid CSAT responses for {account} from {_count(len(closures), 'ticket')} closed from {period}. "
                    "Satisfaction for that period is unknown.")
            caveats += ("No valid satisfaction responses in the selected closure window.",)
        if invalid:
            text += " Invalid survey scores were excluded."
            caveats += ("Invalid survey scores were excluded.",)
        rows = _unique_sources((item for occurrences in closures for item in occurrences), [profile])
        result.evidence.append(Evidence(f"telemetry.support.csat.{name}", "support_csat", text, rows, metrics,
                                        period=period, caveats=caveats, priority=40 if name == "recent" else 60,
                                        model_visible=name == "recent"))


def _support_resolution(ctx: ToolContext, result: ToolResult, periods: dict, windows: dict, selected_rows: dict) -> None:
    account = ctx.client["client_name"]
    profile = source("clients", "client_id", ctx.client)
    products = {
        row["product_id"]: source("products", "product_id", row)
        for row in ctx.db.execute("SELECT rowid, * FROM products ORDER BY rowid")
    }
    _, _, open_rows = selected_rows["recent"]
    for occurrences in open_rows:
        case = occurrences[0].raw
        product = products.get(case["product_id"])
        label = product.raw["product_name"] if product else case["product_id"] or "unspecified product"
        opened = _date(case["opened_date"])
        elapsed = (ctx.as_of - opened).days
        period = _day_window(opened, ctx.as_of)
        text = (f"The {case['priority']} {case['category'].lower()} ticket for {account}'s {label} had been open for "
                f"{_count(elapsed, 'elapsed calendar day')} at {ctx.as_of.isoformat()}. It was opened {opened.isoformat()}.")
        rows = _unique_sources(occurrences, [product] if product else [])
        metrics = (Metric("open_elapsed_calendar_days", elapsed, "calendar days", str(elapsed), period,
                          case["product_id"]),)
        result.evidence.append(Evidence(f"telemetry.support.resolution.open.{case['ticket_id']}", "support_resolution",
                                        text, rows, metrics, case["product_id"], period,
                                        priority=11 if case["priority"] == "P1" else 43))

    for name, window in windows.items():
        _, closures, _ = selected_rows[name]
        period = _day_window(*periods[name])
        days = []
        duration_rows = []
        for occurrences in closures:
            case = occurrences[0].raw
            opened, closed = _date(case["opened_date"]), _date(case["closed_date"])
            elapsed = (closed - opened).days
            days.append(elapsed)
            duration_rows.append({"ticket_id": case["ticket_id"], "opened_date": case["opened_date"],
                                  "closed_date": case["closed_date"], "elapsed_calendar_days": elapsed,
                                  "source_refs": _references(occurrences)})
            text = (f"{account}'s ticket {case['ticket_id']} opened {opened.isoformat()} and closed {closed.isoformat()}, "
                    f"an elapsed span of {_count(elapsed, 'calendar day')}.")
            case_period = _day_window(opened, closed)
            metrics = (Metric("closed_elapsed_calendar_days", elapsed, "calendar days", str(elapsed), case_period,
                              case["product_id"]),)
            result.evidence.append(Evidence(f"telemetry.support.resolution.closed.{case['ticket_id']}", "support_resolution",
                                            text, occurrences, metrics, case["product_id"], case_period,
                                            model_visible=False))

        ordered_days = sorted(days)
        count = len(ordered_days)
        median = (Decimal(ordered_days[(count - 1) // 2] + ordered_days[count // 2]) / 2 if count else None)
        window["resolution"] = {
            "closed_durations": duration_rows, "median_closed_elapsed_calendar_days": str(median) if count else None,
        }
        metrics = (Metric("closure_count", count, "tickets", str(count), period),)
        text = f"The snapshot records no closed tickets for {account} from {period}, so no elapsed closure time is available."
        if count:
            duration = f"{_display(median)} elapsed calendar {'day' if median == 1 else 'days'}"
            metrics += (Metric("median_closed_elapsed_calendar_days", str(median), "calendar days", _display(median), period),)
            text = (f"Tickets for {account} closed from {period} took a median of {duration} from opening to closure "
                    f"across {_count(count, 'ticket')}. The export supplies calendar dates and has no response timestamps or SLA clock.")
        rows = _unique_sources((item for occurrences in closures for item in occurrences), [profile])
        result.evidence.append(Evidence(f"telemetry.support.resolution.summary.{name}", "support_resolution",
                                        text, rows, metrics, period=period, priority=55, model_visible=name == "recent"))
    windows["recent"]["resolution"]["open_durations"] = [
        {"ticket_id": occurrences[0].key, "elapsed_calendar_days": (ctx.as_of - _date(occurrences[0].raw["opened_date"])).days,
         "source_refs": _references(occurrences)} for occurrences in open_rows
    ]


def run(ctx: ToolContext, analyses: tuple[str, ...], prior: dict[str, ToolResult]) -> ToolResult:
    """Run the usage and support analyses selected by the trigger plan."""
    result = ToolResult()
    if USAGE_ANALYSES.intersection(analyses):
        _usage(ctx, analyses, result)
    if {"support_summary", "support_csat", "support_resolution"}.intersection(analyses):
        _support(ctx, analyses, result)
    if any(item.caveats for item in result.evidence):
        result.status = "qualified"
    elif not result.evidence:
        result.status = "empty"
    return result
