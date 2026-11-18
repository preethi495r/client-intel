# Client intelligence agent

Generate a short sales briefing for one synthetic account by name or ID. The CLI writes the briefing, the evidence behind its citations and the verification result. The four core triggers are `renewal`, `meeting_prep`, `upsell` and `at_risk`.

## Setup

Use Python 3.12 or newer. Keep the extracted `starter_pack` directory available and use its `client_intel.db` file. The CLI has no third-party Python runtime dependencies. Run these PowerShell commands from the repository root.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
$dataDir = 'C:\path\to\starter_pack'
```

The writer uses the OpenAI Responses API with `gpt-6.2-sol` and JSON Schema structured output. Copy `.env.example` to `.env` at the repository root and replace the `OPENAI_API_KEY` placeholder with a key that can use `gpt-6.2-sol`.

```powershell
Copy-Item .env.example .env
```

If `.env` already lives elsewhere, point `CLIENT_INTEL_ENV_FILE` to it before running the CLI. The process environment overrides `.env`, and command flags override the corresponding path settings. Supply only synthetic request context. The API receives selected evidence text, the trigger objective and optional rep context. The key is sent in the authentication header and the request sets `store` to `false`.

```powershell
$env:CLIENT_INTEL_ENV_FILE = 'C:\path\to\.env'
```

## Generate a briefing

```powershell
.\.venv\Scripts\python.exe -m client_intel brief --client C007 --trigger renewal --data-dir $dataDir --output-dir .\outputs
.\.venv\Scripts\python.exe -m client_intel brief --client 'Corvane Media' --trigger upsell --data-dir $dataDir --output-dir .\outputs --rep-context 'Seeing their head of digital. What could we grow?'
```

The CLI prints the run directory. Each invocation gets its own directory containing `briefing.md`, `evidence.json` and `verification.json`. `CLIENT_INTEL_DATA_DIR` and `CLIENT_INTEL_OUTPUT_DIR` can supply the corresponding flags. `CLIENT_INTEL_MODEL_TIMEOUT` defaults to 240 seconds. Use `brief --help` for the configured trigger choices. A missing model or an unanswered core question produces an incomplete run and exit code 1. Invalid requests or source setup failures exit with code 2. The model call starts after the tool evidence is saved, so an endpoint failure leaves the analysis available for inspection.

The current checkout also saves `trace.jsonl` and `manifest.json`. Contract and telemetry tools run concurrently by default. Use `CLIENT_INTEL_EXECUTION_MODE=serial` to reproduce the serial path.

`python -m client_intel portfolio --data-dir $dataDir --output-dir .\outputs` runs a separate renewal-priority view across the snapshot. It records the ordering rules, source evidence and coverage per account.

## Local browser

Install the pinned web dependencies and start the loopback server. Open [the local briefing interface](http://127.0.0.1:8000/) while the command is running. `Ctrl+C` stops the server. Completed downloads and job metadata stay in `outputs` and `.client-intel-state` unless overridden with `--output-dir` and `--state-dir`.

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-web.lock
.\.venv\Scripts\python.exe -m client_intel serve --host 127.0.0.1 --port 8000 --data-dir $dataDir
```

Choose an account and trigger, generate a briefing, then open a citation to inspect its metric and source records. Browser refresh restores the last run in that tab. A server restart marks unfinished jobs interrupted and preserves completed artifacts. Use one server process for a state directory. `GET /health` reports process liveness and `GET /ready` checks the source and settings without making an OpenAI call.

## Docker Compose

Start Docker Desktop in Linux container mode. From the repository root, set the host snapshot path and create the two writable mount directories. Compose publishes the UI at [127.0.0.1:8000](http://127.0.0.1:8000/). An existing `.env` outside the checkout can be used by passing `--env-file 'C:\path\to\.env'` after `docker compose` in each command.

```powershell
if (-not (Test-Path -LiteralPath (Join-Path $dataDir 'client_intel.db') -PathType Leaf)) {
    throw 'Set $dataDir to the extracted starter_pack directory containing client_intel.db.'
}
$env:CLIENT_INTEL_HOST_DATA_DIR = $dataDir
New-Item -ItemType Directory -Force .\outputs, .\.client-intel-state | Out-Null
docker compose build
docker compose run --rm app doctor
docker compose run --rm app brief --client C002 --trigger upsell
docker compose up -d --wait
```

Use `docker compose down` to stop the UI. Run `docker compose up -d --wait` to restart it using the same host artifacts and job history. The image runs as UID 10001 with read-only mounts for the starter pack and trigger configuration. Output and state mounts are independent. On Linux, give UID 10001 write access to the two host storage directories. For a port collision, set `CLIENT_INTEL_HOST_PORT` to another available port, then open that port on `127.0.0.1`.

The path check runs before Compose because Docker Desktop created an empty host directory for a missing bind path during verification. The application’s readiness check rejected that directory because the SQLite snapshot was absent.

For native troubleshooting, run `python -m client_intel doctor --data-dir $dataDir` using the project environment. The `--check-model` option makes a small live structured API call so it can distinguish provider or credential errors from local setup failures. The default check performs no API call.

## Design

- **Resolve and plan.** Resolve an exact name or ID, or a unique abbreviated name, and ask for disambiguation when multiple accounts match. Load the trigger objective, analysis roles and section order from configuration. Save selected and skipped analyses. The bundled catalogue retains the wider roadmap.
- **Compute and retrieve.** Use parameterised, account-scoped SQLite reads from the `2026-11-01` snapshot. Contract terms, proposals, tool-calculated quantities and dated note statements have separate evidence records. Usage compares August–October with the same complete months of the prior year, by product unit. Support compares adjacent inclusive 90-day windows and calculates CSAT over actual responses. Equivalent rows count once and raw occurrences stay in provenance.
- **Write.** Send the writer a shortlist of candidate facts and gaps drawn from tool results. The model selects and orders facts and writes suggested questions or actions with cited rationale. Python renders each fact directly from the selected evidence. The evidence file includes source rows, tool inputs, the plan, calculation scope and a mapping from every emitted claim to its evidence key.
- **Verify.** Reject unknown references and unbound quantities in model-written actions. Recheck the rendered Markdown against the selected evidence, including its exact quantity, scope, wording and citations. Withhold rejected entries and publish an incomplete status when a required part of the lead or the next step is lost. Inspect the **Data gaps and confidence** section for qualifications behind a `verified_with_gaps` result.

## Reproduce the evaluation

With `OPENAI_API_KEY` configured, generate all six cases and check the resulting artifacts against the independent spot checks.

```powershell
.\.venv\Scripts\python.exe -m eval.run_live --data-dir $dataDir --output-dir .\outputs\evaluation
.\.venv\Scripts\python.exe -m eval.check_snapshot --data-dir $dataDir --examples .\outputs\evaluation --report .\outputs\evaluation\checks.json
```

The six published live runs passed the rendered number and citation audit with status `verified_with_gaps`. They cover all four triggers, three accounts and both name and ID lookups. A separate clean Windows checkout installed on Python 3.12.9 and ran the documented CLI for all four triggers with `gpt-6.2-sol`. Independent checks matched the raw rows for the sampled calculations and caught both an injected unsupported number and an injected missing citation. The [evaluation report](eval/REPORT.md) links the six briefings, describes the two writer or verifier defects caught in earlier runs, and records the fixes. The [run index](eval/examples/live-runs.json) identifies the corresponding evidence and verification files.

The `milestone-1` tag preserves that baseline. Subsequent work adds a repeatable planning and tool regression across all 25 accounts, live sample and edge checks, and a configuration-only `service_review` trigger. See [stretch evaluation](eval/STRETCH.md) for commands and results.

## Known limits and production next steps

- The verifier guarantees exact rendering and reference integrity for the evidence text under its tested rules. Semantic relevance and implied assumptions in qualitative questions still need review. The snapshot itself can contain incomplete or unconfirmed records.
- Comparisons require comparable coverage. An observed drop and an open incident can motivate a question about timing and impact. Establishing cause requires service exposure and incident evidence. Usage and contract records also leave actual invoices and credits unknown.
- Product fit is a discussion starting point based on current holdings, catalogue relationships and attributed needs. Rights, implementation feasibility, buying authority and budget can require direct confirmation.
- For production, add regression coverage and evaluate held-out accounts, improve source completeness and effective-term reconciliation, expand semantic checks and instrument tool, model and review outcomes. Any broader deployment needs access controls, scoped data retention and auditable source versions.

## How it was built

The candidate brief and implementation plan supplied scope and acceptance criteria. I defined the shared request, tool and evidence contracts, then assigned three Codex agents separate modules for commercial evidence, telemetry, and notes and product fit. I built the CLI, configured planner, structured writer, renderer and verifier, and integrated their modules against the same read-only snapshot. Each agent checked a different source edge case and reported its files, checks and limits. I independently reconciled representative figures to raw rows and exercised the same verifier on good and fault-injected artifacts.
