"""Resolve an account, validate configuration and acquire trigger-scoped evidence."""

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
import json
import re
import sqlite3
from time import perf_counter

from .models import AS_OF, Evidence, Metric, ToolContext, ToolResult, source

PACKAGE_DIR = Path(__file__).resolve().parent
TOOLS = {
    "commercial": ("contract_position", "renewal_calendar", "opportunity_snapshot", "contract_reconciliation",
                   "contract_changes", "opportunity_age", "proposal_comparison"),
    "telemetry": ("usage_history", "usage_trends", "support_summary", "support_csat",
                  "entitlement_utilisation", "overage_exposure", "support_resolution"),
    "contextual": ("meeting_context", "catalogue_relationships", "product_gaps", "recorded_commitments", "peer_holdings"),
    "inquiry": ("decline_breakdown", "notes_record_discrepancies", "contract_usage_reconciliation",
                "need_product_alignment", "prior_offer_context", "renewal_execution_gaps",
                "commercial_change_context", "value_evidence_inquiry", "coverage_inquiry"),
}
BINDINGS = {analysis: tool for tool, analyses in TOOLS.items() for analysis in analyses}
COMMON = ("client_profile", "data_quality")
PREREQUISITES = {
    "product_gaps": {"contract_position"},
    "contract_reconciliation": {"contract_position"},
    "contract_changes": {"contract_position"},
    "opportunity_age": {"opportunity_snapshot"},
    "proposal_comparison": {"contract_position", "opportunity_snapshot"},
    "entitlement_utilisation": {"contract_position", "usage_history"},
    "overage_exposure": {"entitlement_utilisation"},
    "support_resolution": {"support_summary"},
    "recorded_commitments": {"meeting_context"},
    "peer_holdings": {"contract_position", "catalogue_relationships"},
    "decline_breakdown": {"usage_trends"},
    "notes_record_discrepancies": {"meeting_context", "usage_trends"},
    "contract_usage_reconciliation": {"contract_position", "usage_history"},
    "need_product_alignment": {"product_gaps", "meeting_context"},
    "prior_offer_context": {"opportunity_snapshot"},
    "renewal_execution_gaps": {"contract_position", "opportunity_snapshot"},
    "commercial_change_context": {"contract_changes"},
    "value_evidence_inquiry": {"meeting_context"},
    "coverage_inquiry": {"usage_history"},
}


class RequestError(ValueError):
    pass


@dataclass(frozen=True)
class Request:
    client: str
    trigger: str
    rep_context: str = ""


def load_config(config_dir: Path | None = None) -> dict:
    directory = config_dir or PACKAGE_DIR / "config"
    with (directory / "triggers.json").open(encoding="utf-8") as handle:
        config = json.load(handle)
    with (directory / "analysis_catalogue.json").open(encoding="utf-8") as handle:
        catalogue = json.load(handle)
    eligible = {item["id"] for item in catalogue["analyses"]
                if item["availability"] in {"supported", "qualified"}}
    if config["version"] != 1 or set(config["always"]) != set(COMMON):
        raise RequestError("Invalid trigger configuration version or common analyses.")
    if (set(BINDINGS) | set(COMMON)) - eligible:
        raise RequestError("The prototype analysis bindings are inconsistent with the catalogue.")
    trigger_fields = {"label", "lead_heading", "support_heading", "action_heading", "objective",
                      "core", "required_in_lead", "support"}
    if not config["triggers"]:
        raise RequestError("No triggers are configured.")
    for name, trigger in config["triggers"].items():
        if set(trigger) != trigger_fields or not re.fullmatch(r"[a-z][a-z_]+", name):
            raise RequestError(f"Invalid trigger configuration for {name}.")
        selections = trigger["core"] + trigger["support"]
        selected_set = set(selections)
        if len(selections) != len(selected_set) or selected_set - BINDINGS.keys():
            raise RequestError(f"Duplicate or unavailable analysis configured for {name}.")
        groups = trigger["required_in_lead"]
        if not groups or any(not group or set(group) - selected_set for group in groups):
            raise RequestError(f"Invalid lead requirements for {name}.")
        for analysis in selections:
            missing = PREREQUISITES.get(analysis, set()) - selected_set
            if missing:
                raise RequestError(f"{name}.{analysis} requires {', '.join(sorted(missing))}.")
        if any(not isinstance(trigger[key], str) or not trigger[key].strip() or
               "\n" in trigger[key] for key in trigger_fields - {"core", "support", "required_in_lead"}):
            raise RequestError(f"Expected a non-empty label, heading or objective for {name}.")
    config["catalogue"] = [item["id"] for item in catalogue["analyses"]]
    return config


def open_snapshot(data_dir: Path) -> sqlite3.Connection:
    path = data_dir.resolve() / "client_intel.db"
    if not path.is_file():
        raise RequestError(f"SQLite snapshot missing at {path}.")
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only = ON")
    return db


def _normalise(name: str) -> list[str]:
    return re.findall(r"[^\W_]+", name.casefold())


def resolve_client(db: sqlite3.Connection, query: str) -> dict:
    query = query.strip()
    if not _normalise(query):
        raise RequestError("Give a client name or ID.")
    rows = db.execute("""
        SELECT rowid, * FROM clients
        WHERE lower(client_id) = lower(?) OR lower(client_name) = lower(?)
        ORDER BY client_id
    """, (query, query)).fetchall()
    if not rows:
        # A short account name may omit whole words in order, as in the supplied requests.
        words = _normalise(query)
        candidates = db.execute("SELECT rowid, * FROM clients ORDER BY client_id").fetchall()
        for row in candidates:
            name_words = iter(_normalise(row["client_name"]))
            if all(word in name_words for word in words):
                rows.append(row)
    if len(rows) == 1:
        return dict(rows[0])
    if len(rows) > 1:
        choices = ", ".join(f'{row["client_name"]} ({row["client_id"]})' for row in rows)
        raise RequestError(f"Ambiguous client. Choose one of {choices}.")
    raise RequestError(f"Unknown client {query!r}. Use the recorded name or client ID.")


def _profile(ctx: ToolContext) -> ToolResult:
    row = ctx.client
    period = f"Snapshot {ctx.as_of}"
    since = row["customer_since"]
    text = (f'{row["client_name"]} is recorded as a {row["tier"]} {row["segment"]} account '
            f'in {row["country"]}, owned by {row["account_owner"]}, with a customer-since date of {since}.')
    return ToolResult(evidence=[Evidence(
        "profile.account", "client_profile", text, (source("clients", "client_id", row),),
        (Metric("customer_since", since, "date", since, period),), period=period, priority=90,
    )])


def _quality(results: dict[str, ToolResult]) -> ToolResult:
    findings = []
    seen = {item.text for result in results.values() for item in result.evidence
            if item.analysis == "data_quality" and item.model_visible}
    for result in results.values():
        for item in result.evidence:
            if not item.model_visible or not item.caveats:
                continue
            for index, caveat in enumerate(item.caveats, 1):
                if caveat in seen:
                    continue
                seen.add(caveat)
                # Keep the context and scope of the original caveat in the evidence graph.
                findings.append(Evidence(
                    f"quality.{item.key}.{index}", "data_quality", caveat, item.sources,
                    item.metrics, item.product_id, item.period, priority=item.priority,
                ))
    contextual = results.get("contextual", ToolResult())
    cost_statement = next((item for item in contextual.evidence
                           if item.analysis == "meeting_context" and "overage bill" in item.text.casefold()), None)
    if cost_statement:
        findings.append(Evidence(
            "quality.billing_coverage", "data_quality",
            "Invoice and credit records are absent from the snapshot, leaving the actual charges raised in the meeting unconfirmed.",
            cost_statement.sources, period=cost_statement.period, priority=18,
        ))
    commercial = results.get("commercial", ToolResult())
    pending = {item["contract_id"] for item in commercial.data.get("contracts", [])
               if item["position"] == "pending" and item["contract_type"] == "renewal"}
    pending_terms = [item for item in commercial.evidence if item.key.startswith("commercial.contract.")
                     and item.key.rsplit(".", 1)[-1] in pending]
    agreement = next((item for item in contextual.evidence if item.analysis == "meeting_context" and
                      re.search(r"\bagreed commercially\b|\bbasically agreed\b", item.text, re.I)), None)
    if agreement and pending_terms:
        findings.append(Evidence(
            "quality.reported_agreement_and_pending_terms", "data_quality",
            "A meeting note records agreement in principle and the renewal draft is still marked pending signature. "
            "The export leaves any later approval and execution unconfirmed.",
            agreement.sources + tuple(ref for item in pending_terms for ref in item.sources),
            period=agreement.period, priority=7,
        ))
    return ToolResult(findings, status="qualified" if findings else "ok")


def _execute_tool(tool: str, analyses: tuple[str, ...], ctx: ToolContext,
                  prior: dict[str, ToolResult], started: float, snapshot_dir: Path | None = None):
    start = perf_counter()
    call = {"tool": tool, "analyses": analyses, "start_offset_seconds": round(start - started, 6)}
    try:
        module = import_module(f".tools.{tool}", __package__)
        if snapshot_dir is None:
            result = module.run(ctx, analyses, prior)
        else:
            # Each worker gets its own read-only connection to the same snapshot.
            with closing(open_snapshot(snapshot_dir)) as db:
                result = module.run(ToolContext(db, dict(ctx.client), ctx.as_of), analyses, prior)
        call["status"] = result.status
    except (ImportError, sqlite3.Error, ValueError, KeyError, ArithmeticError, OSError) as exc:
        result = ToolResult(status="unavailable")
        call.update(status="error", error=f"{type(exc).__name__}: {exc}")
    call["seconds"] = round(perf_counter() - start, 6)
    return result, call


def collect(ctx: ToolContext, config: dict, trigger: str,
            parallel_snapshot_dir: Path | None = None) -> tuple[dict, dict[str, ToolResult]]:
    started = perf_counter()
    selected = config["triggers"][trigger]
    decisions = {analysis: role for role, analyses in (
        ("common", COMMON), ("core", selected["core"]), ("support", selected["support"])
    ) for analysis in analyses}
    by_tool = defaultdict(list)
    for analysis in (*selected["core"], *selected["support"]):
        by_tool[BINDINGS[analysis]].append(analysis)
    results = {"profile": _profile(ctx)}
    calls = []
    independent = [tool for tool in ("commercial", "telemetry") if tool in by_tool]
    parallel_group = independent if parallel_snapshot_dir is not None and len(independent) > 1 else []
    if parallel_group:
        profile = results.copy()
        with ThreadPoolExecutor(max_workers=len(parallel_group)) as pool:
            pending = {tool: pool.submit(_execute_tool, tool, tuple(by_tool[tool]), ctx, profile, started,
                                         parallel_snapshot_dir) for tool in parallel_group}
            for tool, future in pending.items():
                results[tool], call = future.result()
                calls.append(call)
    for tool in TOOLS:
        if tool not in by_tool or tool in results:
            continue
        results[tool], call = _execute_tool(tool, tuple(by_tool[tool]), ctx, results, started)
        calls.append(call)
    results["quality"] = _quality(results)
    skipped = {key: ("Trigger does not select this analysis" if key in BINDINGS else
                     "Requires the explicit portfolio command" if key == "portfolio_snapshot" else
                     "Roadmap analysis outside the active runtime")
               for key in config["catalogue"] if key not in decisions}
    plan = {"configuration_version": config["version"], "snapshot": str(AS_OF),
            "selection": decisions, "skipped": skipped, "calls": calls,
            "parallel_group": parallel_group,
            "objective": selected["objective"], "required_in_lead": selected["required_in_lead"]}
    return plan, results
