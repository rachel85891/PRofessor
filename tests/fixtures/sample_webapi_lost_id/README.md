# Sample Web API that loses the id on update (test fixture)

A variant of `../sample_webapi/` for the fixer's integration test (stage 14). Written from
scratch for the tests; it contains no student code, names or data.

- `PUT api/users/{id}` replaces the user without keeping its id, so the updated user is
  saved with id 0 and cannot be read back by its id: `hw01-update-read-back` fails. Keeping
  the id (`user.Id = id;`) is one of the small safe fixes of docs/fix-pr-workflow.md.
- `POST` names a local `all_users`, which is not camelCase: a naming note, never a rename.
