"""Reads whether a repo uses a SQL Server database, and where its app reads the connection.

Everything is read as text from the working copy (nothing is compiled or run), and the
student's files are never written to. Three things are found:

- whether the repo uses SQL Server at all: a project file that references a SQL Server
  package, a `UseSqlServer(` call, or a connection string written in the code. A repo that
  uses only another provider (SQLite, PostgreSQL, in-memory, MySQL) gets no SQL Server;
- the names the app may read its connection by (`ConnectionStrings:<name>`): the keys under
  `ConnectionStrings` in any `appsettings*.json`, and the literal names in the code
  (`GetConnectionString("X")`, `["ConnectionStrings:X"]`,
  `GetSection("ConnectionStrings")["X"]`). The runner gives the run's connection under each
  by an environment variable, which overrides what the JSON files say;
- the string literals in `.cs` files that are a SQL Server connection string (a server key
  and a database key). The runner replaces each by the run's connection, in its temporary
  copy only.

A place that cannot be given the connection (an interpolated or concatenated string, a
name read from a variable, a name that cannot be an environment variable's) is counted by
its reason and never changed. Nothing here records the text of a connection string: it may
hold the student's own server name or password, so only its file and line are kept.
"""

import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from hw_checker.adapters.docker_runner.appsettings import (
    CONNECTION_STRINGS_SECTION,
    connection_names,
    is_settings_file,
)
from hw_checker.adapters.docker_runner.csharp_source import (
    MaskedSource,
    StringLiteral,
    escape_literal_text,
    mask_source,
    source_files,
    walk_files,
)
from hw_checker.domain import (
    DatabaseLocation,
    DatabaseUnresolvedReason,
    DatabaseUse,
    DatabaseUseStatus,
    UnresolvedDatabaseUse,
)

SQL_SERVER_PACKAGES = (
    "microsoft.entityframeworkcore.sqlserver",
    "microsoft.data.sqlclient",
    "system.data.sqlclient",
)
OTHER_PROVIDER_PACKAGES = (
    "microsoft.entityframeworkcore.sqlite",
    "microsoft.entityframeworkcore.inmemory",
    "microsoft.data.sqlite",
    "pomelo.entityframeworkcore.mysql",
    "mysql",
    "npgsql",
)
SQL_SERVER_CALL = re.compile(r"\bUseSqlServer\s*\(")
OTHER_PROVIDER_CALL = re.compile(r"\b(?:UseSqlite|UseNpgsql|UseInMemoryDatabase|UseMySql)\s*\(")
PROJECT_SUFFIX = ".csproj"
ENVIRONMENT_PREFIX = f"{CONNECTION_STRINGS_SECTION}__"

_PACKAGE_REFERENCE = re.compile(
    r"<PackageReference\b[^>]*?\bInclude\s*=\s*(?P<quote>[\"'])(?P<name>[^\"'<>]+)(?P=quote)",
    re.IGNORECASE,
)
_NAME = re.compile(r"[A-Za-z0-9_.-]{1,100}")
"""What a connection's name may be to travel as an environment variable's name."""
_SERVER_KEY = re.compile(r"(?:^|;)\s*(?:server|data source)\s*=", re.IGNORECASE)
_DATABASE_KEY = re.compile(r"(?:^|;)\s*(?:database|initial catalog)\s*=", re.IGNORECASE)
_OTHER_ENGINE_KEY = re.compile(r"(?:^|;)\s*(?:host|port)\s*=", re.IGNORECASE)
"""Keys PostgreSQL and MySQL strings have and a SQL Server string never does."""
_GET_CONNECTION_STRING = re.compile(r"\.\s*GetConnectionString\s*\(\s*")
_GET_SECTION = re.compile(r"\.\s*GetSection\s*\(\s*")
_SECTION_KEY = re.compile(rf"{CONNECTION_STRINGS_SECTION}:(?P<name>.+)", re.IGNORECASE)


@dataclass(frozen=True)
class DatabasePlan:
    """What a repo needs from the run's database, and where to give it."""

    status: DatabaseUseStatus
    connection_names: tuple[str, ...] = ()
    literals: tuple[DatabaseLocation, ...] = ()
    unresolved: tuple[UnresolvedDatabaseUse, ...] = ()

    @property
    def uses_database(self) -> bool:
        """True when the repo uses SQL Server, so the run needs a database."""
        return self.status is DatabaseUseStatus.USED

    @property
    def literal_files(self) -> tuple[str, ...]:
        """The files that hold a connection string to replace, each once."""
        return tuple(dict.fromkeys(literal.file_path for literal in self.literals))

    def use(self) -> DatabaseUse:
        """The record of this plan: its places only when the repo uses a database."""
        if not self.uses_database:
            return DatabaseUse(status=self.status)
        return DatabaseUse(
            status=self.status,
            connection_names=self.connection_names,
            rewritten_literals=self.literals,
            unresolved=self.unresolved,
        )


def plan_database(working_copy: Path) -> DatabasePlan:
    """Read the working copy and plan the database the repo needs."""
    packages = _packages(working_copy)
    names: set[str] = set()
    unresolved: Counter[DatabaseUnresolvedReason] = Counter()
    for key in _settings_names(working_copy):
        _add_name(key, names, unresolved)
    literals: list[DatabaseLocation] = []
    sql_call = other_call = False
    for source in source_files(working_copy):
        text = _read(source)
        if text is None:
            continue
        masked = mask_source(text)
        relative = source.relative_to(working_copy).as_posix()
        sql_call = sql_call or SQL_SERVER_CALL.search(masked.text) is not None
        other_call = other_call or OTHER_PROVIDER_CALL.search(masked.text) is not None
        for found in _classify(text, masked):
            if found.complete:
                literals.append(DatabaseLocation(file_path=relative, line=found.literal.line))
            else:
                unresolved[DatabaseUnresolvedReason.BUILT_LITERAL] += 1
        for name in _code_names(text, masked, unresolved):
            _add_name(name, names, unresolved)
    uses = bool(literals) or sql_call or any(_is_sql_server(name) for name in packages)
    if not uses:
        other = other_call or any(_is_other_provider(name) for name in packages)
        status = (
            DatabaseUseStatus.NOT_USED_OTHER_PROVIDER
            if other
            else DatabaseUseStatus.NOT_USED_NO_DATABASE
        )
        return DatabasePlan(status)
    return DatabasePlan(
        DatabaseUseStatus.USED,
        tuple(sorted(names)),
        tuple(literals),
        tuple(
            UnresolvedDatabaseUse(reason=reason, count=unresolved[reason])
            for reason in sorted(unresolved)
        ),
    )


def rewrite_connection_literals(text: str, connection: str) -> tuple[int, str]:
    """`text` with every SQL Server connection string literal replaced by `connection`
    (escaped for the literal's kind), and how many were replaced."""
    found = [item.literal for item in _classify(text, mask_source(text)) if item.complete]
    for literal in sorted(found, key=lambda item: -item.content_start):
        text = (
            text[: literal.content_start]
            + escape_literal_text(connection, literal)
            + text[literal.content_end :]
        )
    return len(found), text


def apply_connection(
    plan: DatabasePlan, working_copy: Path, overrides: dict[str, bytes], connection: str
) -> dict[str, bytes]:
    """`overrides` plus the files whose connection strings are replaced by `connection`.

    A file the overrides already change (a path fix) is rewritten from its new text.
    """
    result = dict(overrides)
    for relative in plan.literal_files:
        data = result.get(relative)
        text = data.decode("utf-8") if data is not None else _read(working_copy / relative)
        if text is None:
            continue
        _, new_text = rewrite_connection_literals(text, connection)
        result[relative] = new_text.encode("utf-8")
    return result


# ---------------------------------------------------------------------- the project files


def _packages(working_copy: Path) -> list[str]:
    """The lowercased names of the packages the repo's project files reference."""
    names: list[str] = []
    for path in walk_files(working_copy):
        if path.suffix.lower() != PROJECT_SUFFIX:
            continue
        text = _read(path)
        if text is not None:
            names += [match["name"].strip().lower() for match in _PACKAGE_REFERENCE.finditer(text)]
    return names


def _is_sql_server(package: str) -> bool:
    return any(package == name or package.startswith(f"{name}.") for name in SQL_SERVER_PACKAGES)


def _is_other_provider(package: str) -> bool:
    return any(package.startswith(name) for name in OTHER_PROVIDER_PACKAGES)


def _settings_names(working_copy: Path) -> list[str]:
    names: list[str] = []
    for path in walk_files(working_copy):
        if is_settings_file(path.name):
            text = _read(path)
            names += (connection_names(text) if text is not None else None) or []
    return names


def _read(path: Path) -> str | None:
    try:
        return path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _add_name(name: str, names: set[str], unresolved: Counter[DatabaseUnresolvedReason]) -> None:
    if _NAME.fullmatch(name):
        names.add(name)
    else:
        unresolved[DatabaseUnresolvedReason.NAME_NOT_USABLE] += 1


# ---------------------------------------------------------------------- literals


@dataclass(frozen=True)
class _Found:
    """A literal that is, or is a piece of, a SQL Server connection string."""

    literal: StringLiteral
    complete: bool
    """A whole string to replace; otherwise it is built (interpolated or concatenated)."""


def _classify(text: str, masked: MaskedSource) -> list[_Found]:
    found: list[_Found] = []
    for literal in masked.literals:
        content = text[literal.content_start : literal.content_end]
        whole = _looks_like_connection(content)
        built = literal.interpolated or _joined(masked.text, literal)
        if built and (whole or _has_key(content)):
            found.append(_Found(literal, complete=False))
        elif whole:
            found.append(_Found(literal, complete=True))
    return found


def _looks_like_connection(content: str) -> bool:
    return (
        ";" in content
        and _SERVER_KEY.search(content) is not None
        and _DATABASE_KEY.search(content) is not None
        and _OTHER_ENGINE_KEY.search(content) is None
    )


def _has_key(content: str) -> bool:
    return _SERVER_KEY.search(content) is not None or _DATABASE_KEY.search(content) is not None


def _joined(masked: str, literal: StringLiteral) -> bool:
    """True when the literal is an operand of a `+`, so it is a piece of a built string."""
    start, end = _token(masked, literal)
    return _previous(masked, start) == "+" or _following(masked, end) == "+"


# ---------------------------------------------------------------------- names in code


def _code_names(
    text: str, masked: MaskedSource, unresolved: Counter[DatabaseUnresolvedReason]
) -> list[str]:
    """The connection names a source reads by literal; what it reads by anything else is
    counted in `unresolved`."""
    tokens = {_token(masked.text, literal)[0]: literal for literal in masked.literals}
    names: list[str] = []
    for call in _GET_CONNECTION_STRING.finditer(masked.text):
        literal = tokens.get(call.end())
        name = _plain_name(masked.text, literal, closing=")")
        if name is None:
            unresolved[DatabaseUnresolvedReason.NAME_NOT_READ] += 1
        else:
            names.append(name)
    for section in _GET_SECTION.finditer(masked.text):
        names += _section_indexer(masked, tokens, section.end(), unresolved)
    for literal in masked.literals:
        key = None if literal.interpolated else _SECTION_KEY.fullmatch(literal.value)
        if key is not None:
            names.append(key["name"])
    return names


def _section_indexer(
    masked: MaskedSource,
    tokens: dict[int, StringLiteral],
    position: int,
    unresolved: Counter[DatabaseUnresolvedReason],
) -> list[str]:
    """The name of `GetSection("ConnectionStrings")["X"]`, whose `GetSection(` ends at
    `position`; nothing for any other section."""
    section = tokens.get(position)
    if (
        section is None
        or _plain_name(masked.text, section, closing=")") is None
        or section.value.lower() != CONNECTION_STRINGS_SECTION.lower()
    ):
        return []
    after = _skip_space(masked.text, _token(masked.text, section)[1])
    after = _skip_space(masked.text, after + 1)  # past the `)`
    if masked.text[after : after + 1] != "[":
        return []
    name = _plain_name(masked.text, tokens.get(_skip_space(masked.text, after + 1)), closing="]")
    if name is None:
        unresolved[DatabaseUnresolvedReason.NAME_NOT_READ] += 1
        return []
    return [name]


def _plain_name(masked: str, literal: StringLiteral | None, closing: str) -> str | None:
    """The value of a literal that is the whole of a name (nothing is added to it, and it
    is followed by `closing`), or None."""
    if literal is None or literal.interpolated:
        return None
    if _following(masked, _token(masked, literal)[1]) != closing:
        return None
    return literal.value


# ---------------------------------------------------------------------- positions


def _token(masked: str, literal: StringLiteral) -> tuple[int, int]:
    """Where the literal, with its prefixes and quotes, starts and ends in `masked`."""
    start = literal.content_start
    while start > 0 and masked[start - 1] in '"@$':
        start -= 1
    end = literal.content_end
    while end < len(masked) and masked[end] == '"':
        end += 1
    return start, end


def _skip_space(masked: str, position: int) -> int:
    while position < len(masked) and masked[position].isspace():
        position += 1
    return position


def _following(masked: str, position: int) -> str:
    """The first character that is not white space at or after `position`."""
    position = _skip_space(masked, position)
    return masked[position] if position < len(masked) else ""


def _previous(masked: str, position: int) -> str:
    """The last character that is not white space before `position`."""
    position -= 1
    while position >= 0 and masked[position].isspace():
        position -= 1
    return masked[position] if position >= 0 else ""
