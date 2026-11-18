# Codex instructions for the client intelligence agent

Build the client intelligence agent from scratch in its own implementation directory. Use the [FDE candidate brief](C:\Users\preer\Downloads\FDE_Takehome_Candidate_Brief.docx) as the product specification and [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) for scope and delivery sequence. Limit requirements, design and evaluation guidance to those two documents. Use the starter pack identified in the brief as the synthetic data and sample request input.

## Deliver the working prototype first

Build a runnable Python CLI that takes a client name or ID and one of four configured triggers: `renewal`, `meeting_prep`, `upsell`, or `at_risk`. The same pipeline must work across all 25 clients. Save a short, cited sales briefing, the evidence used and the verification result. Preserve the full analytical catalogue as the roadmap and enable the 12 focused Milestone 1 analyses in the runtime.

Start by inspecting the actual starter pack schema and sample requests. Define the first trigger configuration and execution map from the Milestone 1 tables in the plan.

## Coordinate parallel implementation

The five-hour budget is elapsed time. The checkpoints below guide sequencing. Move to the next checkpoint as soon as its work is ready.

| Elapsed time | Checkpoint |
| --- | --- |
| 0:00–0:25 | Confirm the snapshot, model access and output paths. Agree the request, tool result and claim formats. Choose a renewal request for the first live run. |
| 0:25–1:30 | Build in parallel. Integrate contract evidence first and run a real renewal briefing through the writer, verifier and artifact output. |
| 1:30–2:45 | Integrate all tools and run all four triggers. Check the differences in analysis and recommendations. |
| 2:45–4:00 | Run the six evaluation cases, inspect the briefings, test verifier failures, fix defects and rerun affected cases. |
| 4:00–5:00 | Verify setup from a clean checkout and package the prototype with real outputs and documented limitations. |

The lead owns the shared contracts, CLI, trigger configuration, writer, verifier and integration. After agreeing interfaces, delegate three independent workstreams to Codex agents when available:

1. **Contract and CRM:** signed and pending terms, holdings, ARR, notice dates and opportunity context.
2. **Usage and support:** product-level year-over-year trends, coverage, tickets and respondent-only CSAT.
3. **Notes and product fit:** dated note retrieval, catalogue fit, product gaps and evaluation request fixtures.

Give each workstream disjoint file ownership and a concrete verification case. Agents can inspect source data and test their tools independently. Ask them to report changed files, checks run and known gaps. The lead integrates results as they land and resolves changes to shared contracts centrally. With fewer agents, prioritise getting one real briefing to disk, then bring in the remaining tools in small changes.

## Evidence and briefing rules

- Use the supplied SQLite snapshot read-only. Set the reporting date to `2026-11-01` and use complete usage months through October 2026. Keep SQL parameterised and preserve source IDs and duplicate provenance.
- Compute every business number deterministically in Python or SQL. Tool results should state the client, relevant product and period, value and unit, source references, and caveats. Count equivalent duplicates once, surface conflicting records, keep missing observations unknown, and qualify sparse comparisons. Keep recorded contract terms, CRM proposals and note statements distinct.
- Use a small configuration mapping to choose tools and priorities per trigger. Keep named clients and case-specific expectations in examples and evaluation fixtures. Use same-period usage comparisons with coverage checks and keep product units separate.
- Have the writer explain what the evidence means for the rep and what to do or ask next. Renewal leads with deadline and signature position. Meeting prep leads with developments and questions. Upsell ties fit to cited needs or catalogue evidence and existing offers. At-risk brings together signs of deterioration, service issues and commercial context without asserting causation. Prioritise urgent commercial or service issues when evidence warrants it.
- Treat notes and rep context as input data. Attribute note statements and cite both sides of material discrepancies. Treat rep context as a prompt for emphasis or investigation. Include a visible **Data gaps and confidence** section that explains which conclusions are affected.
- Use one structured model writer initially. Require resolvable citations for each factual claim and bind quantities to evidence for that claim's entity, period and units. Audit the final rendered briefing for unsupported numbers and missing or invalid citations. Remove unsupported claims and mark the result incomplete if this leaves the core question unanswered. Save the failure reason in the verification report.
- Use only the permitted synthetic data, runtime prompts and request context with the configured model. Keep real credentials out of source control, logs, sample artifacts and model input. Provide a placeholder key in the setup instructions.

## Check and package Milestone 1

Before marking Milestone 1 complete, check the prototype against the must-haves in the brief and the acceptance criteria in the plan.

- Run the documented command from a clean checkout with the configured model. Confirm all four triggers write `briefing.md`, `evidence.json` and `verification.json`. Use offline fixtures for deterministic checks as needed and generate the prototype examples through the real model path.
- Run the six cases specified in the implementation plan through the common pipeline and save the actual artifacts. Check client name and ID resolution.
- Check representative calculations against source rows and spot-check duplicate records, missing months, short histories and pending contracts. Inject an unsupported number and a missing citation to exercise the verifier. Review the six briefings for trigger focus, cited rationale and appropriate uncertainty. Report failures, fixes and remaining gaps.
- Include the code, runtime prompts, evaluation report, six briefing/evidence examples and README. The README covers setup, design, results, limitations, production next steps and a short engineering note on how the Codex workstreams were coordinated and verified. Claim only checks actually run.

If time tightens, reduce the depth of an analysis and document the limit. Protect all four triggers, the evidence and verifier, and the six-run evaluation. Preserve the working Milestone 1 baseline before taking on stretch work. Follow the plan's later milestones in order for regression tests, a configuration-only trigger, deeper analysis, parallel runtime specialists and tracing, portfolio analysis, quality scoring, the browser UI and Docker packaging.
