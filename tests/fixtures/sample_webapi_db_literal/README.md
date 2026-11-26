# Sample Web API with a hard-coded connection string (test fixture)

A minimal Web API whose connection string is written in a string literal (a made-up
local server and database, as the EF scaffold writes it), and that answers
`GET /api/dbname` with `SELECT DB_NAME()`. For the runner's database tests (stage
HW05-DB-2b): the runner replaces the literal in its temporary copy. Written from scratch
for the tests; it contains no student code. It targets `net10.0` and pins
`Microsoft.Data.SqlClient` to 6.1.7: the version an EF Core 10 SQL Server project pulls in,
and the one already in the runner's NuGet cache, so the restore needs no network access.
