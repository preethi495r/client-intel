# Milestone 1 evaluation

All six required cases ran through the shared briefing service on Windows with Python 3.12.9 and the OpenAI Responses API. The request and reported model were both `gpt-6.2-sol`. The snapshot hash matched before and after evaluation. All final briefings returned `verified_with_gaps`, with zero draft rejections and zero rendered audit failures in the saved runs. The final set used 13,496 tokens and each run took 3.27–3.53 seconds on this machine.

| Account and trigger | Manual review of the saved briefing | Time | Tokens |
| --- | --- | ---: | ---: |
| [Halden Bet · At risk](examples/C001_at_risk_cb097f44b9a3/briefing.md) | Leads with the in-play decline, open P1 and live-data decline. Asks about latency, the reported supplier trial and budget as possible contributors. | 3.41 s | 2,487 |
| [Halden Bet · Renewal](examples/C001_renewal_f2bc1d6cc9eb/briefing.md) | Leads with the recorded notice date and signed ARR, then distinguishes CRM negotiation. Asks what needs pricing and what trading needs on latency. | 3.27 s | 2,455 |
| [Corvane Media · Upsell](examples/C002_upsell_ff5e9171b863/briefing.md) | Leads with the Widgets catalogue fit, absence from signed holdings and the client’s companion-app need. Suggests a demo and surfaces the dated analytics budget objection. The evidence file also contains the earlier lost CRM offer. | 3.38 s | 2,366 |
| [Corvane Media · Meeting prep](examples/C002_meeting_prep_1dd1aba06b79/briefing.md) | Gives an agenda for widgets and streaming costs, cites consumption and identifies the missing billing evidence. | 3.38 s | 2,158 |
| [Varrow Racing Authority · Renewal](examples/C007_renewal_33f3e0d50809/briefing.md) | Leads with expired coverage, the historical notice deadline and the unsigned proposal. Asks who can confirm execution and authorization for interim service. | 3.53 s | 1,996 |
| [Varrow Racing Authority · Meeting prep](examples/C007_meeting_prep_5c3416490993/briefing.md) | Brings the meeting account, pending draft and CRM legal review into separate questions about interim service and signature. | 3.27 s | 2,034 |

Each case folder contains its evidence and verification JSON. The [run index](examples/live-runs.json) maps requests to folders. Evidence records the selected and skipped analyses, source coverage, tool inputs and outputs, citations and all emitted actions. Verification records writer timing, usage and rejection details.

A separate clean checkout of runtime commit `96bcd59` created a new virtual environment, installed the package and ran the documented CLI for all four triggers. Each command wrote all three artifacts and returned `verified_with_gaps`. The independent checker also passed against the six versioned examples in that checkout.

## Source and verifier checks

- Recomputed Halden’s notice date from the recorded term and notice days, reconciled signed ARR, independently summed its August–October use for both years and checked its respondent-only CSAT. The 40.0% in-play drop, 36.3% live-data drop and 1.25/5 from four respondents matched.
- Verified Quillon’s short history, Marrowfield’s missing March observation, Tidewell’s repeated contract and equivalent usage records, Varrow’s pending term and historical notice date, and the repeated ticket on C010. Verified exact and abbreviated names, case-insensitive IDs and read-only connection enforcement.
- Inserted `987654321` into a rendered Varrow fact and separately removed its citation. The rendered auditor detected both. Injected an unsupported number and an empty citation array in a draft. Both actions were withheld. See [check output](checks.json).

## Failures and corrections

- The initial endpoint-unavailable smoke test saved evidence and produced an incomplete verification result. A separate structured API smoke test and all published examples used OpenAI live inference.
- The first Corvane meeting draft supplied too many lead facts. The verifier capped the lead and recorded the rejected selection. Adding array limits to the model schema and sharpening the agenda objective resolved the issue in the rerun.
- A later Corvane upsell draft proposed a widget demo and used `second-screen` as a product term. The verifier incorrectly interpreted `second` as a quantity. The matcher now permits the phrase when its cited evidence contains it and recognises `Demo` as an imperative. The replay passed, genuine injected numbers were still caught, and the live upsell rerun retained the widget demonstration.
- Qualitative relevance and assumptions in action wording were reviewed manually. Some rationale repeats lead evidence so it can be read beside the action. The checks cover emitted text and cited records, with semantic and source-completeness limits described in the README.
