"""Ports for building and running student code and for its database.

Both ports are provisional: the Docker spike (stage 5a) may revise them.
"""

from collections.abc import Mapping
from pathlib import Path
from typing import Protocol

from hw_checker.domain import ExecutionResult, HttpProbe


class CodeRunner(Protocol):
    """Builds and runs a project in isolation and probes it over HTTP. Provisional."""

    def execute(
        self,
        project_dir: Path,
        probes: tuple[HttpProbe, ...],
        environment: Mapping[str, str],
        logs_dir: Path,
    ) -> ExecutionResult:
        """Build and run the project in `project_dir`, run `probes` in order, then clean up.

        `environment` is passed to the running app (for example connection string overrides).
        Full build and run logs are written under `logs_dir`. Student build or startup
        failures are reported in the result, not raised. Cleanup always runs.

        Raises:
            ExecutionEnvironmentError: the execution environment itself is unavailable.
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
