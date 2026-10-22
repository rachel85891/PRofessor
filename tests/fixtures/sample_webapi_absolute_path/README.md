# Sample Web API with a hard-coded absolute path (test fixture)

A variant of `../sample_webapi/` for the runner's absolute-path handling (ADR 0015,
amendment of 2026-10-22). Written from scratch for the tests; it contains no student code.

- The project sits at the repo root (no solution folder), net10.0, no NuGet packages.
- `Controllers/UsersController.cs` reads and appends users through the hard-coded path
  `C:\Users\Developer\source\repos\AbsolutePathApi\Data\users.txt`, which exists only on the
  developer's machine. The project has `Data/users.txt`, so the runner rewrites the literal
  to `Data/users.txt` in its temporary copy.
- The integration tests also delete `Data/users.txt` from a copy, to check that an
  unmatched path is flagged and never becomes a finding.
