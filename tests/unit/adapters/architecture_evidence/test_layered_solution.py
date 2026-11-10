"""A synthetic four-layer solution shaped like real course projects: one type per file."""

from pathlib import Path

from tests.unit.layered_solution import write_solution

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
DECLARATION = ArchitectureSignalKind.TYPE_DECLARATION
ANY_PARAMETER = ArchitectureSignalKind.CONSTRUCTOR_PARAMETER


def test_layered_solution_signals_are_exact_and_coverage_complete(tmp_path: Path) -> None:
    evidence = LocalArchitectureEvidenceReader().scan(write_solution(tmp_path))

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
        (DECLARATION, "Api/Controllers/UsersController.cs", 9, "UsersController", None),
        (CONSTRUCTOR, "Api/Controllers/UsersController.cs", 13, "UsersController", None),
        (PARAMETER, "Api/Controllers/UsersController.cs", 13, "IUserService", "UsersController"),
        (
            ANY_PARAMETER,
            "Api/Controllers/UsersController.cs",
            13,
            "IUserService",
            "UsersController",
        ),
        (REGISTRATION, "Api/Program.cs", 7, "IUserService", "UserService"),
        (REGISTRATION, "Api/Program.cs", 8, "IUserRepository", "UserRepository"),
        (REGISTRATION, "Api/Program.cs", 9, "IClock", "SystemClock"),
        (INTERFACE, "Entities/IClock.cs", 3, "IClock", None),
        (DECLARATION, "Entities/SystemClock.cs", 3, "SystemClock", None),
        (DECLARATION, "Entities/User.cs", 3, "User", None),
        (INTERFACE, "Repositories/IUserRepository.cs", 5, "IUserRepository", None),
        (DECLARATION, "Repositories/UserRepository.cs", 5, "UserRepository", None),
        (CONSTRUCTOR, "Repositories/UserRepository.cs", 10, "UserRepository", None),
        (PARAMETER, "Repositories/UserRepository.cs", 10, "IClock", "UserRepository"),
        (ANY_PARAMETER, "Repositories/UserRepository.cs", 10, "IClock", "UserRepository"),
        (INTERFACE, "Services/IUserService.cs", 5, "IUserService", None),
        (CONSTRUCTOR, "Services/UserService.cs", 6, "UserService", None),
        (PARAMETER, "Services/UserService.cs", 6, "IUserRepository", "UserService"),
        (ANY_PARAMETER, "Services/UserService.cs", 6, "IUserRepository", "UserService"),
        (DECLARATION, "Services/UserService.cs", 6, "UserService", None),
    ]
    assert [
        (signal.subject, signal.related_subjects, signal.keywords)
        for signal in evidence.signals
        if signal.kind is DECLARATION
    ] == [
        ("UsersController", ("ControllerBase",), ("public", "class")),
        ("SystemClock", ("IClock",), ("public", "class")),
        ("User", (), ("public", "class")),
        ("UserRepository", ("IUserRepository",), ("public", "class")),
        ("UserService", ("IUserService",), ("public", "class")),
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
