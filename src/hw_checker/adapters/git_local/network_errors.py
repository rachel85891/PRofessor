"""Tells transient network failures of git clone/fetch from permanent ones, by stderr text.

Under parallel load on Windows, git sometimes fails to resolve or reach the host
(`getaddrinfo() thread failed to start`), and the same command succeeds a moment later.
Those failures are worth retrying; a repo that does not exist, or that we may not read,
is not.
"""

import re

_PERMANENT = re.compile(
    r"repository not found|not found|returned error: 40[34]|authentication failed"
    r"|permission denied|access denied|could not read username",
    re.IGNORECASE,
)
_TRANSIENT = (
    ("name resolution", re.compile(r"getaddrinfo|could not resolve host", re.IGNORECASE)),
    ("connection", re.compile(r"connection timed out|connection reset|failed to connect", re.I)),
    ("server error", re.compile(r"the requested url returned error: 5\d\d", re.IGNORECASE)),
    ("interrupted transfer", re.compile(r"early eof|rpc failed", re.IGNORECASE)),
)


def transient_failure(message: str) -> str | None:
    """The class of a transient network failure in `message`, or None when it is not one.

    A message that also shows a missing repo or denied access is never transient.
    """
    if _PERMANENT.search(message):
        return None
    for reason, pattern in _TRANSIENT:
        if pattern.search(message):
            return reason
    return None
