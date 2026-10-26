# Sample Web API whose ids are called userId (test fixture)

A variant for lenient capture (stage 5h). Written from scratch for the tests; it contains
no student code.

- The project sits one folder deep, in `UsersApi/` (net10.0), with users kept as JSON lines
  in a relative `users.txt`.
- The model's key is `UserId`, so `POST /api/users` answers `{"userId": n, ...}`. The hw01
  capture `body:id` still finds the new id, and the chained read-back, update and delete
  work.
