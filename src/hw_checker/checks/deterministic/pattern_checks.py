"""A generic code-pattern check: a pattern that must appear nowhere, or at least once.

The mechanism is here; the patterns, roles and wording are each requirement's params (ADR
0023). Patterns are matched on code (comments and string contents masked), on the text
inside string literals, or on both. A pattern may hold `{dbset}`, replaced before matching
by the names of the `DbSet<T>` properties the repo declares, so it speaks only about those.
The architecture evidence is needed for `roles`/`in` and for `{dbset}` only: a check that
uses neither runs from the files alone.
"""

import re
from typing import Annotated, Literal, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from hw_checker.checks.deterministic.findings import deterministic_finding
from hw_checker.checks.deterministic.layer_support import (
    Problem,
    Role,
    joined,
    line_evidence,
    make_finding,
    read_evidence,
    signals_of,
)
from hw_checker.checks.deterministic.layers import Layers, Roles, layers
from hw_checker.checks.deterministic.messages import (
    PATTERN_FOUND,
    PATTERN_MISSING,
    PATTERN_SCOPE_FILES,
    PATTERN_SCOPE_ROLES,
    PATTERN_WHERE,
    Localized,
)
from hw_checker.checks.deterministic.repository import (
    Suffix,
    checked_files,
    code,
    read_source,
)
from hw_checker.checks.deterministic.source_text import SourceScan, scan_source
from hw_checker.checks.registry import CheckResult, check
from hw_checker.domain import (
    ArchitectureEvidence,
    ArchitectureSignalKind,
    CheckInput,
    NotEvaluatedReason,
)
from hw_checker.domain.assignment import glob_pattern

PLACEHOLDER = "{dbset}"
"""In a pattern: the names of the repo's `DbSet<T>` properties, as an alternation."""
_DUMMY_NAME = "DbSetName"
MAX_MATCH_CHARS = 80
"""A matched text longer than this is cut in a finding's example."""
_CUT = "…"


def _pattern(value: str) -> str:
    if not value.strip():
        raise ValueError("must not be blank")
    try:
        re.compile(value.replace(PLACEHOLDER, _DUMMY_NAME))
    except re.error as error:
        raise ValueError(f"not a valid regular expression ({error})") from error
    return value


def _glob(value: str) -> str:
    if not value.strip():
        raise ValueError("must not be blank")
    return value


Pattern = Annotated[str, AfterValidator(_pattern)]
PathGlob = Annotated[str, AfterValidator(_glob)]


class CodePatternsParams(BaseModel):
    """`patterns`: C# regular expressions (`{dbset}` stands for the repo's `DbSet<T>`
    property names); `where`: `code` (comments and strings masked), `strings` (the text
    inside string literals) or `both`; `must`: `absent` (a finding per match) or `present`
    (a finding when nothing matches); `roles` and `in`: search only the projects of the
    `in` roles (`roles` defines them); `suffixes`: the files searched; `exclude_paths`:
    globs of files left out."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    patterns: tuple[Pattern, ...] = Field(min_length=1)
    where: Literal["code", "strings", "both"] = "code"
    must: Literal["absent", "present"] = "absent"
    roles: Roles | None = None
    in_: tuple[Role, ...] = Field(default=(), alias="in")
    suffixes: tuple[Suffix, ...] = Field(default=(".cs",), min_length=1)
    exclude_paths: tuple[PathGlob, ...] = ()

    @model_validator(mode="after")
    def _roles_and_in_go_together(self) -> Self:
        if self.in_ and self.roles is None:
            raise ValueError("`in` names roles, so `roles` must define them")
        if self.roles is not None and not self.in_:
            raise ValueError("`roles` is only used with `in`")
        unknown = [role for role in self.in_ if self.roles is not None and role not in self.roles]
        if unknown:
            raise ValueError(f"roles not defined in roles: {', '.join(dict.fromkeys(unknown))}")
        return self

    @property
    def uses_db_sets(self) -> bool:
        """Whether a pattern holds `{dbset}`."""
        return any(PLACEHOLDER in pattern for pattern in self.patterns)


@check(
    "code_patterns",
    CodePatternsParams,
    "A code pattern appears nowhere (must: absent) or at least once (must: present): "
    "patterns are C# regexes matched on code without comments and strings (where: code), on "
    "the text inside string literals (strings) or both. in: roles whose projects are "
    "searched (defined in roles), else every file; suffixes, exclude_paths: globs left out. "
    "{dbset} in a pattern stands for the names of the repo's DbSet<T> properties.",
    reads_architecture=True,
)
def code_patterns(check_input: CheckInput, params: CodePatternsParams) -> CheckResult:
    """One finding locating the matches (`absent`), or saying nothing matched (`present`).
    With `in`, a file in no project, or in an unassigned one, is not judged."""
    prepared = _prepare(check_input, params)
    if isinstance(prepared, NotEvaluatedReason):
        return prepared
    patterns, found_layers = prepared
    excluded = [glob_pattern(glob) for glob in params.exclude_paths]
    suffixes = tuple(suffix.lower() for suffix in params.suffixes)
    problems: list[Problem] = []
    for path in checked_files(check_input):
        if not path.lower().endswith(suffixes) or any(g.fullmatch(path) for g in excluded):
            continue
        if found_layers is not None and found_layers.role_of(path) not in params.in_:
            continue
        text = read_source(check_input.repo_dir, path)
        if text is not None:
            problems += _file_problems(path, scan_source(text), patterns, params.where)
    fields = {"where": PATTERN_WHERE[params.where], "scope": _scope(params)}
    if params.must == "present":
        if problems:
            return ()
        message = PATTERN_MISSING.render(**fields)
        return (deterministic_finding(check_input, message, [message.en, *params.patterns]),)
    located = problems[0] if problems else None
    quoted = line_evidence(check_input, located) if located else None
    return make_finding(check_input, PATTERN_FOUND, problems, quoted, **fields)


def _prepare(
    check_input: CheckInput, params: CodePatternsParams
) -> tuple[list[re.Pattern[str]], Layers | None] | NotEvaluatedReason:
    """The compiled patterns (those with `{dbset}` skipped when the repo has no `DbSet`
    property) and the projects by role, or why the check is not evaluated."""
    evidence: ArchitectureEvidence | None = None
    if params.in_ or params.uses_db_sets:
        evidence = read_evidence(check_input, signals=params.uses_db_sets)
        if evidence is None:
            return NotEvaluatedReason.EVIDENCE_UNAVAILABLE
    found_layers = None
    if params.in_ and evidence is not None and params.roles is not None:
        found_layers = layers(evidence, params.roles)
        if found_layers.missing(params.in_):
            return NotEvaluatedReason.DEPENDENCY_FAILED
    names = _db_set_names(evidence) if params.uses_db_sets and evidence is not None else ()
    return _compiled(params.patterns, names), found_layers


def _db_set_names(evidence: ArchitectureEvidence) -> tuple[str, ...]:
    signals = signals_of(evidence, ArchitectureSignalKind.DB_SET_PROPERTY)
    return tuple(sorted({signal.name for signal in signals if signal.name}))


def _compiled(patterns: tuple[str, ...], names: tuple[str, ...]) -> list[re.Pattern[str]]:
    alternation = "|".join(re.escape(name) for name in names)
    return [
        re.compile(pattern.replace(PLACEHOLDER, alternation))
        for pattern in patterns
        if PLACEHOLDER not in pattern or names
    ]


def _file_problems(
    path: str, scan: SourceScan, patterns: list[re.Pattern[str]], where: str
) -> list[Problem]:
    found: list[tuple[int, str]] = []
    if where in ("code", "both"):
        found += _matches(scan.masked, 0, patterns)
    if where in ("strings", "both"):
        found += _string_matches(scan, patterns)
    return [
        Problem(_example(matched), path, scan.line_of(offset)) for offset, matched in sorted(found)
    ]


def _matches(text: str, base: int, patterns: list[re.Pattern[str]]) -> list[tuple[int, str]]:
    """(offset, matched text) of each non-empty match; `base` is where `text` starts."""
    return [
        (base + match.start(), match.group(0))
        for pattern in patterns
        for match in pattern.finditer(text)
        if match.group(0)
    ]


def _string_matches(scan: SourceScan, patterns: list[re.Pattern[str]]) -> list[tuple[int, str]]:
    """Matches in each literal's fixed parts; a part's offset is searched in the file from
    the end of the one before it (an interpolation's hole is not read)."""
    found: list[tuple[int, str]] = []
    for literal in scan.literals:
        cursor = literal.start
        for part in literal.parts:
            if not part:
                continue
            at = scan.text.find(part, cursor, literal.end)
            if at >= 0:
                cursor = at + len(part)
            found += _matches(part, max(at, literal.start), patterns)
    return found


def _example(matched: str) -> Localized:
    text = " ".join(matched.split())
    if len(text) > MAX_MATCH_CHARS:
        text = text[: MAX_MATCH_CHARS - 1] + _CUT
    return Localized(he=code(text), en=code(text))


def _scope(params: CodePatternsParams) -> Localized:
    if not params.in_:
        return PATTERN_SCOPE_FILES.render()
    return PATTERN_SCOPE_ROLES.render(roles=joined(params.in_))
