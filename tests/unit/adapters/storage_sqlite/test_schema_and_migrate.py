from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import delete, inspect, select, text

from hw_checker.adapters.storage_sqlite import SqlStorage, mapping, migrate
from hw_checker.adapters.storage_sqlite.database import Database, sqlite_file
from hw_checker.adapters.storage_sqlite.migrate import MIGRATIONS_DIR
from hw_checker.adapters.storage_sqlite.schema import Base, FindingRow, RepoRunRow, RunRow
from hw_checker.domain.errors import StorageError
from tests.unit.storage import builders

TABLES = {
    "runs",
    "repo_runs",
    "findings",
    "not_evaluated",
    "judged_findings",
    "proposals",
    "review_decisions",
}


def url_for(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


@pytest.fixture
def database(tmp_path: Path) -> Iterator[Database]:
    url = url_for(tmp_path / "hw.db")
    migrate(url)
    db = Database(url)
    yield db
    db.dispose()


def test_migrate_creates_the_folder_the_file_and_every_table(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "folder" / "hw.db"

    migrate(url_for(path))

    assert path.is_file()
    db = Database(url_for(path))
    try:
        assert set(inspect(db.engine).get_table_names()) >= TABLES
    finally:
        db.dispose()


def test_migrate_is_idempotent(tmp_path: Path) -> None:
    url = url_for(tmp_path / "hw.db")

    migrate(url)
    migrate(url)

    db = Database(url)
    try:
        with db.engine.connect() as connection:
            versions = connection.execute(text("SELECT version_num FROM alembic_version")).all()
        assert versions == [("0002",)]
    finally:
        db.dispose()


def run_alembic(url: str, action: str, revision: str) -> None:
    """Upgrade or downgrade to one revision, as `migrate()` does for the head."""
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    db = Database(url)
    try:
        with db.engine.begin() as connection:
            config.attributes["connection"] = connection
            getattr(command, action)(config, revision)
    finally:
        db.dispose()


def test_migrate_upgrades_a_0001_database_and_keeps_its_rows(tmp_path: Path) -> None:
    url = url_for(tmp_path / "hw.db")
    run_alembic(url, "upgrade", "0001")
    run = builders.full_run()
    old_repo_run = builders.proposal_repo_run()
    db = Database(url)
    try:
        with db.session("seed 0001") as session:
            run_record = RunRow(run_id=run.run_id)
            mapping.fill_run_row(run_record, run)
            session.add(run_record)
            session.flush()
            repo_record = RepoRunRow(repo_run_id=old_repo_run.repo_run_id, run_id=run.run_id)
            mapping.fill_repo_run_row(repo_record, old_repo_run)
            session.add(repo_record)
            session.flush()
            session.add_all(mapping.finding_rows(old_repo_run))
    finally:
        db.dispose()

    migrate(url)

    storage = SqlStorage(url)
    try:
        (loaded,) = storage.runs.get(run.run_id).repo_runs
        assert loaded == old_repo_run.model_copy(update={"judged_findings": ()})
        assert loaded.assignment_results[0].not_evaluated == ()
        storage.runs.save(run)
        assert storage.runs.get(run.run_id).repo_runs[1:] == run.repo_runs[:2]
    finally:
        storage.close()


def test_downgrade_to_0001_drops_only_the_not_evaluated_table(tmp_path: Path) -> None:
    url = url_for(tmp_path / "hw.db")
    migrate(url)
    storage = SqlStorage(url)
    try:
        storage.runs.save(builders.full_run())
    finally:
        storage.close()

    run_alembic(url, "downgrade", "0001")

    db = Database(url)
    try:
        tables = set(inspect(db.engine).get_table_names())
        assert "not_evaluated" not in tables
        assert tables >= TABLES - {"not_evaluated"}
        with db.session("count") as session:
            assert len(session.scalars(select(RepoRunRow)).all()) == 3
    finally:
        db.dispose()
    migrate(url)


def test_migrated_schema_matches_the_table_models(database: Database) -> None:
    with database.engine.connect() as connection:
        differences = compare_metadata(MigrationContext.configure(connection), Base.metadata)

    assert differences == []


def test_sqlite_connections_use_wal_and_foreign_keys(database: Database) -> None:
    with database.engine.connect() as connection:
        assert connection.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        assert connection.execute(text("PRAGMA foreign_keys")).scalar() == 1


def run_row(run_id: str = "run-1", started_at: datetime | None = None) -> RunRow:
    return RunRow(
        run_id=run_id,
        assignment_id="hw01",
        started_at=started_at or datetime(2026, 10, 21, 9, 0, tzinfo=UTC),
        status="running",
        assignment_versions=[],
    )


def repo_run_row(repo_run_id: str = "run-1/dana") -> RepoRunRow:
    return RepoRunRow(
        repo_run_id=repo_run_id,
        run_id="run-1",
        repo_id="dana__api",
        repo={},
        status="done",
        assignment_ids=[],
        errors=[],
    )


def test_deleting_a_run_cascades_to_its_repo_runs_and_findings(database: Database) -> None:
    with database.session("seed") as session:
        session.add(run_row())
        session.flush()
        session.add(repo_run_row())
        session.flush()
        session.add(
            FindingRow(
                repo_run_id="run-1/dana",
                result_assignment_id="hw01",
                position=0,
                finding_id="f-1",
                requirement_id="hw01-r1",
                assignment_id="hw01",
                severity="major",
                source="llm",
                confidence=0.5,
                message_he="א",
                message_en="a",
                evidence=None,
            )
        )

    with database.session("delete") as session:
        session.execute(delete(RunRow).where(RunRow.run_id == "run-1"))

    with database.session("count") as session:
        assert session.scalars(select(RepoRunRow)).all() == []
        assert session.scalars(select(FindingRow)).all() == []


def test_foreign_keys_are_enforced(database: Database) -> None:
    with pytest.raises(StorageError, match="IntegrityError"), database.session("orphan") as session:
        session.add(repo_run_row())


def test_datetimes_come_back_timezone_aware_in_utc(database: Database) -> None:
    israel = timezone(timedelta(hours=3))
    started = datetime(2026, 10, 21, 12, 30, 15, 123456, tzinfo=israel)
    with database.session("seed") as session:
        session.add(run_row(started_at=started))

    with database.session("read") as session:
        loaded = session.scalars(select(RunRow)).one().started_at

    assert loaded == started
    assert loaded.tzinfo is UTC
    assert loaded.hour == 9


def test_naive_datetime_is_refused(database: Database) -> None:
    naive = datetime(2026, 10, 21, 9, 0)

    with pytest.raises(StorageError), database.session("seed") as session:
        session.add(run_row(started_at=naive))


def test_migrate_error_is_a_storage_error(tmp_path: Path) -> None:
    folder = tmp_path / "a-folder.db"
    folder.mkdir()

    with pytest.raises(StorageError, match="migration failed"):
        migrate(url_for(folder))


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("sqlite:///workspace/hw.db", Path("workspace/hw.db")),
        ("sqlite://", None),
        ("sqlite:///:memory:", None),
        ("postgresql://hw@localhost/hw", None),
    ],
)
def test_sqlite_file_is_found_only_for_file_databases(url: str, expected: Path | None) -> None:
    assert sqlite_file(url) == expected
