"""Fix service: one verified review branch and PR text per repo (stage 14, ADR 0021).

The model only plans edits; `fix_edits` decides what is applied, in a separate working copy
at the checked commit. Students' files go into prompts as data inside delimiters. Nothing
here logs code, prompts or replies: only counts, durations and tokens.
"""

import fnmatch
import hashlib
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from hw_checker.domain import (
    Assignment,
    AssignmentResult,
    EditApplication,
    EditKind,
    FetchedRepo,
    FixOutcome,
    FixPlan,
    FixProposal,
    FixWorkspace,
    LlmUsage,
    PlannedEdit,
    PullRequestText,
    RejectedEdit,
    RepoTarget,
    SelfReview,
    Severity,
    VerifyResult,
)
from hw_checker.domain.errors import FixError
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
from hw_checker.services.fix_edits import EditRules, apply_edits

logger = logging.getLogger(__name__)

FIXER_ROLE = "fixer"
JUDGE_ROLE = "judge"
WRITER_ROLE = "writer"
PLAN_TEMPLATE = "fixer_plan.md.j2"
REVIEW_TEMPLATE = "fixer_review.md.j2"
WRITER_TEMPLATE = "pr_text.md.j2"
BODY_TEMPLATE = "pr_body.md.j2"
FIXES_FOLDER = "fixes"
BRANCH_PREFIX = "hw-review/"
STRONG_SEVERITIES = frozenset({Severity.MAJOR, Severity.CRITICAL})
NOTHING_TO_CHANGE = "The fixer found nothing to change, so there is no branch and no PR."
TRUNCATED = "\n... (cut here: the file is longer)"
CALL_ID_LENGTH = 24
_FILE_CLOSE = "</student_file>"
_DIFF_CLOSE = "</student_diff>"


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
        self._runs_dir = runs_dir
        self._config = config

    @property
    def max_attempts(self) -> int:
        """How many plans a repo gets before failing fixes fall back to their notes."""
        return self._config.max_attempts

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
    ) -> tuple[FixPlan, LlmUsage]:
        """Ask the `fixer` role for a plan of edits.

        Raises:
            LLMOutputError: the reply could not be read as a plan.
            LLMError: the call failed.
            FixError: the prompt cannot be built within `max_prompt_chars`.
        """
        prompt = self.plan_prompt(workspace, assignment, assignments, results, attempt, feedback)
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

    def review(
        self,
        run_id: str,
        workspace: FixWorkspace,
        assignment: Assignment,
        application: EditApplication,
    ) -> tuple[EditApplication, LlmUsage | None]:
        """Let the `judge` role check the applied edits against the rules, and remove the
        ones it finds breaking a rule (they are kept as rejected, with the reason).

        Skipped (no call) when the self-review is off or nothing was applied.

        Raises:
            LLMOutputError: the reply could not be read.
            LLMError: the call failed.
            GitOperationError: the diff cannot be made or the copy cannot be reset.
            FixError: a file cannot be read or written.
        """
        applied = application.applied
        if not self._config.self_review or not applied:
            return application, None
        diff = self._git.diff(workspace.working_copy, workspace.base_sha)
        prompt = self._templates.render(
            REVIEW_TEMPLATE,
            {
                "assignment": assignment,
                "edits": applied,
                "diff": diff.replace(_DIFF_CLOSE, "<\\/student_diff>"),
            },
        )
        review, tokens = self._llm.complete_with_usage(JUDGE_ROLE, prompt, SelfReview)
        usage = self._usage(run_id, JUDGE_ROLE, prompt, tokens)
        broken: dict[int, str] = {}
        for violation in review.violations:
            if 0 <= violation.edit_index < len(applied):
                broken.setdefault(
                    violation.edit_index, f"self-review: {violation.rule}: {violation.explanation}"
                )
        logger.info("Self-review: %d of %d edits removed", len(broken), len(applied))
        if not broken:
            return application, usage
        kept = [edit for index, edit in enumerate(applied) if index not in broken]
        reapplied = self.apply(workspace, kept)
        removed = tuple(
            RejectedEdit(edit=applied[index], reason=reason) for index, reason in broken.items()
        )
        return (
            reapplied.model_copy(
                update={"rejected": application.rejected + reapplied.rejected + removed}
            ),
            usage,
        )

    def write_text(
        self,
        run_id: str,
        assignment: Assignment,
        assignments: tuple[Assignment, ...],
        results: Sequence[AssignmentResult],
        application: EditApplication,
        files_notes: Sequence[str],
    ) -> tuple[str, str, LlmUsage]:
        """Ask the `writer` role for the PR text, and return the title, the rendered body and
        the call's usage. The title is decided here, not by the model.

        Raises:
            LLMOutputError: the reply could not be read.
            LLMError: the call failed.
            FixError: the body cannot be rendered.
        """
        prompt = self._templates.render(
            WRITER_TEMPLATE,
            {
                "assignment": assignment,
                "passed": _passed_titles(assignments, results),
                "edits": [
                    {
                        "kind": edit.kind.value,
                        "file": edit.file,
                        "reason": edit.reason,
                        "comments": _added_comments(edit),
                    }
                    for edit in application.applied
                ],
                "files_notes": list(files_notes),
            },
        )
        text, tokens = self._llm.complete_with_usage(WRITER_ROLE, prompt, PullRequestText)
        body = self._templates.render(BODY_TEMPLATE, {"text": text}).strip() + "\n"
        title = pr_title(assignment, application.applied, assignments)
        return title, body, self._usage(run_id, WRITER_ROLE, prompt, tokens)

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
    ) -> FixOutcome:
        """Commit the applied edits as one review commit on the review branch and save the
        proposal (pending review, verified); with nothing applied, propose nothing.

        Raises:
            GitOperationError: the branch or the commit cannot be made (for example the
                commit author is not configured).
            StorageError: the diff or the proposal cannot be saved.
        """
        if not application.applied:
            return FixOutcome(repo_id=repo.repo_id, attempts=attempts, reason=NOTHING_TO_CHANGE)
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


def pr_title(
    assignment: Assignment, applied: Sequence[PlannedEdit], assignments: tuple[Assignment, ...]
) -> str:
    """`<short name> review: fixes and questions` when a `fix` addresses a major or critical
    requirement, otherwise `<short name> review: small fixes and questions` (§4)."""
    severity = {r.id: r.severity for a in assignments for r in a.requirements}
    strong = any(
        severity.get(requirement_id) in STRONG_SEVERITIES
        for edit in applied
        if edit.kind is EditKind.FIX
        for requirement_id in edit.finding_ids
    )
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
