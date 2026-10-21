# Assignment requirements: file format

Each assignment's requirements live in a YAML file that HW Checker reads at the start of every round. Until the requirements editor screen exists, you write these files by hand. This guide covers everything you need.

## Folders

```
assignments/
├── _general/requirements.yaml   general requirements, checked in every round
├── hw01/requirements.yaml       assignment 1
└── hw02/requirements.yaml       assignment 2 ...
```

- One folder per assignment. The folder name must be exactly the `id` written inside the file.
- The file must be named `requirements.yaml` (not `.yml`). Folders without it, and folders whose name starts with `.`, are skipped and listed in the log, so a misnamed file is easy to spot.
- `_general` must exist and has `order: 0`. Every other assignment has its own `order` of 1 or more.
- Save files as UTF-8 (the default in VS Code). Hebrew text is fine.

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
requirements:                 # the list of requirements (may be empty: [])
  - id: hw01-r1
    ...
```

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
| `params` | no | Named values for the check, for example a file pattern. Only together with `check`. Values may be text, numbers, `true`/`false`, or a list of those. |
| `probes` | `http` only, at least one | HTTP requests to send to the running app (see below). |

Unknown fields are rejected, so a typo such as `chek` is reported instead of being silently ignored.

## Probe fields (for `http` requirements)

| Field | Required | Meaning |
| --- | --- | --- |
| `method` | yes | `GET`, `POST`, `PUT`, `PATCH`, `DELETE`, `HEAD` or `OPTIONS` (uppercase). |
| `path` | yes | Starts with `/`, for example `/api/products/1`. |
| `body` | no | The request body: either written as YAML fields (converted to JSON) or as JSON text in quotes. |
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

Check names and their `params` are defined together with the check registry. `file_exists` is used here as an illustration.

**code**: reviewed by reading the code. Evidence is required for every finding.

```yaml
  - id: hw01-r2
    kind: code
    title: GET by id returns NotFound when the product is missing
    severity: major
    description: The action should not return 200 with null.
    allowed_alternatives:
      - Returning 204 NoContent is acceptable
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

**build**: the project must build. No other fields are needed.

```yaml
  - id: hw01-r4
    kind: build
    title: The project builds without errors
    severity: critical
```

**custom**: a special check that does not fit the other kinds.

```yaml
  - id: hw01-r5
    kind: custom
    title: Controllers are named in English
    severity: minor
    check: controller_names
    params:
      allowed_suffixes: [Controller]
```

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
| `must include expected_status` | Add `expected_status` to `allowed_statuses`. |
| `String should match pattern '^/'` (field `path`) | The probe path must start with `/`. |
| `Input should be 'minor', 'major' or 'critical'` and similar | The value must be one of the listed options. |
