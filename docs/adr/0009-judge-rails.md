# 0009. Judge rails: deterministic validators, then an LLM judge, evidence required

- **Status:** Accepted
- **Date:** 2026-10-20

## Context

Precision comes before recall: missing a minor note is better than making a wrong one, and invented findings must never reach students (design §10).

## Decision

Every finding carries evidence (file, lines, quote). It first passes deterministic validators (evidence exists and matches, requirement in scope, not an allowed alternative, not a duplicate), then an LLM judge that accepts or rejects it and may only lower its severity. Rejected findings are stored with their reason.

## Consequences

Invented findings are filtered out, and stored rejections let requirements and prompts be calibrated over time. Every finding that passes the validators costs a judge call.

## Rejected alternatives

- Self-critique — not chosen.
- Multi-sample voting — not chosen.
