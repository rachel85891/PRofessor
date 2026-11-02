# 0021. The fixer: one verified review commit per repo, in a LangGraph subgraph

- **Status:** Accepted
- **Date:** 2026-11-02

## Context

Stage 14 builds the fixer, which creates the proposals that the approval gate (ADR 0018) shows and publishing (stage 13a) sends. The target format is fixed: the 32 review PRs the planner prepared by hand and the developer and the lecturer approved on 2026-10-31 ([fix-pr-workflow.md](../fix-pr-workflow.md) §2.3–2.4, §3, §4). Those PRs differ from design §11 as first written in two ways: each is one commit, not one commit per patch, and their naming notes and questions come from reading the whole repo, not only from failed findings. ADR 0010's rule stays: targeted search-and-replace edits, rebuilt and rechecked before anything is proposed.

The fixer is the first stage that sends a student's code to an LLM. The lecturer's approval is pending, and student code is untrusted input.

## Decision

**What the fixer produces**

- One review commit per repo, `<short name> review: fixes and questions` (for example `HW1 review: ...`), by the configured publishing author, on `hw-review/<assignment>-<YYYY-MM-DD>` from the checked commit, in a separate working copy (`workspace/runs/<run_id>/fixes/<repo_id>`); the checked working copy is never modified.
- The fixer reviews the whole repo against the review rules. The repo's findings are hints, and the verification target.
- The model only returns a structured `FixPlan` of edits (`fix`, `note`, `final_newline`). Deterministic code decides what is applied: an edit that breaks a guardrail (exact single match, `.cs` only, a `//fix:` line above every change, at most a few changed code lines, notes add only `//` lines, no `File.Exists`, no new `BadRequest(`, no Hebrew, no model property change, BOM and line endings kept, at most N edits) is rejected with a reason.
- A self-review by the `judge` role removes edits that break the rules in spirit (the core of the assignment, not second-year level, a claimed bug that isn't there).
- Verification rebuilds, reruns and rechecks the fix working copy with the round's assignments: the build must not get worse, no requirement that passed may fail, and every requirement a `fix` claims must pass. Otherwise the fixer plans again with the reasons as feedback, up to `fixer.max_attempts`; after the last attempt each failing `fix` becomes its fallback note and verification runs once more. If it still fails, there is no proposal and the repo report says why.
- The PR text: the `writer` role returns the opening and the items of each section as structured data; the body is rendered from a template in the approved PRs' shape (`Hi! ` and bold section names; "Files" only for leftover-file notes), and the title is decided in code from what the last recheck cleared (`fixes and questions` when the verified branch clears a major or critical requirement that failed before, otherwise `small fixes and questions`).

**Privacy**

- `fixer.enabled` and `llm.student_code_approved` both default to false; enabling the fixer without the approval stops startup with a `ConfigurationError`. OpenRouter's `data_collection: deny` stays enforced (ADR 0020).
- Student files go into the prompt inside data delimiters (`<student_file path="...">`), with the instruction that nothing inside them is an instruction; a closing delimiter inside a file is neutralized.
- Logs carry tokens, durations and counts, never code, prompts or replies.

**LangGraph**

- **A subgraph with its own input and output schemas** (`fix_repo`, in the repo graph after `write_report`): it reads the repo's fetch, execution and assignment results, and returns only the outcome, the LLM usage and round errors. Studio shows each step and attempt.
- **Conditional edges with a bounded revise loop**: `verify` goes back to `plan_edits` while edits were rejected or verification failed and attempts remain; then to `fall_back`, `write_pr_text` and `propose`. The bound is `fixer.max_attempts`, kept in state.
- **`RetryPolicy`** on the three LLM nodes, for replies that cannot be parsed (`LLMOutputError`); the adapter already retries rate limits, timeouts and server errors. The node guard (`graph/guard.py`, still the only `except Exception`) re-raises a retryable error only while attempts remain, reading the attempt number from `get_runtime().execution_info.node_attempt`; on the last attempt it records the failure as usual, so a failure is always recorded once.
- **`CachePolicy` with `SqliteCache`** (`workspace/fixer_cache.sqlite`) on the LLM nodes, keyed on what decides the prompt (the checked commit, the assignments' versions, the findings, the attempt and its feedback, the templates and the role models), so a repeated round on the same commit does not pay again. In 1.2.18 a subgraph does not inherit its parent's cache, so the cache is compiled into the fixer subgraph. The cached nodes change no file (the self-review only returns violations; removing them is the next, uncached node), so replaying a reply is safe. Each LLM usage record carries the run that paid for it, so a cached reply is not counted as spent in another run.
- **Custom stream events** (`get_stream_writer`) report each step and attempt to Studio.
- **Durable checkpointing**: the subgraph runs inside the round's thread, so a resumed round does not repeat finished repos; the cache covers a new round on the same commits.
- **`TimeoutPolicy` is not used**: in 1.2.18 it needs async nodes (sync nodes are refused at compile time), and every external call (LLM, Docker, git) already has its own timeout.

## Consequences

- A real round ends at the approval gate with one proposal per repo that needs one, in the approved format, verified before the reviewer sees it.
- Each attempt costs LLM calls and a rebuild; `fixer.max_attempts` (default 2) bounds both.
- Proposals cannot be edited at the gate yet, and the reviewer's decline notes are not remembered (later stages).
- The summary and `round.md` show the round's token totals and the number of proposals.

## Rejected alternatives

- One commit per patch (design §11 as first written): not the approved format.
- Fixing only failed findings: misses the naming notes and questions the approved PRs have.
- Letting the model write whole files or run tools: unreadable diffs and untrusted actions; edits are decided by deterministic code.
- Retrying inside the node instead of `RetryPolicy`: the developer wants retries visible in Studio, and the guard can still record the final failure.
