# Stretch evaluation

The `milestone-1` tag and submission ZIP preserve the four-trigger take-home baseline and its six live examples. This report covers later work in the repository.

## Milestone 2

- The deterministic regression passed all 100 account/trigger combinations, all five supplied sample requests and seven source-edge fixtures. The checks cover evidence scope, provenance, serialization and core-analysis availability. See [regression results](regression-results.json).
- All 11 distinct sample and edge fixtures also ran live with `gpt-6.2-sol`. One Brenmoor action was initially withheld because `second-division` triggered the numeric-word guard. The verifier now recognises the label only when its cited evidence contains the matching term. The live rerun used both the dated note and the measured streaming comparison to ask a reconciliation question. The local artifacts are under `outputs/regression-live`.
- Six unit tests cover pending coverage, duplicate order invariance, missing observations versus zero, numbers copied from a different metric or period, missing support and cited ordinal labels.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m eval.regression --data-dir $dataDir --report .\outputs\regression.json
```

Use `--live-output .\outputs\regression-live` to make API calls for the sample and edge fixtures in a regression run.

## Milestone 3

`service_review` was added in the trigger configuration. The `milestone-3` tag adds the trigger with a configuration-only diff from `milestone-2`. The CLI discovered the new option and the deterministic matrix passed 125 combinations. A live Greyhaven briefing led with recent support and respondent-only CSAT and asked a follow-up about the recorded score correction. Its [briefing](service_review/briefing.md), [evidence](service_review/evidence.json) and [verification](service_review/verification.json) use the shared run service. The result was `verified`.

```powershell
.\.venv\Scripts\python.exe -m client_intel brief --client C008 --trigger service_review --data-dir $dataDir --output-dir .\outputs
```

## Milestone 4

The original triggers now select deeper signed-term and CRM comparison, usage entitlement and exposure, support resolution, recorded commitment, peer and inquiry tools where relevant. A single bounded inquiry pass uses the previously acquired facts. Peer holdings are same-segment aggregates and run only when the trigger selects them. The regression passed the expanded 125-combination matrix.

- Corvane’s valid monthly usage, allowance and rate yielded a calculated streaming exposure of €42,130. The live briefing directed the rep to check invoices and credits. The same-segment widget comparison was 4 of 5 eligible peers.
- Halden’s proposed renewal ARR was €210,500 below its signed ARR, and its open P1 had been open for 11 elapsed calendar days. The final at-risk rerun recommended escalating the latency ticket and checking actual impact.
- Brenmoor’s attributed streaming increase and measured decline were both cited. The inquiry ended `insufficient_evidence` because populations, months and units could differ. The briefing asked the rep to reconcile them.
- Varrow’s partial exposure subtotal covered two of three months. October signed coverage, the full-period figure and actual charges remained unknown.

The deeper live samples are under `outputs/deeper-verified` and `outputs/deeper-action-fix`. In the first live pass the verifier withheld commitment evidence placed in the gaps section. Per-section identifier enums prevented that error on rerun. A later run withheld an escalation because the imperative was absent from the vocabulary. The at-risk rerun accepted it with valid rationale. The [independent source checks](deeper-results.json) record the figures and the Brenmoor inquiry lineage.

```powershell
.\.venv\Scripts\python.exe -m eval.check_deeper --data-dir $dataDir
```

## Milestone 5

Independent contract and telemetry tools overlap using separate read-only SQLite connections. Dependent context and inquiry work runs after both complete. Tool results and evidence identities matched serial execution in all 125 combinations. A live Corvane run [recorded the overlap](parallel-trace.jsonl), tool and writer times, the model identifier, token usage and final verification outcome. The complete run is under `outputs/traced`.

```powershell
.\.venv\Scripts\python.exe -m eval.regression --data-dir $dataDir --compare-parallel
```

`CLIENT_INTEL_EXECUTION_MODE=serial` disables concurrency for troubleshooting. The trace shows a cost estimate when `CLIENT_INTEL_RATE_VERSION`, `CLIENT_INTEL_INPUT_EUR_PER_MTOK` and `CLIENT_INTEL_OUTPUT_EUR_PER_MTOK` are explicitly supplied. It applies those rates to all input and output tokens before caching discounts. The saved live run has no configured rates and leaves cost unavailable.

## Milestone 6

The explicit `portfolio` command generated a [renewal-priority view](portfolio.md) for all 25 accounts. Independent queries matched every row’s signed ARR, notice deadline and open P1 count. The view prioritises unconfirmed or overlapping signed coverage, then elapsed and upcoming recorded deadlines, with a transparent service and ID tie-breaker. Varrow surfaced first because the snapshot leaves signed coverage unconfirmed.

```powershell
.\.venv\Scripts\python.exe -m client_intel portfolio --data-dir $dataDir --output-dir .\outputs
.\.venv\Scripts\python.exe -m eval.check_portfolio --data-dir $dataDir --run '<portfolio run directory>'
```

The command writes `portfolio.md`, `evidence.json` and `verification.json`. It uses deterministic tools across the explicitly requested account set.

## Milestone 7

The [quality rubric](quality-review.json) scores the six frozen briefings on support, uncertainty, inquiry, focus, rationale and readability. Scores ranged from 10 to 12 out of 12. Every scored reason points to emitted claim IDs. The Corvane meeting briefing passed the deterministic audit while losing points because historical commercial details displaced a current cost concern and some evidence repeated. The review was performed by the coding agent and gives no inter-rater measure. Quality scores leave the deterministic release gate intact.

```powershell
.\.venv\Scripts\python.exe -m eval.check_quality
```

## Milestone 8

The native browser served 25 clients and all five configured triggers. A live Corvane upsell showed the widget discovery and streaming allowance evidence. Browser verification covered rendering, opening the signed-allowance citation, downloading the verification report and recovering the same completed run after refresh and server restart. The API returned all five run artifacts and rejected unknown artifacts and invalid triggers. The job tests cover the pending queue limit and interrupted job state after restart. The complete browser run is under `outputs/web-native/runs`.

## Milestone 9

- A fresh checkout of runtime commit `bf3bb96` installed the package and pinned web dependencies on Windows with Python 3.12.9. The installed CLI reran all six live cases and re-audited the saved artifacts. The installed UI served its assets, and the port collision test rejected a second server. Doctor identified a missing snapshot and separately reported HTTP 401 for a deliberately invalid key.
- Docker Engine 29.9.2 and Compose v5.6.1 built the image from the fresh checkout. The container ran as UID 10001. Inspected mounts showed read-only snapshot and configuration paths and separate writable output and state paths. The Docker CLI generated a live Corvane upsell with `gpt-6.2-sol`. The snapshot SHA-256 matched before and after testing.
- Compose published a healthy UI on `127.0.0.1:8000`. A live Varrow web job produced all five artifacts. The server rejected an unknown artifact and Docker rejected a conflicting port. Compose stop/start preserved the prior verification file byte for byte and marked a simulated unfinished job interrupted. A first, separate host check had arrived after the initial container stopped. A single lifecycle test sequence confirmed the working path.
- The Docker invalid-key test returned HTTP 401. On this Windows host Docker Desktop created an empty bind directory for the missing-source test even though `create_host_path` was false. Doctor failed readiness for the missing snapshot. The documented PowerShell preflight checks the database file before Compose runs.

The working-tree Docker artifacts are under `outputs/docker-verified`. The browser and Docker exercises used local services and the same permitted OpenAI writer used by the CLI.
