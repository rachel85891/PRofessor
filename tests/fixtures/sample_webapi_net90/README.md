# Sample Web API on net9.0 (test fixture)

A minimal Web API targeting `net9.0`, older than the course's `net10.0`, for the
runner's framework mapping (stage 5f). Written from scratch for the tests; it contains no
student code.

- The project sits at the repo root, with no NuGet packages.
- `ItemsController`: GET all (200) and POST (201), using an in-memory list.
- The runner builds and runs it with the pinned `net9.0` images and marks the
  student to move the project to .NET 10; that is never a finding.
