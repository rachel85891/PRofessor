import pytest
from pydantic import ValidationError

from hw_checker.domain import FindingSource, JudgedFinding, Severity, Verdict
from tests.unit.domain import builders


def test_evidence_builds() -> None:
    assert builders.evidence().line_start == 12


def test_evidence_accepts_single_line() -> None:
    assert builders.evidence(line_start=5, line_end=5).line_end == 5


@pytest.mark.parametrize(
    "file_path",
    ["/src/Program.cs", "C:\\repo\\Program.cs", "C:/repo/Program.cs", "../Program.cs", "a\\b.cs"],
)
def test_evidence_rejects_unsafe_paths(file_path: str) -> None:
    with pytest.raises(ValidationError):
        builders.evidence(file_path=file_path)


def test_evidence_rejects_line_start_below_one() -> None:
    with pytest.raises(ValidationError):
        builders.evidence(line_start=0)


def test_evidence_rejects_line_end_before_line_start() -> None:
    with pytest.raises(ValidationError, match="line_end"):
        builders.evidence(line_start=10, line_end=9)


def test_evidence_rejects_blank_quote() -> None:
    with pytest.raises(ValidationError):
        builders.evidence(quote="   ")


def test_llm_finding_with_evidence_builds() -> None:
    assert builders.finding().source is FindingSource.LLM


def test_llm_finding_without_evidence_is_rejected() -> None:
    with pytest.raises(ValidationError, match="must have evidence"):
        builders.finding(evidence=None)


def test_deterministic_finding_without_evidence_builds() -> None:
    finding = builders.finding(source=FindingSource.DETERMINISTIC, evidence=None)

    assert finding.evidence is None


@pytest.mark.parametrize("confidence", [0.0, 1.0])
def test_finding_accepts_confidence_bounds(confidence: float) -> None:
    assert builders.finding(confidence=confidence).confidence == confidence


@pytest.mark.parametrize("confidence", [-0.01, 1.01])
def test_finding_rejects_confidence_out_of_range(confidence: float) -> None:
    with pytest.raises(ValidationError):
        builders.finding(confidence=confidence)


@pytest.mark.parametrize("field", ["message_he", "message_en"])
def test_finding_rejects_blank_messages(field: str) -> None:
    with pytest.raises(ValidationError):
        builders.finding(**{field: " "})


def test_judged_finding_defaults_adjusted_severity_to_finding_severity() -> None:
    judged = JudgedFinding(finding=builders.finding(), verdict=Verdict.ACCEPTED, reason="Correct")

    assert judged.adjusted_severity is Severity.MAJOR


def test_judged_finding_default_also_applies_to_dict_input() -> None:
    judged = JudgedFinding.model_validate(
        {
            "finding": builders.finding(severity=Severity.CRITICAL).model_dump(),
            "verdict": "rejected",
            "reason": "Allowed alternative",
        }
    )

    assert judged.adjusted_severity is Severity.CRITICAL


def test_judged_finding_accepts_lowered_severity() -> None:
    judged = JudgedFinding(
        finding=builders.finding(),
        verdict=Verdict.ACCEPTED,
        reason="Minor for this level",
        adjusted_severity=Severity.MINOR,
    )

    assert judged.adjusted_severity is Severity.MINOR


def test_judged_finding_rejects_raised_severity() -> None:
    with pytest.raises(ValidationError, match="must not be higher"):
        JudgedFinding(
            finding=builders.finding(severity=Severity.MAJOR),
            verdict=Verdict.ACCEPTED,
            reason="Serious",
            adjusted_severity=Severity.CRITICAL,
        )


def test_judged_finding_rejects_blank_reason() -> None:
    with pytest.raises(ValidationError):
        JudgedFinding(finding=builders.finding(), verdict=Verdict.REJECTED, reason=" ")


def test_judged_finding_is_frozen() -> None:
    judged = JudgedFinding(finding=builders.finding(), verdict=Verdict.ACCEPTED, reason="Correct")

    with pytest.raises(ValidationError):
        judged.verdict = Verdict.REJECTED  # type: ignore[misc]


def test_judged_finding_with_invalid_finding_raises_validation_error() -> None:
    with pytest.raises(ValidationError):
        JudgedFinding.model_validate({"finding": {"id": ""}, "verdict": "accepted", "reason": "x"})
