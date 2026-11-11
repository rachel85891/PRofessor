"""A synthetic four-layer solution shaped like real course projects (stage 19), shared by the
evidence reader's tests and the layer and DI checks' tests, with helpers for its variants."""

from collections.abc import Mapping
from pathlib import Path

FILES = {
    "Api/Api.csproj": (
        '<Project Sdk="Microsoft.NET.Sdk.Web">\n'
        "  <ItemGroup>\n"
        '    <ProjectReference Include="..\\Services\\Services.csproj" />\n'
        '    <ProjectReference Include="..\\Entities\\Entities.csproj" />\n'
        "  </ItemGroup>\n"
        "</Project>\n"
    ),
    "Services/Services.csproj": (
        '<Project Sdk="Microsoft.NET.Sdk">\n'
        "  <ItemGroup>\n"
        '    <ProjectReference Include="..\\Repositories\\Repositories.csproj" />\n'
        '    <ProjectReference Include="..\\Entities\\Entities.csproj" />\n'
        "  </ItemGroup>\n"
        "</Project>\n"
    ),
    "Repositories/Repositories.csproj": (
        '<Project Sdk="Microsoft.NET.Sdk">\n'
        "  <ItemGroup>\n"
        '    <ProjectReference Include="..\\Entities\\Entities.csproj" />\n'
        "  </ItemGroup>\n"
        "</Project>\n"
    ),
    "Entities/Entities.csproj": '<Project Sdk="Microsoft.NET.Sdk" />\n',
    "Entities/User.cs": (
        "namespace Entities;\n"
        "\n"
        "public class User\n"
        "{\n"
        "    public int Id { get; set; }\n"
        "    public string Name { get; set; } = string.Empty;\n"
        "}\n"
    ),
    "Entities/IClock.cs": (
        "namespace Entities;\n\npublic interface IClock\n{\n    DateTime Now { get; }\n}\n"
    ),
    "Entities/SystemClock.cs": (
        "namespace Entities;\n"
        "\n"
        "public class SystemClock : IClock\n"
        "{\n"
        "    public DateTime Now => DateTime.Now;\n"
        "}\n"
    ),
    "Repositories/IUserRepository.cs": (
        "using Entities;\n"
        "\n"
        "namespace Repositories;\n"
        "\n"
        "public interface IUserRepository\n"
        "{\n"
        "    Task<User?> GetById(int id);\n"
        "}\n"
    ),
    "Repositories/UserRepository.cs": (
        "using Entities;\n"
        "\n"
        "namespace Repositories;\n"
        "\n"
        "public class UserRepository : IUserRepository\n"
        "{\n"
        "    private readonly IClock _clock;\n"
        "\n"
        '    [Obsolete("Use the context overload")]\n'
        "    public UserRepository(IClock clock)\n"
        "    {\n"
        "        _clock = clock;\n"
        "    }\n"
        "\n"
        "    public Task<User?> GetById(int id) => Task.FromResult<User?>(null);\n"
        "}\n"
    ),
    "Services/IUserService.cs": (
        "using Entities;\n"
        "\n"
        "namespace Services;\n"
        "\n"
        "public interface IUserService\n"
        "{\n"
        "    Task<User?> GetById(int id);\n"
        "}\n"
    ),
    "Services/UserService.cs": (
        "using Entities;\n"
        "using Repositories;\n"
        "\n"
        "namespace Services;\n"
        "\n"
        "public class UserService(IUserRepository repository) : IUserService\n"
        "{\n"
        "    public Task<User?> GetById(int id) => repository.GetById(id);\n"
        "}\n"
    ),
    "Api/Controllers/UsersController.cs": (
        "using Entities;\n"
        "using Microsoft.AspNetCore.Mvc;\n"
        "using Services;\n"
        "\n"
        "namespace Api.Controllers;\n"
        "\n"
        "[ApiController]\n"
        '[Route("api/[controller]")]\n'
        "public class UsersController : ControllerBase\n"
        "{\n"
        "    private readonly IUserService _service;\n"
        "\n"
        "    public UsersController(IUserService service)\n"
        "    {\n"
        "        _service = service;\n"
        "    }\n"
        "\n"
        '    [HttpGet("{id}")]\n'
        "    public async Task<ActionResult<User>> Get(int id)\n"
        "    {\n"
        "        var user = await _service.GetById(id);\n"
        "        return user is null ? NotFound() : Ok(user);\n"
        "    }\n"
        "}\n"
    ),
    "Api/Program.cs": (
        "using Entities;\n"
        "using Repositories;\n"
        "using Services;\n"
        "\n"
        "var builder = WebApplication.CreateBuilder(args);\n"
        "builder.Services.AddControllers();\n"
        "builder.Services.AddScoped<IUserService, UserService>();\n"
        "builder.Services.AddScoped(typeof(IUserRepository), typeof(UserRepository));\n"
        "builder.Services.AddSingleton<IClock, SystemClock>();\n"
        "\n"
        "var app = builder.Build();\n"
        "app.MapControllers();\n"
        "app.Run();\n"
    ),
}


def write_solution(root: Path, files: Mapping[str, str] = FILES) -> Path:
    """Write `files` under `root` and return `root`."""
    for relative, contents in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8", newline="\n")
    return root


def variant(
    changed: Mapping[str, str] | None = None, removed: tuple[str, ...] = ()
) -> dict[str, str]:
    """The solution with some files changed or added, and some removed."""
    files = {path: text for path, text in FILES.items() if path not in removed}
    files.update(changed or {})
    return files
