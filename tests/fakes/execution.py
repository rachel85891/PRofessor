"""In-memory fake for the CodeRunner port."""

from collections.abc import Mapping

from hw_checker.domain import ExecutionRequest, ExecutionResult, StepStatus
from hw_checker.domain.errors import ExecutionEnvironmentError

SUCCEEDED = ExecutionResult(build_status=StepStatus.SUCCEEDED, run_status=StepStatus.SUCCEEDED)


class FakeCodeRunner:
    """Returns a fixed result per repo and records every request.

    `results` maps a repo id to its result (default: build and run succeeded). `errors`
    maps a repo id to the error its execution raises. `environment_error` makes
    `check_environment` fail, `round_error` the same for `start_round`.
    """

    def __init__(
        self,
        results: Mapping[str, ExecutionResult] | None = None,
        errors: Mapping[str, Exception] | None = None,
        environment_error: ExecutionEnvironmentError | None = None,
        leftovers: int = 0,
        round_error: ExecutionEnvironmentError | None = None,
    ) -> None:
        self._results = dict(results or {})
        self._errors = dict(errors or {})
        self._environment_error = environment_error
        self._leftovers = leftovers
        self._round_error = round_error
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

    def start_round(self) -> None:
        self.calls.append("start_round")
        if self._round_error is not None:
            raise self._round_error

    def finish_round(self) -> None:
        self.calls.append("finish_round")

    def sweep_leftovers(self) -> int:
        self.calls.append("sweep_leftovers")
        swept, self._leftovers = self._leftovers, 0
        return swept


FAKE_SECRET = "fake-secret"  # pragma: allowlist secret
FAKE_CONNECTION = f"Server=fake;Database=fake;Password={FAKE_SECRET}"


class FakeDatabaseProvisioner:
    """In-memory `DatabaseProvisioner`: records every call and hands out a fixed string.

    `connection` is what `create_database` returns and `secret` is what `redact` removes.
    `create_error` makes `create_database` fail after recording the call.
    """

    def __init__(
        self,
        connection: str = FAKE_CONNECTION,
        secret: str = FAKE_SECRET,
        create_error: ExecutionEnvironmentError | None = None,
    ) -> None:
        self._connection = connection
        self._secret = secret
        self._create_error = create_error
        self.created: list[tuple[str, str]] = []
        self.dropped: list[tuple[str, str]] = []
        self.calls: list[str] = []

    def start(self) -> None:
        self.calls.append("start")

    def stop(self) -> None:
        self.calls.append("stop")

    def create_database(self, database_name: str, network: str) -> str:
        self.calls.append("create_database")
        self.created.append((database_name, network))
        if self._create_error is not None:
            raise self._create_error
        return self._connection

    def redact(self, text: str) -> str:
        self.calls.append("redact")
        return text.replace(self._secret, "***")

    def drop_database(self, database_name: str, network: str) -> None:
        self.calls.append("drop_database")
        self.dropped.append((database_name, network))
