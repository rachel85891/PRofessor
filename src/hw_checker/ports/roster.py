"""Port for reading the student roster."""

from pathlib import Path
from typing import Protocol

from hw_checker.domain import RosterRow


class RosterReader(Protocol):
    """Reads raw roster rows; merging rows into pairs is the roster service's job."""

    def read_rows(self, source: Path) -> tuple[RosterRow, ...]:
        """Return every data row of the roster at `source`, in file order.

        Raises:
            RosterError: the source cannot be read or has no usable structure.
        """
        ...
