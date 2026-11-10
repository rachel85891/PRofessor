"""In-memory fake for the CheckCatalog port."""

from collections.abc import Iterable, Mapping

from hw_checker.domain import (
    CheckDescription,
    CheckInput,
    CheckPassed,
    ConditionOutcome,
    Finding,
    NotEvaluatedReason,
    ParamValue,
)


class FakeCheckCatalog:
    """Knows the given check names; reports the given param problems per check name."""

    def __init__(
        self,
        names: Iterable[str] = (),
        param_problems: Mapping[str, tuple[str, ...]] | None = None,
    ) -> None:
        self._names = frozenset(names)
        self._param_problems = dict(param_problems or {})
        self.validated: list[tuple[str, dict[str, ParamValue]]] = []

    def check_names(self) -> frozenset[str]:
        return self._names

    def validate_params(self, check_name: str, params: Mapping[str, ParamValue]) -> tuple[str, ...]:
        self.validated.append((check_name, dict(params)))
        if check_name not in self._names:
            return (f"unknown check '{check_name}'",)
        return self._param_problems.get(check_name, ())

    def describe(self) -> tuple[CheckDescription, ...]:
        return tuple(
            CheckDescription(name=name, description=f"Fake check {name}")
            for name in sorted(self._names)
        )


Outcome = tuple[Finding, ...] | NotEvaluatedReason | CheckPassed | Exception


class FakeRequirementChecker:
    """Returns (or raises) a configured outcome per requirement id; passes by default."""

    def __init__(self, outcomes: Mapping[str, Outcome] | None = None) -> None:
        self._outcomes = dict(outcomes or {})
        self.checked: list[CheckInput] = []

    def check(self, check_input: CheckInput) -> tuple[Finding, ...] | NotEvaluatedReason:
        outcome = self._outcome(check_input)
        return () if isinstance(outcome, CheckPassed) else outcome

    def condition(self, check_input: CheckInput) -> ConditionOutcome:
        outcome = self._outcome(check_input)
        if isinstance(outcome, NotEvaluatedReason):
            return ConditionOutcome(holds=None, reason=outcome)
        if isinstance(outcome, CheckPassed):
            return ConditionOutcome(holds=True, message=outcome.message)
        return ConditionOutcome(
            holds=not outcome, message="; ".join(finding.message_en for finding in outcome)
        )

    def _outcome(
        self, check_input: CheckInput
    ) -> tuple[Finding, ...] | NotEvaluatedReason | CheckPassed:
        self.checked.append(check_input)
        outcome = self._outcomes.get(check_input.requirement.id, ())
        if isinstance(outcome, Exception):
            raise outcome
        return outcome
