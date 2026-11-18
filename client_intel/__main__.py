"""Command-line entry point."""

import argparse
import json
from pathlib import Path
import sqlite3
import sys

from .planning import Request, RequestError, load_config
from .diagnostics import model_smoke, readiness
from .portfolio import renewal_priorities
from .run import brief
from .settings import environment


def main(argv: list[str] | None = None) -> int:
    try:
        env = environment()
        config_dir = env.get("CLIENT_INTEL_CONFIG_DIR")
        config = load_config(Path(config_dir) if config_dir else None)
    except (KeyError, OSError, TypeError, ValueError) as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    parser = argparse.ArgumentParser(prog="python -m client_intel", description="Generate a cited sales briefing.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    command = subparsers.add_parser("brief", help="Brief one account.")
    command.add_argument("--client", required=True, help="Client name or ID")
    command.add_argument("--trigger", required=True, choices=config["triggers"], help="Briefing objective")
    command.add_argument("--rep-context", default="", help="Optional synthetic question or context")
    portfolio = subparsers.add_parser("portfolio", help="Review recorded renewal priorities across the snapshot.")
    serve = subparsers.add_parser("serve", help="Start the local briefing browser interface.")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    doctor = subparsers.add_parser("doctor", help="Check the source, local storage and model configuration.")
    doctor.add_argument("--check-model", action="store_true", help="Make a small structured call to the configured model.")
    for entry in (serve, doctor):
        entry.add_argument("--state-dir", type=Path, default=env.get("CLIENT_INTEL_STATE_DIR", ".client-intel-state"))
    for entry in (command, portfolio, serve, doctor):
        entry.add_argument("--data-dir", type=Path, default=env.get("CLIENT_INTEL_DATA_DIR"),
                           help="Directory containing the starter-pack SQLite snapshot")
        entry.add_argument("--output-dir", type=Path, default=env.get("CLIENT_INTEL_OUTPUT_DIR", "outputs"),
                           help="Directory for run artifacts")
    args = parser.parse_args(argv)
    if args.data_dir is None:
        parser.error("set --data-dir or CLIENT_INTEL_DATA_DIR")
    if args.command == "doctor":
        result = readiness(args.data_dir, args.output_dir, args.state_dir, env)
        if result["ready"] and args.check_model:
            result["model_probe"] = model_smoke(env)
            result["ready"] = result["model_probe"]["ok"]
        print(json.dumps(result, indent=2))
        return 0 if result["ready"] else 1
    if args.command == "serve":
        try:
            import uvicorn
            from .web import create_app
        except ImportError:
            print("Install the web dependencies with pip install -r requirements-web.lock.", file=sys.stderr)
            return 2
        uvicorn.run(create_app(args.data_dir, args.output_dir, args.state_dir, config, env),
                    host=args.host, port=args.port, workers=1, access_log=False)
        return 0
    try:
        if args.command == "portfolio":
            result = renewal_priorities(args.data_dir, args.output_dir)
        else:
            result = brief(Request(args.client, args.trigger, args.rep_context), args.data_dir, args.output_dir, config, env)
    except (RequestError, OSError, sqlite3.Error) as exc:
        print(f"Briefing error: {exc}", file=sys.stderr)
        return 2
    print(f"{result.status}: {result.path}")
    return 1 if result.status == "incomplete" else 0


if __name__ == "__main__":
    raise SystemExit(main())
