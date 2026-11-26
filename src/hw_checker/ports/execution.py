"""Ports for building and running student code and for its database.

`CodeRunner` follows ADR 0015, `DatabaseProvisioner` ADR 0004.
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

    def start_round(self) -> None:
        """Start what the whole round shares (the database server, when databases are on).

        Raises:
            ExecutionEnvironmentError: it cannot start; nothing is left running.
        """
        ...

    def finish_round(self) -> None:
        """Remove what `start_round` started; never raises and is safe to call twice."""
        ...

    def sweep_leftovers(self) -> int:
        """Remove resources left by crashed runs (found by label); return how many.

        Shared resources such as the package cache are kept.

        Raises:
            ExecutionEnvironmentError: the engine cannot be reached.
        """
        ...


class DatabaseProvisioner(Protocol):
    """One shared database server per round and an empty database per repo (ADR 0004)."""

    def start(self) -> None:
        """Start the server and wait until it accepts connections; starting twice is fine.

        Raises:
            ExecutionEnvironmentError: the server cannot start (memory, image, timeout).
        """
        ...

    def stop(self) -> None:
        """Remove the server and everything it created; never raises."""
        ...

    def create_database(self, database_name: str, network: str) -> str:
        """Make the server reachable on `network` and create an empty database there.

        Returns the connection string from the app's point of view. It is a secret: never
        log it or put it in a report or a message.

        Raises:
            ValueError: `database_name` is not made only of letters, digits and `_`.
            ExecutionEnvironmentError: the server is not running or refused the request.
        """
        ...

    def redact(self, text: str) -> str:
        """`text` with every secret this provisioner handed out (a password, and so a
        connection string's) replaced by `***`, wherever it appears. For text an app may
        have printed. A secret is only known while its database exists, so redact before
        the database is dropped.
        """
        ...

    def drop_database(self, database_name: str, network: str) -> None:
        """Drop the database (a missing one is fine) and make the server unreachable on
        `network` again, even when the drop fails.

        Raises:
            ValueError: `database_name` is not made only of letters, digits and `_`.
            ExecutionEnvironmentError: the server refused the request.
        """
        ...
