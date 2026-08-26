---
name: pre-commit-workflow
description: >-
  Checklist and workflow for preparing code changes for commit. Use this skill
  before creating any git commit, especially after adding features, fixing bugs,
  or modifying existing behavior. Covers linting, testing, documentation checks,
  and commit message construction in the correct order.
---

# Pre-Commit Workflow

Follow these steps in order before creating any commit. Do not skip steps.
A commit is not ready until all gates pass.

## Step 1: Run pre-commit hooks (if repo has them configured)

If the repo has `.pre-commit-config.yaml`, run all hooks first. This is the
fastest way to catch lint, format, and security issues in one pass.

```bash
pre-commit run --all-files
```

If hooks are not installed yet: `pre-commit install` first.

If the repo has no pre-commit config, skip to Step 2.

## Step 2: Establish a baseline

Before making changes, run the test suite to confirm the baseline is clean.
If the baseline is already broken, stop and surface that to Ali before proceeding.

```bash
pytest tests/ -v
```

## Step 3: Lint (ruff)

Run ruff on the changed files (or whole project if scope is unclear).
Use the `ruff-recursive-fix` skill for iterative fix workflow if needed.

```bash
ruff check
ruff format --check
```

All findings must be resolved before committing. No `# noqa` suppression without
documented justification.

## Step 4: Run tests

Run the full test suite. All tests must pass.

```bash
pytest tests/ -v
```

**Coverage requirement:**
- New behavior or features: must add tests covering the new code paths.
- Bug fixes: must add a regression test that would have caught the bug before the fix.
- Modified behavior: check whether existing tests need updating to match new behavior.

Use the `pytest-coverage` skill to find uncovered lines if needed.

## Step 5: Verify documentation

Before committing, verify that all affected documentation is current:

- **`--help` text**: reflects current behavior, flags, and defaults
- **`README.md`**: any new commands, changed behavior, new config keys
- **`CHANGELOG.md`**: entry under `## [Unreleased]` for any user-visible change
- **`KNOWN_ISSUES.md`**: if this commit resolves a tracked issue, update its status

A code change that affects behavior is **not complete** until docs reflect it.

## Step 6: Review staged changes

Before committing, do a final review of what is staged:

```bash
git diff --cached
```

Check for:
- Accidental debug code, print statements, or commented-out blocks
- Hardcoded secrets, credentials, IPs, or environment-specific values
- Non-ASCII characters (em-dashes, curly quotes, smart apostrophes, etc.)
- Files that shouldn't be committed (`.env`, temp files, `cov_annotate/`, etc.)

## Step 7: Construct the commit message

Use the `conventional-commit` skill to build the commit message.

Format: `<type>: <description>`

Types: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`

For ticket work, include the ticket ID:
```
fix: OPS-1234: handle 404 on missing template
```

Rules:
- Subject line under 72 characters
- Include the Co-authored-by trailer for Copilot-assisted commits:
  ```
  Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>
  ```

## Step 8: Verify branch

Confirm the current branch is correct before committing:

```bash
git branch --show-current
```

- Must match the ticket being worked (e.g., `OPS-1234-fix-thing`)
- Never commit ticket work onto `main`, `master`, or another ticket's branch

## Quick reference

| Gate | Tool | Pass condition |
|------|------|----------------|
| Pre-commit hooks | `pre-commit run --all-files` | All pass (if configured) |
| Lint | `ruff check` | Zero findings |
| Format | `ruff format --check` | No changes needed |
| Security | `bandit -r . -ll` | No medium+ severity findings |
| Tests | `pytest tests/ -v` | All pass |
| Coverage | `pytest-coverage` skill | New code paths covered |
| Help text | Manual review | Reflects current behavior |
| CHANGELOG | Manual review | Entry under [Unreleased] |
| Diff review | `git diff --cached` | No surprises |
| Branch | `git branch --show-current` | Matches ticket |
