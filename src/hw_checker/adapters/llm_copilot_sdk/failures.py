"""How a failed Copilot call is reported: an outcome for the log, a plain message, and
whether another attempt may help.

The runtime reports a failed model call as a `session.error` event whose `error_type` is
a category such as `authentication`, `authorization`, `policy`, `quota`, `rate_limit` or
`context_limit`, with the HTTP status when there was one.
"""

from copilot.session_events import SessionErrorData

from hw_checker.domain.errors import LLMError

MAX_RUNTIME_MESSAGE_CHARS = 200
REDACTED = "[redacted]"
SERVER_ERROR_STATUS = 500
UNAUTHORIZED_STATUS = 401
FORBIDDEN_STATUS = 403
PAYMENT_REQUIRED_STATUS = 402
TOO_MANY_REQUESTS_STATUS = 429
LOGIN_HINT = (
    "run the Copilot login (see the README, 'The LLM provider'), or set "
    "HW_CHECKER_LLM__COPILOT_TOKEN in .env"
)


class AttemptError(LLMError):
    """One failed attempt: the outcome for the log, and whether a retry may help."""

    def __init__(self, outcome: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.outcome = outcome
        self.retryable = retryable


def not_logged_in() -> AttemptError:
    """The runtime has no Copilot login."""
    return AttemptError("not_logged_in", f"Copilot is not logged in; {LOGIN_HINT}.")


def cli_failed_to_start(reason: str) -> AttemptError:
    """The runtime process could not be downloaded, started or reached."""
    return AttemptError(
        "cli_start",
        f"The Copilot CLI could not start ({reason}); see the README, 'The LLM provider'.",
    )


CLI_STOPPED = "cli_stopped"


def cli_stopped() -> AttemptError:
    """The runtime process exited or its connection broke during a call."""
    return AttemptError(CLI_STOPPED, "The Copilot CLI stopped during the call.")


def timed_out(model: str, seconds: float) -> AttemptError:
    """No reply within the time limit."""
    return AttemptError(
        "timeout", f"The model {model} did not answer within {seconds:g} s.", retryable=True
    )


def unparseable(model: str, type_name: str) -> AttemptError:
    """The reply, after one retry with feedback, still did not fit the output type."""
    return AttemptError("unparseable", f"The reply of {model} could not be read as {type_name}.")


def from_session_error(error: SessionErrorData, model: str, message: str) -> AttemptError:
    """The failure a `session.error` event describes; `message` is its cleaned text."""
    kind = error.error_type.lower()
    status = error.status_code
    if kind == "authentication" or status == UNAUTHORIZED_STATUS:
        return AttemptError(
            "not_logged_in", f"Copilot refused the login, or it expired; {LOGIN_HINT}."
        )
    if kind in {"authorization", "policy"} or status == FORBIDDEN_STATUS:
        return AttemptError(
            "no_seat",
            f"Copilot refused the request for {model}: the account has no Copilot seat, or "
            f"the model is not enabled for it ({message}).",
        )
    if kind == "quota" or status == PAYMENT_REQUIRED_STATUS:
        return AttemptError(
            "allowance", f"The Copilot seat's premium-request allowance is used up ({message})."
        )
    if kind == "rate_limit" or status == TOO_MANY_REQUESTS_STATUS:
        return AttemptError("rate_limit", "Copilot's rate limit was reached.", retryable=True)
    if kind == "context_limit":
        return AttemptError("context_limit", f"The prompt is too long for {model}.")
    if status is not None and status >= SERVER_ERROR_STATUS:
        return AttemptError(
            "server_error", f"The Copilot service failed (HTTP {status}).", retryable=True
        )
    return rejected(message)


def rejected(message: str) -> AttemptError:
    """Any other refusal of the runtime or the service."""
    return AttemptError("rejected", f"Copilot rejected the request: {message}")


def clean_message(text: str, secret: str | None) -> str:
    """The runtime's message on one line, short, and with the token removed, just in case."""
    message = text.replace(secret, REDACTED) if secret else text
    return " ".join(message.split())[:MAX_RUNTIME_MESSAGE_CHARS]
