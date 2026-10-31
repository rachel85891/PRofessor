"""Command line: `python -m hw_checker round --assignment hw01 --roster <xlsx>`, and
`python -m hw_checker llm-check`.

This is the only module that writes to stdout and stderr; everything else logs.
"""

import argparse
import sys
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any, TextIO

from pydantic import BaseModel

from hw_checker.bootstrap import (
    build_llm_client,
    build_round_graph_from_settings,
    open_checkpointer,
    require_tracing_off,
)
from hw_checker.config.logging_setup import configure_logging
from hw_checker.config.settings import LLM_ROLES, Settings, load_settings
from hw_checker.domain import RepoSummary, RosterWarning, RunStatus, RunSummary, new_run_id
from hw_checker.domain.errors import HwCheckerError, LLMError
from hw_checker.graph import RoundInput
from hw_checker.ports import LLMClient

EXIT_OK = 0
EXIT_FAILED = 1
LLM_CHECK_PROMPT = (
    "This is a connectivity check; it contains no real data. "
    'Reply with the JSON object {"answer": "pong"}.'
)
"""The synthetic prompt `llm-check` sends to each role's model; never student data."""
LLM_CHECK_ANSWER = "pong"


class LlmCheckReply(BaseModel):
    """The structured reply `llm-check` asks for."""

    answer: str


def build_parser() -> argparse.ArgumentParser:
    """The argument parser: one `round` command for now."""
    parser = argparse.ArgumentParser(prog="python -m hw_checker", description="HW Checker")
    commands = parser.add_subparsers(dest="command", required=True)
    round_parser = commands.add_parser("round", help="check every repo in a roster")
    round_parser.add_argument("--assignment", required=True, help="assignment id, e.g. hw01")
    round_parser.add_argument("--roster", required=True, help="path of the roster .xlsx file")
    round_parser.add_argument(
        "--repo",
        action="append",
        default=[],
        dest="repo_ids",
        metavar="REPO_ID",
        help="check only this repo (owner__name); repeat for more",
    )
    round_parser.add_argument(
        "--limit", type=_positive_int, help="check only the first N repos of the roster"
    )
    commands.add_parser(
        "llm-check",
        help="send one synthetic prompt to each LLM role's model (checks key, models, network)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command in `argv` and return the process exit code."""
    args = build_parser().parse_args(argv)
    try:
        require_tracing_off()
        settings = load_settings()
        configure_logging(settings)
        if args.command == "llm-check":
            return llm_check(settings, sys.stdout)
        return run_round(settings, args, sys.stdout)
    except HwCheckerError as error:
        print(f"Error: {error}", file=sys.stderr)
        return EXIT_FAILED


def run_round(settings: Settings, args: argparse.Namespace, out: TextIO) -> int:
    """Run one round through the graph with a SQLite checkpointer and print its summary.

    The checkpoint thread id is the run id.

    Raises:
        HwCheckerError: the round could not start or finish.
    """
    run_id = new_run_id(datetime.now(UTC))
    round_input = RoundInput(assignment_id=args.assignment, roster_path=args.roster, run_id=run_id)
    if args.repo_ids:
        round_input["repo_ids"] = args.repo_ids
    if args.limit is not None:
        round_input["limit"] = args.limit
    with open_checkpointer(settings) as checkpointer:
        graph = build_round_graph_from_settings(settings, checkpointer)
        output: dict[str, Any] = graph.invoke(round_input, {"configurable": {"thread_id": run_id}})
    summary: RunSummary = output["summary"]
    print_summary(summary, output["roster_warnings"], output.get("round_errors", []), out)
    return EXIT_OK if summary.status is RunStatus.COMPLETED else EXIT_FAILED


def llm_check(
    settings: Settings,
    out: TextIO,
    build_client: Callable[[Settings], LLMClient] = build_llm_client,
    clock: Callable[[], float] = time.monotonic,
) -> int:
    """Send the synthetic prompt to each role's model and print one line per role.

    Returns 0 only when every role answered as asked.

    Raises:
        ConfigurationError: the LLM API key is not set (the message says how to set it).
    """
    client = build_client(settings)
    print(f"LLM endpoint: {settings.llm.base_url}", file=out)
    models = {role: settings.llm.roles[role] for role in LLM_ROLES}
    width = max(len(model) for model in models.values())
    all_ok = True
    for role, model in models.items():
        started = clock()
        try:
            reply = client.complete(role, LLM_CHECK_PROMPT, LlmCheckReply)
        except LLMError as error:
            status, ok = f"error: {error}", False
        else:
            ok = reply.answer.strip().lower() == LLM_CHECK_ANSWER
            status = "ok" if ok else "error: the model did not answer as asked"
        all_ok = all_ok and ok
        print(f"  {role:<8}  {model:<{width}}  {clock() - started:5.1f} s  {status}", file=out)
    return EXIT_OK if all_ok else EXIT_FAILED


def print_summary(
    summary: RunSummary,
    roster_warnings: Sequence[RosterWarning],
    round_errors: Sequence[str],
    out: TextIO,
) -> None:
    """Print one line per repo, the roster warnings, the round's problems, its run id and
    the path of its report."""
    print(f"Round for {summary.assignment_id}: {summary.status.value}", file=out)
    width = max((len(line.repo_id) for line in summary.repos), default=0)
    for line in summary.repos:
        print(f"  {line.repo_id:<{width}}  {_describe(line)}", file=out)
        for error in line.errors:
            print(f"  {'':<{width}}    error: {error}", file=out)
    if not summary.repos:
        print("  No repos were checked.", file=out)
    if roster_warnings:
        print(f"Roster warnings ({len(roster_warnings)}):", file=out)
        for warning in roster_warnings:
            print(f"  - {warning.message}", file=out)
    for error in round_errors:
        print(f"Round error: {error}", file=out)
    print(f"Run id: {summary.run_id}", file=out)
    if summary.round_report is not None:
        print(f"Round report: {summary.round_report}", file=out)


def _describe(line: RepoSummary) -> str:
    return (
        f"{line.status.value:<6}  critical {line.critical}  major {line.major}  "
        f"minor {line.minor}  not evaluated {line.not_evaluated}"
    )


def _positive_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"not a whole number: {text}") from error
    if value < 1:
        raise argparse.ArgumentTypeError(f"must be at least 1: {text}")
    return value
