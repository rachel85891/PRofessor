# Sample Web API that breaks C# naming conventions (test fixture)

A variant for the naming-convention checks (stage 8c). Written from scratch for the tests; it
contains no student code. It builds and runs, and breaks exactly four conventions:

- `LogUser.email`: a public property not in PascalCase
- `class User : ControllerBase`: a controller without the `Controller` suffix
- `Models/loguser.cs`: a file not named after its type (`LogUser`)
- `var FilePath`: a local variable not in camelCase

Its types and methods are named correctly, so those two checks give nothing.
