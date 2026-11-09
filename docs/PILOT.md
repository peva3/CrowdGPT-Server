# External pilot (P4.5) — blocked

Target: three independent operators run orchestrators for 30 days with no
founder operational action; report uptime and certified-round count.

Status: **not started**. This cannot be completed inside the repository
session because it requires three real operators, real machines, and 30 days
of wall-clock operation.

Prerequisites that are complete: onboarding guide, self-check, signed
registry, rotation/revocation, admission policy, reputation/bond, lease and
quorum primitives, signed round records, audit page, restart drills.

Success metrics to record when it starts:

- 3 operators, 3 distinct public endpoints, 3 distinct keys.
- ≥ 99% control-plane uptime per operator.
- ≥ 95% of rounds certified by 2-of-3 witnesses.
- 0 rounds adopted without a certificate.
- Any one operator down for 24 h with no round loss.
