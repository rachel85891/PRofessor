# Sample Web API whose data file has no final newline (test fixture)

A variant of `../sample_webapi/` for the fixer's integration test (stage 14). Written from
scratch for the tests; it contains no student code, names or data.

- `users.txt` does not end with a newline, so the next `POST` appends its JSON line to the
  last line. From then on every read fails to parse that line, so reading a created user
  back fails: `hw01-read-back` fails. Adding the final newline is one of the small safe
  fixes of docs/fix-pr-workflow.md (a broken data file).
- `POST` names a local `all_users`, which is not camelCase: a naming note, never a rename.
