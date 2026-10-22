# Sample Web API with other route names (test fixture)

A variant of `../sample_webapi/` for probe path alternatives (stage 5d). Written from
scratch for the tests; it contains no student code.

- The project sits one folder deep, in `UserApi/` (net10.0, no NuGet packages).
- `UserController` (singular), so its routes are `/api/user...`, not `/api/users...`.
- `[HttpPost ("signin")]`, with a space before the parenthesis, instead of `login`, and
  `[HttpGet("{id}", Name = "GetUser")]`.
- No DELETE action, so a DELETE probe has no declared route and is not sent.
- Users are stored as JSON lines in a relative `users.txt`, like the main fixture.
