# Fix-PR workflow (target for stages 13 and 14)

Written by the planner on 2026-10-29 from the manual pilot of 32 review PRs, which the developer and the lecturer approved (2026-10-31). Publishing (stage 13a, ADR 0018) and the fixer (stage 14) reproduce it exactly.

## 1. Accounts and repos
- **Publishing account:** `seeandbesharp-code-review`, a dedicated review account, never the developer's personal account. Its token is a classic PAT with the `public_repo` scope only, stored as `HW_CHECKER_GITHUB_TOKEN` in `.env` (ignored by git; see `.env.example`), never in logs or reports.
- **One fork per student repo,** in the publishing account, named `check_<student-github-username>`, created once with `default_branch_only: true`, and reused every round.
- **One branch per assignment round:** `hw-review/<assignment-id>-<YYYY-MM-DD>`, based on the commit of the student's default branch (`main` or `master`) that was checked.
- **Commit author:** `seeandbesharp-code-review <243667946+seeandbesharp-code-review@users.noreply.github.com>`, never a personal email.

## 2. Steps per student
1. **Refresh:** fetch the student's default branch. Skip a repo that already has an open review PR from us for this assignment.
2. **Check (before):** the full round (build, run, probes, checks), and record the findings.
3. **Fix (fixer, stage 14):** apply the review rules in section 3, then commit.
4. **Check (after):** re-run the same checks on the fix branch. The branch must build. Every finding the fixer claims to fix must be gone. **No new finding may appear.** Otherwise, drop the patch that caused it.
5. **Developer approval** (approval command, or the review screen in stage 12).
6. **Publish (stage 13):**
   - ensure the fork exists
   - check that the student's default branch still equals the checked commit (if it moved, return to step 2)
   - push the branch, with the token passed only per command, never stored in git config or URLs
   - open the PR on the student's repo: head `seeandbesharp-code-review:<branch>`, base the student's default branch, `maintainer_can_modify: true`
   - publishing again updates the same PR instead of opening a new one
7. **Record** the PR URL and the commit SHAs in storage, and in the reviewer report.

## 3. Review rules (from the developer's example PR and the lecturer, 2026-10-28)

> **Since stage 3f (ADR 0023),** §3 and §4 describe hw01's *data*, not code: the rules are in `course/review_rules.yaml` (every assignment) and `assignments/hw01/review_rules.yaml` (hw01's own), and the PR's opening, sections, title and commit message in `course/course.yaml`. Change them there; the format is in `assignments/README.md`.

- **English comments in the code:** `//fix: ...` on every change the reviewer made. Other `//` comments are questions or instructions for the student.
- **Fix only small, safe things:** a missing `break`, a lost id on update, `readonly` for a fixed path, a stray `using`, a controller class name without the `Controller` suffix (the route doesn't change), a broken data file (for example a missing final newline).
- **Never implement the core of the assignment** (for example an unimplemented GET by id). Leave a question instead.
- **Never add `File.Exists` checks returning `BadRequest`,** and don't comment on it: the path isn't user input.
- **PUT returns `Ok()`:** that's the lecturer's instruction. If PUT is `void`, add a note for the student; don't change it.
- **Don't mention GET all** (not required in HW1), **or layers / a `Models` folder** (taught later).
- **Naming (C# conventions) is in scope,** as notes: PascalCase types, properties and methods; camelCase locals; files named after their type; uncommon file names; unclear abbreviations. **Don't rename stored model properties:** System.Text.Json is case-sensitive, so the existing data file would stop loading. Add a note with the `PropertyNameCaseInsensitive` tip instead.
- **Code level:** second year. No refactoring, and no techniques that haven't been taught.

## 4. PR text
- **Title:** `HW1 review: small fixes and questions` (or `fixes and questions` when a real bug was fixed).
- **Body:** a friendly opening that says what works; "Fixed in this PR"; "For you to do"; "Names (C# conventions)"; "Files". Plain English, short.
