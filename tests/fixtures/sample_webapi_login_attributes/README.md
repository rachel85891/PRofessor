# Sample Web API with descriptive login field names (test fixture)

A variant for lenient name matching (stage 5g). Written from scratch for the tests; it
contains no student code.

- The project sits at the repo root (net10.0), with routes under `/api/customers`.
- `LoginModel` has `[Required, EmailAddress] LogInEmail` and `[Required] LogInPassword`, so
  a login body `{"userName|email": ..., "password": ...}` only fits once `email` is matched
  to `LogInEmail` and `password` to `LogInPassword` by name containment.
