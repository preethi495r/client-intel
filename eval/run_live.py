"""Run the six requested cases through the normal live briefing service."""

from argparse import ArgumentParser
import json
from pathlib import Path

from client_intel.planning import Request, load_config
from client_intel.run import brief
from client_intel.settings import environment


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--case", action="append", help="Run only a named fixture; repeat to select more cases")
    args = parser.parse_args()
    env = environment()
    override = env.get("CLIENT_INTEL_CONFIG_DIR")
    config = load_config(Path(override) if override else None)
    fixtures = json.loads(Path(__file__).with_name("requests.json").read_text(encoding="utf-8"))
    if args.case and set(args.case) - {item["id"] for item in fixtures}:
        parser.error("unknown evaluation case")
    runs = []
    for case in fixtures:
        if args.case and case["id"] not in args.case:
            continue
        request = Request(case["client"], case["trigger"], case["rep_context"])
        result = brief(request, args.data_dir, args.output_dir, config, env)
        runs.append({"fixture": case, "artifacts": result.path.name, "status": result.status})
        print(f"{case['id']}: {result.status} ({result.path.name})", flush=True)
    summary = args.output_dir / "live-runs.json"
    summary.write_text(json.dumps(runs, indent=2) + "\n", encoding="utf-8", newline="\n")
    return int(any(run["status"] == "incomplete" for run in runs))


if __name__ == "__main__":
    raise SystemExit(main())
