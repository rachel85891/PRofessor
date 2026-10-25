# Sample Web API whose POST returns no id (test fixture)

A variant for chained probes (stage 5g). Written from scratch for the tests; it contains
no student code.

- The project sits at the repo root (net10.0).
- `POST /api/users` saves the user and answers `200 OK` with no body and no `Location`
  header, so a probe capturing the new id fails, and the probes that need the id are not
  sent: their requirements are not evaluated (`dependency_failed`), never findings.
