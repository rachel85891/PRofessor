"""Structured logging: JSON lines to a file, readable text on the console.

Every record carries `run_id` and `repo_id`, bound for a scope with `log_context`.
The binding uses contextvars, so it is isolated per thread and per asyncio task.
"""

import json
import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from hw_checker.config.settings import Settings

LOG_FILE_NAME = "hw_checker.jsonl"
FILE_HANDLER_NAME = "hw_checker.file"
CONSOLE_HANDLER_NAME = "hw_checker.console"
CONSOLE_FORMAT = "%(asctime)s %(levelname)-8s %(name)s%(context)s: %(message)s"

_run_id: ContextVar[str | None] = ContextVar("run_id", default=None)
_repo_id: ContextVar[str | None] = ContextVar("repo_id", default=None)

# Attributes every LogRecord has; anything else on a record is an extra field.
_STANDARD_RECORD_ATTRIBUTES = frozenset(
    vars(logging.LogRecord("", logging.INFO, "", 0, "", None, None))
) | {"message", "asctime", "run_id", "repo_id", "context"}


@contextmanager
def log_context(run_id: str | None = None, repo_id: str | None = None) -> Iterator[None]:
    """Bind `run_id` and/or `repo_id` to every log record emitted inside the block.

    An argument left as None keeps the value bound by an outer context.
    The previous values are restored when the block exits.
    """
    run_token = _run_id.set(run_id) if run_id is not None else None
    repo_token = _repo_id.set(repo_id) if repo_id is not None else None
    try:
        yield
    finally:
        if repo_token is not None:
            _repo_id.reset(repo_token)
        if run_token is not None:
            _run_id.reset(run_token)


class ContextFilter(logging.Filter):
    """Copy the bound `run_id` and `repo_id` onto each record."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Attach context attributes; never drops a record."""
        run_id = _run_id.get()
        repo_id = _repo_id.get()
        record.run_id = run_id
        record.repo_id = repo_id
        bound = [f"{key}={value}" for key, value in (("run", run_id), ("repo", repo_id)) if value]
        record.context = f" [{' '.join(bound)}]" if bound else ""
        return True


class JsonFormatter(logging.Formatter):
    """Format a record as one JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        """Serialize the record with its context and extra fields."""
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "run_id": getattr(record, "run_id", None),
            "repo_id": getattr(record, "repo_id", None),
        }
        payload.update(
            (key, value)
            for key, value in vars(record).items()
            if key not in _STANDARD_RECORD_ATTRIBUTES
        )
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(settings: Settings) -> None:
    """Install the console and JSON file handlers on the root logger.

    Call once at startup. Calling it again replaces the handlers it installed before.
    """
    log_dir = settings.logging.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)

    context_filter = ContextFilter()

    file_handler = logging.FileHandler(log_dir / LOG_FILE_NAME, encoding="utf-8")
    file_handler.set_name(FILE_HANDLER_NAME)
    file_handler.setFormatter(JsonFormatter())
    file_handler.addFilter(context_filter)

    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.set_name(CONSOLE_HANDLER_NAME)
    console_handler.setFormatter(logging.Formatter(CONSOLE_FORMAT))
    console_handler.addFilter(context_filter)

    root = logging.getLogger()
    for handler in list(root.handlers):
        if handler.get_name() in {FILE_HANDLER_NAME, CONSOLE_HANDLER_NAME}:
            root.removeHandler(handler)
            handler.close()
    root.addHandler(file_handler)
    root.addHandler(console_handler)
    root.setLevel(settings.logging.level)
