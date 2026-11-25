# Sample Web API that reads its connection from the environment (test fixture)

A minimal Web API that reads `GetConnectionString("Main")`, with no `appsettings.json`
(the connection's value is not in the repo, as in most student repos), and answers
`GET /api/dbname` with `SELECT DB_NAME()`. For the runner's database tests (stage
HW05-DB-2b): the runner gives the app `ConnectionStrings__Main`. Written from scratch for
the tests; it contains no student code. It targets `net10.0` and pins
`Microsoft.Data.SqlClient` to 6.1.7: the version an EF Core 10 SQL Server project pulls in,
and the one already in the runner's NuGet cache, so the restore needs no network access.
