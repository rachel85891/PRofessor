"""In-memory fake for the CodeRunner port."""

from collections.abc import Mapping

from hw_checker.domain import ExecutionRequest, ExecutionResult, StepStatus
from hw_checker.domain.errors import ExecutionEnvironmentError

SUCCEEDED = ExecutionResult(build_status=StepStatus.SUCCEEDED, run_status=StepStatus.SUCCEEDED)


class FakeCodeRunner:
    """Returns a fixed result per repo and records every request.

    `results` maps a repo id to its result (default: build and run succeeded). `errors`
    maps a repo id to the error its execution raises. `environment_error` makes
    `check_environment` fail.
    """

    def __init__(
        self,
        results: Mapping[str, ExecutionResult] | None = None,
        errors: Mapping[str, Exception] | None = None,
        environment_error: ExecutionEnvironmentError | None = None,
        leftovers: int = 0,
    ) -> None:
        self._results = dict(results or {})
        self._errors = dict(errors or {})
        self._environment_error = environment_error
        self._leftovers = leftovers
        self.requests: list[ExecutionRequest] = []
        self.calls: list[str] = []

    def check_environment(self) -> None:
        self.calls.append("check_environment")
        if self._environment_error is not None:
            raise self._environment_error

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        self.calls.append("execute")
        self.requests.append(request)
        error = self._errors.get(request.repo_id)
        if error is not None:
            raise error
        return self._results.get(request.repo_id, SUCCEEDED)

    def sweep_leftovers(self) -> int:
        self.calls.append("sweep_leftovers")
        swept, self._leftovers = self._leftovers, 0
        return swept
