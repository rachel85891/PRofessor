"""The real llm-check path for one role, against the configured OpenRouter endpoint.

Opt-in: skipped unless HW_CHECKER_LLM__API_KEY is set (in the environment or .env).
Synthetic text only; no student data is ever sent.
"""

import pytest

from hw_checker import cli
from hw_checker.bootstrap import build_openrouter_client
from hw_checker.config.settings import load_settings

pytestmark = pytest.mark.integration

ROLE = "reviewer"


def test_llm_check_prompt_gets_pong_from_the_reviewer_model() -> None:
    settings = load_settings()
    if settings.llm.api_key is None:
        pytest.skip("HW_CHECKER_LLM__API_KEY is not set")

    client = build_openrouter_client(settings)
    reply = client.complete(ROLE, cli.LLM_CHECK_PROMPT, cli.LlmCheckReply)

    assert reply.answer.strip().lower() == cli.LLM_CHECK_ANSWER
