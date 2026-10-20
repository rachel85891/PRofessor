"""Machine-readable `git log` format and its parser."""

from datetime import datetime

from hw_checker.domain import CommitInfo

FIELD_SEPARATOR = "\x1f"
"""ASCII unit separator between fields; records are separated by NUL (`git log -z`)."""

LOG_FORMAT = "%H%x1f%an%x1f%aI%x1f%B"
"""Full sha, author name, strict ISO 8601 author date, full message (subject and body)."""

_FIELD_COUNT = 4


def parse_log_output(output: str) -> tuple[CommitInfo, ...]:
    """Parse `git log -z --format=LOG_FORMAT` output into commits, newest first.

    Raises:
        ValueError: a record does not have the expected fields or date format.
    """
    commits: list[CommitInfo] = []
    for record in output.split("\0"):
        record = record.lstrip("\n")
        if not record:
            continue
        fields = record.split(FIELD_SEPARATOR, _FIELD_COUNT - 1)
        if len(fields) != _FIELD_COUNT:
            raise ValueError(f"unexpected git log record: {record[:80]!r}")
        sha, author_name, authored_at, message = fields
        commits.append(
            CommitInfo(
                sha=sha,
                author_name=author_name,
                authored_at=datetime.fromisoformat(authored_at),
                message=message.rstrip(),
            )
        )
    return tuple(commits)
