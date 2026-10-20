# 0011. AI-use signals as a flag only, in a later phase

- **Status:** Accepted
- **Date:** 2026-10-20

## Context

AI use is forbidden in the course, but no reliable detection exists, and fairness to students matters (design §10).

## Decision

In v1, commit history is collected and shown raw. A later phase adds signals (for example a single huge commit or techniques not yet taught), presented as a flag with a reason for human review.

## Consequences

No student is penalized automatically on an unreliable signal; the decision stays human.

## Rejected alternatives

- Automatic disqualification — no reliable detection exists, and it is unfair.
