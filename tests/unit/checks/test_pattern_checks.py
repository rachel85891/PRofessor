"""The generic `code_patterns` check on made-up code: where, must, roles, `{dbset}`,
params validation and the finding."""

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from hw_checker.adapters.architecture_evidence import LocalArchitectureEvidenceReader
from hw_checker.checks.deterministic.builtin import builtin_checks
from hw_checker.checks.deterministic.pattern_checks import _compiled
from hw_checker.checks.registry import CheckRegistry, CheckResult
from hw_checker.domain import (
    ArchitectureEvidence,
    Assignment,
    CheckInput,
    EvidenceCoverage,
    EvidenceCoverageStatus,
    Finding,
    NotEvaluatedReason,
    Requirement,
    RequirementKind,
    Severity,
)

REGISTRY = CheckRegistry(builtin_checks())
ROLES: dict[str, Any] = {
    "api": {"sdk": "Microsoft.NET.Sdk.Web", "name": "api"},
    "services": {"name": "services"},
    "data": {"name": "data"},
}
WEB = '<Project Sdk="Microsoft.NET.Sdk.Web" />\n'
LIB = '<Project Sdk="Microsoft.NET.Sdk" />\n'
LAYERED = {
    "Api/Api.csproj": WEB,
    "Services/Services.csproj": LIB,
    "Data/Data.csproj": LIB,
    "Helpers/Helpers.csproj": LIB,
}
CONTEXT = (
    "public class ShopContext : DbContext\n"
    "{\n"
    "    public DbSet<User> Users { get; set; }\n"
    "    public DbSet<Order> Orders { get; set; }\n"
    "}\n"
)
IN_API: dict[str, Any] = {"roles": ROLES, "in": ["api"]}
IN_DATA: dict[str, Any] = {"roles": ROLES, "in": ["data"]}
BAD = "class X { void M() { Bad(); } }\n"


def write(root: Path, files: dict[str, str]) -> Path:
    for name, text in files.items():
        file = root / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(text, encoding="utf-8")
    return root


def run(root: Path, evidence: ArchitectureEvidence | None = None, **params: Any) -> CheckResult:
    requirement = Requirement(
        id="hw-pattern",
        title="A pattern requirement",
        severity=Severity.MAJOR,
        kind=RequirementKind.STRUCTURE,
        check="code_patterns",
        params=params,
    )
    registered = REGISTRY.get("code_patterns")
    model = registered.spec.params_model.model_validate(dict(params))
    check_input = CheckInput(
        repo_dir=root,
        assignment=Assignment(id="hw05", order=5, title="Patterns", requirements=(requirement,)),
        requirement=requirement,
        architecture=evidence or LocalArchitectureEvidenceReader().scan(root),
    )
    return registered.function(check_input, model)


def finding(outcome: CheckResult) -> Finding:
    assert isinstance(outcome, tuple), outcome
    [found] = outcome
    return found


def unavailable() -> ArchitectureEvidence:
    coverage = EvidenceCoverage(status=EvidenceCoverageStatus.UNAVAILABLE, files_examined=0)
    return ArchitectureEvidence(inventory_coverage=coverage, signal_coverage=coverage)


# ---------------------------------------------------------------- where


SOURCE = (
    "class A\n"
    "{\n"
    "    // Marker in a comment\n"
    '    string s = "Marker in a string";\n'
    '    string t = $"Marker {x} interpolated";\n'
    "    void M() { Marker(); }\n"
    "}\n"
)


@pytest.mark.parametrize(
    ("where", "lines"),
    [("code", [6]), ("strings", [4, 5]), ("both", [4, 5, 6])],
)
def test_where_chooses_what_is_searched(tmp_path: Path, where: str, lines: list[int]) -> None:
    root = write(tmp_path, {"A.cs": SOURCE})

    found = finding(run(root, patterns=["Marker"], where=where))

    for number in (3, 4, 5, 6):
        assert (f"(A.cs, line {number})" in found.message_en) == (number in lines)
    assert f"Total: {len(lines)}." in found.message_en


def test_code_ignores_comments_and_strings_and_strings_ignore_code(tmp_path: Path) -> None:
    only_comment = write(tmp_path / "c", {"A.cs": "// Marker\nclass A { }\n"})
    only_code = write(tmp_path / "k", {"A.cs": "class A { void Marker() { } }\n"})

    assert run(only_comment, patterns=["Marker"], where="code") == ()
    assert run(only_comment, patterns=["Marker"], where="strings") == ()
    assert run(only_code, patterns=["Marker"], where="strings") == ()


def test_an_interpolated_string_is_matched_on_its_fixed_parts(tmp_path: Path) -> None:
    root = write(tmp_path, {"A.cs": 'class A { string t = $"data/{name}/users.txt"; }\n'})

    assert finding(run(root, patterns=[r"users\.txt"], where="strings"))
    assert run(root, patterns=["name"], where="strings") == ()


def test_a_multi_line_match_is_placed_on_its_first_line(tmp_path: Path) -> None:
    root = write(tmp_path, {"A.cs": "class A\n{\n    void M() { x\n        .Foo(); }\n}\n"})

    found = finding(run(root, patterns=[r"x\s*\.Foo"]))

    assert "(A.cs, line 3)" in found.message_en
    assert found.evidence is not None
    assert (found.evidence.file_path, found.evidence.line_start) == ("A.cs", 3)


# ---------------------------------------------------------------- must


def test_absent_passes_without_matches_and_is_the_default(tmp_path: Path) -> None:
    root = write(tmp_path, {"A.cs": "class A { }\n"})

    assert run(root, patterns=["File"]) == ()
    assert run(root, patterns=["File"], must="absent") == ()


def test_present_passes_with_a_match_and_finds_nothing_without(tmp_path: Path) -> None:
    root = write(tmp_path, {"A.cs": "class A { void M() { AddDbContext(); } }\n"})

    assert run(root, patterns=["AddDbContext"], must="present") == ()
    missing = finding(run(root, patterns=["Other"], must="present"))
    assert missing.message_en == (
        "Nothing that should appear was found in the code of all the files searched."
    )
    assert missing.evidence is None
    assert "לא נמצא" in missing.message_he


def test_the_present_finding_names_the_projects_searched(tmp_path: Path) -> None:
    root = write(tmp_path, {**LAYERED, "Api/Program.cs": "class P { }\n"})

    missing = finding(run(root, patterns=["AddDbContext"], must="present", **IN_API))

    assert missing.message_en == (
        "Nothing that should appear was found in the code of the api projects."
    )


def test_present_looks_only_in_the_projects_of_in(tmp_path: Path) -> None:
    root = write(tmp_path, {**LAYERED, "Services/S.cs": "class S { void M() { Add(); } }\n"})

    outcome = run(root, patterns=["Add"], must="present", **IN_API)

    assert finding(outcome).message_en.endswith("the api projects.")


def test_the_found_finding_has_examples_the_total_and_its_evidence(tmp_path: Path) -> None:
    lines = "".join(f"    void M{n}() {{ Bad(); }}\n" for n in range(7))
    root = write(tmp_path, {"A.cs": "class A\n{\n" + lines + "}\n"})

    found = finding(run(root, patterns=[r"Bad\(\)"]))

    assert found.message_en.count("`Bad()`") == 5
    assert "Total: 7." in found.message_en
    assert found.message_en.startswith("Found in the code of all the files searched")
    assert "סך הכול: 7" in found.message_he
    assert found.evidence is not None
    assert found.evidence.quote == "void M0() { Bad(); }"
    assert found.evidence.line_start == 3


# ---------------------------------------------------------------- roles


def test_in_searches_only_the_projects_of_the_roles(tmp_path: Path) -> None:
    root = write(
        tmp_path,
        {
            **LAYERED,
            "Api/A.cs": BAD,
            "Services/S.cs": BAD,
            "Helpers/H.cs": BAD,  # an unassigned project
            "Loose.cs": BAD,  # in no project
        },
    )

    found = finding(run(root, patterns=["Bad"], **IN_API))

    assert "Api/A.cs" in found.message_en
    for left_out in ("Services", "Helpers", "Loose"):
        assert left_out not in found.message_en
    assert "Total: 1." in found.message_en


def test_without_in_every_file_is_searched(tmp_path: Path) -> None:
    root = write(tmp_path, {**LAYERED, "Services/S.cs": BAD, "Loose.cs": BAD})

    assert "Total: 2." in finding(run(root, patterns=["Bad"])).message_en


def test_a_role_with_no_project_is_not_evaluated(tmp_path: Path) -> None:
    root = write(tmp_path, {"Api/Api.csproj": WEB, "Api/A.cs": "class A { }\n"})

    outcome = run(root, patterns=["x"], **IN_DATA)

    assert outcome is NotEvaluatedReason.DEPENDENCY_FAILED


def test_suffixes_and_excluded_paths(tmp_path: Path) -> None:
    root = write(
        tmp_path,
        {
            "A.cs": BAD,
            "Migrations/M.cs": BAD,
            "Tests/T.cs": BAD,
            "B.txt": "Bad",
            "C.cshtml": "Bad",
        },
    )

    found = finding(run(root, patterns=["Bad"], exclude_paths=["Migrations/**", "**/Tests/**"]))
    both = finding(run(root, patterns=["Bad"], suffixes=[".txt", ".cs"], exclude_paths=["**/T*"]))

    assert "Total: 1." in found.message_en
    assert "Total: 3." in both.message_en


# ---------------------------------------------------------------- {dbset}


DBSET = r"(?:\b(?:\w+\.)?(?:{dbset})\b)\s*\.\s*ToList\s*\("
USAGE = (
    "class R\n"
    "{\n"
    "    void A() { var a = _context.Users.ToList(); }\n"
    "    void B() { var b = _context.Orders.Where(x => x.Id == 1).ToList(); }\n"
    "    void C() { var c = users.ToList(); }\n"
    "    void D() { var d = _context.Users.ToListAsync(); }\n"
    "    void E() { var e = _context.Users.Where(x => x.Id == 1).ToList(); }\n"
    "}\n"
)


def test_dbset_speaks_only_about_the_repos_dbset_properties(tmp_path: Path) -> None:
    root = write(tmp_path, {"Data/Context.cs": CONTEXT, "Data/R.cs": USAGE})

    direct = finding(run(root, patterns=[DBSET]))
    chained = finding(run(root, patterns=[r"(?:{dbset})\s*\.\s*Where\b.*\.ToList"]))

    assert "Total: 1." in direct.message_en
    assert "`_context.Users.ToList(`" in direct.message_en
    assert "Total: 2." in chained.message_en


def test_a_repo_with_two_dbsets_uses_both_names(tmp_path: Path) -> None:
    root = write(tmp_path, {"Data/Context.cs": CONTEXT, "Data/R.cs": USAGE})

    found = finding(run(root, patterns=[r"_context\.(?:{dbset})\b"]))

    assert "Total: 4." in found.message_en


def test_a_repo_with_no_dbset_skips_the_pattern(tmp_path: Path) -> None:
    root = write(tmp_path, {"R.cs": "class R { void A() { _context.Users.ToList(); } }\n"})

    assert run(root, patterns=[DBSET]) == ()
    other = finding(run(root, patterns=[DBSET, r"\.ToList\("]))
    assert "Total: 1." in other.message_en


def test_a_dbset_name_is_escaped() -> None:
    [pattern] = _compiled(("^{dbset}$",), ("a.b", "c+"))

    assert [bool(pattern.search(text)) for text in ("a.b", "c+", "aXb", "cc")] == [
        True,
        True,
        False,
        False,
    ]


# ---------------------------------------------------------------- evidence


def test_not_evaluated_when_the_evidence_it_needs_is_unavailable(tmp_path: Path) -> None:
    root = write(tmp_path, {**LAYERED, "Data/C.cs": CONTEXT})

    with_roles = run(root, unavailable(), patterns=["x"], **IN_API)
    with_dbset = run(root, unavailable(), patterns=["{dbset}"])

    assert with_roles is NotEvaluatedReason.EVIDENCE_UNAVAILABLE
    assert with_dbset is NotEvaluatedReason.EVIDENCE_UNAVAILABLE


def test_runs_from_the_files_alone_when_it_needs_no_evidence(tmp_path: Path) -> None:
    root = write(tmp_path, {"A.cs": BAD})

    found = finding(run(root, unavailable(), patterns=["Bad"]))

    assert "Total: 1." in found.message_en


# ---------------------------------------------------------------- params


@pytest.mark.parametrize(
    "params",
    [
        {"patterns": ["(unclosed"]},
        {"patterns": ["(unclosed{dbset}"]},
        {"patterns": []},
        {"patterns": [" "]},
        {"patterns": ["x"], "where": "comments"},
        {"patterns": ["x"], "must": "never"},
        {"patterns": ["x"], "roles": ROLES, "in": ["nowhere"]},
        {"patterns": ["x"], "in": ["api"]},
        {"patterns": ["x"], "roles": ROLES},
        {"patterns": ["x"], "suffixes": ["cs"]},
        {"patterns": ["x"], "exclude_paths": [""]},
        {"patterns": ["x"], "unknown": 1},
    ],
)
def test_bad_params_are_refused(params: dict[str, Any]) -> None:
    assert REGISTRY.validate_params("code_patterns", params)
    with pytest.raises(ValidationError):
        REGISTRY.get("code_patterns").spec.params_model.model_validate(params)


def test_a_bad_regex_says_why() -> None:
    params: dict[str, Any] = {"patterns": ["(unclosed"]}
    problems = REGISTRY.validate_params("code_patterns", params)

    assert any("not a valid regular expression" in problem for problem in problems)


def test_the_check_is_in_the_catalog_with_its_schema() -> None:
    [described] = [d for d in REGISTRY.describe() if d.name == "code_patterns"]

    assert described.reads_architecture
    assert {p.name for p in described.params} == {
        "patterns",
        "where",
        "must",
        "roles",
        "in",
        "suffixes",
        "exclude_paths",
    }
    assert '"must"' in described.params_schema
