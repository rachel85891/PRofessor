"""Fix service: one verified review branch and PR text per repo (stage 14, ADR 0021).

The model only plans edits; `fix_edits` decides what is applied, in a separate working copy
at the checked commit. Students' files go into prompts as data inside delimiters. Nothing
here logs code, prompts or replies: only counts, durations and tokens.
"""

import fnmatch
import hashlib
import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from hw_checker.domain import (
    Assignment,
    AssignmentResult,
    EditApplication,
    EditKind,
    ExecutionResult,
    FetchedRepo,
    FixAttempt,
    FixOutcome,
    FixPlan,
    FixProposal,
    FixWorkspace,
    LlmUsage,
    NoteSection,
    PlannedEdit,
    PullRequestText,
    RejectedEdit,
    RepoTarget,
    SelfReview,
    Severity,
    StepStatus,
    VerifyResult,
)
from hw_checker.domain.errors import FixError, LLMOutputError
from hw_checker.ports import (
    Clock,
    GitClient,
    LLMClient,
    ProposalRepository,
    ReportWriter,
    SourceFiles,
    TextTemplates,
)
from hw_checker.ports.llm import TokenUsage
from hw_checker.services.deterministic_checks import DETERMINISTIC_KINDS, DeterministicCheckService
from hw_checker.services.execution import ExecutionService
from hw_checker.services.fix_edits import EditRules, apply_edits, fallback_edit
from hw_checker.services.fix_review import judge, numbered_rules

logger = logging.getLogger(__name__)

FIXER_ROLE = "fixer"
JUDGE_ROLE = "judge"
WRITER_ROLE = "writer"
PLAN_TEMPLATE = "fixer_plan.md.j2"
REVIEW_TEMPLATE = "fixer_review.md.j2"
RULES_TEMPLATE = "fixer_rules.md.j2"
WRITER_TEMPLATE = "pr_text.md.j2"
BODY_TEMPLATE = "pr_body.md.j2"
FIXES_FOLDER = "fixes"
BRANCH_PREFIX = "hw-review/"
STRONG_SEVERITIES = frozenset({Severity.MAJOR, Severity.CRITICAL})
OPENING = "Hi! "
"""How every PR body starts (the approved PRs)."""
MIN_OPENING_CHARS = 20
"""After "Hi!", a shorter opening is not the sentence on what works that the PR needs."""
MIN_ITEM_CHARS = 15
"""A shorter section item cannot describe its edit."""
EDIT_KIND_LABELS = tuple(f"`{kind.value}`" for kind in EditKind)
"""How the writer's prompt used to label edits; an item never repeats them."""
WRITER_FEEDBACK = (
    "\n\n# Feedback on your previous reply\n\n"
    "It could not be used. Write the whole reply again, without these problems:\n"
)
NOTHING_TO_CHANGE = "The fixer found nothing to change, so there is no branch and no PR."
ALL_REJECTED = "No PR: every edit the fixer planned was rejected ({reasons})."
FIX_LABEL = "--fix"
"""Added to the repo id for the recheck's logs and containers: `<repo_id>--fix<attempt>`."""
TRUNCATED = "\n... (cut here: the file is longer)"
CALL_ID_LENGTH = 24
_FILE_CLOSE = "</student_file>"
_DIFF_CLOSE = "</student_diff>"
_KEPT_CLOSE = "</previous_edits>"


@dataclass(frozen=True)
class FixerConfig:
    """The fixer's limits and file selection (`fixer` and `llm` in config.yaml)."""

    max_attempts: int
    max_edits: int
    max_fix_lines: int
    max_file_chars: int
    max_prompt_chars: int
    self_review: bool
    source_suffixes: tuple[str, ...]
    config_globs: tuple[str, ...]
    data_suffixes: tuple[str, ...]
    ignored_globs: tuple[str, ...]
    skip_dirs: tuple[str, ...]
    reply_attempts: int = 1
    """Runs of an LLM step whose reply cannot be parsed (the graph's RetryPolicy)."""
    cache_salt: str = ""
    """Changes whenever a setting that shapes a reply changes (models, limits)."""


class FixService:
    """Prepares the fix working copy, plans and applies edits, reviews them, writes the PR
    text and proposes the branch. Each method is one step of the `fix_repo` subgraph."""

    def __init__(
        self,
        llm: LLMClient,
        git: GitClient,
        files: SourceFiles,
        templates: TextTemplates,
        proposals: ProposalRepository,
        writer: ReportWriter,
        clock: Clock,
        execution: ExecutionService,
        checks: DeterministicCheckService,
        runs_dir: Path,
        config: FixerConfig,
    ) -> None:
        self._llm = llm
        self._git = git
        self._files = files
        self._templates = templates
        self._proposals = proposals
        self._writer = writer
        self._clock = clock
        self._execution = execution
        self._checks = checks
        self._runs_dir = runs_dir
        self._config = config

    @property
    def max_attempts(self) -> int:
        """How many plans a repo gets before failing fixes fall back to their notes."""
        return self._config.max_attempts

    @property
    def reply_attempts(self) -> int:
        """How many times an LLM step runs when its reply cannot be parsed."""
        return self._config.reply_attempts

    def cache_version(self) -> str:
        """Part of every cached LLM step's key: the templates and the reply-shaping settings."""
        return hashlib.sha256(
            f"{self._templates.version()}|{self._config.cache_salt}".encode()
        ).hexdigest()

    def prepare(
        self, run_id: str, assignment_id: str, repo: RepoTarget, fetched: FetchedRepo
    ) -> FixWorkspace:
        """Clone the checked working copy into `<runs>/<run_id>/fixes/<repo_id>`, at the
        checked commit. The checked working copy is never changed.

        Raises:
            GitOperationError: the clone failed.
            FixError: the clone is not at the checked commit.
        """
        copy = self._runs_dir / run_id / FIXES_FOLDER / repo.repo_id
        self._git.clone_local(fetched.working_copy, copy)
        head = self._git.head_sha(copy)
        if head != fetched.commit_sha:
            raise FixError(
                f"The fix working copy is at {head[:12]}, not the checked commit "
                f"{fetched.commit_sha[:12]}."
            )
        return FixWorkspace(
            working_copy=copy,
            base_sha=fetched.commit_sha,
            branch_name=f"{BRANCH_PREFIX}{assignment_id}-{self._clock.now():%Y-%m-%d}",
        )

    def plan(
        self,
        run_id: str,
        workspace: FixWorkspace,
        assignment: Assignment,
        assignments: tuple[Assignment, ...],
        results: Sequence[AssignmentResult],
        attempt: int,
        feedback: Sequence[str] = (),
        kept: Sequence[PlannedEdit] = (),
    ) -> tuple[FixPlan, LlmUsage]:
        """Ask the `fixer` role for a plan of edits. From the second attempt on, the prompt
        shows the previous attempt's `kept` edits and asks for the whole plan again.

        Raises:
            LLMOutputError: the reply could not be read as a plan.
            LLMError: the call failed.
            FixError: the prompt cannot be built within `max_prompt_chars`.
        """
        prompt = self.plan_prompt(
            workspace, assignment, assignments, results, attempt, feedback, kept
        )
        plan, tokens = self._llm.complete_with_usage(FIXER_ROLE, prompt, FixPlan)
        logger.info("Fix plan, attempt %d: %d edits", attempt, len(plan.edits))
        return plan, self._usage(run_id, FIXER_ROLE, prompt, tokens)

    def plan_prompt(
        self,
        workspace: FixWorkspace,
        assignment: Assignment,
        assignments: tuple[Assignment, ...],
        results: Sequence[AssignmentResult],
        attempt: int,
        feedback: Sequence[str] = (),
        kept: Sequence[PlannedEdit] = (),
    ) -> str:
        """The fixer's prompt, within `max_prompt_chars`: when it is too long, files are left
        out from the end (data and configuration first, then code) and listed as not shown.

        Raises:
            FixError: even without any file the prompt is too long.
        """
        paths = self._files.list_files(workspace.working_copy, frozenset(self._config.skip_dirs))
        shown = self._prompt_files(workspace.working_copy, paths)
        values: dict[str, object] = {
            "assignment": assignment,
            "requirements": [r for a in assignments for r in a.requirements],
            "lenient_notes": [note for a in assignments for note in a.lenient_notes],
            "findings": _findings(results),
            "feedback": list(feedback),
            "kept": _kept_text(kept),
            "attempt": attempt,
            "max_attempts": self._config.max_attempts,
            "max_edits": self._config.max_edits,
            "max_fix_lines": self._config.max_fix_lines,
            "data_files": sorted(self._data_files(paths)),
        }
        omitted: list[str] = []
        while True:
            prompt = self._templates.render(
                PLAN_TEMPLATE, values | {"files": shown, "omitted": omitted}
            )
            if len(prompt) <= self._config.max_prompt_chars:
                return prompt
            if not shown:
                raise FixError(
                    f"The fixer's prompt is longer than {self._config.max_prompt_chars} "
                    "characters even without the student's files."
                )
            omitted.insert(0, shown.pop()["path"])

    def apply(self, workspace: FixWorkspace, edits: Sequence[PlannedEdit]) -> EditApplication:
        """Apply `edits` to the fix working copy from the checked commit, keeping only the
        ones that pass every guardrail.

        Raises:
            GitOperationError: the working copy cannot be reset.
            FixError: a file cannot be read or written.
        """
        copy = workspace.working_copy
        self._git.discard_changes(copy)
        paths = self._files.list_files(copy, frozenset(self._config.skip_dirs))
        files = {path: self._files.read_bytes(copy, path) for path in paths}
        rules = EditRules(
            max_edits=self._config.max_edits,
            max_fix_lines=self._config.max_fix_lines,
            data_files=frozenset(self._data_files(paths)),
        )
        result = apply_edits(files, edits, rules)
        for path, data in result.files.items():
            self._files.write_bytes(copy, path, data)
        application = result.application
        logger.info(
            "Edits applied: %d, rejected: %d", len(application.applied), len(application.rejected)
        )
        return application

    def self_review(
        self,
        run_id: str,
        workspace: FixWorkspace,
        assignment: Assignment,
        application: EditApplication,
    ) -> tuple[SelfReview, LlmUsage] | None:
        """Ask the `judge` role which applied edits break the rules. It changes no file, so
        its reply can be cached; `remove_violations` acts on it.

        None (no call) when the self-review is off or nothing was applied.

        Raises:
            LLMOutputError: the reply could not be read.
            LLMError: the call failed.
            GitOperationError: the diff cannot be made.
        """
        if not self._config.self_review or not application.applied:
            return None
        diff = self._git.diff(workspace.working_copy, workspace.base_sha)
        prompt = self._templates.render(
            REVIEW_TEMPLATE,
            {
                "assignment": assignment,
                "edits": application.applied,
                "diff": diff.replace(_DIFF_CLOSE, "<\\/student_diff>"),
            },
        )
        review, tokens = self._llm.complete_with_usage(JUDGE_ROLE, prompt, SelfReview)
        return review, self._usage(run_id, JUDGE_ROLE, prompt, tokens)

    def remove_violations(
        self,
        workspace: FixWorkspace,
        assignment: Assignment,
        application: EditApplication,
        review: SelfReview,
    ) -> EditApplication:
        """Remove the edits the self-review found breaking a rule (kept as rejected, with the
        reason), applying the others again from the checked commit. A violation that does
        not quote its rule, or reports an edit the rules allow outright, is ignored; the
        reasons are kept for the repo report (`fix_review`).

        Raises:
            GitOperationError: the copy cannot be reset.
            FixError: a file cannot be read or written.
        """
        applied = application.applied
        rules = numbered_rules(self._templates.render(RULES_TEMPLATE, {"assignment": assignment}))
        outcome = judge(review, applied, rules)
        broken = outcome.broken
        logger.info(
            "Self-review: %d of %d edits removed, %d violations ignored",
            len(broken),
            len(applied),
            len(outcome.ignored),
        )
        if not broken:
            return application.model_copy(update={"ignored_review": outcome.ignored})
        kept = [edit for index, edit in enumerate(applied) if index not in broken]
        reapplied = self.apply(workspace, kept)
        removed = tuple(
            RejectedEdit(edit=applied[index], reason=reason) for index, reason in broken.items()
        )
        return reapplied.model_copy(
            update={
                "rejected": application.rejected + reapplied.rejected + removed,
                "ignored_review": outcome.ignored,
            }
        )

    def verify(
        self,
        run_id: str,
        repo: RepoTarget,
        workspace: FixWorkspace,
        assignments: tuple[Assignment, ...],
        execution: ExecutionResult | None,
        results: Sequence[AssignmentResult],
        application: EditApplication,
        attempt: int,
    ) -> VerifyResult:
        """Rebuild, rerun and recheck the fix working copy with the round's `assignments`, and
        compare with the checked commit's `execution` and `results`.

        It passes when the build is not worse, no requirement that passed now fails, and
        every requirement a `fix` claims passes. A worse build or a newly failing
        requirement blames every `fix`; an unmet claim blames its own fix, unless a
        requirement it `depends_on` still fails: then it is kept in `blocked`. Nothing is
        run when nothing was applied.

        Raises:
            ExecutionEnvironmentError: the engine cannot be reached.
            InvalidAssignmentError: a requirement names an unknown check.
        """
        if not application.applied:
            return VerifyResult(passed=True)
        failed_before = {f.requirement_id for result in results for f in result.findings}
        copy = workspace.working_copy
        label = f"{repo.repo_id}{FIX_LABEL}{attempt}"
        rechecked = self._execution.execute(run_id, label, copy, assignments)
        after = tuple(self._checks.evaluate(a, copy, rechecked) for a in assignments)
        passed_after = _passing(assignments, after)
        problems: list[str] = []
        if _built(execution) and not _built(rechecked):
            problems.append("the project no longer builds")
        newly_failing = sorted(_passing(assignments, results) - passed_after)
        if newly_failing:
            problems.append(f"requirements that passed now fail: {', '.join(newly_failing)}")
        fixes = [
            (index, edit)
            for index, edit in enumerate(application.applied)
            if edit.kind is EditKind.FIX
        ]
        blamed = {index for index, _ in fixes} if problems else set()
        depends = {r.id: r.depends_on for a in assignments for r in a.requirements}
        blocked: list[str] = []
        for index, fix in fixes:
            unmet: list[str] = []
            for claim in fix.finding_ids:
                if claim in passed_after:
                    continue
                waiting = [d for d in depends.get(claim, ()) if d not in passed_after]
                if waiting:
                    blocked.append(
                        f"`{claim}` can't pass until {_ticked(waiting)} passes "
                        f"(claimed by the fix in {fix.file})"
                    )
                else:
                    unmet.append(claim)
            if unmet:
                problems.append(
                    f"the fix in {fix.file} claims {', '.join(unmet)}, which still fails"
                )
                blamed.add(index)
        logger.info("Recheck, attempt %d: %d problems", attempt, len(problems))
        return VerifyResult(
            passed=not problems,
            problems=tuple(problems),
            failing_fixes=tuple(sorted(blamed)),
            cleared=tuple(sorted(failed_before & passed_after)),
            blocked=tuple(blocked),
        )

    def fall_back(
        self, workspace: FixWorkspace, application: EditApplication, verification: VerifyResult
    ) -> EditApplication:
        """Replace each blamed `fix` with its fallback note (or drop it without one) and apply
        the edits again from the checked commit.

        Raises:
            GitOperationError: the working copy cannot be reset.
            FixError: a file cannot be read or written.
        """
        blamed = set(verification.failing_fixes)
        edits: list[PlannedEdit] = []
        dropped: list[RejectedEdit] = []
        for index, edit in enumerate(application.applied):
            if index not in blamed:
                edits.append(edit)
                continue
            note = fallback_edit(edit)
            if note is not None:
                edits.append(note)
            reason = "the recheck still failed after the last attempt; " + (
                "kept as its fallback note" if note else "dropped (no fallback note)"
            )
            dropped.append(RejectedEdit(edit=edit, reason=reason))
        reapplied = self.apply(workspace, edits)
        return reapplied.model_copy(
            update={
                "rejected": application.rejected + reapplied.rejected + tuple(dropped),
                "ignored_review": application.ignored_review,
            }
        )

    def restore(self, workspace: FixWorkspace, attempt: FixAttempt) -> EditApplication:
        """Apply an earlier attempt's kept edits again from the checked commit, so the working
        copy holds what that attempt's recheck passed.

        Raises:
            GitOperationError: the working copy cannot be reset.
            FixError: a file cannot be read or written, or the edits no longer apply the same.
        """
        reapplied = self.apply(workspace, attempt.application.applied)
        if reapplied.applied != attempt.application.applied:
            raise FixError(f"Attempt {attempt.number}'s edits no longer apply the same way.")
        return attempt.application

    @staticmethod
    def give_up(repo: RepoTarget, attempts: int, verification: VerifyResult) -> FixOutcome:
        """No proposal: the recheck still fails after the fallback notes."""
        return FixOutcome(
            repo_id=repo.repo_id,
            attempts=attempts,
            reason=(
                "No PR: the recheck still failed after the fallback notes ("
                + "; ".join(verification.problems)
                + ")."
            ),
        )

    def write_text(
        self,
        run_id: str,
        assignment: Assignment,
        assignments: tuple[Assignment, ...],
        results: Sequence[AssignmentResult],
        application: EditApplication,
        files_notes: Sequence[str],
        cleared: Sequence[str],
    ) -> tuple[str, str, tuple[LlmUsage, ...]]:
        """Ask the `writer` role for the PR text, and return the title, the rendered body and
        each call's usage. The title is decided here, not by the model, from the requirements
        the last recheck `cleared`; the body's opening starts with "Hi! ", and its "Files"
        section is `files_notes` only.

        Every applied edit needs an item in its section, and the opening a real sentence on
        what works; a reply that misses one is asked for once more, with the problems.

        Raises:
            LLMOutputError: the reply could not be read, or is still unusable after the
                feedback.
            LLMError: the call failed.
            FixError: the body cannot be rendered.
        """
        groups = _sections(application.applied)
        prompt = self._templates.render(
            WRITER_TEMPLATE,
            {
                "assignment": assignment,
                "passed": _passed_titles(assignments, results),
                "sections": [
                    (heading, [_described(edit) for edit in groups[key]])
                    for key, heading in SECTION_HEADINGS.items()
                ],
            },
        )
        text, tokens = self._llm.complete_with_usage(WRITER_ROLE, prompt, PullRequestText)
        usages = [self._usage(run_id, WRITER_ROLE, prompt, tokens)]
        problems = _text_problems(text, groups)
        if problems:
            retry = prompt + WRITER_FEEDBACK + "".join(f"- {problem}\n" for problem in problems)
            text, tokens = self._llm.complete_with_usage(WRITER_ROLE, retry, PullRequestText)
            usages.append(self._usage(run_id, WRITER_ROLE, retry, tokens))
            problems = _text_problems(text, groups)
        if problems:
            raise LLMOutputError("The writer's PR text is unusable: " + "; ".join(problems))
        # A section without edits stays empty, whatever the writer returned for it.
        text = text.model_copy(
            update={key: getattr(text, key) if groups[key] else () for key in SECTION_HEADINGS}
        )
        opening = text.opening.strip()
        if not opening.startswith(OPENING.strip()):
            opening = OPENING + opening
        body = self._templates.render(
            BODY_TEMPLATE, {"opening": opening, "text": text, "files_notes": list(files_notes)}
        )
        title = pr_title(assignment, cleared, assignments)
        return title, body.strip() + "\n", tuple(usages)

    def propose(
        self,
        run_id: str,
        repo: RepoTarget,
        workspace: FixWorkspace,
        assignment: Assignment,
        application: EditApplication,
        title: str,
        body: str,
        attempts: int,
        attempt_choice: str = "",
        blocked: Sequence[str] = (),
    ) -> FixOutcome:
        """Commit the applied edits as one review commit on the review branch and save the
        proposal (pending review, verified); with nothing applied, propose nothing and say why.

        Raises:
            GitOperationError: the branch or the commit cannot be made (for example the
                commit author is not configured).
            StorageError: the diff or the proposal cannot be saved.
        """
        if not application.applied:
            return FixOutcome(
                repo_id=repo.repo_id,
                attempts=attempts,
                reason=_nothing_applied(application),
                ignored_review=application.ignored_review,
                attempt_choice=attempt_choice,
            )
        copy = workspace.working_copy
        self._git.create_branch(copy, workspace.branch_name, workspace.base_sha)
        head = self._git.commit(
            copy, f"{assignment.short_name} review: fixes and questions", application.changed_files
        )
        diff = self._git.diff(copy, workspace.base_sha)
        diff_path = self._writer.write_diff(run_id, repo.repo_id, diff)
        repo_run_id = f"{run_id}/{repo.repo_id}"
        proposal = FixProposal(
            proposal_id=f"{repo_run_id}/fix",
            repo_run_id=repo_run_id,
            repo=repo,
            branch_name=workspace.branch_name,
            base_sha=workspace.base_sha,
            head_sha=head,
            local_repo_dir=copy,
            pr_title=title,
            pr_body=body,
            diff=diff,
            verified=True,
        )
        self._proposals.save(proposal)
        logger.info("Proposal saved: %d edits", len(application.applied))
        return FixOutcome(
            repo_id=repo.repo_id,
            proposal_id=proposal.proposal_id,
            branch_name=workspace.branch_name,
            pr_title=title,
            diff_path=diff_path,
            attempts=attempts,
            edits=len(application.applied),
            ignored_review=application.ignored_review,
            attempt_choice=attempt_choice,
            blocked=tuple(blocked),
        )

    @staticmethod
    def feedback(application: EditApplication, verification: VerifyResult | None) -> list[str]:
        """What went wrong in an attempt, one line each, for the next plan."""
        lines = [
            f"Rejected {rejected.edit.kind.value} in {rejected.edit.file}: {rejected.reason}"
            for rejected in application.rejected
        ]
        if verification is not None and not verification.passed:
            lines += [f"Recheck failed: {problem}" for problem in verification.problems]
        return lines

    def _prompt_files(self, root: Path, paths: Sequence[str]) -> list[dict[str, str]]:
        code = [p for p in paths if PurePosixPath(p).suffix in self._config.source_suffixes]
        code.sort(key=lambda path: (not path.endswith(".cs"), path))
        configs = [p for p in paths if self._matches(p, self._config.config_globs)]
        shown = code + configs + sorted(self._data_files(paths))
        return [{"path": path, "text": self._file_text(root, path)} for path in shown]

    def _file_text(self, root: Path, path: str) -> str:
        text = self._files.read_bytes(root, path).decode("utf-8", errors="replace")
        text = text.removeprefix("﻿").replace("\r\n", "\n")
        if len(text) > self._config.max_file_chars:
            text = text[: self._config.max_file_chars] + TRUNCATED
        return text.replace(_FILE_CLOSE, "<\\/student_file>")

    def _data_files(self, paths: Sequence[str]) -> set[str]:
        return {
            path
            for path in paths
            if PurePosixPath(path).suffix in self._config.data_suffixes
            and not self._matches(path, self._config.config_globs)
            and not self._matches(path, self._config.ignored_globs)
        }

    @staticmethod
    def _matches(path: str, globs: Sequence[str]) -> bool:
        name = PurePosixPath(path).name
        return any(fnmatch.fnmatch(name, glob) for glob in globs)

    @staticmethod
    def _usage(run_id: str, role: str, prompt: str, tokens: TokenUsage) -> LlmUsage:
        call_id = hashlib.sha256(f"{role}\n{prompt}".encode()).hexdigest()[:CALL_ID_LENGTH]
        return LlmUsage(
            run_id=run_id,
            call_id=call_id,
            role=role,
            input_tokens=tokens.input_tokens,
            output_tokens=tokens.output_tokens,
        )


def best_attempt(tried: Sequence[FixAttempt]) -> FixAttempt | None:
    """The attempt a proposal is made of: among those whose recheck passed, the one with the
    most applied edits, the later one on a tie; None when no recheck passed."""
    best: FixAttempt | None = None
    for attempt in tried:
        if attempt.verification.passed and (
            best is None or len(attempt.application.applied) >= len(best.application.applied)
        ):
            best = attempt
    return best


def attempt_choice(tried: Sequence[FixAttempt], fell_back: bool) -> str:
    """For the repo report: which attempt the proposal is made of, and why."""
    if not tried:
        return ""
    total = len(tried)
    if fell_back:
        return (
            f"Attempt {tried[-1].number} of {total} is used, its failing fixes replaced by "
            "their notes: no attempt passed the recheck."
        )
    best = best_attempt(tried)
    if best is None:
        return ""
    others = "; ".join(
        f"attempt {attempt.number}: "
        + (
            f"recheck passed, {_edits(len(attempt.application.applied))}"
            if attempt.verification.passed
            else "recheck failed"
        )
        for attempt in tried
        if attempt.number != best.number
    )
    choice = (
        f"Attempt {best.number} of {total} is used: its recheck passed with "
        f"{_edits(len(best.application.applied))}"
    )
    return f"{choice} (the most of the attempts that passed; {others})." if others else f"{choice}."


def _ticked(requirement_ids: Sequence[str]) -> str:
    return " and ".join(f"`{requirement_id}`" for requirement_id in requirement_ids)


def _edits(count: int) -> str:
    return f"{count} applied edit" + ("" if count == 1 else "s")


def _kept_text(kept: Sequence[PlannedEdit]) -> str:
    """The previous attempt's kept edits as JSON, in the plan's own fields; empty when none."""
    if not kept:
        return ""
    text = json.dumps([edit.model_dump(mode="json") for edit in kept], ensure_ascii=False, indent=2)
    return text.replace(_KEPT_CLOSE, "<\\/previous_edits>")


def pr_title(
    assignment: Assignment, cleared: Sequence[str], assignments: tuple[Assignment, ...]
) -> str:
    """`<short name> review: fixes and questions` when the verified fix branch clears a major
    or critical requirement that failed before (a real bug), otherwise `<short name> review:
    small fixes and questions` (§4).

    `cleared` comes from the last recheck, not from the model's claims; notes only add
    comments, so they never clear anything.
    """
    severity = {r.id: r.severity for a in assignments for r in a.requirements}
    strong = any(severity.get(requirement_id) in STRONG_SEVERITIES for requirement_id in cleared)
    kind = "fixes and questions" if strong else "small fixes and questions"
    return f"{assignment.short_name} review: {kind}"


def _findings(results: Sequence[AssignmentResult]) -> list[dict[str, str]]:
    found = []
    for result in results:
        for finding in result.findings:
            evidence = finding.evidence
            found.append(
                {
                    "requirement_id": finding.requirement_id,
                    "severity": finding.severity.value,
                    "message": finding.message_en.strip(),
                    "evidence": (
                        f"{evidence.file_path}:{evidence.line_start}-{evidence.line_end}"
                        if evidence
                        else ""
                    ),
                }
            )
    return found


SECTION_HEADINGS = {"fixed": "Fixed", "for_you": "For you to do", "names": "Names"}
"""The PR text's sections (`PullRequestText` fields) and their headings in the writer's prompt."""


def _sections(applied: Sequence[PlannedEdit]) -> dict[str, list[PlannedEdit]]:
    """The applied edits by the PR section they belong in: code and data changes under
    "Fixed", and each note in the one section its plan gave it (a question without one)."""
    groups: dict[str, list[PlannedEdit]] = {key: [] for key in SECTION_HEADINGS}
    for edit in applied:
        if edit.kind is not EditKind.NOTE:
            groups["fixed"].append(edit)
        elif edit.note_section is NoteSection.NAMES:
            groups["names"].append(edit)
        else:
            groups["for_you"].append(edit)
    return groups


def _described(edit: PlannedEdit) -> dict[str, object]:
    """An edit as the writer sees it: the file's name, never the edit kind or the full path."""
    return {
        "file_name": PurePosixPath(edit.file).name,
        "reason": edit.reason,
        "comments": _added_comments(edit),
    }


def _text_problems(text: PullRequestText, groups: dict[str, list[PlannedEdit]]) -> list[str]:
    """What makes the writer's reply unusable: a missing or empty item, an opening that is
    not a sentence, or an item that names an edit's kind or full path."""
    problems: list[str] = []
    if len(text.opening.strip().removeprefix(OPENING.strip()).strip()) < MIN_OPENING_CHARS:
        problems.append('opening: after "Hi!" write one or two sentences on what works')
    for key, heading in SECTION_HEADINGS.items():
        edits, items = groups[key], getattr(text, key)
        if not edits:
            continue
        if len(items) < len(edits):
            problems.append(
                f'{key}: {len(items)} items for the {len(edits)} edits under "{heading}"; '
                "write one item per edit"
            )
        if any(len(item.strip()) < MIN_ITEM_CHARS for item in items):
            problems.append(f"{key}: an item is empty or too short to describe its edit")
        paths = {edit.file for edit in edits if "/" in edit.file}
        if any(label in item for item in items for label in EDIT_KIND_LABELS) or any(
            path in item for item in items for path in paths
        ):
            problems.append(f"{key}: items never name the edit kind or the full path")
    return problems


def _passed_titles(
    assignments: tuple[Assignment, ...], results: Sequence[AssignmentResult]
) -> list[str]:
    failing = {
        item
        for result in results
        for item in [f.requirement_id for f in result.findings]
        + [n.requirement_id for n in result.not_evaluated]
    }
    checked = {result.assignment_id for result in results}
    return [
        requirement.title
        for assignment in assignments
        if assignment.id in checked
        for requirement in assignment.requirements
        if requirement.id not in failing
    ]


def _added_comments(edit: PlannedEdit) -> list[str]:
    before = set(edit.find.replace("\r\n", "\n").split("\n"))
    return [
        line.strip().removeprefix("//").strip()
        for line in edit.replace.replace("\r\n", "\n").split("\n")
        if line.strip().startswith("//") and line not in before
    ]


def _built(execution: ExecutionResult | None) -> bool:
    return execution is not None and execution.build_status is StepStatus.SUCCEEDED


def _passing(assignments: tuple[Assignment, ...], results: Sequence[AssignmentResult]) -> set[str]:
    """The deterministic requirements of the checked assignments that have no finding and
    were evaluated."""
    checked = {result.assignment_id for result in results}
    failing = {
        item
        for result in results
        for item in [f.requirement_id for f in result.findings]
        + [n.requirement_id for n in result.not_evaluated]
    }
    return {
        requirement.id
        for assignment in assignments
        if assignment.id in checked
        for requirement in assignment.requirements
        if requirement.kind in DETERMINISTIC_KINDS and requirement.id not in failing
    }


def _nothing_applied(application: EditApplication) -> str:
    """Why there is no proposal: nothing was planned, or every planned edit was rejected."""
    if not application.rejected:
        return NOTHING_TO_CHANGE
    reasons = dict.fromkeys(rejected.reason for rejected in application.rejected)
    return ALL_REJECTED.format(reasons="; ".join(reasons))
