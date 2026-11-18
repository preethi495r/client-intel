"""Independently check representative tool values and fault-inject the rendered verifier."""

from argparse import ArgumentParser
from contextlib import closing
from datetime import date, timedelta
import json
from pathlib import Path
import sqlite3

from client_intel.models import Evidence, Source, ToolContext
from client_intel.planning import open_snapshot, resolve_client
from client_intel.tools import commercial, telemetry
from client_intel.verification import REFERENCE, audit_rendered, verify_draft


def evidence_index(serialized: dict) -> dict[str, Evidence]:
    return {item["key"]: Evidence(item["key"], item["analysis"], item["text"],
                                 tuple(Source(**ref) for ref in item["sources"]))
            for tool in serialized["tools"].values() for item in tool["evidence"]}


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def source_checks(data_dir: Path) -> dict:
    tools = {}
    with closing(open_snapshot(data_dir)) as db:
        for name, identifier in (("Halden Bet", "C001"), ("Corvane Media", "C002"),
                                 ("Varrow Racing Authority", "C007"), ("Brenmoor League", "C003")):
            assert resolve_client(db, name)["client_id"] == resolve_client(db, identifier.lower())["client_id"] == identifier
        try:
            db.execute("CREATE TABLE forbidden (id INTEGER)")
        except sqlite3.OperationalError:
            pass
        else:
            raise AssertionError("The snapshot connection permitted a write.")
        for identifier in ("C001", "C004", "C005", "C006", "C007", "C010"):
            ctx = ToolContext(db, resolve_client(db, identifier))
            commercial_result = commercial.run(ctx, ("contract_position", "renewal_calendar", "opportunity_snapshot"), {})
            telemetry_result = telemetry.run(ctx, ("usage_history", "usage_trends", "support_summary", "support_csat"), {})
            tools[identifier] = {item.key: item for result in (commercial_result, telemetry_result)
                                 for item in result.evidence}

        k2 = db.execute("SELECT end_date, notice_period_days, arr_eur FROM contracts WHERE contract_id=?", ("K-0002",)).fetchone()
        threshold = date.fromisoformat(k2["end_date"]) - timedelta(days=int(k2["notice_period_days"]))
        notice = {metric.name: metric.value for metric in tools["C001"]["commercial.notice.K-0002"].metrics}
        assert str(threshold) == notice["notice_deadline"] == "2026-12-02"
        signed_term = tools["C001"]["commercial.contract.K-0002"]
        assert any(metric.value == int(k2["arr_eur"]) == 752000 for metric in signed_term.metrics)

        result = {"C001": {"notice_deadline": str(threshold), "signed_arr_eur": int(k2["arr_eur"]), "usage": {}}}
        in_play = db.execute("SELECT product_id FROM products WHERE lower(product_name) LIKE ?", ("%in-play%",)).fetchone()[0]
        for product in (in_play, "LIVE_DATA"):
            observed = dict(db.execute("""
                SELECT substr(month, 1, 4), SUM(CAST(units_used AS INTEGER)) FROM usage_monthly
                WHERE client_id=? AND product_id=? AND substr(month, 6, 2) IN ('08', '09', '10')
                GROUP BY substr(month, 1, 4)
            """, ("C001", product)))
            recent, baseline = observed["2026"], observed["2025"]
            text = tools["C001"][f"telemetry.usage.{product}.yoy"].text
            magnitude = abs(100 * (recent / baseline - 1))
            assert f"{recent:,}" in text and f"{baseline:,}" in text and f"{magnitude:.1f}%" in text
            result["C001"]["usage"][product] = {"recent": recent, "baseline": baseline, "yoy_percent": -round(magnitude, 1)}
        scores = [int(row[1]) for row in db.execute("""
            SELECT DISTINCT ticket_id, csat_score FROM support_tickets
            WHERE client_id=? AND closed_date BETWEEN ? AND ? AND csat_score <> ''
        """, ("C001", "2026-08-04", "2026-11-01"))]
        assert len(scores) == 4 and sum(scores) / len(scores) == 1.25
        assert "1.25/5" in tools["C001"]["telemetry.support.csat.recent"].text
        result["C001"]["csat"] = {"responses": len(scores), "average": sum(scores) / len(scores)}

        short = list(db.execute("SELECT DISTINCT month FROM usage_monthly WHERE client_id=? ORDER BY month", ("C004",)))
        assert [row[0] for row in short] == ["2026-09", "2026-10"]
        assert not any(key.endswith(".yoy") and "year over year" in item.text and "%" in item.text
                       for key, item in tools["C004"].items())
        result["C004"] = {"months": [row[0] for row in short], "yoy_percentage_withheld": True}
        row_count = db.execute("SELECT COUNT(*) FROM usage_monthly WHERE client_id=? AND month=?", ("C005", "2026-03")).fetchone()[0]
        assert row_count == 0 and any("2026-03" in item.text or "Mar 2026" in item.text for item in tools["C005"].values())
        result["C005"] = {"missing_month": "2026-03"}
        duplicate = tools["C006"]["commercial.rows.contracts.K-0010"]
        assert {item.rowid for item in duplicate.sources} == {10, 46}
        assert any(metric.value == 176000 for metric in tools["C006"]["commercial.contract.K-0010"].metrics)
        assert any({"U-00422", "U-01991"}.issubset({ref.key for ref in item.sources}) for item in tools["C006"].values())
        result["C006"] = {"signed_arr_eur": 176000, "contract_occurrences": [10, 46], "usage_ids": ["U-00422", "U-01991"]}
        pending = db.execute("SELECT status, signed_date, arr_eur FROM contracts WHERE contract_id=?", ("K-0045",)).fetchone()
        assert tuple(pending) == ("pending_signature", "", "200000")
        assert "pending signature" in tools["C007"]["commercial.contract.K-0045"].text
        expired = tools["C007"]["commercial.elapsed.K-0011"]
        assert "no signed term covering the snapshot" in expired.text
        old_term = db.execute("SELECT end_date, notice_period_days FROM contracts WHERE contract_id=?", ("K-0011",)).fetchone()
        old_notice = date.fromisoformat(old_term[0]) - timedelta(days=int(old_term[1]))
        assert any(metric.name == "notice_deadline" and metric.value == str(old_notice) for metric in expired.metrics)
        result["C007"] = {"draft_arr_eur": int(pending["arr_eur"]), "executed_coverage_confirmed": False,
                          "historical_notice_deadline": str(old_notice)}
        assert any(len({ref.rowid for ref in item.sources if ref.key == "T-0281"}) == 2
                   for item in tools["C010"].values())
        result["C010"] = {"duplicate_ticket": "T-0281", "physical_occurrences": 2}
    return result


def artifact_checks(directory: Path) -> dict:
    rows = read_json(directory / "live-runs.json")
    if len(rows) != 6 or len({row["fixture"]["id"] for row in rows}) != 6:
        raise AssertionError("Expected the six live evaluation cases.")
    report = {}
    for row in rows:
        path = directory / row["artifacts"]
        evidence = read_json(path / "evidence.json")
        verification = read_json(path / "verification.json")
        markdown = (path / "briefing.md").read_text(encoding="utf-8")
        index = evidence_index(evidence)
        assert verification["model"] and verification["status"] != "incomplete"
        assert not audit_rendered(markdown, evidence, index)
        report[row["fixture"]["id"]] = {"status": verification["status"],
                                        "claims": verification["emitted_claim_count"],
                                        "actions": verification["emitted_action_count"],
                                        "draft_checks": verification["draft_checks"]}

    claim = next(item for item in evidence["claims"] if item["kind"] == "fact")
    line = markdown.splitlines()[claim["line"] - 1]
    unsupported = markdown.replace(line, line.replace(" [^", " 987654321 [^", 1), 1)
    uncited = markdown.replace(line, REFERENCE.sub("", line).rstrip(), 1)
    number_checks = audit_rendered(unsupported, evidence, index)
    citation_checks = audit_rendered(uncited, evidence, index)
    assert {"rendered_unbound_number"}.issubset(item["check"] for item in number_checks)
    assert {"rendered_missing_or_invalid_citation"}.issubset(item["check"] for item in citation_checks)

    existing = next(iter(index.values()))
    draft = {"lead": ["F01"], "context": [], "gaps": [], "actions": [
        {"action": "Ask whether the budget is 987654321?", "why": ["F01"], "readiness": "needs_validation"},
        {"action": "Ask about budget.", "why": [], "readiness": "needs_validation"},
    ]}
    selected, rejections = verify_draft(draft, {"F01": existing})
    assert not selected["actions"]
    assert {"unbound_number", "unresolved_or_missing_citation"} == {item["check"] for item in rejections}
    widgets_run = next(row for row in rows if row["fixture"]["id"] == "c002_upsell")
    widgets = evidence_index(read_json(directory / widgets_run["artifacts"] / "evidence.json"))["contextual.gap.WIDGETS"]
    draft["actions"] = [{"action": "Demo the embeddable live match tracker in a second-screen app mockup.",
                         "why": ["F01"], "readiness": "ready_for_discussion"}]
    selected, compound_checks = verify_draft(draft, {"F01": widgets})
    assert len(selected["actions"]) == 1 and not compound_checks
    return {"runs": report, "fault_injections": {"rendered_number": number_checks,
                                                 "rendered_missing_citation": citation_checks,
                                                 "draft_rejections": rejections,
                                                 "cited_second_screen_action_allowed": True}}


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--examples", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    report = {"independent_source_checks": source_checks(args.data_dir)}
    if args.examples:
        report.update(artifact_checks(args.examples))
    content = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.report:
        args.report.write_text(content, encoding="utf-8", newline="\n")
    print(content, end="")


if __name__ == "__main__":
    main()
