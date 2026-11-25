# Sample Web API with a hard-coded connection string (test fixture)

A minimal Web API whose connection string is written in a string literal (a made-up
local server and database, as the EF scaffold writes it), and that answers
`GET /api/dbname` with `SELECT DB_NAME()`. For the runner's database tests (stage
HW05-DB-2b): the runner replaces the literal in its temporary copy. Written from scratch
for the tests; it contains no student code. It needs network access to restore the
`Microsoft.Data.SqlClient` package.
