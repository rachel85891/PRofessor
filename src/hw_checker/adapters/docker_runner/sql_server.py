"""The round's shared SQL Server container and its per-repo databases (ADR 0004).

One container per round, on a network of its own that is internal (no route to the host or
the internet), with no published port. Each repo's app network gets the server attached for
the run under a fixed alias, so an app reaches only the server on its own network, and the
server is detached again at the end. Databases are created and dropped with `sqlcmd` run
inside the server container, so the host needs no SQL client and no new dependency.

The SA password is generated per round and lives only in this object's memory and in the
container's environment. It is used only by this class's own `sqlcmd` calls: an app is given
a login of its own that owns its one database and nothing else (see `create_database`), with
a password generated per database and kept in memory only. No command passed to the engine
and no message raised or logged here holds a secret; `sqlcmd` reads the SA secret
from the container's own environment and a login's secret from the environment of the
one `exec` that creates the login (a `sqlcmd` scripting variable, `LOGIN_PASSWORD_ENV`).
"""

import hashlib
import logging
import re
import secrets
import threading
import time
import uuid
from collections.abc import Callable, Mapping

from hw_checker.adapters.docker_runner.engine import (
    ContainerSpec,
    DockerEngine,
    EngineError,
    ExecResult,
)
from hw_checker.adapters.docker_runner.runner_config import SqlServerConfig
from hw_checker.domain.errors import ExecutionEnvironmentError

logger = logging.getLogger(__name__)

SERVER_KIND = "database-server"
NETWORK_KIND = "database-network"
DATABASE_PREFIX = "hw"
HASH_LENGTH = 8
PASSWORD_PREFIX = "Aa1_"  # pragma: allowlist secret
"""Keeps the generated password within SQL Server's complexity rule (upper, lower, digit)."""
PASSWORD_ENV = "MSSQL_SA_PASSWORD"  # pragma: allowlist secret
LOGIN_PASSWORD_ENV = "HW_LOGIN_PASSWORD"  # pragma: allowlist secret
SQLCMD_SCRIPT = f'exec "$0" -C -S localhost -U "$1" -P "${PASSWORD_ENV}" -b -Q "$2"'
"""Run by `sh -c`: the password is read from the container's environment, never passed in."""
READY_QUERY = "SELECT 1"
KILL_LOGIN_SESSIONS = (
    "DECLARE @kill nvarchar(max) = N''; "
    "SELECT @kill += N'KILL ' + CAST(session_id AS nvarchar(10)) + N'; ' "
    "FROM sys.dm_exec_sessions WHERE login_name = N'{login}'; EXEC(@kill); "
)
"""A login cannot be dropped while it has a session, in any database (it may sit in master)."""
MAX_ERROR_CHARS = 300
_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_]+$")
_UNSAFE_NAME_CHARACTERS = re.compile(r"[^A-Za-z0-9_]")

Clock = Callable[[], float]
Sleep = Callable[[float], None]


def database_name(repo_id: str, run_id: str, max_length: int) -> str:
    """`hw_<repo_id>_<run_id>` with unsafe characters replaced by `_`.

    A name over `max_length` is cut and ends with a short hash of the full name, so two long
    names stay different.
    """
    full = _UNSAFE_NAME_CHARACTERS.sub("_", f"{DATABASE_PREFIX}_{repo_id}_{run_id}")
    if len(full) <= max_length:
        return full
    digest = hashlib.sha256(full.encode("utf-8")).hexdigest()[:HASH_LENGTH]
    return f"{full[: max_length - HASH_LENGTH - 1]}_{digest}"


def validate_database_name(name: str, max_length: int) -> None:
    """Refuse a name that is not made only of letters, digits and `_`, or is too long.

    Raises:
        ValueError: the name is unsafe to put into SQL.
    """
    if not _NAME_PATTERN.fullmatch(name) or len(name) > max_length:
        raise ValueError(
            f"a database name may hold only letters, digits and '_' (at most {max_length}): "
            f"{name!r}"
        )


def _generate_password() -> str:
    return PASSWORD_PREFIX + secrets.token_hex(16)


class SqlServerProvisioner:
    """`DatabaseProvisioner` over the Docker engine port, with a SQL Server container."""

    def __init__(
        self,
        config: SqlServerConfig,
        engine: DockerEngine,
        label_prefix: str,
        clock: Clock = time.monotonic,
        sleep: Sleep = time.sleep,
        password_factory: Callable[[], str] = _generate_password,
        login_password_factory: Callable[[], str] = _generate_password,
    ) -> None:
        self._config = config
        self._engine = engine
        self._label_prefix = label_prefix
        self._clock = clock
        self._sleep = sleep
        self._password_factory = password_factory
        self._login_password_factory = login_password_factory
        self._lock = threading.Lock()
        self._password: str | None = None
        self._login_passwords: dict[str, str] = {}
        self._container: str | None = None
        self._home_network: str | None = None

    # ------------------------------------------------------------------ lifecycle

    def start(self) -> None:
        """Start the server once and wait until it accepts connections.

        Raises:
            ExecutionEnvironmentError: the image is missing, the container cannot be
                created or started, it stops (for example out of memory), or it does not
                answer within the start-up timeout. Nothing is left behind.
        """
        with self._lock:
            if self._container is not None:
                return
            self._password = self._password_factory()
            try:
                self._start_container()
                self._wait_until_ready()
            except EngineError as error:
                reason = self.redact(str(error))
                self._teardown()
                raise ExecutionEnvironmentError(
                    f"cannot start the SQL Server container: {reason}"
                ) from error
            except ExecutionEnvironmentError:
                self._teardown()
                raise

    def stop(self) -> None:
        """Remove the server container and its network; never raises."""
        with self._lock:
            self._teardown()

    def create_database(self, database_name: str, network: str) -> str:
        """Attach the server to `network`, create an empty database and a login that owns
        only that database, and return the app's connection string for the login (secret:
        the caller never logs it, and it never holds the SA password).

        The login has the database's name and is `db_owner` of that database and nothing
        else: it has no server role, the database is its default, and `DENY VIEW ANY
        DATABASE` hides every other database from it. Another database's `guest` user is
        disabled by default, so it cannot enter one even by name.

        Raises:
            ValueError: the name is unsafe to put into SQL.
            ExecutionEnvironmentError: the server is not running, or it refused the request.
                When the login cannot be created the database is dropped again.
        """
        validate_database_name(database_name, self._config.max_database_name_length)
        container = self._require_container()
        try:
            self._engine.connect_network(container, network, (self._config.alias,))
        except EngineError as error:
            raise ExecutionEnvironmentError(
                f"cannot attach the SQL Server to the repo's network: {self.redact(str(error))}"
            ) from error
        self._run_sql(f"CREATE DATABASE [{database_name}]", "create the database")
        password = self._login_password_factory()
        self._login_passwords[database_name] = password
        try:
            self._run_sql(
                self._create_login_sql(database_name),
                "create the database's login",
                {LOGIN_PASSWORD_ENV: password},
            )
        except ExecutionEnvironmentError:
            self._undo_creation(database_name)
            raise
        return self._connection_string(database_name, password)

    def drop_database(self, database_name: str, network: str) -> None:
        """Drop the database (sessions killed first) and then its login (a missing one is
        fine), and detach the server from `network`, even when a step fails.

        Raises:
            ValueError: the name is unsafe to put into SQL.
            ExecutionEnvironmentError: the server refused the drop.
        """
        validate_database_name(database_name, self._config.max_database_name_length)
        container = self._container
        if container is None:
            return
        try:
            self._drop_pair(database_name)
        finally:
            self._detach(container, network)

    # ------------------------------------------------------------------ start-up

    def _start_container(self) -> None:
        config = self._config
        if not self._engine.image_exists(config.image):
            raise ExecutionEnvironmentError(
                f"the SQL Server image is missing: {config.image}; run the environment check"
            )
        suffix = uuid.uuid4().hex[:8]
        self._home_network = f"{self._label_prefix}-sqlserver-net-{suffix}"
        self._engine.create_network(self._home_network, self._labels(NETWORK_KIND), internal=True)
        self._container = self._engine.create_container(
            ContainerSpec(
                name=f"{self._label_prefix}-sqlserver-{suffix}",
                image=config.image,
                command=(),
                labels=self._labels(SERVER_KIND),
                user=config.container_user,
                memory_mb=config.memory_mb,
                cpus=config.cpus,
                pids=config.pids,
                network=self._home_network,
                network_aliases=(config.alias,),
                environment={
                    "ACCEPT_EULA": config.accept_eula,
                    "MSSQL_PID": config.edition,
                    "MSSQL_MEMORY_LIMIT_MB": str(config.mssql_memory_limit_mb),
                    PASSWORD_ENV: self._secret(),
                },
                working_dir="/",
                added_capabilities=config.added_capabilities,
            )
        )
        self._engine.start(self._container)
        logger.info("SQL Server container started; waiting until it accepts connections")

    def _wait_until_ready(self) -> None:
        container = self._require_container()
        started = self._clock()
        deadline = started + self._config.startup_timeout_seconds
        while True:
            self._fail_if_stopped(container)
            if self._probe(container).exit_code == 0:
                logger.info("SQL Server ready after %.1f s", self._clock() - started)
                return
            if self._clock() >= deadline:
                raise ExecutionEnvironmentError(
                    "the SQL Server did not accept connections within "
                    f"{self._config.startup_timeout_seconds:g} s"
                )
            self._sleep(self._config.readiness_poll_seconds)

    def _fail_if_stopped(self, container: str) -> None:
        state = self._engine.state(container)
        if state.running:
            return
        if state.oom_killed:
            raise ExecutionEnvironmentError(
                f"the SQL Server ran out of memory (container limit {self._config.memory_mb} MB): "
                "free memory or lower the number of parallel repos"
            )
        lines = self.redact(self._engine.logs(container)).strip().splitlines()
        last = f": {lines[-1][:MAX_ERROR_CHARS]}" if lines else ""
        raise ExecutionEnvironmentError(
            f"the SQL Server container stopped with exit code {state.exit_code}{last}"
        )

    def _probe(self, container: str) -> ExecResult:
        return self._engine.exec(container, self._sqlcmd(READY_QUERY))

    # ------------------------------------------------------------------ commands

    def _create_login_sql(self, name: str) -> str:
        return (
            f"CREATE LOGIN [{name}] WITH PASSWORD = N'$({LOGIN_PASSWORD_ENV})', "
            f"DEFAULT_DATABASE = [{name}], CHECK_POLICY = OFF, CHECK_EXPIRATION = OFF; "
            f"USE [{name}]; CREATE USER [{name}] FOR LOGIN [{name}]; "
            f"ALTER ROLE db_owner ADD MEMBER [{name}]; "
            f"USE master; DENY VIEW ANY DATABASE TO [{name}];"
        )

    def _drop_pair(self, name: str) -> None:
        """Drop the database, then the login, even when the first step fails.

        When both steps fail the first error is the one raised; the second is logged.
        """
        first_error: ExecutionEnvironmentError | None = None
        steps = (
            (
                f"IF DB_ID(N'{name}') IS NOT NULL BEGIN "
                f"ALTER DATABASE [{name}] SET SINGLE_USER WITH ROLLBACK IMMEDIATE; "
                f"DROP DATABASE [{name}]; END",
                "drop the database",
            ),
            (
                KILL_LOGIN_SESSIONS.format(login=name)
                + f"IF EXISTS (SELECT 1 FROM sys.server_principals WHERE name = N'{name}') "
                f"DROP LOGIN [{name}];",
                "drop the database's login",
            ),
        )
        for sql, purpose in steps:
            try:
                self._run_sql(sql, purpose)
            except ExecutionEnvironmentError as error:
                if first_error is None:
                    first_error = error
                else:
                    logger.warning("Also failed: %s", error)
        self._login_passwords.pop(name, None)
        if first_error is not None:
            raise first_error

    def _undo_creation(self, name: str) -> None:
        """Remove a half-created database and login; a failure here is logged only."""
        try:
            self._drop_pair(name)
        except ExecutionEnvironmentError as error:
            logger.warning("Could not undo a half-created database: %s", error)

    def _run_sql(
        self, sql: str, purpose: str, environment: Mapping[str, str] | None = None
    ) -> None:
        container = self._require_container()
        try:
            result = self._engine.exec(container, self._sqlcmd(sql), environment)
        except EngineError as error:
            raise ExecutionEnvironmentError(
                f"cannot {purpose}: {self.redact(str(error))}"
            ) from error
        if result.exit_code != 0:
            detail = self.redact(result.output.strip())[:MAX_ERROR_CHARS]
            logger.warning(
                "sqlcmd failed to %s (exit code %d): %s", purpose, result.exit_code, detail
            )
            raise ExecutionEnvironmentError(f"the SQL Server could not {purpose}: {detail}")

    def _sqlcmd(self, sql: str) -> tuple[str, ...]:
        config = self._config
        return ("sh", "-c", SQLCMD_SCRIPT, config.sqlcmd_path, config.user, sql)

    def _detach(self, container: str, network: str) -> None:
        try:
            self._engine.disconnect_network(container, network)
        except EngineError as error:
            logger.warning("Could not detach the SQL Server from a repo network: %s", error)

    # ------------------------------------------------------------------ helpers

    def _connection_string(self, database: str, password: str) -> str:
        return (
            f"Server={self._config.alias};Database={database};User Id={database};"
            f"Password={password};Encrypt=True;TrustServerCertificate=True"
        )

    def _secret(self) -> str:
        if self._password is None:
            raise ExecutionEnvironmentError("the SQL Server has not been started")
        return self._password

    def _require_container(self) -> str:
        if self._container is None:
            raise ExecutionEnvironmentError("the SQL Server has not been started")
        return self._container

    def redact(self, text: str) -> str:
        """`text` without any password (the SA's or a login's), wherever it appears."""
        secrets_in_use = [*self._login_passwords.values()]
        if self._password is not None:
            secrets_in_use.append(self._password)
        for secret in secrets_in_use:
            text = text.replace(secret, "***")
        return text

    def _labels(self, kind: str) -> dict[str, str]:
        return {self._label_prefix: "1", f"{self._label_prefix}.kind": kind}

    def _teardown(self) -> None:
        if self._container is not None:
            self._remove(self._engine.remove_container, self._container)
        if self._home_network is not None:
            self._remove(self._engine.remove_network, self._home_network)
        self._container = None
        self._home_network = None
        self._password = None
        self._login_passwords.clear()

    @staticmethod
    def _remove(remove: Callable[[str], None], name: str) -> None:
        try:
            remove(name)
        except EngineError as error:
            logger.warning("Could not remove the SQL Server resource %s: %s", name, error)
