# 0005. Analysis separated from publishing; publish only after approval

- **Status:** Accepted
- **Date:** 2026-10-20

## Context

Reviewer approval can take days, publishing a PR is deterministic, and the reviewer may want to edit a proposal before it goes out (design §11).

## Decision

The graph ends with a saved proposal in status `pending_review`. A separate deterministic publishing service (no LLM) pushes and opens a PR only for approved proposals.

## Consequences

The graph never pushes and never opens a PR. Proposals can be edited before publishing, and publishing again updates the same PR.

## Rejected alternatives

- An interrupt inside the graph — approval can take days, and editing before publishing would be harder.
