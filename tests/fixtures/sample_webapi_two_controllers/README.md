# Sample Web API with two user controllers (test fixture)

A variant of `../sample_webapi/` for the primary-controller choice (stage 5e). Written from
scratch for the tests; it contains no student code.

- The project sits at the repo root (net10.0, no NuGet packages).
- `UsersController` (`/api/users`) is the full API: GET, GET by id, POST, PUT and DELETE,
  kept in `users.txt`.
- `UserController` (`/api/user`) is a partial one with its own `user.txt`: PUT, DELETE and
  `POST login`. A user created through `UsersController` does not exist there.
- Probes that name `/api/user...` first would split between the two when resolved one by
  one; the runner sends them all to `UsersController`, which serves the most probes, and
  only the login goes to `UserController`.
