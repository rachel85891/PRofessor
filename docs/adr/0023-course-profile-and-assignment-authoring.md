# 0023. Course profile, review rules as data, and LLM-assisted assignment authoring

- **Status:** Accepted
- **Date:** 2026-11-04

## Context

A new assignment arrives every week or two. Requirements are already data (ADR 0002), but writing them by hand from `assignments/README.md` is slow, and course policy is still in code: the fixer prompt holds all of its review rules, including hw01-only ones (no `File.Exists` checks, PUT returns `Ok()`, no GET all or `Models` folder), and the prompts and `FixService` hold the audience ("second-year"), the stack, the wording rules, the PR's opening, sections and title. A second assignment would be fixed under hw01's rules, and every new course decision would need a code change. The developer wants no hard-coding, a basic schema plus per-assignment data, and an LLM that drafts each assignment's rules from its task description.

## Decision

Code holds only mechanism; policy is schema-validated data in three levels:

- `course/`: the course profile (`course.yaml`: audience, level, stack, wording, PR format and title rule) and the base `review_rules.yaml`, separate from `assignments/`. `_general` keeps only the general requirements.
- Each assignment folder: `task.md`, `intake.yaml`, `requirements.yaml` and its own `review_rules.yaml`. Rules regress like requirements (course rules plus assignments 1..N), with `until` and `replaces`, and each rule names the roles that see it.
- Prompts are a fixed frame (role, output contract, student files as data) with slots filled from that data. The authoring LLM writes content only. It may suggest a frame change, which is applied only with the developer's approval, by a developer's commit.

A new assignment goes through an authoring graph: the developer fills the intake stages, the `author` role drafts requirements and rules with the origin of each item (task text, intake, or inferred), the system validates them (schema, a dry run on a reference solution, a preview on the last round's repos), and the developer approves them. A round refuses a draft. The developer approves; inferred items are flagged so they can be confirmed with the lecturer first. The UI comes later, on top of the same service.

## Consequences

- A new assignment, course or semester is data only; a new stack is a plugin (runner, check library, edit kinds).
- One schema per file serves validation, the LLM's structured output and a future form editor.
- Stage 3f moves the existing policy out of code with a golden test that hw01's prompts and PR text do not change; stage 16 builds the authoring flow. Both come before hw02, and before 8b and 9, which read their rules from data.
- The authoring LLM sees the task text and the reference solution, never student code.
- More files per assignment, and inheritance (`until`, `replaces`) must be explained in `assignments/README.md`.

## Rejected alternatives

- Keeping course rules in `_general` — it is an assignment (order 0); mixing course policy into it blurs what is checked with how it is reviewed.
- Letting the LLM write whole prompts per assignment — the frame carries the output contract and the prompt-injection boundary; regenerating it would make quality uneven and could break structured output.
- Hand-written YAML only, with a form editor later (ADR 0014 alone) — still slow and error-prone, and gives no help turning a task description into requirements.
- Policy in `config.yaml` — configuration is for operational values; teaching decisions need their own schema, per-assignment inheritance and Git history next to the assignments.
