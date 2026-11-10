"""A synthetic four-layer solution shaped like real course projects: one type per file."""

from pathlib import Path

from hw_checker.adapters.architecture_evidence import LocalArchitectureEvidenceReader
from hw_checker.domain import (
    ArchitectureSignalKind,
    EvidenceCoverageStatus,
    ProjectReferenceStatus,
    ServiceLifetime,
)

INTERFACE = ArchitectureSignalKind.INTERFACE_DECLARATION
CONSTRUCTOR = ArchitectureSignalKind.CONSTRUCTOR_CANDIDATE
PARAMETER = ArchitectureSignalKind.CONSTRUCTOR_INTERFACE_PARAMETER
REGISTRATION = ArchitectureSignalKind.REGISTRATION_CANDIDATE

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


def test_layered_solution_signals_are_exact_and_coverage_complete(tmp_path: Path) -> None:
    for relative, contents in FILES.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")

    evidence = LocalArchitectureEvidenceReader().scan(tmp_path)

    assert evidence.project_files == (
        "Api/Api.csproj",
        "Entities/Entities.csproj",
        "Repositories/Repositories.csproj",
        "Services/Services.csproj",
    )
    assert [
        (reference.source_project, reference.target_project, reference.status)
        for reference in evidence.project_references
    ] == [
        ("Api/Api.csproj", "Services/Services.csproj", ProjectReferenceStatus.MATCHED),
        ("Api/Api.csproj", "Entities/Entities.csproj", ProjectReferenceStatus.MATCHED),
        (
            "Repositories/Repositories.csproj",
            "Entities/Entities.csproj",
            ProjectReferenceStatus.MATCHED,
        ),
        (
            "Services/Services.csproj",
            "Repositories/Repositories.csproj",
            ProjectReferenceStatus.MATCHED,
        ),
        ("Services/Services.csproj", "Entities/Entities.csproj", ProjectReferenceStatus.MATCHED),
    ]
    assert [
        (signal.kind, signal.file_path, signal.line, signal.subject, signal.related_subject)
        for signal in evidence.signals
    ] == [
        (CONSTRUCTOR, "Api/Controllers/UsersController.cs", 13, "UsersController", None),
        (PARAMETER, "Api/Controllers/UsersController.cs", 13, "IUserService", "UsersController"),
        (REGISTRATION, "Api/Program.cs", 7, "IUserService", "UserService"),
        (REGISTRATION, "Api/Program.cs", 8, "IUserRepository", "UserRepository"),
        (REGISTRATION, "Api/Program.cs", 9, "IClock", "SystemClock"),
        (INTERFACE, "Entities/IClock.cs", 3, "IClock", None),
        (INTERFACE, "Repositories/IUserRepository.cs", 5, "IUserRepository", None),
        (CONSTRUCTOR, "Repositories/UserRepository.cs", 10, "UserRepository", None),
        (PARAMETER, "Repositories/UserRepository.cs", 10, "IClock", "UserRepository"),
        (INTERFACE, "Services/IUserService.cs", 5, "IUserService", None),
        (CONSTRUCTOR, "Services/UserService.cs", 6, "UserService", None),
        (PARAMETER, "Services/UserService.cs", 6, "IUserRepository", "UserService"),
    ]
    assert [
        (signal.registration_method, signal.lifetime)
        for signal in evidence.signals
        if signal.kind is REGISTRATION
    ] == [
        ("AddScoped", ServiceLifetime.SCOPED),
        ("AddScoped", ServiceLifetime.SCOPED),
        ("AddSingleton", ServiceLifetime.SINGLETON),
    ]
    assert evidence.inventory_coverage.status is EvidenceCoverageStatus.COMPLETE
    assert evidence.inventory_coverage.issues == ()
    assert evidence.signal_coverage.status is EvidenceCoverageStatus.COMPLETE
    assert evidence.signal_coverage.issues == ()
    assert evidence.signal_coverage.files_examined == 9
