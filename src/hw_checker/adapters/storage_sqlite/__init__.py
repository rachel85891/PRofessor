"""SQL storage adapter (SQLite now, PostgreSQL by connection string later; ADR 0007)."""

from hw_checker.adapters.storage_sqlite.migrate import migrate

__all__ = ["migrate"]
