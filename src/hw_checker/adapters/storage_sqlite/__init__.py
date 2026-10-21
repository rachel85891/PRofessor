"""SQL storage adapter (SQLite now, PostgreSQL by connection string later; ADR 0007)."""

from hw_checker.adapters.storage_sqlite.migrate import migrate
from hw_checker.adapters.storage_sqlite.repositories import (
    SqlFindingRepository,
    SqlProposalRepository,
    SqlRunRepository,
    SqlStorage,
)

__all__ = [
    "SqlFindingRepository",
    "SqlProposalRepository",
    "SqlRunRepository",
    "SqlStorage",
    "migrate",
]
