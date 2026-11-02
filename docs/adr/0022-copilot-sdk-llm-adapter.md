# 0022. Copilot SDK LLM adapter: the developer's Copilot seat, tools always off

- **Status:** Accepted
- **Date:** 2026-11-02

## Context

ADR 0020 used OpenRouter for development and planned a later switch to GitHub Models. GitHub Models was retired on 2026-07-30: its inference API, catalog and BYOK are gone (docs.github.com/en/github-models). That, not the network, is why `models.github.ai` answers a bare `200 OK` on every path.

The developer has a GitHub Copilot seat that their manager provides at no cost to them, and does not want to pay for OpenRouter credit. GitHub's supported way to call Copilot from a program is the GitHub Copilot SDK (`github-copilot-sdk`, generally available). The SDK drives the Copilot runtime over JSON-RPC: on first use it downloads the pinned runtime (1.0.105 for SDK 1.0.28, about 150 MB, checked by SHA-256, from GitHub Releases into `%LOCALAPPDATA%\github-copilot-sdk`) and starts it as a child process. The runtime is self-contained, so it needs no Node.js. Every prompt counts toward the seat's premium requests.

The Copilot runtime is an agent. It can run shell commands, read and edit files, and call MCP servers, including a built-in GitHub MCP server that connects with the logged-in account. Prompts will later carry student code, which is untrusted data.

## Decision

- A second adapter, `adapters/llm_copilot_sdk`, implements `LLMClient`. `llm.provider` picks it (`copilot`) or the OpenAI-compatible adapter (`openrouter`, the default). Switching is configuration only (ADR 0008). The Copilot model of each role is `llm.copilot.roles`. The call limits (`timeout_seconds`, `max_retries`, `retry_backoff_seconds`, `max_prompt_chars`) are shared, and the OpenRouter settings and validators apply only to `openrouter`.
- **Tools are always off.** Every session gets an empty tool allowlist (`available_tools=[]`), no MCP servers, and the built-in `github-mcp-server` disabled by name. A permission handler rejects every request. The working directory is an empty temporary directory, never the repo or a student's code. The runtime's environment context is removed from the system message. Configuration discovery, custom instructions, skills, file hooks, host git operations, the session store, memory and telemetry are all off. The prompt is sent as text only, never as attachments.
- Each call opens a new session and deletes it afterwards, so no prompt is kept in the Copilot home. The runtime process starts once per client, and closing the client stops it. `bootstrap.open_llm_client` is a context manager, so `llm-check` and later callers always close it.
- **Structured output:** the prompt goes out with the output type's JSON schema as the model's response format, in the closed (strict) form: every object lists all its properties as required and allows no others. The runtime does not validate the reply, so the adapter extracts the JSON object and validates it into the Pydantic type. An unreadable reply gets one retry in the same session, with the validation problems as feedback (never the reply); after that it is an `LLMError`.
- **Authentication:** by default the Copilot CLI's logged-in user, after the developer has logged in once with `copilot login` (README). Optionally a token in `HW_CHECKER_LLM__COPILOT_TOKEN`, which the SDK passes to the runtime only through the runtime process's environment. With a token the runtime runs in the SDK's isolated `empty` mode: its own temporary Copilot home, no keychain, no installed plugins. HW Checker never reads, prints or stores the CLI's credentials.
- **Errors** map to `LLMError` with plain messages from the runtime's error category: not logged in (with the login hint), no seat or a model not enabled, premium-request allowance used up, rate limit, prompt too long for the model, server error, timeout, and a CLI that could not start or stopped. As in ADR 0020, only rate limits, timeouts and server errors are retried, with the same backoff, and a prompt longer than `max_prompt_chars` is refused before it is sent.
- Each call is logged on one line: role, model, outcome, duration, attempts, tokens and premium requests as the runtime reports them. The prompt, the reply and the token are never logged.

## Consequences

- The LLM stages can run on the Copilot seat without OpenRouter credit, and nothing above the adapter knows the provider.
- Every call costs premium requests from the seat. On 2026-11-02 the runtime reported 1.0 premium request per call for both `claude-haiku-5.5` and `claude-sonnet-5.5`. The SDK's model list carries no multiplier, so the per-call log is where usage shows.
- Copilot adds its own agent system prompt: about 3,500 input tokens per call even with tools off (2026-11-02).
- `temperature` and `max_output_tokens` do not apply to Copilot; the SDK has no such session options.
- In logged-in mode the runtime uses the developer's own Copilot home (`~/.copilot`), so plugins installed there later for interactive use would load. Their tools stay hidden by the empty allowlist, but plugin hooks are not covered by the session options. Token mode never loads them. The integration test fails if any tool, permission request or MCP server appears in a session.
- The built-in MCP server is disabled by name, because the runtime's list of built-in servers is not public API. A runtime upgrade that adds another built-in server is caught by the integration test, not by the adapter.
- The SDK's own errors (`JsonRpcError`, `ProcessExitedError`) live in its private `copilot._jsonrpc` module. The adapter imports them from there, so an SDK upgrade may need a change.
- Student code still goes to no LLM until the lecturer approves it. The tests and `llm-check` use synthetic text only.

## Rejected alternatives

- GitHub Models: retired on 2026-07-30.
- Calling `api.githubcopilot.com` directly with the OpenAI-compatible adapter: not a supported way to use a Copilot seat from a program, and the token exchange it needs would mean handling the developer's credentials ourselves.
- Relying on the SDK's `empty` mode alone for isolation: it turns off the keychain, so the logged-in user cannot be used. The session options are therefore set explicitly in both modes.
- Putting the schema in the prompt instead of the response format: the SDK has a JSON-schema mode, so it is used, and the reply is still validated here.
