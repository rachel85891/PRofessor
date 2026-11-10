from pathlib import Path

import pytest

from hw_checker.adapters.architecture_evidence import LocalArchitectureEvidenceReader
from hw_checker.domain import (
    ArchitectureEvidence,
    ArchitectureSignalKind,
    EvidenceCoverageStatus,
    EvidenceIssueKind,
    ProjectReferenceStatus,
    ServiceLifetime,
)


def write(root: Path, relative: str, contents: str | bytes) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(contents, bytes):
        path.write_bytes(contents)
    else:
        path.write_text(contents, encoding="utf-8")


def signal_kinds(result: ArchitectureEvidence) -> set[ArchitectureSignalKind]:
    return {signal.kind for signal in result.signals}


def test_inventory_references_and_di_candidates_are_factual(tmp_path: Path) -> None:
    write(
        tmp_path,
        "Api/Api.csproj",
        '<Project Sdk="Microsoft.NET.Sdk.Web">'
        '<ItemGroup><ProjectReference Include="..\\Core\\Core.csproj" /></ItemGroup></Project>',
    )
    write(tmp_path, "Core/Core.csproj", '<Project Sdk="Microsoft.NET.Sdk" />')
    write(
        tmp_path,
        "Api/Services/UserService.cs",
        "namespace Example;\n"
        "public interface IUserRepository { }\n"
        "public class UserService\n"
        "{\n"
        "    public UserService(IUserRepository repository) { }\n"
        "}\n",
    )
    write(
        tmp_path,
        "Api/Program.cs",
        "builder.Services.AddScoped<IUserRepository, UserRepository>();\n",
    )
    write(tmp_path, "Api/bin/Release/Generated.cs", "public interface IFake { }")
    write(tmp_path, "Api/Properties/AssemblyInfo.cs", "public interface IGenerated { }")
    write(tmp_path, "Api/Migrations/Generated.cs", "public interface IMigration { }")
    write(tmp_path, "Api/Views/Page.g.cs", "public interface IGeneratedView { }")
    write(tmp_path, "Api/Forms/Page.Designer.cs", "public interface IGeneratedForm { }")
    write(tmp_path, "obj/Generated.csproj", '<Project Sdk="Microsoft.NET.Sdk" />')
    write(tmp_path, ".git/Hidden/Hidden.csproj", '<Project Sdk="Microsoft.NET.Sdk" />')

    evidence = LocalArchitectureEvidenceReader().scan(tmp_path)

    assert evidence.project_files == ("Api/Api.csproj", "Core/Core.csproj")
    assert evidence.projects[0].sdk_references == ("Microsoft.NET.Sdk.Web",)
    assert evidence.projects[1].sdk_references == ("Microsoft.NET.Sdk",)
    assert evidence.project_references[0].source_project == "Api/Api.csproj"
    assert evidence.project_references[0].target_project == "Core/Core.csproj"
    assert evidence.project_references[0].status is ProjectReferenceStatus.MATCHED
    assert evidence.inventory_coverage.status is EvidenceCoverageStatus.COMPLETE
    assert evidence.signal_coverage.status is EvidenceCoverageStatus.COMPLETE
    assert signal_kinds(evidence) == {
        ArchitectureSignalKind.INTERFACE_DECLARATION,
        ArchitectureSignalKind.CONSTRUCTOR_CANDIDATE,
        ArchitectureSignalKind.CONSTRUCTOR_INTERFACE_PARAMETER,
        ArchitectureSignalKind.REGISTRATION_CANDIDATE,
    }
    parameter_signal = next(
        signal
        for signal in evidence.signals
        if signal.kind is ArchitectureSignalKind.CONSTRUCTOR_INTERFACE_PARAMETER
    )
    assert (parameter_signal.subject, parameter_signal.related_subject) == (
        "IUserRepository",
        "UserService",
    )
    assert parameter_signal.file_path == "Api/Services/UserService.cs"
    assert parameter_signal.line == 5
    assert all(not hasattr(signal, "source") for signal in evidence.signals)
    serialized = evidence.model_dump_json()
    assert "public interface" not in serialized
    assert str(tmp_path) not in serialized


def test_constructor_parameter_matches_an_interface_declared_in_another_project(
    tmp_path: Path,
) -> None:
    write(tmp_path, "Core/IClock.cs", "namespace Core;\npublic interface IClock { }\n")
    write(
        tmp_path,
        "Api/Timer.cs",
        "namespace Api;\npublic class Timer\n{\n    public Timer(Core.IClock clock) { }\n}\n",
    )

    evidence = LocalArchitectureEvidenceReader().scan(tmp_path)

    parameters = [
        (signal.subject, signal.related_subject, signal.file_path)
        for signal in evidence.signals
        if signal.kind is ArchitectureSignalKind.CONSTRUCTOR_INTERFACE_PARAMETER
    ]
    assert parameters == [("Core.IClock", "Timer", "Api/Timer.cs")]


def test_duplicate_interface_name_still_matches_but_lowers_coverage(tmp_path: Path) -> None:
    write(tmp_path, "A/IClock.cs", "public interface IClock { }\n")
    write(tmp_path, "B/IClock.cs", "public interface IClock { }\n")
    write(tmp_path, "Timer.cs", "public class Timer\n{\n    public Timer(IClock clock) { }\n}\n")

    evidence = LocalArchitectureEvidenceReader().scan(tmp_path)

    assert ArchitectureSignalKind.CONSTRUCTOR_INTERFACE_PARAMETER in signal_kinds(evidence)
    assert evidence.signal_coverage.status is EvidenceCoverageStatus.PARTIAL
    duplicates = [
        (issue.file_path, issue.line)
        for issue in evidence.signal_coverage.issues
        if issue.kind is EvidenceIssueKind.DUPLICATE_TYPE_NAME
    ]
    assert duplicates == [("Timer.cs", 3)]


def test_constructor_with_attributes_is_still_a_constructor(tmp_path: Path) -> None:
    write(
        tmp_path,
        "Timer.cs",
        "public interface IClock { }\n"
        "public class Timer\n"
        "{\n"
        '    [Obsolete("Use the other one")]\n'
        "    [ActivatorUtilitiesConstructor]\n"
        "    public Timer([FromServices] IClock clock) { }\n"
        "}\n",
    )

    evidence = LocalArchitectureEvidenceReader().scan(tmp_path)

    assert [
        (signal.kind, signal.line, signal.subject)
        for signal in evidence.signals
        if signal.kind is not ArchitectureSignalKind.INTERFACE_DECLARATION
    ] == [
        (ArchitectureSignalKind.CONSTRUCTOR_CANDIDATE, 6, "Timer"),
        (ArchitectureSignalKind.CONSTRUCTOR_INTERFACE_PARAMETER, 6, "IClock"),
    ]


def test_registrations_name_their_types_only_when_written_literally(tmp_path: Path) -> None:
    write(
        tmp_path,
        "Program.cs",
        "builder.Services.AddScoped<IUserRepository, UserRepository>();\n"
        "builder.Services.AddSingleton<Clock>();\n"
        "builder.Services.AddTransient(typeof(IRepository<>), typeof(Repository<>));\n"
        "builder.Services.AddScoped(typeof(Audit));\n"
        "builder.Services.AddScoped<IMailer>(provider => new Mailer());\n"
        "builder.Services.AddKeyedSingleton<ICache, Cache>(1);\n"
        "builder.Services.AddScoped(Factory.Create);\n",
    )

    evidence = LocalArchitectureEvidenceReader().scan(tmp_path)

    assert [
        (
            signal.line,
            signal.subject,
            signal.related_subject,
            signal.registration_method,
            signal.lifetime,
        )
        for signal in evidence.signals
    ] == [
        (1, "IUserRepository", "UserRepository", "AddScoped", ServiceLifetime.SCOPED),
        (2, "Clock", None, "AddSingleton", ServiceLifetime.SINGLETON),
        (3, "IRepository<>", "Repository<>", "AddTransient", ServiceLifetime.TRANSIENT),
        (4, "Audit", None, "AddScoped", ServiceLifetime.SCOPED),
        (5, None, None, "AddScoped", ServiceLifetime.SCOPED),
        (6, None, None, "AddKeyedSingleton", ServiceLifetime.SINGLETON),
        (7, None, None, "AddScoped", ServiceLifetime.SCOPED),
    ]
    assert {signal.kind for signal in evidence.signals} == {
        ArchitectureSignalKind.REGISTRATION_CANDIDATE
    }


def test_comments_and_strings_are_not_reported_as_active_di(tmp_path: Path) -> None:
    write(
        tmp_path,
        "Program.cs",
        "// public interface ICommented { }\n"
        'var example = "public class Fake { public Fake(IHidden x) { } }; AddScoped<IFake>";\n'
        "/* AddScoped<IBlock, Block>(); */\n",
    )

    evidence = LocalArchitectureEvidenceReader().scan(tmp_path)

    assert evidence.signals == ()
    assert evidence.signal_coverage.status is EvidenceCoverageStatus.COMPLETE
    assert evidence.signal_coverage.issues == ()


def test_malformed_and_unsafe_project_references_are_explicit(tmp_path: Path) -> None:
    write(tmp_path, "Bad/Bad.csproj", "<Project><")
    write(
        tmp_path,
        "Api/Api.csproj",
        "<Project><ItemGroup>"
        '<ProjectReference Include="../../outside/Secret.csproj" />'
        '<ProjectReference Include="$(SharedProject)" />'
        '<ProjectReference Include="../Missing/Missing.csproj" />'
        "</ItemGroup></Project>",
    )

    evidence = LocalArchitectureEvidenceReader().scan(tmp_path)

    assert evidence.project_files == ("Api/Api.csproj", "Bad/Bad.csproj")
    assert evidence.inventory_coverage.status is EvidenceCoverageStatus.PARTIAL
    assert evidence.projects[0].file_path == "Api/Api.csproj"
    assert [reference.status for reference in evidence.project_references] == [
        ProjectReferenceStatus.UNSAFE,
        ProjectReferenceStatus.UNSUPPORTED,
        ProjectReferenceStatus.MISSING,
    ]
    assert evidence.project_references[0].target_project is None
    assert evidence.project_references[1].target_project is None
    assert {issue.kind for issue in evidence.inventory_coverage.issues} >= {
        EvidenceIssueKind.MALFORMED_PROJECT,
        EvidenceIssueKind.UNSAFE_PROJECT_REFERENCE,
        EvidenceIssueKind.UNSUPPORTED_PROJECT,
        EvidenceIssueKind.MISSING_PROJECT_REFERENCE,
    }


def test_project_xml_bom_is_supported_but_dtd_and_invalid_utf8_are_not(
    tmp_path: Path,
) -> None:
    write(tmp_path, "Bom.csproj", '<Project Sdk="Microsoft.NET.Sdk" />'.encode("utf-8-sig"))
    write(
        tmp_path,
        "Dtd.csproj",
        '<!DOCTYPE Project [<!ENTITY x SYSTEM "file:///outside/secret.txt">]><Project Sdk="&x;" />',
    )
    write(tmp_path, "Invalid.csproj", b"\xff")

    evidence = LocalArchitectureEvidenceReader().scan(tmp_path)

    assert [project.file_path for project in evidence.projects] == ["Bom.csproj"]
    assert evidence.projects[0].sdk_references == ("Microsoft.NET.Sdk",)
    assert evidence.inventory_coverage.status is EvidenceCoverageStatus.PARTIAL
    issue_kinds = {issue.kind for issue in evidence.inventory_coverage.issues}
    assert EvidenceIssueKind.UNSUPPORTED_PROJECT in issue_kinds
    assert EvidenceIssueKind.INVALID_UTF8 in issue_kinds


def test_case_mismatched_reference_is_not_claimed_as_a_match(tmp_path: Path) -> None:
    write(
        tmp_path,
        "Api/Api.csproj",
        '<Project><ProjectReference Include="../core/core.csproj" /></Project>',
    )
    write(tmp_path, "Core/Core.csproj", "<Project />")

    evidence = LocalArchitectureEvidenceReader().scan(tmp_path)

    assert evidence.project_references[0].target_project == "core/core.csproj"
    assert evidence.project_references[0].status is ProjectReferenceStatus.CASE_MISMATCHED
    assert evidence.inventory_coverage.status is EvidenceCoverageStatus.PARTIAL


def test_preprocessor_alias_and_unbalanced_syntax_lower_coverage(tmp_path: Path) -> None:
    write(
        tmp_path,
        "Program.cs",
        "#if DEBUG\n"
        "using IRepo = Example.IRepository;\n"
        "public class Broken { public Broken(IRepo repo) { }\n",
    )

    evidence = LocalArchitectureEvidenceReader().scan(tmp_path)

    kinds = {issue.kind for issue in evidence.signal_coverage.issues}
    assert evidence.signal_coverage.status is EvidenceCoverageStatus.PARTIAL
    assert {
        EvidenceIssueKind.PREPROCESSOR_CONDITION,
        EvidenceIssueKind.USING_ALIAS,
        EvidenceIssueKind.AMBIGUOUS_SYNTAX,
    } <= kinds
    assert evidence.signals == ()


def test_primary_constructor_is_read_like_a_classic_one(tmp_path: Path) -> None:
    write(tmp_path, "IRepository.cs", "public interface IRepository<T> { }\n")
    write(
        tmp_path,
        "Service.cs",
        "public class Service<T>(IRepository<T> repository, int size = 10)\n"
        "    : Base where T : class\n"
        "{\n"
        "}\n"
        "public record Point(int X, int Y);\n",
    )

    evidence = LocalArchitectureEvidenceReader().scan(tmp_path)

    assert [
        (signal.kind, signal.line, signal.subject, signal.related_subject)
        for signal in evidence.signals
        if signal.file_path == "Service.cs"
    ] == [
        (ArchitectureSignalKind.CONSTRUCTOR_CANDIDATE, 1, "Service", None),
        (ArchitectureSignalKind.CONSTRUCTOR_INTERFACE_PARAMETER, 1, "IRepository<T>", "Service"),
        (ArchitectureSignalKind.CONSTRUCTOR_CANDIDATE, 5, "Point", None),
    ]


def test_unreadable_primary_constructor_parameters_are_ambiguous(tmp_path: Path) -> None:
    write(tmp_path, "Service.cs", "public class Service((int, int) pair) { }\n")

    evidence = LocalArchitectureEvidenceReader().scan(tmp_path)

    assert evidence.signal_coverage.status is EvidenceCoverageStatus.PARTIAL
    assert [
        (issue.kind, issue.file_path, issue.line) for issue in evidence.signal_coverage.issues
    ] == [(EvidenceIssueKind.AMBIGUOUS_SYNTAX, "Service.cs", 1)]


def test_invalid_utf8_and_missing_sources_are_not_success_shaped(tmp_path: Path) -> None:
    write(tmp_path, "Bad.cs", b"\xff\xfe")

    evidence = LocalArchitectureEvidenceReader().scan(tmp_path)

    assert evidence.signal_coverage.status is EvidenceCoverageStatus.PARTIAL
    assert EvidenceIssueKind.INVALID_UTF8 in {
        issue.kind for issue in evidence.signal_coverage.issues
    }

    empty = LocalArchitectureEvidenceReader().scan(tmp_path / "empty")
    assert empty.signal_coverage.status is EvidenceCoverageStatus.UNAVAILABLE
    assert empty.inventory_coverage.status is EvidenceCoverageStatus.UNAVAILABLE
    assert EvidenceIssueKind.REPOSITORY_UNAVAILABLE in {
        issue.kind for issue in empty.signal_coverage.issues
    }


def test_size_and_inventory_limits_are_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hw_checker.adapters.architecture_evidence import inventory, local

    write(tmp_path, "A.cs", "class A { }")
    write(tmp_path, "B.cs", "class B { }")
    monkeypatch.setattr(inventory, "MAX_INVENTORY_FILES", 1)

    evidence = LocalArchitectureEvidenceReader().scan(tmp_path)

    assert evidence.signal_coverage.status is EvidenceCoverageStatus.PARTIAL
    assert EvidenceIssueKind.SCAN_LIMIT_REACHED in {
        issue.kind for issue in evidence.signal_coverage.issues
    }

    monkeypatch.setattr(inventory, "MAX_INVENTORY_FILES", 10)
    monkeypatch.setattr(local, "MAX_SOURCE_FILE_BYTES", 3)
    large = LocalArchitectureEvidenceReader().scan(tmp_path)
    assert EvidenceIssueKind.FILE_TOO_LARGE in {
        issue.kind for issue in large.signal_coverage.issues
    }


def test_external_project_reference_is_never_opened(tmp_path: Path) -> None:
    outside = tmp_path.parent / "secret-project.csproj"
    write(outside.parent, outside.name, '<Project Sdk="Sensitive.Value" />')
    write(
        tmp_path,
        "Api.csproj",
        f'<Project><ProjectReference Include="{outside.as_posix()}" /></Project>',
    )

    evidence = LocalArchitectureEvidenceReader().scan(tmp_path)

    assert evidence.project_references[0].status is ProjectReferenceStatus.UNSAFE
    assert evidence.project_references[0].target_project is None
    assert all(project.file_path != outside.name for project in evidence.projects)


def test_windows_absolute_project_reference_is_unsafe(tmp_path: Path) -> None:
    write(
        tmp_path,
        "Api.csproj",
        '<Project><ProjectReference Include="C:\\outside\\Secret.csproj" /></Project>',
    )

    evidence = LocalArchitectureEvidenceReader().scan(tmp_path)

    assert evidence.project_references[0].status is ProjectReferenceStatus.UNSAFE
    assert evidence.project_references[0].target_project is None


def test_separate_roots_never_share_evidence(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    write(first, "First.cs", "public interface IFirst { }")
    write(second, "Second.cs", "public interface ISecond { }")
    reader = LocalArchitectureEvidenceReader()

    first_evidence = reader.scan(first)
    second_evidence = reader.scan(second)

    assert {signal.subject for signal in first_evidence.signals} == {"IFirst"}
    assert {signal.subject for signal in second_evidence.signals} == {"ISecond"}
    assert all(signal.file_path != "Second.cs" for signal in first_evidence.signals)
    assert all(signal.file_path != "First.cs" for signal in second_evidence.signals)


def test_symlinked_source_is_skipped_when_supported(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    outside = tmp_path / "outside.cs"
    repo.mkdir()
    outside.write_text("public interface IOutside { }", encoding="utf-8")
    try:
        (repo / "Linked.cs").symlink_to(outside)
    except OSError:
        pytest.skip("This Windows environment does not permit creating symbolic links")

    evidence = LocalArchitectureEvidenceReader().scan(repo)

    assert evidence.signals == ()
    assert evidence.inventory_coverage.status is EvidenceCoverageStatus.PARTIAL
    assert EvidenceIssueKind.UNSAFE_PATH_SKIPPED in {
        issue.kind for issue in evidence.inventory_coverage.issues
    }
