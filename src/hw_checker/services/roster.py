"""Roster service: turns raw roster rows into repos to check, pairs and warnings."""

import logging
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from hw_checker.domain import (
    GitHubRepo,
    Pair,
    RepoTarget,
    RosterResult,
    RosterRow,
    RosterWarning,
    RosterWarningKind,
    Student,
    parse_github_url,
)
from hw_checker.domain.errors import RosterError
from hw_checker.ports import RosterReader

logger = logging.getLogger(__name__)

PAIR_ID_SEPARATOR = "+"
"""Joins member repo ids into a pair id; `+` never appears in a repo id."""


@dataclass(frozen=True)
class _Entry:
    """A usable roster row together with its parsed repo."""

    row: RosterRow
    repo: GitHubRepo


@dataclass(frozen=True)
class _PartnerLookup:
    """The result of looking up an entry's named partner among the usable rows."""

    target: _Entry | None = None
    problem: RosterWarningKind | None = None
    candidates: tuple[_Entry, ...] = ()


class RosterService:
    """Loads a roster and merges its rows into pairs, never dropping a row silently.

    Every input row ends up either in exactly one `RepoTarget` or in at least one
    `RosterWarning`.
    """

    def __init__(self, reader: RosterReader) -> None:
        self._reader = reader

    def load(self, source: Path) -> RosterResult:
        """Read the roster at `source` and return its repos, pairs and warnings.

        Raises:
            RosterError: the reader cannot read the roster.
        """
        rows = self._reader.read_rows(source)
        warnings: list[RosterWarning] = []
        entries = _drop_duplicate_repos(_parse_rows(rows, warnings), warnings)
        groups = _PairMatcher(entries, warnings).match()
        result = _build_result(groups, warnings)
        logger.info(
            "Roster loaded: %d rows, %d repos, %d pairs, %d warnings",
            len(rows),
            len(result.repos),
            len(result.pairs),
            len(result.warnings),
            extra={
                "rows": len(rows),
                "repos": len(result.repos),
                "pairs": len(result.pairs),
                "warnings": len(result.warnings),
            },
        )
        return result

    @staticmethod
    def select_repos(
        repos: tuple[RepoTarget, ...],
        repo_ids: tuple[str, ...] = (),
        limit: int | None = None,
    ) -> tuple[RepoTarget, ...]:
        """Return the repos to check: only `repo_ids` if given, then the first `limit`.

        Roster order is kept. An empty `repo_ids` keeps every repo; a None `limit` keeps
        them all.

        Raises:
            RosterError: a repo id is not in the roster, or `limit` is below 1.
        """
        if limit is not None and limit < 1:
            raise RosterError(f"The repo limit must be at least 1 (got {limit}).")
        known = {repo.repo_id for repo in repos}
        unknown = [repo_id for repo_id in repo_ids if repo_id not in known]
        if unknown:
            raise RosterError(
                f"Not in the roster: {', '.join(unknown)}. Check the repo ids "
                "(owner__name, lowercase)."
            )
        selected = tuple(repo for repo in repos if not repo_ids or repo.repo_id in repo_ids)
        return selected if limit is None else selected[:limit]


def _parse_rows(rows: tuple[RosterRow, ...], warnings: list[RosterWarning]) -> list[_Entry]:
    entries: list[_Entry] = []
    for row in rows:
        repo = parse_github_url(row.github_url)
        if repo is None:
            warnings.append(
                RosterWarning(
                    kind=RosterWarningKind.INVALID_URL,
                    row=row.row_number,
                    message=(
                        f"Row {row.row_number} ({row.name}): '{row.github_url}' is not a GitHub "
                        "repo URL. The row was skipped; check the link."
                    ),
                )
            )
            continue
        entries.append(_Entry(row=row, repo=repo))
    return entries


def _drop_duplicate_repos(entries: list[_Entry], warnings: list[RosterWarning]) -> list[_Entry]:
    first_by_url: dict[str, _Entry] = {}
    kept: list[_Entry] = []
    for entry in entries:
        first = first_by_url.get(entry.repo.url)
        if first is None:
            first_by_url[entry.repo.url] = entry
            kept.append(entry)
            continue
        warnings.append(
            RosterWarning(
                kind=RosterWarningKind.DUPLICATE_REPO,
                row=entry.row.row_number,
                message=(
                    f"Row {entry.row.row_number} ({entry.row.name}) has the same repo as row "
                    f"{first.row.row_number} ({first.row.name}): {entry.repo.url}. Row "
                    f"{first.row.row_number} was kept; check which student owns the repo."
                ),
            )
        )
    return kept


def _normalize_name(name: str) -> str:
    return " ".join(unicodedata.normalize("NFC", name).split()).casefold()


class _PairMatcher:
    """Groups entries into pairs following the roster merge rules, in row order."""

    def __init__(self, entries: list[_Entry], warnings: list[RosterWarning]) -> None:
        self._entries = entries
        self._warnings = warnings
        self._partner_of: dict[int, _Entry | None] = {}
        by_name: dict[str, list[_Entry]] = defaultdict(list)
        for entry in entries:
            by_name[_normalize_name(entry.row.name)].append(entry)
        self._lookups = {entry.row.row_number: self._look_up(entry, by_name) for entry in entries}

    def match(self) -> list[tuple[_Entry, ...]]:
        """Return the groups (pairs of one or two entries) in row order."""
        self._pair_mutual()
        self._pair_with_partnerless()
        for entry in self._entries:
            if not self._is_grouped(entry):
                self._make_single(entry)
        return self._groups()

    @staticmethod
    def _look_up(entry: _Entry, by_name: dict[str, list[_Entry]]) -> _PartnerLookup:
        if entry.row.partner_name is None:
            return _PartnerLookup()
        matches = [
            candidate
            for candidate in by_name.get(_normalize_name(entry.row.partner_name), [])
            if candidate is not entry
        ]
        if not matches:
            return _PartnerLookup(problem=RosterWarningKind.MISSING_PARTNER)
        if len(matches) > 1:
            return _PartnerLookup(
                problem=RosterWarningKind.AMBIGUOUS_PARTNER, candidates=tuple(matches)
            )
        return _PartnerLookup(target=matches[0])

    def _target(self, entry: _Entry) -> _Entry | None:
        return self._lookups[entry.row.row_number].target

    def _is_grouped(self, entry: _Entry) -> bool:
        return entry.row.row_number in self._partner_of

    def _pair(self, first: _Entry, second: _Entry) -> None:
        self._partner_of[first.row.row_number] = second
        self._partner_of[second.row.row_number] = first

    def _pair_mutual(self) -> None:
        for entry in self._entries:
            target = self._target(entry)
            if (
                target is not None
                and self._target(target) is entry
                and not self._is_grouped(entry)
                and not self._is_grouped(target)
            ):
                self._pair(entry, target)

    def _pair_with_partnerless(self) -> None:
        for entry in self._entries:
            target = self._target(entry)
            if (
                target is None
                or self._is_grouped(entry)
                or self._is_grouped(target)
                or target.row.partner_name is not None
            ):
                continue
            self._pair(entry, target)
            self._warn(
                RosterWarningKind.ASYMMETRIC_DUPLICATE,
                entry,
                f"Row {entry.row.row_number} ({entry.row.name}) names "
                f"{target.row.name} (row {target.row.row_number}) as partner, but row "
                f"{target.row.row_number} names no partner. They were paired; confirm the pair.",
            )

    def _make_single(self, entry: _Entry) -> None:
        self._partner_of[entry.row.row_number] = None
        lookup = self._lookups[entry.row.row_number]
        if lookup.problem is RosterWarningKind.MISSING_PARTNER:
            self._warn(
                lookup.problem,
                entry,
                f"Row {entry.row.row_number} ({entry.row.name}) names partner "
                f"'{entry.row.partner_name}', but no row with a valid repo has that name. "
                "Treated as working alone; check the partner's name and row.",
            )
        elif lookup.problem is RosterWarningKind.AMBIGUOUS_PARTNER:
            rows = ", ".join(str(candidate.row.row_number) for candidate in lookup.candidates)
            self._warn(
                lookup.problem,
                entry,
                f"Row {entry.row.row_number} ({entry.row.name}) names partner "
                f"'{entry.row.partner_name}', which matches rows {rows}. Treated as working "
                "alone; check which student is meant.",
            )
        elif lookup.target is not None:
            self._warn(
                RosterWarningKind.ASYMMETRIC_DUPLICATE,
                entry,
                f"Row {entry.row.row_number} ({entry.row.name}) names "
                f"{lookup.target.row.name} (row {lookup.target.row.row_number}) as partner, "
                f"but {self._describe_partner_of(lookup.target)}. Treated as working alone; "
                "check the pair.",
            )

    def _describe_partner_of(self, target: _Entry) -> str:
        paired_with = self._partner_of.get(target.row.row_number)
        if paired_with is not None:
            return (
                f"row {target.row.row_number} is paired with {paired_with.row.name} "
                f"(row {paired_with.row.row_number})"
            )
        return f"row {target.row.row_number} names '{target.row.partner_name}'"

    def _warn(self, kind: RosterWarningKind, entry: _Entry, message: str) -> None:
        self._warnings.append(RosterWarning(kind=kind, row=entry.row.row_number, message=message))

    def _groups(self) -> list[tuple[_Entry, ...]]:
        groups: list[tuple[_Entry, ...]] = []
        seen: set[int] = set()
        for entry in self._entries:
            if entry.row.row_number in seen:
                continue
            partner = self._partner_of[entry.row.row_number]
            group = (entry,) if partner is None else (entry, partner)
            seen.update(member.row.row_number for member in group)
            groups.append(group)
        return groups


def _build_result(groups: list[tuple[_Entry, ...]], warnings: list[RosterWarning]) -> RosterResult:
    repos: list[RepoTarget] = []
    pairs: list[Pair] = []
    for group in groups:
        pair_id = PAIR_ID_SEPARATOR.join(sorted(member.repo.repo_id for member in group))
        students = tuple(
            Student(name=member.row.name, github_url=member.repo.url) for member in group
        )
        pairs.append(Pair(pair_id=pair_id, members=students))
        repos.extend(
            RepoTarget(
                repo_id=member.repo.repo_id,
                owner=member.repo.owner,
                name=member.repo.name,
                url=member.repo.url,
                student=student,
                pair_id=pair_id,
            )
            for member, student in zip(group, students, strict=True)
        )
    ordered_warnings = sorted(warnings, key=lambda warning: warning.row or 0)
    return RosterResult(repos=tuple(repos), pairs=tuple(pairs), warnings=tuple(ordered_warnings))
