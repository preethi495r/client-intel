# Halden Bet · At risk

Snapshot 2026-11-01 · **Verified With Gaps**

## Retention signals

- For Halden Bet, In-play Odds Feed usage fell by 40.0% year over year. Its total was 3,331 markets priced (thousands) in Aug 2026 through Oct 2026, compared with 5,549 markets priced (thousands) in Aug 2025 through Oct 2025. [^e1]
- Halden Bet's P1 latency ticket for In-play Odds Feed, opened 2026-10-21, was still open at 2026-11-01. The ticket subject reads “In-play latency spike during evening fixtures”. [^e2]
- For Halden Bet, Live Match Data Feed usage fell by 36.3% year over year. Its total was 14,267 API calls (thousands) in Aug 2026 through Oct 2026, compared with 22,394 API calls (thousands) in Aug 2025 through Oct 2025. [^e3]

## Service and commercial context

- Halden Bet's P2 data accuracy ticket for Live Match Data Feed, opened 2026-10-12, was still open at 2026-11-01. The ticket subject reads “Missing corner and card events in live feed”. [^e4]
- CRM lists the renewal opportunity O-0001 at Negotiation. The proposed ARR is €541,500. The recorded expected close is 2027-01-15. Recorded CRM next step is ‘Client asked for pricing on reduced in-play scope’. [^e5]
- In the 2026-10-07 meeting note, Maya Lindgren recorded “Procurement wants pricing for a reduced in-play scope at renewal. Budget pressure from their finance team.” [^e6]

## Retention conversation

- How, if at all, have in-play latency, the supplier trial and budget pressure each influenced your in-play usage and the scope you want at renewal? *(Ready for discussion)* [^e7] [^e8] [^e6]
  - Why: In the 2026-06-12 meeting note, Maya Lindgren recorded “Complaints about in-play price delays during the busy June fixtures. They say traders suspended markets manually several times.” [^e7]
  - Why: In the 2026-10-07 meeting note, Maya Lindgren recorded “Head of trading mentioned they are trialling another in-play odds supplier "for two leagues" since the summer.” [^e8]
  - Why: In the 2026-10-07 meeting note, Maya Lindgren recorded “Procurement wants pricing for a reduced in-play scope at renewal. Budget pressure from their finance team.” [^e6]
- Verify the notice status before presenting reduced-scope renewal options. *(Needs validation)* [^e9] [^e10]
  - Why: Contract K-0002 ends on 2027-01-31. The recorded term is 24 months. The 60-day notice period puts the recorded deadline on 2026-12-02, 31 days after the snapshot. The recorded terms provide for auto-renewal. [^e9]
  - Why: In the 2026-10-07 meeting note, Maya Lindgren recorded “Actions: Maya to prepare renewal options before notice date.” [^e10]

## Data gaps and confidence

- Notice delivery and waivers are absent from the available records. The recorded rule does not establish renewal execution. [^e11]

[^e1]: telemetry.usage.ODDS\_LIVE.yoy — usage\_monthly/U-00038, usage\_monthly/U-00042, usage\_monthly/U-00046, usage\_monthly/U-00086, plus additional source rows in evidence.json. See `evidence.json`.
[^e2]: telemetry.support.open.T-0284 — support\_tickets/T-0284, products/ODDS\_LIVE. See `evidence.json`.
[^e3]: telemetry.usage.LIVE\_DATA.yoy — usage\_monthly/U-00037, usage\_monthly/U-00041, usage\_monthly/U-00045, usage\_monthly/U-00085, plus additional source rows in evidence.json. See `evidence.json`.
[^e4]: telemetry.support.open.T-0279 — support\_tickets/T-0279, products/LIVE\_DATA. See `evidence.json`.
[^e5]: commercial.opportunity.O-0001 — crm\_opportunities/O-0001. See `evidence.json`.
[^e6]: contextual.note.N-003.3.264 — meeting\_notes/N-003. See `evidence.json`.
[^e7]: contextual.note.N-002.2.145 — meeting\_notes/N-002. See `evidence.json`.
[^e8]: contextual.note.N-003.3.151 — meeting\_notes/N-003. See `evidence.json`.
[^e9]: commercial.notice.K-0002 — contracts/K-0002. See `evidence.json`.
[^e10]: contextual.note.N-003.3.434 — meeting\_notes/N-003. See `evidence.json`.
[^e11]: quality.commercial.notice.K-0002.1 — contracts/K-0002. See `evidence.json`.
