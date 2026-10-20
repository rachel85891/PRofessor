import json
import logging
import threading
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from hw_checker.config.logging_setup import LOG_FILE_NAME, configure_logging, log_context
from hw_checker.config.settings import LoggingSettings, Settings

LOGGER_NAME = "hw_checker.tests"


@pytest.fixture
def log_file(tmp_path: Path) -> Iterator[Path]:
    root = logging.getLogger()
    original_handlers = list(root.handlers)
    original_level = root.level
    settings = Settings(logging=LoggingSettings(level="DEBUG", log_dir=tmp_path / "logs"))
    configure_logging(settings)
    yield tmp_path / "logs" / LOG_FILE_NAME
    for handler in list(root.handlers):
        if handler not in original_handlers:
            root.removeHandler(handler)
            handler.close()
    root.setLevel(original_level)


def _records(path: Path) -> list[dict[str, Any]]:
    for handler in logging.getLogger().handlers:
        handler.flush()
    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines]


def test_json_line_contains_required_fields(log_file: Path) -> None:
    logging.getLogger(LOGGER_NAME).info("hello %s", "world", extra={"attempt": 2})

    (record,) = _records(log_file)

    assert record["level"] == "INFO"
    assert record["logger"] == LOGGER_NAME
    assert record["message"] == "hello world"
    assert record["attempt"] == 2
    assert "run_id" in record
    assert "repo_id" in record
    timestamp = datetime.fromisoformat(record["timestamp"])
    assert timestamp.utcoffset() == timedelta(0)


def test_exception_is_included(log_file: Path) -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        logging.getLogger(LOGGER_NAME).exception("failed")

    (record,) = _records(log_file)

    assert "ValueError: boom" in record["exception"]


def test_context_is_bound_inside_and_cleared_after(log_file: Path) -> None:
    logger = logging.getLogger(LOGGER_NAME)

    with log_context(run_id="run-1", repo_id="repo-1"):
        logger.info("inside")
    logger.info("after")

    inside, after = _records(log_file)
    assert (inside["run_id"], inside["repo_id"]) == ("run-1", "repo-1")
    assert (after["run_id"], after["repo_id"]) == (None, None)


def test_nested_context_restores_outer_values(log_file: Path) -> None:
    logger = logging.getLogger(LOGGER_NAME)

    with log_context(run_id="run-1", repo_id="repo-1"):
        with log_context(repo_id="repo-2"):
            logger.info("nested")
        logger.info("outer")

    nested, outer = _records(log_file)
    assert (nested["run_id"], nested["repo_id"]) == ("run-1", "repo-2")
    assert (outer["run_id"], outer["repo_id"]) == ("run-1", "repo-1")


def test_context_does_not_leak_between_threads(log_file: Path) -> None:
    logger = logging.getLogger(LOGGER_NAME)
    bound = threading.Event()
    logged = threading.Event()

    def worker() -> None:
        bound.wait()
        logger.info("from worker")
        logged.set()

    thread = threading.Thread(target=worker)
    thread.start()
    with log_context(run_id="run-main", repo_id="repo-main"):
        bound.set()
        logged.wait()
        logger.info("from main")
    thread.join()

    worker_record, main_record = _records(log_file)
    assert (worker_record["run_id"], worker_record["repo_id"]) == (None, None)
    assert (main_record["run_id"], main_record["repo_id"]) == ("run-main", "repo-main")


def test_console_output_is_readable_text(
    log_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Reconfigure so the console handler binds to the stderr that capsys captures.
    configure_logging(Settings(logging=LoggingSettings(log_dir=tmp_path / "logs")))
    with log_context(run_id="run-1"):
        logging.getLogger(LOGGER_NAME).warning("visible")

    console = capsys.readouterr().err

    assert "WARNING" in console
    assert "[run=run-1]" in console
    assert "visible" in console


def test_reconfiguring_does_not_duplicate_handlers(log_file: Path, tmp_path: Path) -> None:
    configure_logging(Settings(logging=LoggingSettings(log_dir=tmp_path / "logs")))
    logging.getLogger(LOGGER_NAME).info("once")

    assert len(_records(log_file)) == 1
