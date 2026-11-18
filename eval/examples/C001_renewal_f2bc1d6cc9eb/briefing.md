# Halden Bet · Renewal

Snapshot 2026-11-01 · **Verified With Gaps**

## Deadline and signature position

- Contract K-0002 ends on 2027-01-31. The recorded term is 24 months. The 60-day notice period puts the recorded deadline on 2026-12-02, 31 days after the snapshot. The recorded terms provide for auto-renewal. [^e1]
- Signed contract K-0002 is in force from 2025-02-01 through 2027-01-31 and records €752,000 in ARR. [^e2]
- CRM lists the renewal opportunity O-0001 at Negotiation. The proposed ARR is €541,500. The recorded expected close is 2027-01-15. Recorded CRM next step is ‘Client asked for pricing on reduced in-play scope’. [^e3]

## What to address before renewal

- Halden Bet's P1 latency ticket for In-play Odds Feed, opened 2026-10-21, was still open at 2026-11-01. The ticket subject reads “In-play latency spike during evening fixtures”. [^e4]
- For Halden Bet, In-play Odds Feed usage fell by 40.0% year over year. Its total was 3,331 markets priced (thousands) in Aug 2026 through Oct 2026, compared with 5,549 markets priced (thousands) in Aug 2025 through Oct 2025. [^e5]
- In the 2026-10-07 meeting note, Maya Lindgren recorded “Head of trading mentioned they are trialling another in-play odds supplier "for two leagues" since the summer.” [^e6]

## Next renewal conversation

- Confirm the status of any notice or waiver and whether the proposed renewal has been signed. *(Needs validation)* [^e7] [^e3]
  - Why: Notice delivery and waivers are absent from the available records. The recorded rule does not establish renewal execution. [^e7]
  - Why: CRM lists the renewal opportunity O-0001 at Negotiation. The proposed ARR is €541,500. The recorded expected close is 2027-01-15. Recorded CRM next step is ‘Client asked for pricing on reduced in-play scope’. [^e3]
- Confirm what reduced in-play scope procurement wants priced and what latency fixes trading needs before agreeing to it. *(Ready for discussion)* [^e8] [^e4]
  - Why: In the 2026-10-07 meeting note, Maya Lindgren recorded “Procurement wants pricing for a reduced in-play scope at renewal. Budget pressure from their finance team.” [^e8]
  - Why: Halden Bet's P1 latency ticket for In-play Odds Feed, opened 2026-10-21, was still open at 2026-11-01. The ticket subject reads “In-play latency spike during evening fixtures”. [^e4]

## Data gaps and confidence

- Notice delivery and waivers are absent from the available records. The recorded rule does not establish renewal execution. [^e7]

[^e1]: commercial.notice.K-0002 — contracts/K-0002. See `evidence.json`.
[^e2]: commercial.contract.K-0002 — contracts/K-0002, contract\_lines/L-00005, contract\_lines/L-00006, contract\_lines/L-00007, plus additional source rows in evidence.json. See `evidence.json`.
[^e3]: commercial.opportunity.O-0001 — crm\_opportunities/O-0001. See `evidence.json`.
[^e4]: telemetry.support.open.T-0284 — support\_tickets/T-0284, products/ODDS\_LIVE. See `evidence.json`.
[^e5]: telemetry.usage.ODDS\_LIVE.yoy — usage\_monthly/U-00038, usage\_monthly/U-00042, usage\_monthly/U-00046, usage\_monthly/U-00086, plus additional source rows in evidence.json. See `evidence.json`.
[^e6]: contextual.note.N-003.3.151 — meeting\_notes/N-003. See `evidence.json`.
[^e7]: quality.commercial.notice.K-0002.1 — contracts/K-0002. See `evidence.json`.
[^e8]: contextual.note.N-003.3.264 — meeting\_notes/N-003. See `evidence.json`.
