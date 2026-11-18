"""Explicit, descriptive portfolio scope for renewal-priority review."""

from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from uuid import uuid4

from .models import AS_OF, ToolContext
from .planning import RequestError, open_snapshot
from .run import Run, _save
from .tools import commercial, telemetry
from .verification import escape

ORDER = ("Signed coverage unconfirmed", "Multiple signed terms", "Recorded notice due or elapsed",
         "Upcoming recorded notice", "Notice unavailable")


@dataclass(frozen=True)
class AccountPosition:
    client_id: str
    name: str
    priority: str
    notice_date: str | None
    signed_arr_eur: int | None
    open_p1_tickets: int | None
    evidence_keys: tuple[str, ...]

    def sort_key(self):
        return (ORDER.index(self.priority), self.notice_date or date.max.isoformat(),
                self.open_p1_tickets is None, -(self.open_p1_tickets or 0), self.client_id)


def _position(ctx: ToolContext) -> tuple[AccountPosition, dict]:
    terms = commercial.run(ctx, ("contract_position", "renewal_calendar"), {})
    support = telemetry.run(ctx, ("support_summary",), {})
    contracts = terms.data["current_contract_ids"]
    facts = [item for item in terms.evidence if item.analysis in {"contract_position", "renewal_calendar"}]
    notice = None
    if not contracts:
        priority = ORDER[0]
    elif len(contracts) > 1:
        priority = ORDER[1]
    else:
        notice_evidence = next((item for item in facts if item.key == f"commercial.notice.{contracts[0]}"), None)
        if notice_evidence:
            notice = next((metric.display for metric in notice_evidence.metrics if metric.name == "notice_deadline"), None)
        priority = ORDER[4] if notice is None else ORDER[2] if notice <= str(ctx.as_of) else ORDER[3]
    ticket_gaps = [item for item in support.evidence if item.key.endswith((".conflict", ".invalid_dates"))]
    p1_cases = [item for item in support.evidence if item.key.startswith("telemetry.support.open.") and
                any(ref.table == "support_tickets" and ref.raw["priority"] == "P1" for ref in item.sources)]
    facts.extend(p1_cases + ticket_gaps)
    facts.extend(item for item in support.evidence if item.key == "telemetry.support.summary")
    position = AccountPosition(ctx.client_id, ctx.client["client_name"], priority, notice, terms.data["current_arr_eur"],
                               None if ticket_gaps else len(p1_cases), tuple(item.key for item in facts))
    return position, {item.key: asdict(item) for item in facts}


def _markdown(rows: list[AccountPosition], facts: dict) -> str:
    content = ["# Portfolio renewal priorities", "", f"Snapshot {AS_OF}", "",
               "Review unconfirmed or overlapping terms first, then elapsed and upcoming recorded notice deadlines. "
               "Within a priority use the earliest deadline, open P1 count and client ID, in that order.", "",
               "| Account | Renewal position | Recorded notice deadline | Signed ARR | Open P1 |",
               "| --- | --- | --- | ---: | ---: |"]
    citations = []
    for index, row in enumerate(rows, 1):
        cited = f"p{index}"
        name = escape(row.name).replace("|", "\\|")
        amount = f"€{row.signed_arr_eur:,}" if row.signed_arr_eur is not None else "Unknown"
        opened = str(row.open_p1_tickets) if row.open_p1_tickets is not None else "Unknown"
        content.append(f"| {name} ({row.client_id}) | {row.priority} [^{cited}] | "
                       f"{row.notice_date or 'Unknown'} | {amount} | {opened} |")
        refs = list(dict.fromkeys(f'{source["table"]}/{source["key"]}' for key in row.evidence_keys
                                 for source in facts[row.client_id][key]["sources"] if source["table"] in {"contracts", "support_tickets"}))
        labels = ", ".join(refs[:4]) or f"clients/{row.client_id}"
        citations.append(f"[^{cited}]: {escape(labels)}. See the `{row.client_id}` evidence bundle.")
    content.extend(("", "## Data gaps and confidence", "",
                    "Notice delivery, waivers and future execution require separate confirmation. Signed ARR is the recorded contract amount. "
                    "Unknown cells reflect unavailable or ambiguous snapshot records.", "", *citations))
    return "\n".join(content) + "\n"


def renewal_priorities(data_dir: Path, output_dir: Path) -> Run:
    rows, facts = [], {}
    with closing(open_snapshot(data_dir)) as db:
        clients = db.execute("SELECT rowid, * FROM clients ORDER BY client_id").fetchall()
        if not clients:
            raise RequestError("The portfolio has no client rows.")
        for record in clients:
            row, evidence = _position(ToolContext(db, dict(record)))
            rows.append(row)
            facts[row.client_id] = evidence
    rows.sort(key=AccountPosition.sort_key)
    for row in rows:
        if not row.evidence_keys or set(row.evidence_keys) != facts[row.client_id].keys():
            raise RequestError(f"The portfolio evidence for {row.client_id} is incomplete.")
        if any(source.get("raw", {}).get("client_id", row.client_id) != row.client_id
               for fact in facts[row.client_id].values() for source in fact["sources"]):
            raise RequestError(f"The portfolio evidence for {row.client_id} includes another account.")
    if len(facts) != len(rows):
        raise RequestError("The portfolio contains duplicate client identifiers.")
    location = output_dir.resolve() / f"portfolio_renewal_priority_{uuid4().hex[:12]}"
    location.mkdir(parents=True)
    _save(location / "portfolio.md", _markdown(rows, facts))
    _save(location / "evidence.json", {"scope": "portfolio", "analysis": "portfolio_snapshot", "snapshot": str(AS_OF),
                                      "rows": [asdict(item) for item in rows], "by_client": facts})
    status = "verified_with_gaps"
    _save(location / "verification.json", {"status": status, "row_count": len(rows),
                                          "checks": ["unique accounts", "resolving row evidence", "account-scoped source rows"],
                                          "coverage": "Notice delivery and renewal execution are outside the snapshot."})
    return Run(location, status)
