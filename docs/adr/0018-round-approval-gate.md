# 0018. One approval gate per round, inside the graph

- **Status:** Accepted
- **Date:** 2026-10-29

## Context

ADR 0005 ended the graph at saved proposals and left publishing to a separate service started from the UI. With Studio as the first UI (ADR 0017) there is no review screen to start that service from, and the developer wants to approve a round's results where the round runs (developer decision, 2026-10-29). Approval can still take days, and the reviewer approves repos a few at a time.

## Decision

The round graph gets one approval gate: an interrupt after every repo is checked. The reviewer approves a subset of the repos; those are published, and the gate interrupts again for the rest, until every repo is approved or declined. Publishing itself stays deterministic (no LLM) and lives in the publishing service; the gate's node calls it. The checkpointer keeps the round's state, so the wait can last days. Before each publish, the student's head commit is checked again. This supersedes ADR 0005. The gate and publishing are built in stage 13a.

## Consequences

- Approval happens in the round, one decision per repo, and the round's state shows what was published and what is still waiting.
- Publishing happens only through this gate, in the publishing service; no other node pushes or opens a PR.
- Risk: resuming a round after a code change runs new code on old state. Mitigation: the head check before each publish, and a round can simply be started again.
- Editing a proposal before it is published needs a way to change the state at the gate; that is designed with stage 13a.

## Rejected alternatives

- Keep ADR 0005 (publishing outside the graph) — needs a UI to start publishing, which is deferred.
- One interrupt per repo — a round of about 40 repos would need 40 separate resumes, and approvals would be scattered across threads.
