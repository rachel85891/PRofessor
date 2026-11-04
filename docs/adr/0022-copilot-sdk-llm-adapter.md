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

## Amendment (stage 14c, 2026-11-04): the runtime's coding-agent rules are removed

The runtime's own system prompt also carries coding-agent rules: answer in 100 words or less (`tone`), batch tool calls (`tool_efficiency`), comment code only where needed (`code_change_rules`) and ask for guidance when unsure (`guidelines`). They conflict with a long structured reply. On a real pilot repo the fixer's plan through Copilot came back as one placeholder edit (an empty or "x" `reason` and `replace`) in 5 of 10 runs, although the model's reasoning had planned the full review. With these four sections removed, 5 of 5 runs gave full plans. Masking the data file did not help, the reply was never cut (`finish_reason=stop`) and no content filter fired.

- Every session now removes `environment_context`, `tone`, `tool_efficiency`, `code_change_rules` and `guidelines` (`options.REMOVED_SECTIONS`). `safety` and the identity preamble stay.
- The runtime silently ignores a section name it does not know (runtime 1.0.105), so the adapter checks each name against the SDK's `SYSTEM_MESSAGE_SECTIONS` and refuses to create the session otherwise.
- The fixer's guardrails reject an edit with a placeholder `reason` (ADR 0021), so a placeholder plan is fed back and never reaches a proposal unnoticed.
- Copilot's added system prompt is smaller: about 1,700 characters instead of about 4,300.

## Amendment (stage 14c fix, 2026-11-04): no strict response format, and role timeouts

With the sections above removed, the pilot reruns still got placeholder plans. Replaying one pilot's fixer prompt through the adapter at the production 60 s timeout gave 5 placeholder plans in 11 runs, each in one attempt of 4 to 15 s: no timeout, abort or retry was involved. The timeout is only the client's wait; the runtime never sees it. The placeholders came fast, with about 100 output tokens and often no reasoning: the model wrote straight into the strict schema and filled it minimally. Without the response format, 6 of 6 runs gave full plans, and with this amendment's adapter 9 of 9 on two pilots did. The writer's empty PR text on a pilot came back filled 3 of 3 times.

- **Structured output** (replaces the decision above): the runtime's strict `responseFormat` is not sent. The prompt ends with a "Reply format" part holding the output type's closed JSON schema as text. The adapter still extracts the JSON object (the last fenced `json` block first) and validates it, with the same one retry. This reverses the rejected alternative "Putting the schema in the prompt instead of the response format".
- `tool_instructions`, `runtime_instructions` and `last_instructions` are removed too. Without them the model once wrote a tool call as text before its reply, and the last one says "Respond concisely to the user".
- **Role timeouts:** `llm.role_timeouts` gives a role its own wait instead of `timeout_seconds` (for both providers). The fixer's calls took up to 28 s on the pilots; `config.yaml` gives it 120 s. A timeout aborts the run and is always an error (retried, then recorded); a run's later output is never read.
