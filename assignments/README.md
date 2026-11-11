# Assignment requirements: file format

Each assignment's requirements live in a YAML file that HW Checker reads at the start of every round. A new assignment is drafted for you from its task text and your intake, validated, and written once you approve it (see "Adding an assignment" below); you can still write or edit the files by hand. This guide covers everything you need.

## Folders

```
assignments/
├── _general/requirements.yaml   general requirements, checked in every round
├── hw01/requirements.yaml       assignment 1
├── hw02/requirements.yaml       assignment 2 (layers)
├── hw03/requirements.yaml       assignment 3 (dependency injection)
└── hw04/                        a new assignment, while it is added ...
    ├── task.md                  the task's text (for drafting)
    ├── intake.yaml              your intake (for drafting)
    ├── reference/               optional: a reference solution
    ├── requirements.yaml        what the round checks
    └── review_rules.yaml        the assignment's own review rules
```

- One folder per assignment. The folder name must be exactly the `id` written inside the file.
- The file must be named `requirements.yaml` (not `.yml`). Folders without it, and folders whose name starts with `.`, are skipped and listed in the log, so a misnamed file is easy to spot.
- `_general` must exist and has `order: 0`. Every other assignment has its own `order` of 1 or more.
- Save files as UTF-8 (the default in VS Code). Hebrew text is fine.

## Adding an assignment

Adding a task needs no code change. You give the task's text and your intake; the `author` role (an LLM, `llm.*.roles.author`) drafts `requirements.yaml` and `review_rules.yaml`; the system validates the draft and waits for your approval. The author sees only the task text, the intake, the course's files, the earlier assignments' files and the check catalog, never students' code, so it does not need `llm.student_code_approved`.

1. `python -m hw_checker assignment new hw02 [--order 2]` creates `assignments/hw02/` with a commented `intake.yaml` and an empty `task.md`. It refuses an id that exists. `taught` and `not_yet_taught` start as the previous assignment's intake says (by `order`).
2. Paste the task's text into `task.md`, fill in `intake.yaml`, and optionally put a reference solution in `reference/`.
3. `python -m hw_checker assignment draft hw02 [--with-execution]` drafts, validates (going back to the author with the reasons, up to `authoring.max_attempts` drafts), writes the report and stops for your answer. It prints where the report is.
4. Answer with one of:
   - `python -m hw_checker assignment approve hw02`: the files are written with `status: approved`, the origins kept, and a first comment line with the day. Commit them, and restart Studio's server before the next round.
   - `python -m hw_checker assignment revise hw02 --note "POST returns 201 only"` (repeat `--note` for more): the author drafts again with your notes; this does not count against `max_attempts`.
   - `python -m hw_checker assignment stop hw02`: the drafted files are removed; nothing is written.

In Studio, the `authoring` graph is next to `round`: start it with `{"assignment_id": "hw02"}` and answer its interrupt with `{"action": "approve"}`, `{"action": "revise", "notes": ["..."]}` or `{"action": "stop"}`. The command line uses the round's SQLite checkpointer, with the thread `authoring-<id>`.

### The intake, by category

```yaml
id: hw02
order: 2
title: Layers
check_commit: latest           # or: {ends_when: {check: project_count, params: {min: 2}}}
taught: [controllers, layers]
not_yet_taught: [dependency injection]
lecturer_decisions:
  accepted_alternatives: ["Returning 200 from POST is fine"]
  not_to_mention: ["async/await"]
  required_statuses: ["GET by id returns 404 for an unknown user"]
requirements:                  # each item: text in your words, and an optional severity
  return_values:               # return values and statuses: http probes
    - {text: "POST /api/users returns 201", severity: critical}
  clean_code:                  # clean code: naming checks, or code requirements
    - {text: "Services end with Service"}
  architecture:                # architecture: the architecture checks
    - {text: "Four projects: API, Services, Repositories, Entities"}
  general: []                  # anything else
```

`short_name` is derived from the id (`hw02` gives `HW2`); give it only to confirm it. Every item of the four lists, of `lecturer_decisions` and of `not_yet_taught` must be covered by at least one drafted requirement, rule or example. `taught` becomes the assignment's `taught_techniques`, and `check_commit` its `ends_when`. The schema is `course/schemas/intake.schema.json`.

The author is offered every registered check with its params schema (a check added later is offered automatically), and uses `code` only when no check fits. Each drafted item has `status: draft`, an `origin` (`task_text` with a quote, `intake` with the item's key, or `inferred`) and a one-line summary for the report.

### What the report shows

`workspace/authoring/<id>/authoring_report.md` (private; the draft is next to it as `draft.json`):

- **The drafted items**, each with its summary, origin and flags. The **inferred** ones come first: neither the task text nor the intake says them, so check them, or confirm them with the lecturer, before approving.
- **The coverage**: each intake item and the drafted items that cover it.
- **Problems**, all listed together: the file format, unknown checks or invalid params, `depends_on`, `applies_when` / `skip_when`, review-rule ids, `until`, `replaces`, and origins (a `task_text` quote must be in `task.md`).
- **The dry run** on `reference/`: it is built and run, and every drafted requirement must pass (a `code` requirement, or one of an assignment that does not apply, is not run). It is:
  - **passed** when every other requirement was evaluated there and passed;
  - **failed** when one fails: the draft goes back to the author;
  - **incomplete** when nothing failed but some requirement could not be evaluated there (for example, the reference does not build, so no `http` requirement runs). It is shown **at the top** of the report with each requirement and its reason: the draft is not verified on them. It is not the author's fault, so the draft still comes to you;
  - **"Skipped (no reference solution)"** without a `reference/` folder.
- **The preview** on the workspace clones (what the rounds left in `workspace/clones`), at each clone's current working copy: for each drafted requirement, how many repos fail, pass or could not be evaluated, never which. By default only the checks that read files run (`structure` and `custom`); `--with-execution` also builds and runs every clone, like a round with the fixer off. A requirement that fails at least `authoring.preview_flag_share` of the repos it evaluated is flagged; a flag never fails the draft.
- **The review rules against the previous assignment**: new, replaced and ended.

While it waits, the drafted files are in the assignment's folder with `status: draft`, so a round refuses the assignment. If the draft still fails after the last attempt, the run ends with the report and nothing is written to the assignment's folder.

### Editing the draft before approving

You may edit the drafted files while they wait (change a word, a severity or a param) instead of another LLM round. On `approve`, when the files differ from the draft:

- They are read back and validated again with no LLM, like a draft: the schemas and the catalog, the coverage, the dry run and the preview.
- Every item you changed or added gets `origin: developer`; the others keep theirs. An item you add without `origin` and `status` gets them.
- A new report is written, marked **"Edited by hand"** at the top with the items changed, and the run waits again: "Your edits were validated; review the report and approve again." Nothing is approved in that step.
- If the edits have problems (for example an unknown check), the report lists them and the run keeps waiting: edit again, `revise --note`, or `stop`.
- The basics (`id`, `order`, `title`, `taught_techniques`, `ends_when`) come from `intake.yaml`: change them there and draft again.
- A second `approve` with no further edits writes the files, as you edited them, with `status: approved`. The approved files are written anew from the draft, so comments you added in them are not kept.

`stop` removes only the files the draft wrote; files you edited are kept.

### The author's limits

The author's prompt is long: it holds the course's files, every earlier assignment's files and the check catalog (about 64,000 characters for a fourth assignment, more on a revise, which adds the previous draft). So the `author` role has its own limits in `config.yaml`: `llm.role_max_prompt_chars` (150,000 characters), `llm.role_max_output_tokens` (16,000 tokens) and `llm.role_timeouts` (300 s). The other roles keep `max_prompt_chars` and `max_output_tokens`. The Copilot runtime has no setting for a reply's length, so the token limit applies to `openrouter` only.

## The file

```yaml
id: hw01                      # required; same as the folder name
order: 1                      # required; 0 only for _general, unique for the others
title: Products controller    # required
taught_techniques:            # optional; what has been taught up to this assignment
  - controllers
  - routing attributes
lenient_notes:                # optional; things the checker must not be strict about
  - Do not require async/await at this stage
ends_when:                    # optional; when the student has started the next task
  check: project_count
  params:
    min: 2
applies_when:                 # optional; the assignment applies to a repo only when this
  check: di_started           #   holds (or skip_when: it does not apply when it holds)
  params:
    min_registrations: 1
    min_interface_constructors: 1
reviewed_files:               # optional; files besides the code the fixer reviews
  include: ["**/wwwroot/**/*.js"]
  exclude: ["**/*.min.js", "**/lib/**"]
requirements:                 # the list of requirements (may be empty: [])
  - id: hw01-r1
    ...
```

## The files the fixer reviews (`reviewed_files`)

The fixer always sees the stack's code (`fixer.source_suffixes` in `config.yaml`: `.cs` and `.csproj`), then configuration and data files. `reviewed_files` adds more files after the code: `include` and `exclude` are repo globs (`**/` any folders, `*` within one folder, case ignored); a file `exclude` matches is left out, such as a library. The round uses the `reviewed_files` of every checked assignment. hw01 reviews the `.html`, `.js` and `.css` files under `wwwroot` at any depth, never `*.min.js`, `*.min.css` or a `lib/` folder. Without `reviewed_files`, only the code is shown, as before stage 18.

A shown file is edited only when its suffix has a `comment_style` in `course/course.yaml`; `.csproj` is shown but never edited.

## Which commit is checked (`ends_when`)

Students keep working in the same repo, so by the time a round runs, a repo may already hold the next task. There is no deadline: a student who finishes late is still checked. Instead, an assignment may say when the next task has started:

```yaml
ends_when:
  check: project_count   # a structure check that looks at file names only
  params:
    min: 2               # the next task (layers) adds a second project
```

`ends_when` takes the same `check` and `params` as a `structure` requirement, but only a check that looks at file names (`file_exists`, `folder_exists`, `project_count`); a check that reads the files' contents is refused when a round starts. The condition holds on a commit when its check passes there.

When a round checks a repo, it walks the default branch's main line (first parents) from the oldest commit, reading each commit's file list without checking it out:

- The **checked commit** is the last one before the first commit where `ends_when` holds. The build, the run, every check and the fixer's branch use it, so the pull request is based on that older commit.
- If `ends_when` never holds, the latest commit is checked, as without `ends_when`.
- If it already holds at the first commit (for example, the repo had two projects from the start), the commits cannot tell the tasks apart: the latest commit is checked, and the report says so.

The repo report and the approval gate show the checked commit, its date, the commit where the next task started and how many commits came after it. Publishing refuses a pull request only when the branch moved after the round, not because the checked commit is older than the head.

## Which assignments apply to a repo (`applies_when` / `skip_when`)

Students take different paths: one repo is still on the layers task while another has started dependency injection. An assignment may say, per repo, whether it applies:

```yaml
skip_when:                    # hw02: not this assignment once DI has started
  check: di_started
  params: {min_registrations: 1, min_interface_constructors: 1}
```

`applies_when` (the assignment applies only when the condition holds) and `skip_when` (it does not apply when the condition holds) take the same `check` and `params` as a `structure` requirement; give one of them, not both. Unlike `ends_when`, the check may read file contents: it runs on the checked commit's working copy, for each repo, before the assignment's requirements. The condition holds when its check passes. When a round starts, a condition that names an unknown check or gives it invalid params is refused, like a requirement's.

- **The assignment applies:** its requirements are checked as usual.
- **It does not apply** (`applies_when` does not hold, or `skip_when` holds): none of its requirements is checked. Each is "not evaluated" with the reason `assignment_not_applicable`, and the detail names the condition and the check's own words, for example `skip_when di_started: DI has started (3 registrations, 2 constructors taking an interface).`
- **The condition cannot be evaluated** (its check is "not evaluated", or fails unexpectedly): nothing is guessed. Every requirement is "not evaluated" with the reason `assignment_condition_unknown`, and the detail says why.

`ends_when` keeps its meaning (it chooses the commit); `applies_when` and `skip_when` never choose a commit. The repo report lists each assignment as "checked" or "not checked" with the reason; the round's `round.md`, the approval gate and `review` list first the repos with `assignment_condition_unknown`, so a reviewer looks at them before anything else.

**The fixer's target is per repo:** the highest-order selected assignment that applies to the repo, not the round's target. In a round on hw03, a repo on the layers path gets an "HW2 review" branch and pull request; the prompts name hw02 and show only the requirements of the assignments that apply. The fixer's recheck evaluates every selected assignment with the same conditions.

## Requirement fields

| Field | Required | Meaning |
| --- | --- | --- |
| `id` | yes | Unique across **all** assignments, for example `hw01-r1`. Findings refer to requirements by this id. |
| `kind` | yes | `structure`, `code`, `http`, `build` or `custom` (see below). |
| `title` | yes | One line describing the requirement. |
| `severity` | yes | `critical`, `major` or `minor`. `minor` does not lower the grade. |
| `description` | no | More detail for the reviewer and the checker. |
| `allowed_alternatives` | no | A list of answers that are also correct, for example "Returning 204 is acceptable". |
| `check` | `structure` and `custom` only | The name of the deterministic check to run. |
| `params` | no | Named values for the check, for example a file pattern. Only together with `check`. Values may be text, numbers, `true`/`false`, a list of those, or a mapping of named values (such as the layer `roles`), nested to any depth. |
| `probes` | `http` only, at least one | HTTP requests to send to the running app (see below). |
| `depends_on` | no | Ids of requirements that must pass before this one can, for example reading back an updated user needs GET by id. Each id must exist (in any assignment), and a requirement cannot depend on itself, also not through others. The fixer's recheck does not hold a fix's claim on this requirement against it while one of these still fails. A `structure` or `custom` requirement whose dependency, checked before it in the same file, failed (or was itself not run for this reason) is not run: it is "not evaluated" (`dependency_failed`), and the report names the requirement it needs. List the dependency first. |
| `origin` | no | Where the requirement comes from, as in a review rule: `source` (`task_text`, `intake`, `inferred` or `developer`), `quote`, and `item` (an intake item's key). Drafted requirements have it. |
| `status` | no | `approved` (the default, for a file written by hand) or `draft`: a round refuses an assignment with a draft requirement. |
| `reviewer_guidance` | `code` only | What the code reviewer looks for, and what is a finding. |
| `lenient_notes` | `code` only | What the reviewer and the judge must not be strict about for this requirement, besides the assignment's own `lenient_notes`. |
| `concerns` | `code` only | The files (`files`: repo globs) or the projects' roles (`roles`) the requirement is about, so the reviewer reads only those. |
| `feedback` | no | `fixes_allowed` (the default) or `notes_only`: the fixer may only leave notes about this requirement's findings (architecture feedback). This is enforced in code: a `fix` that claims the requirement (or one of its findings) is rejected with "<id> allows notes only", and so is a `fix` that claims nothing in a file only notes-only findings point to; notes and `final_newline` edits are not affected. The rejection goes back to the fixer like any other, and the course rule `notes-only-requirements` names the failing notes-only requirements in the fixer's prompt. |

Unknown fields are rejected, so a typo such as `chek` is reported instead of being silently ignored.

## Probe fields (for `http` requirements)

| Field | Required | Meaning |
| --- | --- | --- |
| `method` | yes | `GET`, `POST`, `PUT`, `PATCH`, `DELETE`, `HEAD` or `OPTIONS` (uppercase). |
| `path` | yes | Starts with `/`, for example `/api/products/1`. |
| `path_alternatives` | no | Other paths a correct solution may use, each starting with `/`. The task does not fix route names, so one student writes `/api/users` and another `/api/user`. The checker reads the routes declared in the student's code and sends the probe to the first of `path`, then `path_alternatives`, that the code declares for the probe's method. If none is declared, the probe is not sent and the requirement is "not evaluated" (reason `route_not_found`), with the student's routes shown to the reviewer. |
| `body` | no | The request body: either written as YAML fields (converted to JSON) or as JSON text in quotes. It says what the request *means*; the checker adapts it to each student's own model (see "Bodies and student models" below). |
| `expect_json` | no | The shape the JSON response must have: `object` or `array_of_objects`. A list of plain values (such as the template's `"value1", "value2"`) where objects are expected is reported with its own message. |
| `expect_values` | no | Values that must appear anywhere in the JSON response, for example `["probe.user@example.com"]`; they may use `{name}`. Text is compared ignoring upper and lower case and surrounding spaces, numbers as their text. |
| `capture` | no | A value to take from the response for later probes: `capture: {name: userId, from: body:id}` (a top-level field of the JSON response, upper and lower case ignored; see "Which field a capture takes" below) or `from: location` (the last part of the `Location` header). Later probes use it as `{userId}` in their `path`, `path_alternatives` or body text. |
| `expected_status` | yes | The status code a correct solution returns, 100–599. |
| `allowed_statuses` | no | All status codes that count as correct. Must include `expected_status`. If omitted, only `expected_status` counts. |
| `notes` | no | A note for the reviewer. |

## One example per kind

**structure**: a file or folder must exist. Checked without running the project.

```yaml
  - id: hw01-r1
    kind: structure
    title: ProductsController exists under Controllers/
    severity: major
    check: file_exists
    params:
      glob: "**/Controllers/ProductsController.cs"
```

A folder can be required the same way:

```yaml
  - id: hw01-r5
    kind: structure
    title: A Models folder exists
    severity: minor
    check: folder_exists
    params:
      path: Models
```

The available checks:

| Check | Params | Passes when |
| --- | --- | --- |
| `file_exists` | `glob` (required), `min_count` (optional, default 1) | At least `min_count` files match `glob`. In `glob`, `**/` means "in any folder", `*` matches part of a name, and `?` one character. |
| `folder_exists` | `path` (required), for example `Controllers` or `src/Models` | A folder with that path exists, at any depth (projects often sit in a subfolder). |
| `project_count` | `min` (required, 1 or more) | At least `min` projects (`.csproj` files) exist, at any depth. |

These checks ignore upper and lower case (`controllers/` matches `Controllers`), skip `bin/`, `obj/` and `.git/`, and only read the files; nothing from the student's repo is run. `glob` and `path` are relative to the repository root, with forward slashes, and may not contain `..`.

**code**: reviewed by reading the code (the code reviewer, stage 8b). Evidence is required for every finding. Until the reviewer is enabled, a round marks each `code` requirement "not evaluated" with the reason `awaiting_reviewer` ("checked by the code reviewer, which isn't enabled yet"): never a failure, and never sent to the fixer.

```yaml
  - id: hw01-r2
    kind: code
    title: GET by id returns NotFound when the product is missing
    severity: major
    description: The action should not return 200 with null.
    allowed_alternatives:
      - Returning 204 NoContent is acceptable
    reviewer_guidance: Look at GET by id; a missing product returns NotFound.
    concerns:
      files: ["**/Controllers/*.cs"]
```

**http**: requests to the running app.

```yaml
  - id: hw01-r3
    kind: http
    title: POST /api/products creates a product
    severity: critical
    probes:
      - method: POST
        path: /api/products
        body:                       # written as YAML; sent as {"name":"Pen","price":3.5}
          name: Pen
          price: 3.5
        expected_status: 201
        allowed_statuses: [201, 200]
        notes: 200 is accepted at this stage
      - method: GET
        path: /api/products
        expected_status: 200
```

The same body written as JSON text: `body: '{"name": "Pen", "price": 3.5}'` (use single quotes around it).

When route names are up to the student, list the other names as `path_alternatives`. Upper and lower case do not matter (`/api/Users/Login` matches a route `api/users/login`), and a value such as `3` matches a route parameter such as `{id}` or `{id:int}`:

```yaml
  - id: hw01-r6
    kind: http
    title: Login with a registered user
    severity: major
    probes:
      - method: POST
        path: /api/users/login
        path_alternatives: [/api/user/login, /api/users/signin, /api/user/signin]
        body:
          userName: newuser
          password: pw123
        expected_status: 200
```

**Chained probes.** Ids differ between students, because each data file holds different users. To create a user and then read it back, update it, log in with it and delete it, capture the id from the create response and use it as `{name}`. Probes run in the order they are written, across requirements:

```yaml
  - id: hw01-r3
    kind: http
    title: POST creates a user
    severity: critical
    probes:
      - method: POST
        path: /api/users
        path_alternatives: [/api/user]
        body: {userName: newuser, password: pw123}
        expected_status: 201
        allowed_statuses: [201, 200]
        capture: {name: userId, from: body:id}
  - id: hw01-r4
    kind: http
    title: The created user can be read back
    severity: major
    probes:
      - method: GET
        path: /api/users/{userId}
        path_alternatives: ["/api/user/{userId}"]
        expected_status: 200
  - id: hw01-r5
    kind: http
    title: Login with the created user
    severity: major
    probes:
      - method: POST
        path: /api/users/login
        path_alternatives: [/api/user/login]
        body: {"userName|email": newuser, password: pw123}
        expected_status: 200
```

A `{name}` must be captured by an earlier probe of the same file, or the file is rejected. Inside `[...]` (and at the very start of a value) YAML reads `{` as the start of a mapping, so quote such paths there: `["/api/user/{userId}"]`; a plain `path: /api/users/{userId}` needs no quotes. If a capture fails (the response has no JSON, no such field, or no `Location`), the probes that need it are not sent, and their requirements are "not evaluated" (reason `dependency_failed`), never failed; the probe that could not capture is judged on its own. A captured value is text: in a body, a field the student's model declares as a number receives it as a number.

**Checking what a response contains.** A status alone does not show that the data is right, so a probe can also check the response: `expect_json` its shape, `expect_values` values that must appear in it. A probe passes only when both its status and its content are right; a content problem is reported in the same finding, naming the shape or the missing values. Content that cannot be judged is never a finding: a response cut at the size limit, an empty list where objects are expected, or no JSON body on an accepted error status.

**Which field a capture takes.** Students name the id of a new record differently, so `from: body:id` looks for it in this order:

1. a field named exactly `id`;
2. else a field named `id` in other letter case (`Id`, `ID`);
3. else the one field whose name ends with `id`, ignoring case, `_`, `-` and digits: `userId`, `UserID`, `user_id` or `productId`.

If step 2 or 3 finds several fields (for example both `userId` and `roleId`), nothing is guessed: the capture fails as "ambiguous", the reviewer sees the candidates, and the probes that need the value are "not evaluated" (`dependency_failed`). The same order applies to any `body:<field>`.

**Bodies and student models.** Students define their own classes for request bodies, so the checker reads the model of the action a probe reaches and adapts the body to it:

- Each body key is matched to a field of the model, upper and lower case ignored. A value is converted when that is safe (`"12345"` becomes `12345` for an `int` field).
- A key may list alternatives, `"userName|email"` (quote it in YAML): the value goes to every listed field the model has. It is a mismatch only when none of them exists.
- When no field has a key's name, the one field whose name contains it is used, ignoring case, `_`, `-` and digits: `password` matches `LogInPassword`, `l_password` or `logpassword`. When several fields contain it (`name` in `FirstName` and `LastName`), nothing is guessed.
- Required fields the body does not mention get placeholder values; an e-mail field gets an e-mail address.
- A failing probe whose body had a key that fits no field (or a value that could not be converted) is "not evaluated" (reason `body_mismatch`), with the details shown to the reviewer.
- An unconvertible value is replaced with a type-appropriate placeholder before sending. For example, an alphanumeric password for an `int` field becomes `1`: a failing probe is `body_mismatch`, but a passing probe still passes with that placeholder, not the intended password.
- The model is looked up in every project of the solution, since a student may keep it in a separate project (for example `Entities`). When several projects declare the name, the one the controller's `using` lines point to is used (or a `global using`, the controller's own namespace, or a namespace written in the parameter's type); otherwise the model is ambiguous and the body is sent as written.

**When a request fails.** For a failed request answered with a 4xx or 5xx status, the start of the response body (up to 1,000 characters, cut after a whole word or line) is kept with the finding: the reviewer sees it in the report, and the fixer sees it as data. For a validation error it names the failing field.

**Windows paths.** A path such as `"..\data\users.txt"` works on Windows, but in the Linux container `\` is part of a file name, so the request answers 500. When a request fails with a 5xx and the app's run log shows a `FileNotFoundException` or `DirectoryNotFoundException` for a path containing `\`, the requirement is "not evaluated" (reason `windows_path`, with the path from the log), and so are the requirements that need it: probes left unsent for a value it should have returned, and requirements whose `depends_on` reaches it and that failed. The fixer may add one note about it, as the course rule `windows-paths` says.

**build**: The project builds and starts without crashing. No other fields are needed.

```yaml
  - id: hw01-r4
    kind: build
    title: The project builds and starts without crashing
    severity: critical
```

**custom**: a check for something the kinds above cannot express. It is written like a `structure` requirement, with `kind: custom` and the check's name in `check`. The custom checks are the C# naming conventions below (`_general` uses all six, as minor requirements), and the repository and client-file checks after them.

```yaml
  - id: gen-naming-properties
    kind: custom
    title: Public properties use PascalCase
    severity: minor
    check: pascal_case_properties
```

| Check | Passes when |
| --- | --- |
| `pascal_case_types` | Every class, record, struct, interface and enum name starts with a capital letter and has no `_`. |
| `pascal_case_properties` | Every public property name starts with a capital letter and has no `_`. |
| `pascal_case_methods` | Every method name starts with a capital letter. `Main` and functions in the top-level statements of `Program.cs` are skipped. |
| `camel_case_locals` | Every local variable starts with a lowercase letter or `_`. Constants (`const`) are skipped. |
| `file_matches_type` | A `.cs` file that declares exactly one type is named after it, in PascalCase (`User.cs` for `class User`). `Program.cs` and files with several types are skipped. |
| `controller_suffix` | Every class that derives from `ControllerBase` or `Controller` ends with `Controller`. |

A name in capitals only, such as `ID` or `URL`, counts as PascalCase. Each check gives one finding with up to five examples (the name, its file and line, and a suggested name such as `email` → `Email`) and the total. The checks read the code as text and never run it, and they never report names a student cannot choose: `override` members, explicit interface implementations, constructors and operators, names inside comments or strings, and generated files (`bin/`, `obj/`, `Properties/`, `Migrations/`, `*.Designer.cs`, `*.g.cs`, files marked `<auto-generated>`). They need the project to be prepared for the run; when it was not, they are "not evaluated".

### Repository checks (stage 17, in `_general`)

These read the checked commit as the fetch step leaves it: the files git tracks and git's own answer about `.gitignore`, never files made during the run. Each gives one finding with up to five examples and the total.

```yaml
  - id: gen-gitignore
    kind: structure
    title: The repository has a .gitignore that leaves out build and IDE files
    severity: minor
    check: gitignore_covers
    params:
      patterns: [bin/, obj/, .vs/, "*.user"]
      how_to_add: "in Visual Studio: Git > Settings, or `dotnet new gitignore`"
```

| Check | Params | Finding when |
| --- | --- | --- |
| `gitignore_covers` | `patterns` (required): `.gitignore`-style names, a folder as `bin/` or a file name pattern as `*.user` (no other `/`, no `!`); `how_to_add` (optional): a hint shown in parentheses after "Add a `.gitignore`" | There is no `.gitignore` at the repository root or in a folder that holds (or contains) a `.sln`, `.slnx` or `.csproj`: one finding, "no `.gitignore`". Otherwise: a tracked file matches a pattern and no `.gitignore` leaves it out. Git decides what is left out (only the repo's `.gitignore` files count, at any depth; the machine's global excludes do not). A pattern nothing in the repo matches passes. |
| `not_committed` | `patterns` (required), as above | A tracked file matches a pattern, even if `.gitignore` lists it now (it was committed before). The finding names the folders and files and says to remove them with `git rm -r --cached`. |
| `no_absolute_paths` | `suffixes` (required): the source files to read, such as `[.cs, .js]` | A string literal is a full disk path: it starts with `C:\`, `D:/` (any drive), `/home/<name>/…` or `/Users/<name>/…`. The finding shows the file and line, with the path masked after the drive or first folder (`D:\…`). Comments are skipped; a path inside a longer text (such as a connection string) is not reported. |

Upper and lower case are ignored when matching `patterns` (`Bin/` matches `bin/`), as on the students' Windows machines. Without git's state (the clone could not be read) `gitignore_covers` and `not_committed` are "not evaluated".

### A data file the repo lacks (stage 22, in `hw01`)

When the code opens a data file by a hard-coded path and the file is not in the repo (the student has it on their own machine only), the run creates it **empty**, in its own copy only, so the app can start and the `http` requirements are evaluated (developer decision 2026-11-11). The student's working copy and repo are never changed, and the fixer never adds the file. This check reads the run's record and gives the student a note:

```yaml
  - id: hw01-data-file-in-repo
    kind: custom
    title: The data file the code reads is in the repo
    severity: minor
    feedback: notes_only
    check: data_files_in_repo
```

| Check | Params | Finding when |
| --- | --- | --- |
| `data_files_in_repo` | none | The run created an empty data file because the repo lacks it: one finding per file (however many literals name it), naming its place in the repo: "`Repository/users.txt` isn't in the repo, so the app fails until it is created. Add the data file to the repo, or create it when it's missing." Without a run, or when the project was not prepared, it is "not evaluated". |

Which paths count: an absolute path (`D:\…\Repository\users.txt`) that matches no file of the repo, and a relative one whose folder is missing too. The file is created where the path's trailing part points (under `Repository/` here), or in the Web project's folder when that is ambiguous. A relative path whose folder exists is left alone: the app may create the file itself, as it would on Windows. If an empty file still makes the app fail (for example, JSON read from an empty file), the `http` requirements report what they find.

### Client-file checks (stage 17, in `hw01`)

These read the pages and scripts under a client folder and never run them. A finding means the page really breaks; whatever cannot be told from the files (a value built at run time, a URL that starts with a variable) is skipped, never reported.

```yaml
  - id: hw01-client-file-refs
    kind: custom
    title: Every script, stylesheet and image a page refers to exists, with the same letter case
    severity: major
    check: client_file_refs
    params:
      root: wwwroot
```

All three take one param, `root` (required): the client folder, such as `wwwroot`, found at any depth with upper and lower case ignored. Each gives one finding with up to five examples (with file and line) and the total.

| Check | Finding when |
| --- | --- |
| `client_file_refs` | A local `<script src>`, `<link rel="stylesheet" href>` or `<img src>` in a `.html` page names a file that does not exist. Letter case counts, as on a Linux server: a reference that differs only in case is reported with the real name. Skipped: external URLs (`https:`, `//`, `data:`), templates (`{{`, `${`, `@`), references without a file extension (an API call), and pages with `<base href>`. |
| `client_element_ids` | A script looks up a literal id, `getElementById("x")`, `querySelector("#x")` or `querySelectorAll("#x")`, that no page loading the script declares. Inline `<script>` blocks are checked against their page. Skipped: ids built at run time; a script no page loads; an id whose text appears in another string of the page's scripts (`el.id = "x"`, `'<div id="x">'`); and every lookup of a page whose scripts write HTML (`innerHTML`, `insertAdjacentHTML`, `document.write`) or set an id from a non-literal value. |
| `client_fetch_routes` | A `fetch` URL's path matches no route the API declares for its method (the same route table and matching as `http` probes: case ignored, `{id}` matches one part). The URL may be a string, a template literal or a `+` concatenation; each `${…}` or variable part counts as a route parameter. The method is the literal `method:` of an options object, GET when there is none. A full URL with `localhost` is matched by its path. Skipped: a URL that starts with a variable, another host, a parameter inside a path part (`user${id}`), a path with a file extension (a static file), a relative URL in a script loaded from a page in a subfolder, and a method that is not a literal. Without the route table (the project was not prepared, or it uses routing the table cannot read, such as `app.MapGet`) it is "not evaluated". |

JavaScript is read with regexes over the code, with comments and strings masked, not with a full parser; event attributes (`onclick="…"`), jQuery, `XMLHttpRequest` and `axios` are not read.

### Architecture-evidence checks (stage 20)

These read the repo's local architecture evidence (stage 19): the `.csproj` inventory and project references, and C# signals (interface declarations, constructors and their interface parameters, service registrations) read lexically, never compiled or run. A signal is a candidate and a missing one is not proof, so these checks only count signals against thresholds that are the assignment's data. The evidence is read at most once per assignment evaluation, and only when a selected requirement or condition uses a check that reads it (`di_started` and the layer checks below); when it cannot be read, or the coverage a check needs is unavailable, those checks are "not evaluated" (`evidence_unavailable`: the report says the repo's projects or C# code could not be read).

```yaml
applies_when:
  check: di_started
  params:
    min_registrations: 1           # registration signals (AddScoped, AddSingleton, ...)
    min_interface_constructors: 1  # constructors with a parameter typed by a declared interface
```

| Check | Params | Passes when |
| --- | --- | --- |
| `di_started` | `min_registrations`, `min_interface_constructors` (both required, 0 or more) | Both counts are reached. It passes saying what it counted ("DI has started (3 registrations, 2 constructors taking an interface)."); otherwise its finding gives both counts and thresholds. When the C# signals are unavailable (no C# source could be read) it is "not evaluated". Meant as the condition of `applies_when` / `skip_when`. |

### Layer and DI checks (stage 21, in `hw02` and `hw03`)

These are generic: the code holds the mechanism, and each requirement's params say which layers, patterns and pairs count (ADR 0023). They read the same evidence, plus three more signals of the reader: each class or record with its base list, every constructor parameter, and each `new T(…)` / `new T { … }` with its enclosing type.

**Roles.** Every layer check takes `roles`: a role name (lowercase, such as `services`) and how its project is recognized.

```yaml
roles:
  api:          {sdk: Microsoft.NET.Sdk.Web, name: "api|web"}
  entities:     {name: "entit|model|dto|domain"}
  services:     {name: "servic|business|\\bbl\\b|logic"}
  repositories: {name: "repositor|\\bdal\\b|data|access"}
```

- `sdk` is an SDK the `.csproj` names; it wins over `name`.
- `name` is a regular expression searched in the project file's name without `.csproj` (`Shop.Services`), ignoring case. In double quotes, write `\\b` for the regex `\b`.
- A project matching no role, or more than one (by SDK, or else by name), is **unassigned**: it is listed, never guessed.
- A file belongs to the project whose folder is its nearest parent. A file in no project, or in an unassigned one, is not judged.
- Every role a check's other params name must be defined in `roles`.

**Write the roles once per file.** Define them with a YAML anchor at their first use and reuse them with an alias. hw02 and hw03 each do this, with the same roles:

```yaml
    params:
      roles: &roles            # the first requirement: define them
        api: {sdk: Microsoft.NET.Sdk.Web, name: "api|web"}
        ...
  - id: hw02-references
    params:
      roles: *roles            # every later requirement: reuse them
```

**Results.** A finding gives up to five examples and the total. A check is "not evaluated" when the coverage it reads is unavailable (`evidence_unavailable`), or when a role it names has no project (`dependency_failed`: the `layer_projects` requirement reports that, and the other requirements `depends_on` it). Type names are matched by their simple name (`Data.IUserService<T>` is `IUserService`). A name declared more than once (two classes, or a class and an interface; the parts of one partial class count once) is never guessed: it is left out, and the finding says "Not checked, because the name is declared more than once".

| Check | Params (besides `roles`) | Finding when |
| --- | --- | --- |
| `layer_projects` | `required`: roles | A required role has no project. It names the missing roles, the projects found and the unassigned ones. It reads the project inventory only. |
| `layer_references` | `required`, `forbidden`: `[source, target]` role pairs; a forbidden target may be `*` (any other role). `transitive_required` (true/false, default false): a required pair may go through other projects | No project of a required pair's source references a project of its target (with `transitive_required: true`, also not through other projects, as SDK-style references flow through: Services → Repositories → Entities satisfies services → entities; the finding then says "directly or through another layer"), or a project references one of a forbidden pair **directly** (never through others, or api → repositories would always be found through Services). It says which project references which ("`Api` references `Repositories` (api → repositories is not allowed)"). A reference counts when it names a project of the repo, also with other letter case (the build and file-reference checks own the letter case). It reads the project inventory only. hw02 and hw03 set `transitive_required: true`. |
| `code_only_in_layer` | `patterns`: C# regular expressions; `allowed`: roles | A pattern matches, on code with comments and strings masked, in a `.cs` file of a project whose role is not allowed. It gives the match, file and line. |
| `implements_interface` | `role`; `exclude`: class name patterns (searched, case-sensitive) | A public class of the role's projects implements no interface declared in the repo, directly or through a base class declared in the repo. Static classes (which cannot) and records are not read. |
| `constructor_takes_interface` | `consumer`, `dependency`: roles | A constructor in a consumer project takes a class of the dependency role that implements an interface: "`UsersController` takes `UserService`; take `IUserService`". A class with no interface is left to `implements_interface`. |
| `interfaces_registered` | `consumers`: roles | An interface declared in the repo that a constructor in a consumer project takes has no registration (`AddScoped`, `AddTransient`, `AddSingleton`, either form) with it as the service type. A registration whose types are not written literally (a factory, a key) names no type; the finding then says how many there are, to check by hand. |
| `no_concrete_construction` | `in`, `of`: roles | A class in an `in` project creates a class of an `of` role with `new`. Creating entities, DTOs and lists is fine, and so is `new` in top-level statements (`Program.cs`, where a factory registration may create a class). |

**HW02 and HW03.** `hw02` has `skip_when` and `hw03` has `applies_when`, both `di_started` with `min_registrations: 0` and `min_interface_constructors: 1`: a repo is on the HW02 path until a constructor takes an interface declared in the repo, then on the HW03 path. HW03 repeats HW02's two layer checks. Every requirement is `major` and `feedback: notes_only`, and every one but the layer projects `depends_on` it, so a repo still in one project gets one finding. The draft's two interface requirements of HW03 each have two conditions, so each is two requirements (`hw03-service-interfaces` and `hw03-service-interfaces-taken`, the same for repositories).

The notes come from `hw02/review_rules.yaml` and `hw03/review_rules.yaml`: one `note_only` rule per group of requirements, with its note as the example (hw02's layer notes also name hw03's repeated layer requirements in `when_found`). hw02's `no-new-structure` rule (never add an interface, a project, a constructor parameter or a registration; ask instead) applies to hw03 too and is enforced in code: an added code line (not a `//` comment) with `AddScoped`, `AddTransient`, `AddSingleton`, `interface I` or `ProjectReference` is rejected, even in a `fix` that claims nothing.

**How results are decided.** A `build` requirement gives a finding only when the build failed because of the code, or when the project built but crashed on startup; a build or run that failed because of the environment, or timed out, is not evaluated. An `http` requirement is judged only when the app built and started; a status listed in `allowed_statuses` always counts as correct. A requirement that could not be checked, for example every `http` requirement when the build failed, is shown as "not evaluated" with the reason, and never counts against the student.

## YAML tips

- Indentation is with spaces, never tabs. Items under `requirements:` start with `  - `.
- Put text in quotes when it contains `:`, `#`, or starts with `*`, `{` or `[`.
- A list can be written on one line, `[201, 200]`, or one item per line with `- `.

## Error messages

If anything is wrong, the round does not start, and **all** problems are listed together, each naming the file, and the requirement and field where relevant. For example:

```
- assignments/hw01/requirements.yaml: requirement 'hw01-r2', field 'chek': unknown key; check the spelling.
```

| Message | What to do |
| --- | --- |
| `unknown key; check the spelling` | The field name is misspelled or not part of the format. Compare it with the tables above. |
| `this required field is missing` | Add the named field. |
| `id 'x' does not match the folder name 'y'` | Rename the folder or change the `id` so they match. |
| `use the same order N` | Two assignments have the same `order`; give each its own number. |
| `use the same requirement id 'x'` | Requirement ids must be unique across all assignments; rename one. |
| `_general is missing` | Create `_general/requirements.yaml` with `order: 0`. |
| `_general must have order: 0` / `order must be 1 or more` | `_general` is order 0; every other assignment is 1 or more. |
| `not valid YAML (line L, column C …)` | Usually indentation or a missing quote near that line. |
| `the file is not UTF-8 text` | Save the file again with UTF-8 encoding. |
| `an http requirement needs at least one probe` | Add a `probes` list, or change the `kind`. |
| `a … requirement needs a check` / `must not have a check` | `structure` and `custom` need `check`; the other kinds must not have one. |
| `params are only allowed together with a check` | Remove `params` or add `check`. |
| `give applies_when or skip_when, not both` | An assignment has one condition; keep the one you mean. |
| `applies_when, check 'x': unknown check` / `param '…'` | The condition names a registered check with valid params, like a requirement. |
| `must include expected_status` | Add `expected_status` to `allowed_statuses`. |
| `String should match pattern '^/'` (field `path`) | The probe path must start with `/`. |
| `Input should be 'minor', 'major' or 'critical'` and similar | The value must be one of the listed options. |

## Course policy and review rules (ADR 0023)

Requirements say *what is checked*. How the review is written, what the fixer may change and what it must never mention is also data, never code: the course profile in `course/` and a `review_rules.yaml` in `course/` and in any assignment folder. The fixer's prompts are fixed frames that these files fill. Until the authoring flow (stage 16) drafts them, you write them by hand; the commit is the approval.

```
course/
├── course.yaml          the course profile (required)
├── review_rules.yaml    rules for every assignment (optional)
├── schemas/             JSON Schemas generated from the models; never edit by hand
└── prompts/             optional: a course's own prompt frame, used instead of the package's
assignments/hw01/
├── requirements.yaml
└── review_rules.yaml    rules for hw01 and later assignments (optional)
```

VS Code validates the files as you type when the YAML extension is installed: the first line of each file names its schema (`# yaml-language-server: $schema=...`). The same rules are checked when the catalog loads, and every problem is listed together, like requirement problems.

### The course profile (`course/course.yaml`)

| Field | Meaning |
| --- | --- |
| `audience` | Whose homework it is, as the prompts say it: `a second-year student`. |
| `level` | The students' level: `second-year`. |
| `stack` | The technology: `ASP.NET Core Web API`. |
| `student_wording` | How every student-facing text is written (plain English, "The assignment asks…", never the lecturer). |
| `taught_so_far` | What `{taught_so_far}` in a rule becomes when the assignment lists `taught_techniques`; `{techniques}` is the list. Nothing when no technique is listed. |
| `comment_style` | By file suffix, in the order the prompts and the PR list them: `language` (`C#`, `JavaScript`, `HTML`, `CSS`: how the PR text names the type), `line` (what starts a comment, `//`), `fix_marker` (`//fix:`, starting with `line`) and an optional `end` (for example `-->`). A file the fixer is shown is edited only when its suffix is here, besides a data file's final newline. The plan lists the syntax and marker of every type it shows, and only those. |
| `pr_format.opening` | How the PR body starts (`Hi! `); added when the writer's opening lacks it. `opening_intro` tells the writer what to put in it. |
| `pr_format.sections` | The body's sections, in order: each of `fixed`, `for_you`, `names` and `files` exactly once, with its bold `heading`. A heading may use `{languages}` (the languages of the section's edited files, in `comment_style` order, joined with "and": `Names ({languages} conventions)` gives `Names (C# conventions)` or `Names (C# and JavaScript conventions)`) and `{fix_markers}` (their fix markers without repeats: `` `//fix:` `` or `` `//fix:` and `<!--fix: … -->` ``). The writer's three sections also need a `label` (their name in the writer's prompt) and an `intro` (what to write there). |
| `pr_format.item_rule` | How the writer writes every item. |
| `pr_format.title_rule` | `strong` and `normal` titles with `{short_name}` (`HW1`). The code chooses: `strong` when the verified fix branch clears a major or critical requirement that failed before. |
| `pr_format.commit_message` | The review commit's message, with `{short_name}`. |

### Review rules (`review_rules.yaml`)

```yaml
rules:
  - id: put-returns-ok              # unique across the course and all assignments
    applies_to: [fixer, judge]      # the roles whose prompts show it
    kind: note_only
    text: >-
      PUT returns `Ok()`: the assignment asks for it. ...
    after: no-file-exists           # optional: placed right after this earlier rule
    approved_notes:                 # optional: a note in this wording is never a violation
      - words: The assignment asks PUT to return Ok()
        what: the PUT note in the approved wording
    never_a_violation: >-           # optional: the judge's line for what this rule allows
      The PUT note in the approved wording (...).
    origin: {source: developer, quote: "the lecturer, 2026-10-28"}
    status: approved
examples:
  - id: break-and-naming
    applies_to: [fixer, judge]
    situation: An invented repo, in one paragraph.
    plan: |-
      {"edits": [...], "files_notes": []}
    origin: {source: developer, quote: "the approved PRs"}
    status: approved
```

| Field | Meaning |
| --- | --- |
| `id` | Unique among the rules (or among the examples) of the course and every assignment. |
| `applies_to` | One or more of `fixer` (plans the edits), `judge` (the self-review), `writer` (the PR text) and `reviewer` (the LLM review, later). Each prompt shows only its role's rules. |
| `kind` | `fix_allowed` (may be changed), `note_only` (only a comment), `never_do` (never added or changed), `never_mention` (not even commented on), `style` (how text is written). |
| `text` | The rule as the prompts number it. It may use `{short_name}`, `{taught_so_far}`, `{rule:<id>}` (that rule's number in the role's list) and `{notes_only}` (the failing requirements with `feedback: notes_only`, as `` `id` `` items; a rule that uses it is left out of a prompt when none fails); any other braces stay as written. |
| `files` | Optional (rules and examples): the file types (suffixes, `[.html, .js]`) the item is limited to. It is in a prompt only when the prompt shows a file of one of them, and its `forbidden_text`, `forbidden_change`, detectors and approved notes apply only to edits in those files. Without it, every file type. |
| `note_section` | Optional on `note_only` rules: `names` or `for_you`, the PR section for the rule's notes. The plan lists naming-convention languages only for shown file types covered by a `names` rule. |
| `when_found` | Optional (rules and examples): requirement ids. The item is in a prompt only when one of them has a finding (the course's `repository-files` rule). |
| `wordings` | Optional: `files` and `text`. In a prompt whose edited file types (those with a `comment_style`) are all in a wording's `files`, the first such wording replaces `text` (the `comments` rule keeps its `//` wording for C#-only prompts). |
| `shown_in` | `rules` (default: numbered) or `fix_kind`: shown with the `fix` edit kind in the plan, not numbered; for a rule that applies to the `fixer` only (`property-declarations`). |
| `detectors` | `fix_allowed` only: the safe fixes the judge never reports. Registered detectors: `missing_break`, `id_assignment`, `readonly_field`, `stray_using`, `controller_suffix`, `final_newline`, `client_reference` (one `src`, `href` or `id` value, or the id a script looks up), `assignment_in_condition` (`=` to `===` in an `if` condition, nothing else). A fix of a detector's shape that the detector still refuses (a `using` commented out instead of deleted, a reference no finding names) is rejected before it is applied. A new kind of safe fix needs a detector in code; choosing which ones apply is data. |
| `named_by` | With `detectors`: requirement ids whose findings must name what the fix changes (`client_reference`); without such a finding the fix is rejected. |
| `approved_notes` | `words` (compared ignoring case) and `what` (for the repo report): a note containing the words is never a violation. |
| `forbidden_text` | Text an edit must never add: `text` (literal) or `pattern` (a regular expression), `message` (why; fed back to the fixer), optional `ignore_case`, and `new_only` (reject only an edit that adds an occurrence). |
| `forbidden_change` | Text a fix must never remove or change, checked on the lines it removes, in the same shape as `forbidden_text` (the `{ get; set; }` guard). |
| `never_a_violation`, `clarification` | For the judge: one line in its "never a violation" list, and one paragraph on how to read the rule. |
| `example` | Optional: one edit that follows the rule (`file`, `find`, `replace`), shown after the examples. |
| `until` | An assignment id: the rule stops after that assignment. |
| `replaces` | An earlier rule's id: this rule takes its place (and its number). |
| `after` | An earlier rule's id: this rule is placed right after it. Without `after` or `replaces`, a rule follows the course's rules and the earlier assignments' rules. |
| `origin` | `source` (`developer`, `task_text`, `intake` or `inferred`) and `quote`: where the rule comes from. |
| `status` | `approved`, or `draft`: a round refuses to start while a rule or example of the course or of a checked assignment is a draft, and lists them. |

An example (under `examples:`) has the same `id`, `applies_to`, `files`, `when_found`, `until`, `replaces`, `after`, `origin` and `status`, plus `situation` (the invented repo) and `plan` (the plan's JSON, shown exactly as written).

**Inheritance.** Checking assignment N uses the course's rules, then the rules of assignments 1..N by `order`, the same way requirements regress. hw01's rules (no `File.Exists`, PUT returns `Ok()`) apply to hw02 too, unless hw02 has a rule that `replaces` one of them, or the hw01 rule has `until: hw01`, as `not-yet-taught` (no GET all, layers or `Models` folder) does: layers and DI are taught in hw02 and hw03.

| Message | What to do |
| --- | --- |
| `the id is used again (first in …)` | Rule and example ids are unique across the course and every assignment; rename one. |
| `field 'until': unknown assignment 'x'` | `until` names an assignment folder's id. |
| `field 'replaces'` / `'after': no earlier rule 'x'` | Name a rule of the course, of an earlier assignment, or earlier in the same file. |
| `{rule:x} names an unknown rule` | Fix the id inside the braces. |
| `Input should be 'reviewer', 'fixer', 'judge' or 'writer'` and similar | `applies_to`, `kind`, `detectors`, `status` and section ids take only the listed values. |
| `every section once: missing …` | `pr_format.sections` lists `fixed`, `for_you`, `names` and `files`, each once. |
| `these items are drafts; approve them first` | Review each listed item and set `status: approved`. |
