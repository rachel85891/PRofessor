"""Port for calling a language model with structured output."""

from typing import Protocol, TypeVar

from pydantic import BaseModel, Field

from hw_checker.domain.base import DomainModel

OutputT = TypeVar("OutputT", bound=BaseModel)


class TokenUsage(DomainModel):
    """The tokens one call used, as the endpoint reported them (0 when it did not)."""

    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)


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

    def complete_with_usage(
        self, role: str, prompt: str, output_type: type[OutputT]
    ) -> tuple[OutputT, TokenUsage]:
        """Like `complete`, and also return the tokens the call used.

        Raises:
            LLMOutputError: the model answered, but the reply could not be parsed.
            LLMError: the call failed.
        """
        ...
