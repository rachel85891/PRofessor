"""LLM client for any OpenAI-compatible endpoint (OpenRouter, GitHub Models, OpenAI)."""

from hw_checker.adapters.llm_openai_compatible.client import (
    LlmClientConfig,
    OpenAICompatibleLLMClient,
)

__all__ = ["LlmClientConfig", "OpenAICompatibleLLMClient"]
