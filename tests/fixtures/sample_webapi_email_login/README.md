# Sample Web API that logs in by e-mail (test fixture)

A variant for probe field alternatives (stage 5g). Written from scratch for the tests; it
contains no student code.

- The project sits at the repo root (net10.0), with routes under `/api/members`.
- `Member` and `LoginRequest` have `Email` and no user name, so a probe body key
  `userName|email` sends its value to `Email`.
