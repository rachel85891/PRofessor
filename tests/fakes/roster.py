"""In-memory fake for the RosterReader port."""

from collections.abc import Iterable
from pathlib import Path

from hw_checker.domain import RosterRow
from hw_checker.domain.errors import RosterError


class FakeRosterReader:
    """Returns the given rows, or raises the given error, and records each source read."""

    def __init__(self, rows: Iterable[RosterRow] = (), error: RosterError | None = None) -> None:
        self._rows = tuple(rows)
        self._error = error
        self.sources: list[Path] = []

    def read_rows(self, source: Path) -> tuple[RosterRow, ...]:
        self.sources.append(source)
        if self._error is not None:
            raise self._error
        return self._rows
