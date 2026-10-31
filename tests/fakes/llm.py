"""In-memory fake for the LLMClient port."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TypeVar

from pydantic import BaseModel

from hw_checker.domain.errors import LLMError

OutputT = TypeVar("OutputT", bound=BaseModel)


@dataclass(frozen=True)
class LLMCall:
    role: str
    prompt: str
    output_type: type[BaseModel]


class FakeLLMClient:
    """Returns scripted replies per role, in order, and records every call.

    `replies` maps a role to the objects its calls return, one per call (the last one is
    repeated). `errors` maps a role to the `LLMError` its calls raise.
    """

    def __init__(
        self,
        replies: Mapping[str, Sequence[BaseModel]] | None = None,
        errors: Mapping[str, LLMError] | None = None,
    ) -> None:
        self._replies = {role: list(objects) for role, objects in (replies or {}).items()}
        self._errors = dict(errors or {})
        self.calls: list[LLMCall] = []

    def complete(self, role: str, prompt: str, output_type: type[OutputT]) -> OutputT:
        self.calls.append(LLMCall(role, prompt, output_type))
        if role in self._errors:
            raise self._errors[role]
        scripted = self._replies.get(role)
        if not scripted:
            raise LLMError(f"No reply scripted for the role '{role}'.")
        reply = scripted.pop(0) if len(scripted) > 1 else scripted[0]
        if not isinstance(reply, output_type):
            raise LLMError(f"The reply scripted for '{role}' is not a {output_type.__name__}.")
        return reply
