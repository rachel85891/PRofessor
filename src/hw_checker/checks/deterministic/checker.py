"""`RequirementChecker` implementation: dispatches deterministic requirements by kind."""

from pydantic import ValidationError

from hw_checker.checks.deterministic.build import evaluate_build
from hw_checker.checks.deterministic.http import evaluate_http
from hw_checker.checks.registry import CheckRegistry, CheckResult
from hw_checker.domain import (
    CheckInput,
    CheckPassed,
    ConditionOutcome,
    Finding,
    NotEvaluatedReason,
    RequirementKind,
)
from hw_checker.domain.errors import InvalidAssignmentError, NotFoundError


class DeterministicRequirementChecker:
    """Runs registered checks for structure and custom requirements, and the build and HTTP
    evaluators for their kinds. Implements `RequirementChecker`."""

    def __init__(self, registry: CheckRegistry) -> None:
        self._registry = registry

    def check(self, check_input: CheckInput) -> tuple[Finding, ...] | NotEvaluatedReason:
        """Evaluate one structure, custom, build or http requirement.

        Raises:
            InvalidAssignmentError: a code requirement, an unknown check, invalid params, or
                a file list (`check_input.files`) given to a check that reads file contents.
        """
        requirement = check_input.requirement
        match requirement.kind:
            case RequirementKind.BUILD:
                return evaluate_build(check_input)
            case RequirementKind.HTTP:
                return evaluate_http(check_input)
            case RequirementKind.STRUCTURE | RequirementKind.CUSTOM:
                outcome = self._run_registered(check_input)
                return () if isinstance(outcome, CheckPassed) else outcome
            case RequirementKind.CODE:
                raise InvalidAssignmentError(
                    f"requirement {requirement.id!r} is a code requirement, not a deterministic one"
                )

    def condition(self, check_input: CheckInput) -> ConditionOutcome:
        """Run the requirement's registered check as a condition; it holds when it passes.

        Raises:
            InvalidAssignmentError: an unknown check, invalid params, or a file list given
                to a check that reads file contents.
        """
        outcome = self._run_registered(check_input)
        if isinstance(outcome, NotEvaluatedReason):
            return ConditionOutcome(holds=None, reason=outcome)
        if isinstance(outcome, CheckPassed):
            return ConditionOutcome(holds=True, message=outcome.message)
        messages = (finding.message_en.strip() for finding in outcome)
        return ConditionOutcome(holds=not outcome, message="; ".join(messages))

    def _run_registered(self, check_input: CheckInput) -> CheckResult:
        requirement = check_input.requirement
        if requirement.check is None:
            raise InvalidAssignmentError(f"requirement {requirement.id!r} names no check")
        try:
            registered = self._registry.get(requirement.check)
            params = registered.spec.params_model.model_validate(dict(requirement.params))
        except NotFoundError as error:
            raise InvalidAssignmentError(f"requirement {requirement.id!r}: {error}") from error
        except ValidationError as error:
            raise InvalidAssignmentError(
                f"requirement {requirement.id!r}: invalid params for check "
                f"{requirement.check!r} ({error.error_count()} problems)"
            ) from error
        if check_input.files is not None and not registered.spec.reads_file_list:
            raise InvalidAssignmentError(
                f"requirement {requirement.id!r}: check {requirement.check!r} reads file "
                "contents, so it cannot run on a commit's file list"
            )
        return registered.function(check_input, params)
