"""Port for calling a language model with structured output."""

from typing import Protocol, TypeVar

from pydantic import BaseModel

OutputT = TypeVar("OutputT", bound=BaseModel)


class LLMClient(Protocol):
    """Calls the model configured for a role; the role-to-model mapping is configuration."""

    def complete(self, role: str, prompt: str, output_type: type[OutputT]) -> OutputT:
        """Send `prompt` to the model for `role` and parse the reply into `output_type`.

        `role` is a role name such as `reviewer`, `judge`, `fixer` or `writer`, never a model
        name.

        Raises:
            LLMError: the call failed, or the reply could not be parsed into `output_type`.
        """
        ...
