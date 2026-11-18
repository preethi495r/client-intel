"""Check the deterministic client/trigger matrix and optionally rerun sample requests live."""

from argparse import ArgumentParser
from contextlib import closing
from dataclasses import asdict
import json
from pathlib import Path

from client_intel.models import ToolContext
from client_intel.planning import Request, collect, load_config, open_snapshot, resolve_client
from client_intel.run import brief
from client_intel.settings import environment
from client_intel.writer import candidates
from eval.check_snapshot import read_json, source_checks


def verify_scope(client_id, evidence, account_contracts, peer_scope):
    for item in evidence.values():
        peer_fact = item.analysis == "peer_holdings" or item.key.startswith("quality.contextual.peer.")
        allowed_clients, allowed_contracts = peer_scope if peer_fact and peer_scope else ({client_id}, account_contracts)
        for row in item.sources:
            if "client_id" in row.raw and row.raw["client_id"] not in allowed_clients:
                raise AssertionError(f"{client_id} received an out-of-scope row in {item.key}")
            if row.table == "contract_lines" and row.raw["contract_id"] not in allowed_contracts:
                raise AssertionError(f"{client_id} received an out-of-scope contract line in {item.key}")


def regression(data_dir: Path, config: dict, compare_parallel: bool = False) -> tuple[dict, list[Request]]:
    plan_checks = []
    case_checks = []
    matrix = {}
    requests = {}
    with closing(open_snapshot(data_dir)) as db:
        for row in db.execute("SELECT rowid, * FROM clients ORDER BY client_id").fetchall():
            client_id = row["client_id"]
            contracts = {record[0] for record in db.execute("SELECT contract_id FROM contracts WHERE client_id=?", (client_id,))}
            for trigger, settings in config["triggers"].items():
                context = ToolContext(db, dict(row))
                plan, tools = collect(context, config, trigger)
                if compare_parallel:
                    concurrent, concurrent_tools = collect(context, config, trigger, data_dir)
                    if concurrent_tools != tools or concurrent["selection"] != plan["selection"]:
                        raise AssertionError(f"Serial and parallel outputs differ for {client_id}/{trigger}")
                entries = [item for result in tools.values() for item in result.evidence]
                evidence = {item.key: item for item in entries}
                if len(evidence) != len(entries) or any(call["status"] == "error" for call in plan["calls"]):
                    raise AssertionError(f"{client_id}/{trigger} had a tool error or duplicate evidence key")
                peer_scope = None
                if "peer_holdings" in plan["selection"]:
                    peer_scope = (
                        {record[0] for record in db.execute("SELECT client_id FROM clients WHERE segment=?", (row["segment"],))},
                        {record[0] for record in db.execute("SELECT contract_id FROM contracts JOIN clients USING(client_id) WHERE segment=?",
                                                           (row["segment"],))},
                    )
                verify_scope(client_id, evidence, contracts, peer_scope)
                json.dumps({tool: asdict(result) for tool, result in tools.items()}, allow_nan=False)
                options = candidates(entries, settings)
                available = {item.analysis for item in options.values()}
                if any(not available.intersection(group) for group in settings["required_in_lead"]):
                    raise AssertionError(f"{client_id}/{trigger} cannot assess a required lead group")
                matrix[(client_id, trigger)] = options
                plan_checks.append({"client_id": client_id, "trigger": trigger,
                                    "model_candidates": len(options), "planned_analyses": len(plan["selection"])})

        samples = read_json(data_dir / "sample_requests.json")
        for sample in samples:
            request = Request(sample["client"], sample["trigger"], sample["rep_context"])
            client_id = resolve_client(db, request.client)["client_id"]
            key = (client_id, request.trigger)
            if key not in matrix:
                raise AssertionError(f"The matrix lacks {sample['request_id']}")
            requests[key] = request
            case_checks.append({"sample_request": sample["request_id"], "client_id": client_id,
                                "trigger": request.trigger, "evidence_bundle_available": True})

        for case in read_json(Path(__file__).with_name("regression_cases.json")):
            key = (case["client"], case["trigger"])
            requests.setdefault(key, Request(*key))
            options = {item.key: item for item in matrix[key].values()}
            for evidence_key, expected in case["expect"].items():
                if evidence_key not in options or expected not in options[evidence_key].text:
                    raise AssertionError(f"{case['label']} failed for {evidence_key}")
            case_checks.append({"case": case["label"], "client_id": key[0], "trigger": key[1],
                                "checked_evidence": list(case["expect"])})
    return {"matrix_checked": len(plan_checks), "matrix": plan_checks, "fixtures": case_checks,
            "serial_parallel_comparisons": len(plan_checks) if compare_parallel else 0,
            "distinct_sample_and_edge_requests": len(requests),
            "independent_source_checks": source_checks(data_dir)}, list(requests.values())


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--report", type=Path, help="Optional path for the JSON report")
    parser.add_argument("--live-output", type=Path, help="Opt in to live model calls for the deduplicated fixtures")
    parser.add_argument("--compare-parallel", action="store_true", help="Compare serial and parallel tool values and evidence")
    args = parser.parse_args()
    env = environment()
    override = env.get("CLIENT_INTEL_CONFIG_DIR")
    config = load_config(Path(override) if override else None)
    report, requests = regression(args.data_dir, config, args.compare_parallel)
    print(f"Passed {report['matrix_checked']} tool/plan combinations and {len(report['fixtures'])} sample or edge checks.")
    if args.compare_parallel:
        print(f"Serial and parallel tool results matched for {report['serial_parallel_comparisons']} combinations.")
    runs = []
    if args.live_output:
        for request in requests:
            result = brief(request, args.data_dir, args.live_output, config, env)
            runs.append({"request": asdict(request), "status": result.status, "artifacts": result.path.name})
            print(f"{request.client} / {request.trigger}  {result.status}", flush=True)
    report["live_runs"] = runs
    if args.report:
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return int(any(run["status"] == "incomplete" for run in runs))


if __name__ == "__main__":
    raise SystemExit(main())
