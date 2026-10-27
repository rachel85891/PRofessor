# Sample .NET 10 Web API (test fixture)

A controller-based Web API written from scratch to imitate the shape of real second-year
student projects. It contains no student code, names or data.

- Layout like a Visual Studio solution: `WebApi/WebApi.slnx` and the project in
  `WebApi/WebApi/` (net10.0, one NuGet package: `Microsoft.AspNetCore.OpenApi`).
- `UsersController` with GET all, GET by id (404 when missing), POST (201 with
  `CreatedAtAction`), PUT (204), DELETE (204) and `POST login` (200, or 204 `NoContent` for wrong details, as hw01 requires).
- Users are stored in `users.txt` next to the project, one JSON object per line, opened
  with the relative path `"users.txt"` through `File.ReadLines`, `File.AppendAllText` and
  `File.WriteAllLines`, so the app needs its project folder as the working directory and
  write access to it, as `dotnet run` gives it.
- `Program.cs` calls `UseHttpsRedirection()`, as every real project does.

Used by the Docker runner integration tests, the deterministic checks' end-to-end test and
discovery tests. `../sample_webapi_absolute_path/` is the variant with a hard-coded path.
