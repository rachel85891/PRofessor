# 0020. OpenAI-compatible LLM adapter; OpenRouter for development

- **Status:** Accepted
- **Date:** 2026-10-31

## Context

ADR 0008 put the role → model mapping in configuration but left the provider open (design §10). The plan was GitHub Models (`models.github.ai`), but it is blocked on the developer's network: every request, even an authenticated POST, gets a fake `200 text/plain "OK"` with no GitHub headers. The reviewer (stage 8b), the judge (9) and the fixer (14) all need an LLM, and the developer decided (2026-10-31) not to wait for the network to be fixed.

OpenRouter is reachable and speaks the same OpenAI-compatible chat completions API as GitHub Models and OpenAI. It routes each request to one of several providers, and some of those providers may keep or train on the data they receive.

## Decision

- One adapter, `adapters/llm_openai_compatible`, implements `LLMClient` with `langchain-openai`'s `ChatOpenAI` against any OpenAI-compatible endpoint: `llm.base_url`, the key `HW_CHECKER_LLM__API_KEY` (in `.env`), and a model id per role in `llm.roles`. Replies are structured output (`json_schema`) validated into the caller's Pydantic model.
- Development uses OpenRouter (`https://openrouter.ai/api/v1`).
- On OpenRouter every request carries the provider routing option `data_collection: "deny"`, so providers that may train on the data are never used, and `require_parameters: true`, so only providers that support structured output are used. Both are sent in `llm.extra_body`. The settings refuse an OpenRouter `base_url` whose `extra_body` does not deny data collection.
- Switching to GitHub Models or OpenAI is configuration only: change `base_url`, the model ids and the key, and drop the OpenRouter-only `extra_body`.
- Retries (rate limits, timeouts, server errors), the prompt size limit, error mapping to `LLMError` and per-call logging (role, model, duration, tokens, outcome; never the prompt, the reply or the key) live in the adapter.
- `python -m hw_checker llm-check` sends one tiny synthetic prompt per role, to check the key, the models and the network.

## Consequences

- The LLM stages can start now; nothing above the adapter knows the provider.
- OpenRouter adds its own fee on top of the model price, and routing may pick different providers for the same model; `data_collection: "deny"` narrows the choice.
- No student code or names are sent to any LLM until the lecturer approves it; until then the tests and `llm-check` use synthetic text only.
- Model ids are provider-specific (OpenRouter's `anthropic/...` versus GitHub Models' own names), so a switch means choosing models again.

## Rejected alternatives

- Wait for GitHub Models to be reachable: blocks stages 8b, 9 and 14 for an unknown time.
- An SDK per provider (for example `ChatAnthropic`): the provider would leak into the code, against ADR 0008.
- `langchain-openrouter` (`ChatOpenRouter`): ties the adapter to OpenRouter, while `ChatOpenAI` with a `base_url` works for every OpenAI-compatible endpoint.
