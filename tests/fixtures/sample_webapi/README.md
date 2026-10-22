# Sample .NET 10 Web API (test fixture)

A minimal controller-based Web API at second-year level, imitating student code: one
`ProductsController` with GET all, GET by id (404 when missing) and POST (201), using an
in-memory list. No database. Generated with `dotnet new webapi --use-controllers -f net9.0`,
moved to `net10.0`, and trimmed. Used by the Docker run spike and, later, by integration and golden tests.
