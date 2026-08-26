---
name: hunk-review-loop
description: >-
  Drives the interactive hunk-by-hunk approval loop that must precede any git
  commit of code changes. Load this before showing diffs, requesting commit
  approval, or running git commit. Covers review depth, diff-state triage,
  per-hunk presentation, approval choices, skip and abort handling, non-hunk
  diffs, trivial-hunk grouping, and verbal overrides. Dispatches the
  hunk-reviewer skill for each hunk.
---

# Hunk Review Loop

Never run `git commit` or `git push` without completing this loop in the current
turn. Approval of a plan or approach does not authorize the commit.

## Review depth

**Fast path**, one presentation and one approval for the whole diff: every hunk
is mechanical per the closed list in Model Authority, or the change is docs-only
or comment-only.

**Full loop**, hunk by hunk: everything else. This is the default.

If any hunk falls outside the closed list, or the diff mixes mechanical and
substantive changes, the whole change takes the full loop. Uncertain means full
loop.

Behavioural files are never docs-only, whatever their extension: any `SKILL.md`,
any `copilot-instructions.md`, `AGENTS.md`, `CLAUDE.md`, or anything under
`configs/` that installs into an agent's instructions. Changes to these always
take the full loop.

The fast path never applies, at any size, to changes touching secrets or
authentication, IAM policies or security groups, alert rules, or production IaC
values. A one-line version pin in a prod chart is not a trivial change.

## 1. Determine diff state

- **Mixed** (both `git diff --cached` and `git diff` show output): review staged
  changes first. After those are approved, ask Ali whether to also stage and
  review the unstaged changes or leave them for a separate commit.
- **Staged only** (`git diff --cached` shows output, `git diff` does not): review
  staged changes. This is the source of truth for what will be committed.
- **Unstaged only** (`git diff` shows output, `git diff --cached` does not):
  review unstaged changes, then stage them before commit.

## 2. Present each hunk

For each hunk (one `@@` block), sequentially:

- Show the **raw unified diff** in a fenced code block. Include the diff file
  header (`--- a/path`, `+++ b/path`) and the `@@` context line.
- Dispatch an `explore` subagent (fast tier, any ecosystem) with the hunk and the
  post-change file content for a code review. Prefer an ecosystem other than the
  one that wrote the change. The subagent is a reporter: it surfaces findings for
  Ali, it does not approve or block. Use the prompt from the `hunk-reviewer`
  skill. If the subagent fails, times out, or returns unusable output, proceed
  without it: show the diff and explanation, and note "Review unavailable" in
  place of the summary.
- Below the diff, present: (a) the subagent review summary (or "Review
  unavailable"); (b) what the code does technically; (c) why this change was made.

## 3. Ask for approval

Call `ask_user`; never ask for approval in plain text.

- Non-final hunk (more hunks remain in this file or other files):
  `choices=["Approve: next hunk", "Skip this file", "Request changes", "Abort"]`
- Final hunk (last hunk of the last file to review):
  `choices=["Approve: commit and push", "Skip this file", "Request changes", "Abort"]`
- If "Skip this file" is selected on the last remaining file, present the
  subagent summary for skipped hunks, then ask:
  `choices=["Commit as-is (reviewed + skipped files)", "Request changes", "Abort"]`

"Final hunk" means the last reviewable hunk across all files, accounting for
skipped files and trivial-hunk groups.

Do not proceed to the next hunk until the current one is approved.

## 4. Handle the response

- **Request changes:** Ali describes what needs to change. Make the edit, re-run
  `git diff` for the affected file, and re-present only the changed hunk(s)
  starting from the rejected hunk. Previously approved hunks in other files are
  not re-reviewed.
- **Skip this file:** skip remaining hunks for that file. Run the subagent review
  on skipped hunks and present a one-line summary of findings (if any) before
  moving to the next file.

## Special cases

- **Non-hunk diffs** (binary files, mode-only changes, renames, submodule
  updates): present at file level. Show the diff header and a one-line
  description, then offer the same approval choices. No subagent review.
- **Trivial hunk grouping:** if multiple consecutive hunks in the same file are
  purely whitespace, import reordering, or single-line version bumps, present
  them as a group with a single approval.
- **Skip-review patterns** (auto-skip, still committed): files matching declared
  patterns skip hunk review but are staged and committed normally. Default: none.
- **Never-commit patterns** (auto-skip, never staged): see "Commit safety" in the
  global instructions.
- **Verbal override:** Ali can say "skip the hunk review for this change" or
  "skip review for this repo" at any point. A verbal override lasts for the
  current commit operation only. It does not carry to subsequent commits unless
  Ali says "skip review for the rest of this session." To make a skip permanent,
  Ali must request it be added to the skip-review patterns list.
