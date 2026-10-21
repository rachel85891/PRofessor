"""In-memory fake for the CheckCatalog port."""

from collections.abc import Iterable, Mapping

from hw_checker.domain import CheckDescription, CheckInput, Finding, NotEvaluatedReason, ParamValue


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


Outcome = tuple[Finding, ...] | NotEvaluatedReason | Exception


class FakeRequirementChecker:
    """Returns (or raises) a configured outcome per requirement id; passes by default."""

    def __init__(self, outcomes: Mapping[str, Outcome] | None = None) -> None:
        self._outcomes = dict(outcomes or {})
        self.checked: list[CheckInput] = []

    def check(self, check_input: CheckInput) -> tuple[Finding, ...] | NotEvaluatedReason:
        self.checked.append(check_input)
        outcome = self._outcomes.get(check_input.requirement.id, ())
        if isinstance(outcome, Exception):
            raise outcome
        return outcome
