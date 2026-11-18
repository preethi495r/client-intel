"""Shared run service and durable per-run artifacts."""

from contextlib import closing
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
from time import perf_counter
from typing import Callable
from uuid import uuid4
import json

from .models import AS_OF, Evidence, ToolContext
from .planning import Request, RequestError, collect, open_snapshot, resolve_client
from .settings import environment
from .verification import Line, audit_rendered, render, verify_draft
from .writer import PROMPT_PATH, ModelSettings, WriterError, candidates, write


@dataclass(frozen=True)
class Run:
    path: Path
    status: str


def _save(path: Path, value) -> None:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
    pending = path.with_suffix(path.suffix + ".tmp")
    pending.write_text(text, encoding="utf-8", newline="\n")
    pending.replace(path)


def _save_trace(path: Path, entries: list[dict]) -> None:
    _save(path, "".join(json.dumps(item, ensure_ascii=False, allow_nan=False) + "\n" for item in entries))


def _estimated_cost(model_info: dict | None, env: dict[str, str]) -> dict:
    usage = (model_info or {}).get("usage") or {}
    version = env.get("CLIENT_INTEL_RATE_VERSION")
    if not version or not {"input_tokens", "output_tokens"}.issubset(usage):
        return {"available": False, "reason": "Usage or configured rate version is unavailable."}
    try:
        input_rate = Decimal(env["CLIENT_INTEL_INPUT_EUR_PER_MTOK"])
        output_rate = Decimal(env["CLIENT_INTEL_OUTPUT_EUR_PER_MTOK"])
        if any(not rate.is_finite() or rate < 0 for rate in (input_rate, output_rate)):
            raise InvalidOperation
        estimate = (usage["input_tokens"] * input_rate + usage["output_tokens"] * output_rate) / 1_000_000
    except (KeyError, InvalidOperation, TypeError):
        return {"available": False, "reason": "Configured token rates are incomplete or invalid."}
    return {"available": True, "estimated_eur": str(estimate), "rate_version": version,
            "input_eur_per_million": str(input_rate), "output_eur_per_million": str(output_rate),
            "basis": "All input and output tokens at the declared rates before any cache discounts."}


def _finish_sections(sections: dict, options: dict[str, Evidence], by_key: dict[str, Evidence],
                     trigger: dict, issues: list[dict], plan: dict) -> str:
    if not sections["gaps"]:
        gap = next((item for item in options.values() if item.analysis == "data_quality"), None)
        if gap:
            sections["gaps"].append(Line("gaps.default", "fact", gap.text, (gap.key,)))
    lead_analyses = {by_key[key].analysis for line in sections["lead"] for key in line.evidence}
    missing = [group for group in trigger["required_in_lead"] if not lead_analyses.intersection(group)]
    if missing:
        issues.append({"check": "core_answer_missing", "target": "lead", "groups": missing, "severity": "critical"})
    if not sections["actions"]:
        issues.append({"check": "grounded_next_step_missing", "target": "actions", "severity": "critical"})
    if any(call["status"] == "error" for call in plan["calls"]):
        issues.append({"check": "tool_unavailable", "target": "plan", "severity": "critical"})
    if any(issue["check"] in {"core_answer_missing", "grounded_next_step_missing", "tool_unavailable"} for issue in issues):
        return "incomplete"
    if sections["gaps"] or issues:
        return "verified_with_gaps"
    return "verified"


def brief(request: Request, data_dir: Path, output_dir: Path, config: dict,
          env: dict[str, str] | None = None, progress: Callable[[str], None] | None = None) -> Run:
    start = perf_counter()
    if request.trigger not in config["triggers"]:
        raise RequestError("Choose a configured trigger.")
    env = environment() if env is None else env
    mode = env.get("CLIENT_INTEL_EXECUTION_MODE", "parallel")
    if mode not in {"serial", "parallel"}:
        raise RequestError("CLIENT_INTEL_EXECUTION_MODE must be serial or parallel.")
    credential = env.get("OPENAI_API_KEY", "").strip()
    if credential and credential != "replace-with-your-openai-api-key" and any(
        credential in value for value in (request.client, request.rep_context)
    ):
        raise RequestError("Remove the configured API credential from the request.")
    trigger = config["triggers"][request.trigger]
    if progress:
        progress("resolving")
    with closing(open_snapshot(data_dir)) as db:
        client = resolve_client(db, request.client)
        resolved_at = perf_counter()
        if progress:
            progress("evidence")
        plan, results = collect(ToolContext(db, client), config, request.trigger,
                                data_dir if mode == "parallel" else None)
    evidence = [item for result in results.values() for item in result.evidence]
    by_key = {item.key: item for item in evidence}
    if len(by_key) != len(evidence):
        raise RequestError("A tool returned duplicate evidence keys.")
    options = candidates(evidence, trigger)
    prompt = PROMPT_PATH.read_text(encoding="utf-8")
    run_id = uuid4().hex[:12]
    location = output_dir.resolve() / f"{client['client_id']}_{request.trigger}_{run_id}"
    location.mkdir(parents=True)
    snapshot = str(AS_OF)
    configuration_hash = sha256(json.dumps(trigger, sort_keys=True).encode()).hexdigest()
    document = {"schema_version": 1, "run_id": run_id, "request": asdict(request),
                "client": client, "snapshot": snapshot,
                "snapshot_sha256": sha256((data_dir / "client_intel.db").read_bytes()).hexdigest(),
                "prompt_sha256": sha256(prompt.encode()).hexdigest(), "trigger_configuration": trigger,
                "configuration_sha256": configuration_hash,
                "plan": plan, "tools": {key: asdict(result) for key, result in results.items()},
                "writer_candidates": {alias: item.key for alias, item in options.items()}}
    trace = [{"event": "resolve", "client_id": client["client_id"], "trigger": request.trigger,
              "seconds": round(resolved_at - start, 6)},
             {"event": "plan", "parallel_group": plan["parallel_group"],
              "selection": plan["selection"], "skipped": plan["skipped"]},
             *({"event": "tool", **item} for item in plan["calls"])]
    _save(location / "evidence.json", document)
    _save(location / "verification.json", {"status": "incomplete", "stage": "evidence_saved"})
    _save_trace(location / "trace.jsonl", trace)
    payload = {"client": client["client_name"], "trigger": request.trigger, "snapshot": snapshot,
               "objective": trigger["objective"], "required_lead_groups": trigger["required_in_lead"],
               "rep_context": request.rep_context,
               "fact_options": [[alias, item.analysis, item.text] for alias, item in options.items()]}
    issues = []
    model_info = None
    writer_start = perf_counter()
    sections = {"lead": [], "context": [], "actions": [], "gaps": []}
    if progress:
        progress("writing")
    try:
        settings = ModelSettings.from_env(env)
        draft, model_info = write(settings, prompt, payload)
    except WriterError as exc:
        issues.append({"check": "writer_failed", "target": "writer", "reason": str(exc), "severity": "critical"})
    else:
        sections, issues = verify_draft(draft, options)
    trace.append({"event": "writer", "status": "completed" if model_info else "failed", "model": model_info,
                  "elapsed_seconds": round(perf_counter() - writer_start, 6),
                  "cost_estimate": _estimated_cost(model_info, env)})
    if progress:
        progress("verifying")
    status = _finish_sections(sections, options, by_key, trigger, issues, plan)
    if status == "incomplete":
        notice = "The core answer or an evidence-backed next step could not be verified. Review the saved evidence and verification report."
    elif issues:
        notice = "Some proposed statements or actions were withheld after verification."
    else:
        notice = ""
    markdown, mapping = render(client, snapshot, trigger, sections, by_key, status, notice)
    audit_failures = audit_rendered(markdown, mapping, by_key)
    if audit_failures:
        status = "incomplete"
        empty_sections = {section: [] for section in sections}
        markdown, mapping = render(client, snapshot, trigger, empty_sections, by_key, status,
                                   "The briefing was withheld after a rendered-output verification failure.")
    document.update(mapping)
    report = {"status": status, "stage": "finished", "model": model_info,
              "draft_checks": issues, "render_checks": audit_failures,
              "emitted_claim_count": len(mapping["claims"]),
              "emitted_action_count": sum(claim["kind"] == "action" for claim in mapping["claims"]),
              "seconds": round(perf_counter() - start, 3)}
    _save(location / "evidence.json", document)
    _save(location / "briefing.md", markdown)
    _save(location / "verification.json", report)
    trace.append({"event": "verify", "status": status, "draft_issues": len(issues),
                  "rendered_issues": len(audit_failures), "claims": len(mapping["claims"])})
    _save_trace(location / "trace.jsonl", trace)
    _save(location / "manifest.json", {
        "schema_version": 1, "run_id": run_id, "snapshot": snapshot, "snapshot_sha256": document["snapshot_sha256"],
        "configuration_sha256": configuration_hash, "prompt_sha256": document["prompt_sha256"],
        "status": status, "model": model_info["reported_model"] if model_info else None,
        "artifacts": ["briefing.md", "evidence.json", "verification.json", "trace.jsonl"],
    })
    return Run(location, status)
