"""Check selected deep analyses independently against raw SQLite rows."""

from argparse import ArgumentParser
from contextlib import closing
from datetime import date
from decimal import Decimal
import json
from pathlib import Path

from client_intel.models import AS_OF, ToolContext
from client_intel.planning import collect, load_config, open_snapshot, resolve_client


def independently_check(directory: Path) -> dict:
    config = load_config()
    report = {}
    with closing(open_snapshot(directory)) as db:
        results = {}
        for account, trigger in (("C001", "renewal"), ("C002", "upsell"),
                                 ("C003", "meeting_prep"), ("C007", "renewal")):
            _, tools = collect(ToolContext(db, resolve_client(db, account)), config, trigger)
            results[account] = ({item.key: item for target in tools.values() for item in target.evidence}, tools)
        halden, _ = results["C001"]
        amounts = db.execute("""
            SELECT contracts.arr_eur, crm_opportunities.amount_eur FROM contracts
            JOIN crm_opportunities ON crm_opportunities.client_id = contracts.client_id
            WHERE contract_id=? AND opp_id=?
        """, ("K-0002", "O-0001")).fetchone()
        delta = int(amounts[1]) - int(amounts[0])
        assert delta == -210500 and any(item.value == delta for item in halden["commercial.proposal.O-0001"].metrics)
        opened = date.fromisoformat(db.execute("SELECT opened_date FROM support_tickets WHERE ticket_id=?", ("T-0284",)).fetchone()[0])
        age = (AS_OF - opened).days
        assert age == 11 and halden["telemetry.support.resolution.open.T-0284"].metrics[0].value == age
        report["C001"] = {"proposal_minus_signed_eur": delta, "open_p1_elapsed_days": age}

        corvane, corvane_tools = results["C002"]
        allowance, rate = map(Decimal, db.execute("SELECT included_units_monthly, overage_rate_eur_per_unit FROM contract_lines WHERE line_id=?",
                                                 ("L-00013",)).fetchone())
        values = [Decimal(row[0]) for row in db.execute("""
            SELECT units_used FROM usage_monthly WHERE client_id=? AND product_id=? AND month BETWEEN ? AND ?
        """, ("C002", "STREAM", "2026-08", "2026-10"))]
        exposure = sum(max(Decimal(0), used - allowance) * rate for used in values)
        totals = {item.name: item.value for item in corvane["telemetry.usage.STREAM.exposure.summary"].metrics}
        assert exposure == Decimal(42130) == Decimal(totals["eligible_month_exposure"])
        peers = db.execute("""
            SELECT count(DISTINCT contracts.client_id), count(DISTINCT CASE WHEN contract_lines.product_id='WIDGETS' THEN contracts.client_id END)
            FROM clients JOIN contracts USING(client_id) JOIN contract_lines USING(contract_id)
            WHERE segment=(SELECT segment FROM clients WHERE client_id=?) AND client_id<>?
              AND start_date<=? AND end_date>=? AND signed_date<>'' AND signed_date<=? AND status='active'
        """, ("C002", "C002", str(AS_OF), str(AS_OF), str(AS_OF))).fetchone()
        assert tuple(peers) == (5, 4)
        peer_fact = {item.name: item.value for item in corvane["contextual.peer.WIDGETS"].metrics}
        assert (peer_fact["eligible_peer_count"], peer_fact["holding_peer_count"]) == tuple(peers)
        fit = corvane_tools["inquiry"].data["inquiries"]["need_product_alignment"]
        assert fit["conclusion"] == "supported" and "contextual.gap.WIDGETS" in fit["supporting_evidence_keys"]
        report["C002"] = {"calculated_streaming_exposure_eur": int(exposure), "widget_peers": [peers[1], peers[0]]}

        brenmoor, brenmoor_tools = results["C003"]
        inquiry = brenmoor_tools["inquiry"].data["inquiries"]["notes_record_discrepancies"]
        combined = brenmoor["inquiry.notes_record_discrepancies"]
        assert inquiry["conclusion"] == "insufficient_evidence" and inquiry["product_id"] == "STREAM"
        assert any(ref.table == "meeting_notes" and ref.key == "N-006" for ref in combined.sources)
        assert any(ref.table == "usage_monthly" and ref.raw["product_id"] == "STREAM" for ref in combined.sources)
        report["C003"] = inquiry

        varrow, _ = results["C007"]
        exposure = varrow["telemetry.usage.LIVE_DATA.exposure.summary"]
        totals = {item.name: item.value for item in exposure.metrics}
        assert totals["charge_assessed_months"] == 2 and totals["window_months"] == 3
        assert "no matching signed term" in exposure.text and "full-period exposure" in exposure.text
        report["C007"] = {"assessed_months": 2, "window_months": 3, "full_period_exposure": None}
    return report


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    output = json.dumps(independently_check(args.data_dir), ensure_ascii=False, indent=2) + "\n"
    if args.report:
        args.report.write_text(output, encoding="utf-8", newline="\n")
    print(output, end="")


if __name__ == "__main__":
    main()
