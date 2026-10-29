# HW Checker: Design and Implementation Plan

Oct 20, 2026 · @no name

## 1. Overview, goals and non-goals

The system is a LangGraph-based agent that, twice a week, checks the homework of about 40 second-year students in a .NET 10 Web API course, reports what is present and what is missing, and proposes fixes as a PR to each student's repo after manual approval. A first version must be in use at the start of next week; the system will keep evolving while in use and will later be handed over to another developer.

**Course context.** Students build a single project incrementally over the semester; each assignment adds a few dozen lines of code. Most work in fixed pairs, but each has their own repo. AI use is forbidden, and the expected code level is relatively basic. At some point the projects use SQL Server, first through a direct connection (ADO.NET) and later through EF Core.

**Goals**

- Check each repo against the general requirements and the requirements of every assignment up to the current one (regression).
- Build and actually run the project and verify HTTP behavior, not only read the code.
- Find only real, substantive problems: precision before recall.
- Produce a Hebrew report for the reviewer, a grades file per assignment, and plain-English feedback for the student.
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
| F7 | Hebrew report per repo, grades file per assignment, plain-English feedback | Must |
| F8 | UI to start a round, track progress, review and approve | Must |
| F9 | Publish a PR to the student's repo only after approval | Should |
| F10 | Targeted fixes verified by a rebuild | Should |
| F11 | Use the partner's findings as a hint for a re-check | Must |
| F12 | AI-use signals (commit history, techniques not yet taught) | Next phase |

**Non-functional requirements**

- **Maintainability:** layered architecture, SOLID, single responsibility per component, clean code, documented decisions (ADRs).
- **Extensibility:** a new assignment is added as data files, without changing core code.
- **Scalability:** bounded parallelism (3–4 repos at a time on a local machine); move to a server and a central database without a rewrite.
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
| ui | Streamlit; calls services only, through bootstrap | services, domain |

**Mandatory rules**

- A graph node is thin: it receives state, calls one service, and returns a state update. It holds no business logic.
- One service = one responsibility (SRP). A service never creates adapters itself; they are injected (DIP).
- Only `bootstrap.py` imports concrete adapters.
- The UI knows neither LangGraph nor the adapters, so it can later be replaced by FastAPI + React.
- Adding a check, an assignment or an LLM provider is done by addition (registry/configuration), not by changing existing code (OCP).
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
├── assignments/                 # requirements as data, not code
│   ├── _general/requirements.yaml
│   └── hw01/
│       ├── requirements.yaml
│       ├── checks.py            # optional: custom checks (plugin)
│       └── reference/           # optional: reference code
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
| Assignment | id (hw01), order, title, requirements, taught\_techniques | Loaded from YAML |
| Requirement | id, title, description, severity, kind, allowed\_alternatives, http\_probes | kind: structure / code / http / build |
| HttpProbe | method, path, body, expected\_status, allowed\_statuses, notes |  |
| Severity | critical / major / minor | minor does not lower the grade in v1 |
| Evidence | file\_path, line\_start, line\_end, quote | Required for every code finding |
| Finding | id, requirement\_id, assignment\_id, severity, message\_he, message\_en, evidence, source (deterministic/llm), confidence |  |
| JudgedFinding | finding, verdict (accepted/rejected), reason | Rejected findings are kept for calibration |
| ExecutionResult | build\_status, run\_status, failure\_kind, logs\_excerpt, probe\_results | failure\_kind separates student issues from environment issues |
| RepoRunResult | repo, commit\_sha, results per assignment, judged\_findings, score, status |  |
| FixProposal | repo, branch\_name, patches, diff, verified |  |
| ReviewDecision | repo\_run\_id, approved/rejected/edited, reviewer\_notes |  |
| Run | run\_id, assignment\_id, started\_at, status, repo\_runs | One round |

**Run failure classification (failure\_kind):** `build_failed`, `startup_failed`, `db_unreachable`, `hardcoded_connection_unresolved`, `timeout`, `environment_error`. Only `build_failed` and `startup_failed` (when the environment is healthy) count as student issues; the rest are flagged for manual review.

## 6. Ports and adapters

Every external capability is defined as a narrow interface in `ports/` and implemented in `adapters/`; in tests, every adapter is replaced by an in-memory fake.

| Port | Responsibility | v1 adapter | Future replacement |
| --- | --- | --- | --- |
| RosterReader | Read the student list | ExcelRosterReader (openpyxl) | Google Sheets, CSV |
| GitClient | clone/fetch, commit sha, log, branch, commit, push | LocalGitClient (git CLI via subprocess) |  |
| CodeHost | Open PRs, check permissions | GitHubCodeHost (PyGithub) | GitLab |
| CodeRunner | build, run, health check, HTTP probes, cleanup | DockerCodeRunner | Remote runner |
| DatabaseProvisioner | Create and drop a database per repo | SqlServerProvisioner |  |
| LLMClient | Call with structured output, by role name | LangChainLLMClient (init\_chat\_model) | Any provider |
| Storage (repositories) | RunRepository, FindingRepository, ProposalRepository | SQLAlchemy + SQLite | PostgreSQL |
| AssignmentSource | Load assignments and requirements | YamlAssignmentSource | Database / editing UI |
| ReportWriter | Write reports and the grades file | Jinja2 to Markdown/CSV files |  |
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
3. `finish_round` – mark the run completed (failed only if no repo could be checked at all) and put the round's summary in state.

Planned: `share_partner_hints` (see note), the grades file (11a), and the approval gate with publishing (13a, ADR 0018).

**Level 2: repo subgraph** (`build_repo_graph`)

1. `fetch_repo` – clone or fetch into `workspace/clones/<repo_id>` (reused across rounds) and read the commit sha. A failed fetch goes straight to `record_repo`.
2. `build_and_run` – build, run and probe the repo in Docker with the probes of every selected assignment; logs go to `workspace/runs/<run_id>/logs/<repo_id>`. The runner always cleans up after itself. A failed build still runs the assignment nodes; their checks mark build and HTTP requirements not evaluated.
3. `assignment_<id>` – one node per assignment in the catalog (`assignment__general`, `assignment_hw01`, ...), generated when the graph is built and run in parallel (ADR 0019). Each wraps its assignment subgraph.
4. `record_repo` – save the repo's result (status, commit, execution, assignment results, errors) in the run.

Planned: `validate_findings`, `judge_findings`, `score_and_report`, `propose_fixes`.

**Level 3: assignment subgraph** (`build_assignment_graph`) – one node now, `deterministic_checks` (structure, custom, build and HTTP requirements). Later `llm_review → judge` for the code requirements.

**State**

- Round: the input fields, `run_id`, `repos`, `selected_assignment_ids`, `roster_warnings`, `recorded_repos` and `round_errors` (reducer: append), `summary`.
- Repo: `run_id`, `repo`, `selected_assignment_ids`, `fetched` (working copy and commit sha), `execution`, `assignment_results` and `errors` (reducer: append). Only `recorded_repos` and `round_errors` flow back to the round.
- State holds only ids and small models; clones and logs live on disk, results in the database.

**Persistence and errors**

- The command line uses a SQLite checkpointer in its own file (`workspace/checkpoints.sqlite`), one thread per round with thread id = run id. Studio's dev server supplies its own checkpointer.
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

The role → provider + model mapping lives in configuration. The provider will be chosen once API access through Copilot is clarified; the code does not depend on the provider.

**Prompt rules**

- Every prompt is a versioned template file in the repo, not a string in code.
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
| Repo report | The reviewer | Hebrew | Review screen + Markdown file |
| Assignment grades file | Reviewer and lecturer | Hebrew | CSV/Markdown in the reviewer's grades repo, a new file per assignment |
| Student feedback | The student | Plain English | PR description + `FEEDBACK.md` in the branch |

The LLM writes only the message text of each finding; structure, headings and score are set by the template and code, so the format is stable.

**Score:** a simple, transparent formula in configuration (weight by severity; minor does not lower the grade in v1). The final rubric will be set with the lecturer; the formula is isolated in a single service so it is easy to replace.

**Fixes (should, in v1)**

1. For each accepted, fixable finding, the fixer returns a targeted patch (search-and-replace in a file) and a short plain-English explanation.
2. Patches are applied on a local branch named `hw-review/<assignment>-<date>`, based on the commit that was checked.
3. Rebuild and rerun the checks; a patch that breaks something is dropped and the finding stays as a comment only.
4. One commit per patch, with a message referencing the requirement.
5. Fixes stay at student level: no refactoring, no renames, no techniques not yet taught.

**Publishing**

- Allowed only for a proposal in status `approved`.
- Push the branch to the student's repo (or to a fork if there is no permission) and open a PR with the feedback as its description.
- Idempotent: publishing again updates the same PR rather than opening a new one.
- Every action is logged with the PR link.
- Until the service is ready: reports and feedback are produced as files and sent manually.

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
| Assignments | View each assignment's requirements, and create or edit them through a form (method and path, request body, expected statuses, special requirements); the form writes the YAML files |

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
- Domain exception hierarchy: `HwCheckerError` ← `RosterError`, `RepoUnavailableError`, `BuildError`, `ExecutionEnvironmentError` (not `EnvironmentError`, which is a Python built-in name), `LLMError`, `PublishError`.
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
| 11a | Reports | Jinja2 templates, score, grades file, English feedback | All three outputs produced from a real round | Must | 10 |
| 12 | Streamlit UI (deferred, ADR 0017) | The four screens from section 12, background run, requirements editor | A full round runs from the UI without a terminal | Deferred | 13a |
| 13a | Approval gate and publishing | One interrupt per round (ADR 0018), PublishService, GitHubCodeHost, head check before publishing | A PR opens only for an approved repo; re-publishing updates it | Should | 10a, 11a |
| 14 | Fixes | fixer, patch application, rebuild, diff in the UI | A fix that breaks the build is dropped automatically | Should | 10a |
| 15 | First real run | A round over real repos, calibrating requirements and prompts, bug fixes | A full round completes and results are checked manually | Must | 13a |

**Practical order:** 0 → 1 → 5a (early, because of the risk) → 2, 3, 4 → 6 → 5b → 7 → 8 → 9 → 10a → 11a → 13a, 14 if time allows → 15. Stage 12 is deferred (ADR 0017); LangGraph Studio is the UI until then. Stages 2, 3 and 4 are independent of each other and can be done in any order.

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

- [ ] Does the Copilot account allow API access from Python code, and to which models?
- [ ] Grading rubric (with the lecturer).
- [ ] Point in time to check: repo state at run time, or the last commit before the deadline (with the lecturer).
- [ ] Collaborator access to student repos.
- [ ] The exact requirement list for the current assignment (for stage 3).
- [ ] A sample repo or reference project at the current stage (for stages 5a and 8).
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
