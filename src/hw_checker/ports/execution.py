"""Ports for building and running student code and for its database.

`CodeRunner` follows ADR 0015. `DatabaseProvisioner` stays provisional until the course
reaches a database.
"""

from typing import Protocol

from hw_checker.domain import ExecutionRequest, ExecutionResult


class CodeRunner(Protocol):
    """Prepares, builds, runs and probes one repo in isolation, and cleans up after it."""

    def check_environment(self) -> None:
        """Check that the execution engine answers and the configured images are present.

        Raises:
            ExecutionEnvironmentError: the engine doesn't answer or an image is missing.
        """
        ...

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        """Prepare, restore, build, run and probe one repo, and always clean up after it.

        Student failures and failures inside a step (including engine errors during a step)
        are reported in the result as a `FailureKind`, not raised. Full logs are written
        under `request.logs_dir`.

        Raises:
            ExecutionEnvironmentError: the engine cannot be reached, so no step could start.
        """
        ...

    def sweep_leftovers(self) -> int:
        """Remove resources left by crashed runs (found by label); return how many.

        Shared resources such as the package cache are kept.

        Raises:
            ExecutionEnvironmentError: the engine cannot be reached.
        """
        ...


class DatabaseProvisioner(Protocol):
    """Creates and drops one database per repo in the shared SQL Server. Provisional."""

    def create_database(self, database_name: str) -> str:
        """Create an empty database named `database_name` and return its connection string.

        Raises:
            ExecutionEnvironmentError: the database server is unavailable or refused the request.
        """
        ...

    def drop_database(self, database_name: str) -> None:
        """Drop the database named `database_name`; dropping a missing database is not an error.

        Raises:
            ExecutionEnvironmentError: the database server is unavailable or refused the request.
        """
        ...
