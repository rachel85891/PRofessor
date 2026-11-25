# Sample Web API that reads its connection from the environment (test fixture)

A minimal Web API that reads `GetConnectionString("Main")`, with no `appsettings.json`
(the connection's value is not in the repo, as in most student repos), and answers
`GET /api/dbname` with `SELECT DB_NAME()`. For the runner's database tests (stage
HW05-DB-2b): the runner gives the app `ConnectionStrings__Main`. Written from scratch for
the tests; it contains no student code. It needs network access to restore the
`Microsoft.Data.SqlClient` package.
