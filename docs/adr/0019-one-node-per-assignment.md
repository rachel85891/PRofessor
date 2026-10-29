# 0019. One node per assignment, generated from the assignment data

- **Status:** Accepted
- **Date:** 2026-10-29

## Context

ADR 0003 fans each repo out to its assignments with Send. Send tasks all appear under one node, so in Studio (ADR 0017) the developer cannot see which assignment a repo is being checked against, or which one failed. ADR 0002 requires that adding an assignment is a data change only (developer decision, 2026-10-29).

## Decision

The repo graph gets one node per assignment, named `assignment_<id>` (`assignment__general`, `assignment_hw01`, ...), generated from the assignment catalog when the graph is built. Each node wraps that assignment's own subgraph (for now one node, `deterministic_checks`; later `llm_review` and `judge`). All of them run in parallel after `build_and_run`. An assignment that is not in the round's selection (its order is above the round's target) returns no result. This refines ADR 0003: repos are still sent with Send; assignments are nodes.

## Consequences

- Adding `assignments/hw02/` adds an `assignment_hw02` node with no code change; the graph is rebuilt when the program or the dev server starts.
- Studio shows each assignment as its own node and subgraph, with its own result.
- The catalog is read when the graph is built. A round whose selected assignments no longer match the built graph (an assignment was added or edited since) stops with a message to restart, so it never checks against stale requirements.
- Every round runs every assignment node; the ones outside the selection finish at once with no result.

## Rejected alternatives

- Send per assignment (ADR 0003 as written) — works, but hides the assignments behind one node.
- Hand-written nodes per assignment — breaks ADR 0002 (an assignment would need a code change).
