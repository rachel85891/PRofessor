"""The architecture evidence reaches the checks through `CheckInput` (stage 20)."""

import logging
from pathlib import Path

import pytest

from hw_checker.adapters.architecture_evidence import LocalArchitectureEvidenceReader
from hw_checker.checks.deterministic.builtin import builtin_checks
from hw_checker.checks.deterministic.checker import DeterministicRequirementChecker
from hw_checker.checks.registry import CheckRegistry
from hw_checker.domain import (
    ArchitectureEvidence,
    Assignment,
    CheckCondition,
    EvidenceCoverage,
    EvidenceCoverageStatus,
    NotEvaluatedReason,
    Requirement,
    RequirementKind,
    Severity,
)
from hw_checker.services.deterministic_checks import DeterministicCheckService
from tests.fakes.architecture_evidence import FakeArchitectureEvidenceReader
from tests.fakes.checks import FakeRequirementChecker


def structure(requirement_id: str, check: str, **params: int | str) -> Requirement:
    return Requirement(
        id=requirement_id,
        title=requirement_id,
        severity=Severity.MAJOR,
        kind=RequirementKind.STRUCTURE,
        check=check,
        params=params,
    )


DI_PARAMS = {"min_registrations": 1, "min_interface_constructors": 1}
WITH_DI = Assignment(
    id="hw03",
    order=3,
    title="DI",
    requirements=(
        structure("hw03-di", "di_started", **DI_PARAMS),
        structure("hw03-di-again", "di_started", **DI_PARAMS),
        structure("hw03-file", "file_exists", glob="**/*.cs"),
    ),
)
WITHOUT_DI = WITH_DI.model_copy(update={"requirements": WITH_DI.requirements[2:]})
COMPLETE = EvidenceCoverage(status=EvidenceCoverageStatus.COMPLETE, files_examined=1)


class BrokenReader:
    def __init__(self) -> None:
        self.calls = 0

    def scan(self, root: Path) -> ArchitectureEvidence:
        self.calls += 1
        raise OSError("the walk failed")


def test_the_evidence_is_read_once_and_only_for_the_checks_that_read_it(tmp_path: Path) -> None:
    found = ArchitectureEvidence(inventory_coverage=COMPLETE, signal_coverage=COMPLETE)
    reader = FakeArchitectureEvidenceReader({tmp_path: found})
    checker = FakeRequirementChecker(architecture_checks=["di_started"])
    service = DeterministicCheckService(checker, architecture=reader)

    service.evaluate(WITH_DI, tmp_path, None)

    assert reader.scanned == [tmp_path]
    assert {i.requirement.id: i.architecture for i in checker.checked} == {
        "hw03-di": found,
        "hw03-di-again": found,
        "hw03-file": None,
    }


def test_without_a_check_that_reads_it_the_evidence_is_never_read(tmp_path: Path) -> None:
    reader = FakeArchitectureEvidenceReader()
    service = DeterministicCheckService(
        FakeRequirementChecker(architecture_checks=["di_started"]), architecture=reader
    )

    service.evaluate(WITHOUT_DI, tmp_path, None)

    assert reader.scanned == []


def test_evidence_that_cannot_be_read_leaves_its_checks_not_evaluated(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    reader = BrokenReader()
    checker = DeterministicRequirementChecker(CheckRegistry(builtin_checks()))
    service = DeterministicCheckService(checker, architecture=reader)
    (tmp_path / "Program.cs").write_text("class Program { }\n", encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        result = service.evaluate(WITH_DI, tmp_path, None)

    assert reader.calls == 1
    assert [(e.requirement_id, e.reason) for e in result.not_evaluated] == [
        ("hw03-di", NotEvaluatedReason.EVIDENCE_UNAVAILABLE),
        ("hw03-di-again", NotEvaluatedReason.EVIDENCE_UNAVAILABLE),
    ]
    assert caplog.text.count("Could not read the architecture evidence (OSError)") == 1


def write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def synthetic_solution(root: Path, *, with_di: bool) -> Path:
    """A small invented two-project solution; with DI, a registration and a constructor
    that takes an interface."""
    write(root, "Api/Api.csproj", '<Project Sdk="Microsoft.NET.Sdk.Web" />')
    write(root, "Core/Core.csproj", '<Project Sdk="Microsoft.NET.Sdk" />')
    write(
        root,
        "Core/IGreetingService.cs",
        "namespace Core;\npublic interface IGreetingService { string Greet(); }\n",
    )
    if with_di:
        write(
            root,
            "Api/Program.cs",
            "builder.Services.AddScoped<IGreetingService, GreetingService>();\n",
        )
        write(
            root,
            "Api/Controllers/GreetingController.cs",
            "public class GreetingController\n"
            "{\n"
            "    public GreetingController(IGreetingService greetings) { }\n"
            "}\n",
        )
    else:
        write(root, "Api/Program.cs", "var app = builder.Build();\n")
    return root


@pytest.mark.parametrize(
    ("with_di", "applies"), [(True, True), (False, False)], ids=["di", "layers"]
)
def test_the_real_reader_decides_applies_when_di_started(
    tmp_path: Path, with_di: bool, applies: bool
) -> None:
    repo = synthetic_solution(tmp_path, with_di=with_di)
    condition = CheckCondition(check="di_started", params=DI_PARAMS)
    di = WITHOUT_DI.model_copy(update={"applies_when": condition})
    service = DeterministicCheckService(
        DeterministicRequirementChecker(CheckRegistry(builtin_checks())),
        architecture=LocalArchitectureEvidenceReader(),
    )

    result = service.evaluate(di, repo, None)

    if applies:
        assert result.not_checked is None
    else:
        entry = result.not_checked
        assert entry is not None
        assert entry.reason is NotEvaluatedReason.ASSIGNMENT_NOT_APPLICABLE
        assert entry.detail == (
            "applies_when di_started: DI has not started: 0 registrations (at least 1) and 0 "
            "constructors taking an interface (at least 1)."
        )
