# HW Checker: Design and Implementation Plan

Oct 20, 2026 · @no name

## 1. Overview, goals and non-goals

The system is a LangGraph-based agent that, twice a week, checks the homework of about 40 second-year students in a .NET 10 Web API course, reports what is present and what is missing, and proposes fixes as a PR to each student's repo after manual approval. A first version must be in use at the start of next week; the system will keep evolving while in use and will later be handed over to another developer.

**Course context.** Students build a single project incrementally over the semester; each assignment adds a few dozen lines of code. Most work in fixed pairs, but each has their own repo. AI use is forbidden, and the expected code level is relatively basic. At some point the projects use SQL Server, first through a direct connection (ADO.NET) and later through EF Core.

**Goals**

- Check each repo against the general requirements and the requirements of every assignment up to the current one (regression).
- Build and actually run the project and verify HTTP behavior, not only read the code.
- Find only real, substantive problems: precision before recall.
- Produce an English report for the reviewer, the round's counts (a grades file once there is a formula), and plain-English feedback for the student.
- Propose minimal, student-level fixes, and publish them as a PR only after manual approval.
- Collect AI-use signals and present them as a flag for human review.
- Be easy to extend (a new assignment = a data file) and easy to hand over to another developer.

**Non-goals (for now)**

- Automatic disqualification on suspected AI use.
- A final grade without human oversight.
- Multiple users, permissions, and server-side automatic scheduling.
- Support for languages or frameworks other than .NET 10 Web API.

## 2. Requirements

The table lists the functional requirements and their priority for the first version; "Should" = included if time allows, otherwise done manually.

| ID | Requirement | Priority |
| --- | --- | --- |
| F1 | Ingest the Excel file (name, partner's name, GitHub link) and merge duplicate rows into pairs | Must |
| F2 | Clone or update each repo; read the commit hash and commit history | Must |
| F3 | Build and run the project in a container against a shared SQL Server | Must |
| F4 | Check general requirements and the requirements of all assignments 1..N | Must |
| F5 | HTTP checks against expected status codes, including allowed alternatives | Must |
| F6 | Filter findings through judge rails (deterministic + LLM) | Must |
| F7 | English report per repo, the round's counts (grades once there is a formula), plain-English feedback | Must |
| F8 | UI to start a round, track progress, review and approve | Must |
| F9 | Publish a PR to the student's repo only after approval | Should |
| F10 | Targeted fixes verified by a rebuild | Should |
| F11 | Use the partner's findings as a hint for a re-check | Must |
| F12 | AI-use signals (commit history, techniques not yet taught) | Next phase |

**Non-functional requirements**

- **Maintainability:** layered architecture, SOLID, single responsibility per component, clean code, documented decisions (ADRs).
- **Extensibility:** a new assignment is added as data files, without changing core code, and most of those files are drafted by an LLM from the task description and approved by the developer (§8, ADR 0023).
- **No hard-coded policy:** code holds only mechanism (running, checking, applying edits). Every teaching or course decision (audience and level, review rules, what may be fixed or mentioned, the PR's wording, sections and title) lives in schema-validated data files, and every operational value in configuration (ADR 0023).
- **Scalability:** bounded parallelism (3–4 repos at a time on a local machine); move to a server and a central database without a rewrite; a new course is a new course profile and a new stack is a plugin, both by addition.
- **Reliability:** a failure in one repo does not stop the round; a stopped round resumes from where it left off.
- **Precision:** every finding carries evidence (file, line, quote) and a reference to a requirement.
- **Safety:** student code runs only in an isolated container; secrets live only in `.env`.
- **Cost:** a cheap model for simple checks, a strong model for the judge and fixes; model choice in configuration.
- **Environment:** Windows, Docker Desktop, .NET 10 SDK, Python 3.12.

## 3. Layered architecture and dependency rules

The system uses a layered Ports & Adapters (hexagonal) architecture, so every external component (Excel, Git, GitHub, Docker, LLM, SQLite, UI) can be replaced without touching the logic. Dependencies point inward only, toward the domain.

&#91;embedded content: layered architecture · dependencies point inward\]

The arrows show dependency direction: each layer on the left depends only on the layer below it, the adapters implement the ports, and only `bootstrap.py` knows both sides and wires them together.

| Layer | Responsibility | May depend on |
| --- | --- | --- |
| domain | Pure models and business rules (repo, pair, requirement, finding) | stdlib and Pydantic only |
| ports | Interfaces (Protocol/ABC) for every external capability | domain |
| services | The logic: ingestion, checking, judging, reporting, publishing | domain, ports |
| checks, judge | Checks and judge; registered in a registry | domain, ports |
| graph | LangGraph: state, thin nodes, builders | services, domain |
| adapters | Concrete implementations of the ports | ports, domain, external libraries |
| bootstrap | Composition root: creates adapters and injects them | Everything |
| ui | Deferred (ADR 0017); LangGraph Studio is the v1 UI. When built: Streamlit, calling services only, through bootstrap | services, domain |

**Mandatory rules**

- A graph node is thin: it receives state, calls one service, and returns a state update. It holds no business logic.
- One service = one responsibility (SRP). A service never creates adapters itself; they are injected (DIP).
- Only `bootstrap.py` imports concrete adapters.
- The UI knows neither LangGraph nor the adapters, so it can later be replaced by FastAPI + React.
- Adding a check, an assignment or an LLM provider is done by addition (registry/configuration), not by changing existing code (OCP).
- Code is mechanism, data is policy: no course or assignment decision is written in code or in a prompt's frame (§8, ADR 0023).
- Every LLM output passes through a Pydantic model; no free text crosses layers.

## 4. Project structure

The folder structure mirrors the layers one-to-one, and assignment requirements live outside the code as data.

```
hw-checker/
├── CLAUDE.md                    # working rules for the implementing agent
├── README.md
├── pyproject.toml
├── .env.example
├── docs/
│   ├── design.md                # export of this document
│   └── adr/                     # 0001-ports-and-adapters.md ...
├── course/                      # the course profile: course-wide policy as data (ADR 0023)
│   ├── course.yaml              # audience, level, stack, wording, PR format, title rule
│   ├── review_rules.yaml        # base rules for the reviewer, fixer and judge
│   └── prompts/                 # optional: frame overrides, changed only with the developer's approval
├── assignments/                 # requirements as data, not code
│   ├── _general/requirements.yaml
│   └── hw01/
│       ├── task.md              # the task text as given to students, plus lecturer notes
│       ├── intake.yaml          # the developer's answers to the authoring stages
│       ├── requirements.yaml    # what is checked (LLM draft → approved)
│       ├── review_rules.yaml    # rules for this assignment only (LLM draft → approved)
│       ├── examples/            # optional: approved fix examples for the prompts
│       ├── checks.py            # optional: custom checks (plugin)
│       └── reference/           # optional: reference solution, used by the authoring dry run
├── docker/
│   ├── dotnet-runner.Dockerfile
│   └── sqlserver.compose.yml
├── src/hw_checker/
│   ├── domain/
│   ├── ports/
│   ├── adapters/
│   │   ├── roster_excel/
│   │   ├── git_local/
│   │   ├── github_api/
│   │   ├── docker_runner/
│   │   ├── llm/
│   │   └── storage_sqlite/
│   ├── services/
│   ├── checks/
│   │   ├── deterministic/
│   │   ├── llm_review/
│   │   └── registry.py
│   ├── judge/
│   ├── graph/
│   │   ├── state.py
│   │   ├── nodes/
│   │   └── builders.py
│   ├── reporting/templates/
│   ├── config/
│   ├── bootstrap.py
│   └── ui/
├── workspace/                   # clones and artifacts (in .gitignore)
└── tests/
    ├── unit/
    ├── integration/
    └── fixtures/                # sample repo, sample Excel
```

**Tooling:** Python 3.12, uv, ruff, mypy (strict), pytest, pre-commit, Pydantic v2, pydantic-settings, SQLAlchemy 2, LangGraph, Docker SDK for Python, PyGithub, openpyxl, Jinja2, Streamlit. Exact versions are chosen and pinned in stage 0.

## 5. Domain model

The unit of work is a repo, not a student; a pair is a group of two separate repos whose findings serve as hints for each other. All models are Pydantic v2, immutable where possible, with no external dependencies.

| Model | Key fields | Notes |
| --- | --- | --- |
| Student | name, github\_url | Identified by normalized URL |
| Pair | pair\_id, members (1–2 Students) | A student working alone = a pair of one |
| RepoTarget | repo\_id, owner, name, url, student, pair\_id |  |
| RosterWarning | kind, message, row | Missing partner, invalid URL, asymmetric duplicate |
| CourseProfile | audience, level, stack, student\_wording, pr\_format (opening, sections, title rule), rules | Loaded from `course/`; one per course (ADR 0023) |
| ReviewRule | id, applies\_to (roles), kind, text, example, until, replaces, origin, status | kind: fix\_allowed / note\_only / never\_do / never\_mention / style |
| AssignmentIntake | basics, taught / not yet taught, lecturer decisions | The developer's answers to the authoring stages |
| Origin | source (task\_text / intake / inferred), quote | On every drafted requirement and rule; `inferred` is flagged for review |
| FrameSuggestion | template, change, reason | An LLM suggestion for a prompt frame; never applied without the developer |
| Assignment | id (hw01), order, title, requirements, taught\_techniques, review\_rules, status (draft / approved) | Loaded from YAML; a round refuses a draft |
| Requirement | id, title, description, severity, kind, allowed\_alternatives, http\_probes | kind: structure / code / http / build |
| HttpProbe | method, path, body, expected\_status, allowed\_statuses, notes |  |
| Severity | critical / major / minor | minor does not lower the grade in v1 |
| Evidence | file\_path, line\_start, line\_end, quote | Required for every code finding |
| Finding | id, requirement\_id, assignment\_id, severity, message\_he, message\_en, evidence, source (deterministic/llm), confidence |  |
| JudgedFinding | finding, verdict (accepted/rejected), reason | Rejected findings are kept for calibration |
| ExecutionResult | build\_status, run\_status, failure\_kind, logs\_excerpt, probe\_results | failure\_kind separates student issues from environment issues |
| RepoRunResult | repo, commit\_sha, results per assignment, judged\_findings, score, status |  |
| FixProposal | repo, branch\_name, base\_sha, head\_sha, local\_repo\_dir, pr\_title, pr\_body, patches, diff, verified, status | pending\_review → approved → published, or rejected; a failed publish goes back to pending\_review |
| PublishRecord | repo\_run\_id, branch, fork, pr\_url, pr\_number, published\_at, outcome, message | outcome: published / updated / stale / failed; kept in the publish log |
| ReviewDecision | repo\_run\_id, approved/rejected/edited, reviewer\_notes |  |
| Run | run\_id, assignment\_id, started\_at, status, repo\_runs | One round |

**Run failure classification (failure\_kind):** `build_failed`, `startup_failed`, `db_unreachable`, `hardcoded_connection_unresolved`, `timeout`, `environment_error`. Only `build_failed` and `startup_failed` (when the environment is healthy) count as student issues; the rest are flagged for manual review.

## 6. Ports and adapters

Every external capability is defined as a narrow interface in `ports/` and implemented in `adapters/`; in tests, every adapter is replaced by an in-memory fake.

| Port | Responsibility | v1 adapter | Future replacement |
| --- | --- | --- | --- |
| RosterReader | Read the student list | ExcelRosterReader (openpyxl) | Google Sheets, CSV |
| GitClient | clone/fetch, commit sha, log, branch, commit, push | LocalGitClient (git CLI via subprocess) |  |
| CodeHost | Default branch head, the publishing account's fork, open or update a PR | GitHubCodeHost (REST API with urllib) | GitLab |
| CodeRunner | build, run, health check, HTTP probes, cleanup | DockerCodeRunner | Remote runner |
| DatabaseProvisioner | Create and drop a database per repo | SqlServerProvisioner |  |
| LLMClient | Call with structured output, by role name | LangChainLLMClient (init\_chat\_model) | Any provider |
| Storage (repositories) | RunRepository, FindingRepository, ProposalRepository | SQLAlchemy + SQLite | PostgreSQL |
| AssignmentSource | Load the course profile, assignments, requirements and review rules, with their status | YamlAssignmentSource | Database / editing UI |
| AssignmentDraftWriter | Write an assignment's drafted files and its validation report; mark items approved | YamlAssignmentDraftWriter | Database / editing UI |
| ReportWriter | Write the reviewer's reports and the round's files | Jinja2 to Markdown, plus a CSV counts file |  |
| Clock | Current time | SystemClock | FixedClock in tests |

**Principles**

- Narrow interfaces (ISP): GitClient knows nothing about PRs; CodeHost knows nothing about cloning.
- LLMClient receives a role (for example `reviewer`, `judge`, `fixer`), not a model name; the role → model mapping lives in configuration.
- Every adapter documents the exceptions it raises, mapped to domain exceptions (for example `RepoUnavailableError`).

## 7. LangGraph design

The graph has three levels (built in stage 10a): a round graph that sends each repo to a repo subgraph, and inside it one node per assignment, each wrapping that assignment's own subgraph (ADR 0003, refined by ADR 0019). Publishing happens only through the round's approval gate (ADR 0018, stage 13a).

&#91;embedded content: graph flow · round, repo and assignments\]

The round sends each repo to its own subgraph (at most `max_parallel_repos` at a time); inside it, every assignment node runs in parallel, and the ones outside the round's selection return no result.

**Level 1: round graph** (`build_round_graph`)

1. `start_round` – load the roster (pairs and warnings), select `_general` and assignments 1..N, prepare the runner (sweep leftovers, check the environment), and start the run in storage. Input: `assignment_id`, `roster_path`, and optionally `repo_ids` (check only these), `limit` (the first N repos) and `run_id`.
2. `check_repo` – the repo subgraph, sent once per repo with Send; `max_concurrency` is `max_parallel_repos` from configuration (default 3).
3. `finish_round` – mark the run completed (failed only if no repo could be checked at all) and put the round's summary in state: one line per repo in roster order, with its counts, a short list of its findings (requirement, severity, first line of the message) and its report's path.
4. `write_round_report` – write `round.md` (one row per repo, in roster order) and `counts.csv` to `workspace/runs/<run_id>/reports/`, and add the reports folder to the summary. After publishing, `round.md` also lists each repo's pull request. A failure is a round error; the run is already saved.
5. `approval_gate` – the round's one approval gate (ADR 0018, stage 13a). With no proposal pending review in the run, the round ends without interrupting. Otherwise it interrupts with a small JSON payload: for each pending repo the repo id, student, counts, branch, pull request title, report path and diff path (the diff is written to `reports/<repo_id>.diff`), the last publish attempt if it failed, and how to answer. The resume value is `{"approve": [repo ids], "decline": [repo ids], "notes": {repo id: text}}`, validated as `GateDecision` by `interrupt(..., response_schema=...)` (a wrong shape is refused and the interrupt stays open). An unknown repo id, a repo both approved and declined, or a note for an unmentioned repo records nothing; the gate asks again with the reason. Repos not mentioned stay pending. Declined repos are never published.
6. `publish_approved` – publish each approved repo through `PublishService`, one after another, put the records in `publish_results` and in the summary, then go back to `write_round_report` and `approval_gate`, which loops while repos are pending. A failed publish is recorded, goes back to pending review and is shown at the next gate; it never stops the round.

Planned: `share_partner_hints` (see note), the grade once there is a formula, and editing a proposal at the gate.

**Level 2: repo subgraph** (`build_repo_graph`)

1. `fetch_repo` – clone or fetch into `workspace/clones/<repo_id>` (reused across rounds) and read the commit sha. A failed fetch goes straight to `record_repo`.
2. `build_and_run` – build, run and probe the repo in Docker with the probes of every selected assignment; logs go to `workspace/runs/<run_id>/logs/<repo_id>`. The runner always cleans up after itself. A failed build still runs the assignment nodes; their checks mark build and HTTP requirements not evaluated.
3. `assignment_<id>` – one node per assignment in the catalog (`assignment__general`, `assignment_hw01`, ...), generated when the graph is built and run in parallel (ADR 0019). Each wraps its assignment subgraph.
4. `record_repo` – save the repo's result (status, commit, execution, assignment results, errors) in the run.
5. `write_report` – write the reviewer's report, `workspace/runs/<run_id>/reports/<repo_id>.md`. A report that cannot be written is a round error; the repo keeps its status.
6. `fix_repo` – the fixer subgraph (stage 14, ADR 0021), only when `fixer.enabled` is set, the repo was fetched and it has assignment results; otherwise the repo ends after `write_report`. Its own input and output schemas: it reads the repo's fetch, execution and assignment results, and returns the fix outcome, the LLM usage and round errors.
7. `update_report` – after `fix_repo`, write the repo report again with the fix outcome: the proposal's branch, title and diff, or why there is no proposal.

**Fixer subgraph** (`build_fix_graph`, ADR 0021): `prepare_branch → plan_edits → apply_edits → self_review → verify`, then back to `plan_edits` while edits were rejected or verification failed and attempts remain (`fixer.max_attempts`), else `fall_back` (failing fixes become their fallback notes, then `verify` once more) or `write_pr_text → propose`. Each node calls `FixService` and is guarded; a fixer failure is a round error for that repo and never stops the round. The LLM nodes have a `RetryPolicy` for unparseable replies and a `CachePolicy` (a `SqliteCache` in `workspace/fixer_cache.sqlite`); every node sends a custom stream event with its step and attempt.

Planned: `validate_findings`, `judge_findings`, `score`.

**Level 3: assignment subgraph** (`build_assignment_graph`) – one node now, `deterministic_checks` (structure, custom, build and HTTP requirements). Later `llm_review → judge` for the code requirements.

**Authoring graph** (`build_authoring_graph`, stage 16, ADR 0023) – separate from the round, served by Studio next to `round` and run from the command line: `load_intake → draft → validate`, back to `draft` with the validation's reasons while it fails and attempts remain, then `approval` (an interrupt, like the round's gate) → `write_approved`. Details in §8.

**State**

- Round: the input fields, `run_id`, `repos`, `selected_assignment_ids`, `roster_warnings`, `partners`, `recorded_repos` and `round_errors` (reducer: append), `report_paths` (reducer: merge), `llm_usage` and `fix_outcomes` (reducer: append), `summary`, and for the gate `gate_open`, `gate_message`, `approved` and `publish_results` (reducer: append).
- Repo: `run_id`, `assignment_id`, `repo`, `selected_assignment_ids`, `partner`, `fetched` (working copy and commit sha), `execution`, `assignment_results` and `errors` (reducer: append). Only `recorded_repos`, `round_errors` and `report_paths` flow back to the round.
- State holds only ids and small models; clones and logs live on disk, results in the database.

**Persistence and errors**

- The command line uses a SQLite checkpointer in its own file (`workspace/checkpoints.sqlite`), one thread per round with thread id = run id. Studio's dev server supplies its own checkpointer. The approval gate needs a checkpointer; the wait at the gate can last days. `python -m hw_checker review <run_id>` shows the gate's payload and `approve <run_id> --repo ... [--decline ...] [--note id="..."]` resumes the thread with `Command(resume=...)`; in Studio the developer resumes the interrupt with the same JSON.
- Every repo-level node is wrapped at its boundary, the only place `except Exception` is allowed: the error is logged with run_id and repo_id, added to the repo's `errors`, and the repo continues to `record_repo` with status failed. A failed repo never stops the round. A problem before any repo is checked (roster, assignments, Docker) stops the round with a message.
- Planned: RetryPolicy for nodes with network calls (LLM, GitHub), with a timeout. Git fetches already retry transient network errors in the adapter.

**Note on partner hints:** in v1, hints are simple: after both repos finish, an accepted finding in one that was not found in the other triggers one focused check in the other. The finding is never copied automatically.

## 8. Requirements engine and YAML schema

A new assignment is added as a folder under `assignments/` with no code change: the loader validates the YAML against a Pydantic model, and the registry auto-discovers custom checks in `checks.py`. Checking assignment N loads `_general` plus every assignment whose order is ≤ N.

**Example schema (illustrative; finalized in stage 3)**

```yaml
id: hw03
order: 3
title: "Products controller with GET/POST"
taught_techniques:        # what has been taught so far; also used for AI signals
  - controllers
  - routing attributes
  - IActionResult
requirements:
  - id: hw03-r1
    kind: structure
    title: "ProductsController exists under Controllers/"
    severity: major
    check: file_exists
    params: { glob: "**/Controllers/ProductsController.cs" }
  - id: hw03-r2
    kind: code
    title: "GET by id returns NotFound when product is missing"
    severity: major
    description: >
      The action should not return 200 with null.
    allowed_alternatives:
      - "Returning 204 NoContent is acceptable"
  - id: hw03-r3
    kind: http
    title: "GET /api/products returns 200"
    severity: critical
    probe:
      method: GET
      path: /api/products
      expected_status: 200
      allowed_statuses: [200]
lenient_notes:
  - "Do not require async/await at this stage"
  - "Naming in Hebrew transliteration is acceptable"
```

**Requirement kinds and checks**

| kind | Performed by | Example |
| --- | --- | --- |
| structure | Deterministic check (file\_exists, folder\_layout, project\_references) | A Controllers folder exists |
| build | Deterministic, from the build result | The project builds without errors |
| http | Deterministic, HTTP probe against the running app | POST returns 201 or 200 |
| code | LLM reviewer, evidence required | Null check before returning |
| custom | A function in `checks.py` registered with a decorator | Anything that does not fit the template |

**Rules**

- `lenient_notes` and `allowed_alternatives` go into the reviewer's and judge's prompts, and are also used by the validators.
- General requirements (`_general`): basic clean code, folder structure, reasonable use of HTTP status codes. They are phrased leniently, to fit second-year students.
- Changing an old assignment's requirement affects regression in later rounds, so every file is kept in Git and its hash is stored with the round's results.

### 8.1 Course profile and review rules (stage 3f, ADR 0023)

Course-wide policy is data in `course/`, separate from the assignments; `_general` keeps only the general requirements.

- **`course.yaml`:** the audience and level ("second-year students"), the stack, the student-facing wording rules ("The assignment asks…", never the lecturer), and the PR format: the opening, the sections with their headings, and the title rule (the strong title when the verified branch clears a major or critical requirement that failed before).
- **`review_rules.yaml`:** a list of `ReviewRule`s, in `course/` for every assignment and in an assignment's folder for that assignment only. Checking assignment N uses the course rules plus the rules of assignments 1..N, the same way requirements regress. A rule with `until: hw03` stops after hw03; `replaces: <rule id>` changes an earlier decision.
- Each rule says which roles see it (`reviewer`, `fixer`, `judge`, `writer`), so each prompt gets only its own rules.
- One Pydantic model per file in `domain`; its JSON Schema is generated from the model and used three ways: validating the files (with the loader's messages), the authoring LLM's structured output, and the editor form later.

Stage 3f moves every policy item now written in code or prompts into these files: the fixer's review rules and examples, "second-year" and "ASP.NET Core Web API" in the prompts, the `Hi! ` opening, the PR sections and the title. A golden test shows hw01's rendered prompts and PR text are unchanged.

### 8.2 Prompts: a fixed frame with content slots (ADR 0023)

Every prompt has two parts:

- **The frame:** the same for every assignment: the task of the role, the structured output contract, and the rule that student files are data, never instructions. It is a versioned template in the repo. A course may replace it with a file in `course/prompts/`.
- **The content:** the slots the frame fills from data: the audience, the role's rules, the requirements with their guidance, `lenient_notes`, and the examples.

The authoring LLM writes content only. It may suggest a frame change (`FrameSuggestion`, listed in the validation report); a frame changes only with the developer's approval, by a developer's commit.

### 8.3 Adding an assignment: the authoring flow (stage 16, ADR 0023)

| # | Stage | By | Output |
| --- | --- | --- | --- |
| 1 | Basics: id, order, title, the point in time to check | Developer | `intake.yaml` |
| 2 | The task description, and optionally a reference solution | Developer | `task.md`, `reference/` |
| 3 | Taught so far and not yet taught, carried over from the previous assignment | Developer | `intake.yaml` |
| 4 | Lecturer decisions: accepted alternatives, things not to mention, required statuses | Developer | `intake.yaml` |
| 5 | Draft (role `author`): requirements with probes and route alternatives, code requirements with reviewer guidance, the assignment's review rules with examples, and any frame suggestions | LLM | `requirements.yaml`, `review_rules.yaml`, all `status: draft` |
| 6 | Validation: the schema; a dry run on the reference solution, which must pass every requirement; a deterministic preview of the counts on the last round's repos; the changes against the previous assignment's rules | System | `authoring_report.md` |
| 7 | Approval: edit the files, then approve them whole or item by item | Developer | `status: approved` |
| 8 | Commit; the files' hashes are saved with each round, as today | Developer | Git |

- **What is approved:** everything the LLM drafted: what is checked (requirements, severities, probes, statuses, alternatives), what the fixer may fix, may only note, or must never mention, and the reviewer's guidance. A round refuses an assignment with a draft item.
- **The report shows**, for each requirement and rule, one plain-English line and its origin: quoted from `task.md`, from the intake, or inferred by the LLM. Inferred items are flagged: those are the ones to check, or to confirm with the lecturer before approving. A requirement that fails the reference solution, or almost every repo in the preview, is flagged too.
- **Who approves:** the developer. Changes to `course/` are not drafted by the LLM; they are made by hand, and the commit is the approval.
- Stages 5 and 6 loop: a failed validation goes back to the draft with its reasons, up to `authoring.max_attempts`.
- **Privacy:** the authoring LLM sees the task text and the reference solution, never student code, so it does not need `llm.student_code_approved`; the preview runs deterministic checks only.
- **Interface:** the authoring graph (§7) from Studio and `python -m hw_checker assignment new <id>` / `assignment approve <id>`. The UI comes later, on top of the same service (§12).

## 9. Running code in Docker with SQL Server

Each repo is built and run in an isolated .NET 10 container, against one shared SQL Server container with a separate database per repo. This is the highest-risk stage, so it starts with an isolated spike before the full implementation.

**Flow per repo**

1. Locate the main `.csproj` (the Web project). If there are several, choose by `Microsoft.NET.Sdk.Web`; if still ambiguous, `environment_error` with an explanation.
2. Create a dedicated database (`hw_<repo_id>_<run_id>`) in the shared container.
3. Override the connection string: environment variables in the form `ConnectionStrings__<Name>` for every name found in `appsettings*.json`.
4. If a connection string is hard-coded (ADO.NET), replace it in the local working copy only and record the replacement in the report. If it cannot be identified safely: `hardcoded_connection_unresolved`, with no grade penalty.
5. Create the schema: for EF Core, run migrations if present; for a direct connection, use a SQL script the student included (if any) or a reference script from the assignment folder.
6. `dotnet build`, then `dotnet run` in the container with a dynamically mapped port.
7. Health check: wait until the port responds (timeout in configuration).
8. Run the HTTP probes from all assignments 1..N, in the defined order.
9. Teardown: stop the container and drop the database, always.

**Decisions**

- One shared SQL Server is started once per round (compose), because a SQL Server container per repo is too heavy for a local machine.
- The student's container has no external network access other than the shared database, and has memory and CPU limits.
- Build and run logs are saved to a file; only a relevant excerpt goes into the report.
- Working-copy changes made to run the project (connection string) never go into the fixes branch.

**Questions the spike must answer:** average build time, memory use when running in parallel, Docker Desktop behavior on Windows with volumes, and whether a single SDK image is enough.

## 10. LLM layer and judge rails

The LLM is used only where a deterministic check is not enough, and every finding it produces passes two filtering layers before it reaches a report. Guiding principle: missing a minor note is better than making a wrong one.

**Roles and models**

| Role | Use | Model tier |
| --- | --- | --- |
| reviewer | Review code against `code` requirements | Cheap to mid |
| judge | Decide whether a finding is substantive and level-appropriate | Strong |
| fixer | Write a targeted fix and its explanation | Strong |
| writer | Phrase plain-English feedback | Cheap |
| author | Draft a new assignment's requirements and rules from its task description (§8.3) | Strong |

The provider and the role → model mapping live in configuration (`llm.provider`, with the models in `llm.roles` or `llm.copilot.roles`); switching providers is configuration only. There are two adapters:

- **`openrouter`** (ADR 0020): any OpenAI-compatible endpoint. OpenRouter for development, with provider routing `data_collection: deny`, so no provider that may train on the data is used.
- **`copilot`** (ADR 0022): the developer's GitHub Copilot seat through the GitHub Copilot SDK. GitHub Models was retired on 2026-07-30. The Copilot runtime is an agent, so every session has no tools, no MCP servers and no permissions, and runs in an empty temporary directory. Each call's session is deleted afterwards, and every prompt counts toward the seat's premium requests.

`python -m hw_checker llm-check` prints the provider and checks the login or key, the models and the network with synthetic text. No student code or names are sent to an LLM until the lecturer approves it.

**Prompt rules**

- Every prompt is a versioned template file in the repo, not a string in code.
- A template is a frame only: it holds no course or assignment policy, which reaches it through slots from `course/` and the assignment's files (§8.2).
- The prompt includes the requirement, `allowed_alternatives`, `lenient_notes`, the students' level, and only the relevant files (not the whole repo).
- Structured output is mandatory (a Pydantic model); a finding without Evidence is rejected at parse time.
- Low temperature; bounded input size, with long files trimmed by relevance.

**Judge rails: two layers**

1. **Deterministic validators** (cheap, always run):
   - The file and lines in Evidence exist in the repo.
   - The quote matches the code at those lines (whitespace-normalized comparison).
   - The requirement\_id exists and belongs to assignments 1..N.
   - The finding does not fall under `allowed_alternatives` (for example, a status listed in `allowed_statuses`).
   - No duplicate of the same finding.
2. **LLM judge** (only on what passed layer 1):
   - Is the finding correct according to the quoted code?
   - Is it substantive relative to the requirement, or minor/stylistic?
   - Is it fair for a second-year student and for what has been taught so far?
   - Output: accepted/rejected + reason + adjusted severity (it may only lower severity, never raise it).

Every rejected finding is stored with its rejection reason and shown, collapsed, on the review screen, so requirements and prompts can be calibrated over time.

**AI-use signals (next phase)**

In v1, commit history is collected and shown raw. The next phase adds signals: a single huge commit, techniques not listed in `taught_techniques`, unusual code volume, and a style gap relative to earlier rounds. The output is a flag with a reason for human review, never a disqualification.

## 11. Reports, fixes and publishing

Publishing happens only through the round's approval gate (ADR 0018, stage 13a): after every repo is checked the round waits for the reviewer, and for each approved repo a deterministic publishing service (no LLM) pushes and opens a PR.

**Three outputs, all from Jinja2 templates**

| Output | Audience | Language | Destination |
| --- | --- | --- | --- |
| Repo report | The reviewer | English | `workspace/runs/<run_id>/reports/<repo_id>.md` |
| Round files: `round.md` and `counts.csv` | Reviewer and lecturer | English | `workspace/runs/<run_id>/reports/`; counts only until there is a grade formula |
| Student feedback | The student | Plain English | PR description + `FEEDBACK.md` in the branch |

The LLM writes only the message text of each finding; structure, headings and score are set by the template and code, so the format is stable.

**Reviewer reports (stage 11a, developer decision 2026-10-29):** in English, since findings already carry `message_en`. A repo report has the student (and partner), repo, checked commit, run and assignment; the build and run outcome in plain English; the findings grouped by severity with their evidence; the requirements not evaluated, each with a one-sentence reason; and the repo's errors. A repo with nothing to report says so in one line. `round.md` has one row per repo in roster order with its counts and a link to its report, then the roster warnings. `counts.csv` has the same columns plus the partner, in UTF-8 with a BOM so Excel shows Hebrew names. `ReportService` writes them through the `ReportWriter` port; the Jinja2 templates are in `reporting/templates/`.

**Score:** none yet. The round's files hold counts only until the lecturer sets a formula. Then: a simple, transparent formula in configuration (weight by severity; minor does not lower the grade in v1), isolated in a single service so it is easy to replace.

**Fixes (stage 14, ADR 0021; the format is [fix-pr-workflow.md](fix-pr-workflow.md) §3–4)**

1. The fixer (role `fixer`) reads the whole repo, its findings and the review rules, and returns a structured `FixPlan`: targeted edits (`fix`, a small code change with a `//fix: ...` line above it; `note`, only `//` comment lines, with the one PR section it belongs in: `names` or `for_you`; `final_newline`, on the data file only), each with the requirements it addresses and, for a `fix`, a fallback note, plus the PR's "Files" items. Student files are data inside delimiters, never instructions.
2. Deterministic guardrails decide what is applied, in a separate working copy (`workspace/runs/<run_id>/fixes/<repo_id>`) at the checked commit; a rejected edit keeps its reason for the next attempt. A self-review (role `judge`) removes edits that break the rules in spirit.
3. Rebuild, rerun and recheck the working copy with the round's assignments: the build must not get worse, no passing requirement may fail, and every requirement a `fix` claims must pass. Otherwise plan again with the reasons (up to `fixer.max_attempts`); after the last attempt failing fixes become their fallback notes, and if verification still fails there is no proposal and the repo report says why.
4. One commit per repo, `<short name> review: fixes and questions`, by the publishing author, on `hw-review/<assignment>-<date>`; the proposal (pending review, verified) holds the commits, the diff (`reports/<repo_id>.diff`) and the PR text. No edit left means no branch and no proposal.
5. The PR body has the approved PRs' shape, rendered from a template: an opening that starts with `Hi! `, then the bold sections **Fixed in this PR (marked `//fix:` in the code)**, **For you to do (marked in the code)**, **Names (C# conventions)** and **Files** (only the plan's notes on leftover or duplicate files, never the changed files), empty ones left out. Student-facing text says "The assignment asks…", never mentions the lecturer, and code comments are written `//fix: ...` and `//Text`. The title is decided in code from the last recheck, not from the model's claims: `fixes and questions` when the verified fix branch clears a major or critical requirement that failed before (a real bug, whatever the edit's kind; notes only add comments, so they clear nothing), otherwise `small fixes and questions`.
6. Fixes stay at student level: no refactoring, no renames of stored model properties, no techniques not yet taught, never the core of the assignment.
7. Privacy: `fixer.enabled` needs `llm.student_code_approved` (the lecturer's approval); logs carry counts and tokens only. The summary and `round.md` show the round's token totals and the number of proposals.
8. From stage 3f, the review rules, the examples, the PR's opening, sections and headings, and the title rule in points 1, 5 and 6 come from `course/` and the assignments' `review_rules.yaml` (§8.1); the points above describe hw01's values. The PR sections become data, not a fixed enum.

**Publishing (stage 13a, as built)**

`PublishService` follows [fix-pr-workflow.md](fix-pr-workflow.md) §2.6 and runs only from the round's approval gate (§7). It is deterministic, with no LLM.

- Allowed only for a proposal in status `approved`, and only when the commit author (`git.commit_author_name` / `commit_author_email`, the publishing account's no-reply address) is set.
- Head check: the student's default branch must still point to the checked commit (`base_sha`); otherwise the attempt is `stale`, nothing is pushed and the repo needs a new round.
- Fork: `POST /repos/{owner}/{repo}/forks` with `name: check_<owner>` and `default_branch_only: true` in the publishing account (`github.account`), reusing the name GitHub returns for an existing fork, then waiting until the fork answers.
- Push: `git push` of the branch to the fork. The token reaches git only through a credential helper that reads it from that one command's environment: never in a URL, the git config or a log.
- Pull request: on the student's repo, head `<account>:<branch>`, base the default branch, `maintainer_can_modify: true`. Publishing again finds the open pull request from that branch and updates its title and body instead of opening a new one.
- Every attempt is saved in the publish log (`publish_log`: branch, fork, PR link and number, time, outcome, message) and logged. The outcome is `published`, `updated`, `stale` or `failed`; a failure moves the proposal back to pending review. The links go into the summary and `round.md`.
- The GitHub adapter (`adapters/github_api`) uses the standard library (`urllib`) and the system certificate store, and maps every error to `PublishError` with GitHub's message, never the token. `CodeHost.has_push_access` was removed: publishing always goes through the fork.

## 12. User interface and storage

The v1 UI is LangGraph Studio over the local dev server (ADR 0017), and a command line runs the same graph. The Streamlit app below is deferred until after the approval gate; it will call only the service layer. Storage is SQLite behind the repository pattern. These choices allow a later move to FastAPI + React and to PostgreSQL without touching the core.

**LangGraph Studio (v1)**

- `langgraph.json` serves the graph `round`, built from the assignment catalog when the server starts (restart it after adding or editing an assignment).
- The developer starts a round from the graph's input (`assignment_id`, `roster_path`, optional `repo_ids` and `limit`) and watches each repo pass through `fetch_repo`, `build_and_run`, the `assignment_<id>` nodes and `record_repo`.
- Results are saved in the database as for a round run from the command line.
- Privacy: the state holds students' names and findings, so LangSmith tracing stays off (no `LANGSMITH_API_KEY`, `LANGSMITH_TRACING=false`). The state stays in the local dev server; the Studio page reads it from `127.0.0.1`.

**Screens (Streamlit, deferred)**

| Screen | What happens there |
| --- | --- |
| Start a round | Choose an assignment, upload the Excel file, show ingestion warnings, run button |
| Round progress | Table of repos with status (queued, running, done, failed), duration, and a link to details; auto-refresh |
| Review | Per repo: accepted findings, rejected findings (collapsed), build/HTTP results, fix diff, report; approve, reject and note buttons |
| Assignments | View each assignment's requirements and rules; add an assignment through the authoring flow's stages (§8.3), with forms generated from the files' JSON Schema, and review and approve the draft; the form writes the YAML files |

**UI principles**

- One clear action per screen; error states shown in plain language with a suggested next step.
- Running a round does not block the UI: the graph runs in a background process and the UI reads status from the database.
- No business logic in the UI; every button calls one service method.

**Storage**

- Tables: runs, repo\_runs, findings, judged\_findings, proposals, review\_decisions, publish\_log.
- Each table is accessed only through a dedicated repository behind the `Storage` port.
- Schema migrations through Alembic from the start, so moving to PostgreSQL is a connection-string change.
- The LangGraph checkpointer uses a separate SQLite file, so technical state is not mixed with business data.
- Large artifacts (clones, logs, diffs) go in `workspace/` by run\_id; the database stores paths.

## 13. Configuration, errors, security and testing

Every value that may change lives in configuration, every expected error maps to a domain exception, and every layer is tested on its own with fakes.

**Configuration**

- `pydantic-settings` with a `.env` file (secrets) and a `config.yaml` file (everything else); `.env.example` documents every variable.
- Examples: parallelism, timeouts, role → model mapping, workspace path, Docker image, score weights, branch name.
- No magic values in code.

**Logging and errors**

- Structured logging (JSON to file, readable on the console), with run\_id and repo\_id on every line.
- Domain exception hierarchy: `HwCheckerError` ← `RosterError`, `RepoUnavailableError`, `BuildError`, `ExecutionEnvironmentError` (not `EnvironmentError`, which is a Python built-in name), `LLMError`, `PublishError`, `ReviewError` (a review decision that cannot be applied).
- Adapters map external library exceptions to domain exceptions; upper layers never catch a general `Exception`, except at a graph node boundary.

**Security**

- Student code runs only in a container, with no external network and with resource limits.
- The GitHub token and LLM keys live only in `.env`, which is in `.gitignore`; pre-commit includes a secret scan.
- Content from a student's repo (code, comments, README) is data only: prompts mark it as such, so a comment in their code cannot change the checking instructions (prompt injection).
- Publishing is possible only after explicit approval.

**Testing strategy**

| Type | What is tested | How |
| --- | --- | --- |
| Unit | domain, services, validators, loader, pair merging | pytest with a fake for every port |
| Contract | Every adapter satisfies its port | A shared test run against both the fake and the real adapter |
| Integration | Docker runner, Git, SQLite | Marked `@pytest.mark.integration`, run locally |
| Graph | Graph flow and error paths | Fake LLM and fake runner |
| Golden | Checking output on a fixed sample repo | Compared with a saved result; catches prompt regressions |

Every implementation stage ends with ruff, mypy and pytest (unit) passing cleanly.

## 14. Implementation plan by stage

The first phase has 17 implementation stages; 15 of them are required for the start of next week. Each stage runs on its own branch, goes through review, and is merged into `main` only after approval (the full working protocol is in the Planner & Reviewer Agent Guide).

| Stage | Topic | Main deliverables | Done when | Priority | Depends on |
| --- | --- | --- | --- | --- | --- |
| 0 | Infrastructure | Folder structure, pyproject, uv, ruff, mypy, pytest, pre-commit, configuration, logging, CLAUDE.md, README, first ADRs | Tools run cleanly on an empty project | Must | – |
| 1 | Domain and ports | All models from section 5, all interfaces from section 6, exception hierarchy | Validation tests pass; no implementations | Must | 0 |
| 2 | Excel ingestion and pairs | ExcelRosterReader, RosterService | A sample Excel file yields unique repos and pairs; edge cases covered | Must | 1 |
| 3 | Requirements engine | YAML schema, loader, registry, 1..N selection, YAML for the current assignment | A new assignment folder is picked up without code changes | Must | 1 |
| 4 | Git | LocalGitClient | Clone and re-run both work; sha and history are read | Must | 1 |
| 5a | Run spike | Isolated experiment script: build+run a sample repo against SQL Server | The sample repo answers HTTP; spike answers recorded in an ADR | Must | 0 |
| 6 | Storage | SQLAlchemy models, Alembic, repositories | A full round is saved and loaded, with tests | Must | 1 |
| 5b | Full runner | DockerCodeRunner, SqlServerProvisioner, connection string override, probes, teardown | Every failure kind is classified correctly in integration tests | Must | 5a, 1 |
| 7 | LLM layer | LLMClient, role mapping, structured output, retry | A real call returns a valid model; switching models is config-only | Must (blocked: provider) | 1 |
| 8 | Checks | Deterministic checks + LLM reviewer + prompts as files | The sample repo yields structured findings with evidence | Must | 3, 5b, 7 |
| 9 | Judge rails | Validators + LLM judge + storing rejected findings | Fake findings are filtered out in tests | Must | 8 |
| 10a | Round graph | Round, repo and assignment graphs (Send per repo, one node per assignment), checkpointer, error isolation, CLI, LangGraph Studio | A round over 2–3 repos runs end to end from the CLI and from Studio and is saved | Must | 2–8 |
| 11a | Reports | Jinja2 templates, English repo reports, `round.md` and `counts.csv` (no score until there is a formula; student feedback moves to 13a) | Every repo of a real round has a report, and the round has its files | Must | 10 |
| 12 | Streamlit UI (deferred, ADR 0017) | The four screens from section 12, background run, requirements editor | A full round runs from the UI without a terminal | Deferred | 13a |
| 13a | Approval gate and publishing | One interrupt per round (ADR 0018), PublishService, GitHubCodeHost, head check before publishing | A PR opens only for an approved repo; re-publishing updates it | Should | 10a, 11a |
| 14 | Fixes | fixer, patch application, rebuild, diff in the UI | A fix that breaks the build is dropped automatically | Should | 10a |
| 3f | Course profile and review rules (ADR 0023) | `course/`, `ReviewRule` and `CourseProfile` with their schemas, rule inheritance (`until`, `replaces`), every policy item moved out of code and prompts, PR sections as data | hw01's rendered prompts and PR text are unchanged (golden test), and no course policy is left in code | Must (before hw02) | 3, 14 |
| 16 | Assignment authoring (ADR 0023) | Intake schema, role `author` and its prompt, authoring graph with validation, the reference-solution dry run, the preview, the report and the approval interrupt, `assignment new` / `approve`, draft status refused by rounds | A new assignment goes from `task.md` to approved files from the command line or Studio, with inferred items flagged | Must (before hw02) | 3f |
| 15 | First real run | A round over real repos, calibrating requirements and prompts, bug fixes | A full round completes and results are checked manually | Must | 13a |

**Practical order:** 0 → 1 → 5a (early, because of the risk) → 2, 3, 4 → 6 → 5b → 7 → 8 → 9 → 10a → 11a → 13a, 14 if time allows → 15. Stages 3f and 16 come before the next assignment (hw02) is checked, and before 8b (LLM reviewer) and 9 (judge), so those read their rules from data from the start. Stage 12 is deferred (ADR 0017); LangGraph Studio is the UI until then. Stages 2, 3 and 4 are independent of each other and can be done in any order.

**Definition of done for every stage**

- [ ] The deliverables in the table exist, and nothing more (no creep into other stages).
- [ ] ruff, mypy and pytest (unit) pass cleanly.
- [ ] New tests cover the new code, including failure paths.
- [ ] README, `.env.example` and ADRs updated as needed.
- [ ] The implementer's report was received, reviewed and approved.

## 15. Risks, open questions and roadmap

The highest risk is running the projects in Docker on Windows against SQL Server; the only immediate blocker is choosing the LLM provider.

**Risks**

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Docker/SQL Server on Windows is slow or unstable | No HTTP checks | Early spike (5a); fall back to code-only checks, flagged |
| Connection strings hard-coded in varied forms | Repos do not run | Detect and replace in the local copy; classify as `hardcoded_connection_unresolved` with no penalty |
| The LLM is too strict or invents findings | Wrong feedback to students | Required evidence, validators, judge, allowed\_alternatives, manual approval |
| API access through Copilot unavailable | Stage 7 blocked | LLMClient abstraction; choose another provider in configuration |
| No write access to student repos | No PR | PR from a fork, or send feedback manually |
| Short timeline | Partial version | Must/should priorities; stages 13–14 can be deferred |
| Handover to another developer | Knowledge loss | ADRs, CLAUDE.md, this document, golden tests |

**Open questions**

- [x] Does the Copilot account allow API access from Python code, and to which models? Yes, through the GitHub Copilot SDK (ADR 0022). On 2026-11-02 the seat offered Claude Haiku 5.5 and Claude Sonnet 5.5, among others.
- [ ] Grading rubric (with the lecturer).
- [ ] Point in time to check: repo state at run time, or the last commit before the deadline (with the lecturer).
- [ ] Collaborator access to student repos.
- [ ] The exact requirement list for the current assignment (for stage 3).
- [ ] A sample repo or reference project at the current stage (for stages 5a and 8).
- [ ] Whether the lecturer can give a reference solution for each assignment (for the authoring dry run, stage 16); without one, the dry run is skipped and flagged in the report.
- [ ] Whether the projects already have Swagger.
- [ ] The project's final name.

**Roadmap after the first version**

1. AI-use signals (commit history, techniques not yet taught, style gap).
2. Caching by commit sha + requirement hash, so regression on an unchanged repo is nearly free.
3. Automatic scheduling (Windows Task Scheduler, later a server).
4. Selective review: only repos flagged as problematic go to manual approval.
5. FastAPI on top of the service layer, later React and multiple users.
6. PostgreSQL instead of SQLite.
7. Checking students' unit tests once the course reaches that stage.
