"""Namespaces in the architecture evidence (HW05-G3): a type declaration's namespace and a
project's `<RootNamespace>`; on synthetic code."""

from pathlib import Path

from hw_checker.adapters.architecture_evidence import LocalArchitectureEvidenceReader
from hw_checker.domain import (
    ArchitectureEvidence,
    ArchitectureSignalKind,
    EvidenceCoverageStatus,
    EvidenceIssueKind,
)


def scan(tmp_path: Path, files: dict[str, str]) -> ArchitectureEvidence:
    for relative, text in files.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    return LocalArchitectureEvidenceReader().scan(tmp_path)


def namespaces(evidence: ArchitectureEvidence) -> dict[str, str | None]:
    return {
        signal.subject or "": signal.namespace
        for signal in evidence.signals
        if signal.kind is ArchitectureSignalKind.TYPE_DECLARATION
    }


def test_a_file_scoped_namespace_holds_every_type_after_it(tmp_path: Path) -> None:
    evidence = scan(
        tmp_path,
        {"User.cs": "namespace Shop.Entities;\n\npublic class User { }\npublic record Tag;\n"},
    )

    assert namespaces(evidence) == {"User": "Shop.Entities", "Tag": "Shop.Entities"}
    assert evidence.signal_coverage.status is EvidenceCoverageStatus.COMPLETE


def test_a_block_namespace_holds_its_types(tmp_path: Path) -> None:
    evidence = scan(
        tmp_path,
        {"User.cs": "namespace Shop.Entities\n{\n    public class User { }\n}\n"},
    )

    assert namespaces(evidence) == {"User": "Shop.Entities"}


def test_nested_block_namespaces_are_joined_with_a_dot(tmp_path: Path) -> None:
    evidence = scan(
        tmp_path,
        {
            "User.cs": "namespace Shop\n{\n    namespace Entities\n    {\n"
            "        public class User { }\n    }\n    public class Root { }\n}\n"
        },
    )

    assert namespaces(evidence) == {"User": "Shop.Entities", "Root": "Shop"}


def test_two_types_in_two_namespaces_of_one_file(tmp_path: Path) -> None:
    evidence = scan(
        tmp_path,
        {
            "Both.cs": "public class Outside { }\n"
            "namespace First { public class A { } }\n"
            "namespace Second.Part { public class B { } }\n"
        },
    )

    assert namespaces(evidence) == {"Outside": None, "A": "First", "B": "Second.Part"}


def test_a_type_outside_every_namespace_is_in_the_global_one(tmp_path: Path) -> None:
    evidence = scan(tmp_path, {"User.cs": "public class User { }\n"})

    assert namespaces(evidence) == {"User": None}


def test_a_nested_type_has_its_outer_types_namespace(tmp_path: Path) -> None:
    evidence = scan(
        tmp_path,
        {"User.cs": "namespace Shop;\npublic class Outer\n{\n    public class Inner { }\n}\n"},
    )

    assert namespaces(evidence) == {"Outer": "Shop", "Inner": "Shop"}


def test_a_namespace_in_a_comment_or_string_is_not_read(tmp_path: Path) -> None:
    evidence = scan(
        tmp_path,
        {"User.cs": '// namespace Wrong;\npublic class User { string s = "namespace Other;"; }\n'},
    )

    assert namespaces(evidence) == {"User": None}
    assert evidence.signal_coverage.status is EvidenceCoverageStatus.COMPLETE


def test_two_file_scoped_namespaces_are_ambiguous_and_give_no_declaration(
    tmp_path: Path,
) -> None:
    evidence = scan(tmp_path, {"User.cs": "namespace A;\nnamespace B;\npublic class User { }\n"})

    assert namespaces(evidence) == {}
    assert evidence.signal_coverage.status is EvidenceCoverageStatus.PARTIAL
    assert [
        (issue.kind, issue.file_path, issue.line) for issue in evidence.signal_coverage.issues
    ] == [(EvidenceIssueKind.AMBIGUOUS_SYNTAX, "User.cs", 1)]


def test_a_file_scoped_namespace_beside_a_block_one_is_ambiguous(tmp_path: Path) -> None:
    evidence = scan(tmp_path, {"User.cs": "namespace A;\nnamespace B { public class User { } }\n"})

    assert namespaces(evidence) == {}
    assert evidence.signal_coverage.status is EvidenceCoverageStatus.PARTIAL


def test_a_namespace_the_reader_cannot_parse_is_ambiguous(tmp_path: Path) -> None:
    evidence = scan(tmp_path, {"User.cs": "namespace ;\npublic class User { }\n"})

    assert namespaces(evidence) == {}
    kinds = [issue.kind for issue in evidence.signal_coverage.issues]
    assert EvidenceIssueKind.AMBIGUOUS_SYNTAX in kinds


def test_an_unreadable_file_does_not_hide_other_files_namespaces(tmp_path: Path) -> None:
    evidence = scan(
        tmp_path,
        {
            "Bad.cs": "namespace A;\nnamespace B;\npublic class Bad { }\n",
            "Good.cs": "namespace Shop;\npublic class Good { }\n",
        },
    )

    assert namespaces(evidence) == {"Good": "Shop"}


def test_only_type_declarations_carry_a_namespace(tmp_path: Path) -> None:
    evidence = scan(
        tmp_path,
        {
            "Shop.cs": "namespace Shop;\n"
            "public interface IClock { }\n"
            "public class Context : DbContext\n{\n"
            "    public DbSet<User> Users { get; set; }\n"
            "    public Context(IClock clock) { }\n}\n"
        },
    )

    carrying = {signal.kind for signal in evidence.signals if signal.namespace is not None}
    assert carrying == {ArchitectureSignalKind.TYPE_DECLARATION}


def test_a_project_names_its_root_namespace(tmp_path: Path) -> None:
    evidence = scan(
        tmp_path,
        {
            "Entities/Entities.csproj": '<Project Sdk="Microsoft.NET.Sdk">\n'
            "  <PropertyGroup>\n    <RootNamespace>Shop.Core</RootNamespace>\n"
            "  </PropertyGroup>\n</Project>\n",
            "Plain/Plain.csproj": '<Project Sdk="Microsoft.NET.Sdk" />\n',
        },
    )

    assert {project.file_path: project.root_namespace for project in evidence.projects} == {
        "Entities/Entities.csproj": "Shop.Core",
        "Plain/Plain.csproj": None,
    }
    assert evidence.inventory_coverage.status is EvidenceCoverageStatus.COMPLETE


def test_a_root_namespace_that_is_an_expression_is_not_guessed(tmp_path: Path) -> None:
    evidence = scan(
        tmp_path,
        {
            "A/A.csproj": '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup>'
            "<RootNamespace>$(Company).A</RootNamespace></PropertyGroup></Project>\n"
        },
    )

    assert [project.root_namespace for project in evidence.projects] == [None]
    assert evidence.inventory_coverage.status is EvidenceCoverageStatus.PARTIAL
    assert [issue.kind for issue in evidence.inventory_coverage.issues] == [
        EvidenceIssueKind.UNSUPPORTED_PROJECT
    ]
