# Sample Web API with its own body models (test fixture)

A variant for probe body adaptation (stage 5f). Written from scratch for the tests; it
contains no student code.

- The project sits at the repo root (net10.0, `<Nullable>enable</Nullable>`).
- `Account` has `int Password` and a `Name` that `[ApiController]` treats as required, so
  the probe body `{"userName": ..., "password": "..."}` only works once the password is
  converted to a number and `Name` is filled; the runner does both.
- `LoginForm` logs in by `Email`, not by user name, so a login probe body with `userName`
  does not fit it: that field is unmatched, the probe fails, and the requirement is not
  evaluated (`body_mismatch`) instead of becoming a finding.
