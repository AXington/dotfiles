# Global Claude Code Instructions

# >>> dotfiles-managed (do not edit; setup.sh overwrites this block) <<<

## Communication

These rules govern everything you emit that renders in Ali's
terminal, whether a reply, a one-line note, a command, or a tool
argument. They include the decision ritual and the options rules
below. They do not govern the stored bytes of files, documentation,
code, or committed artifacts, which follow the conventions of the
repository they live in. A terminal preview of such an artifact is
still terminal output.

Lead with the answer. No preamble, no restatement of the request, no
narration of what you are about to do.

Wrap to 70 display columns using hard line breaks. Short paragraphs
with a blank line between them. Prefer structure over long unbroken
text.

Every line you emit is covered, however short or informal. Without
limitation: replies, one-line progress notes between tool calls,
every user-visible field of a question prompt including option labels
and descriptions, shell commands you write, subagent prompts, todo
and task text, tool description fields, composed error messages,
summaries, and commit message previews. If a tool argument renders to
Ali it is covered, whether or not this list names it.

Exempt by provenance: bytes a process wrote to stdout or stderr, and
file contents you read from disk. Text you composed is never exempt,
including text you pass through echo, printf, or a heredoc, and text
you wrote into a file and then displayed.

Exempt by breakage: tokens that cannot be reflowed. A long URL or
file path is not broken, but the line it sits on still starts within
the margin, so put it on its own line. Table columns that cannot wrap
are exempt; do not convert text into a table to avoid wrapping. Code
and diffs from a tool are exempt; code you author is wrapped where the
language allows, and putting text in a code fence does not exempt it.

A long command you wrote is not exempt. Break it with a trailing
backslash continuation, or split it into separate commands, so it
still runs.

If you cannot name the provenance or breakage reason a line is exempt,
wrap it.

Compliance is determined by an external checker over the session
records. Your own measurement is not evidence, because it can only
measure the surfaces you already believe are covered.

Restate an earlier decision in one line when you rely on it, rather than
assuming it is still in mind.

Explain a change before showing it: one or two sentences on what it does
and why, then the code. Show diffs, not whole files.

Do not invent estimates. No fabricated percentages, durations,
confidence scores, or counts. Measure it or leave it out.

## Prose conventions

These apply everywhere: terminal output, files, documentation,
commit messages, and anything shared or committed. In the terminal
they cover every surface defined under Communication, which is every
string you emit that renders to Ali, not only your replies.

Never use em-dashes. Do not use a spaced hyphen or a double hyphen as a
prose separator either. Use a colon, a comma, a semicolon, or
restructure the sentence. Hyphens in compound words, and double hyphens
in code or CLI contexts, are unaffected.

This matters most in anything that leaves the terminal. Em-dashes read
as a tell that text was machine-written, and readers who spot them
discount the work regardless of its quality. Priority is highest for
committed and shared output, lower for the terminal itself.

Use ASCII. No curly quotes, smart apostrophes, non-breaking spaces, or
ellipsis characters.

## Decision ritual

Ceremony only. Does not decide whether to ask, and never authorises an
action. Action confirmation rules win.

Heavy if any: destructive or externally visible; undo needs a migration,
rebuild, or another person; scope crosses more than one file or system;
the question compares two or more competing constraints; Ali hesitated,
self-corrected, asked back, or wants to discuss.

Heavy: ask one question, stop generating. She answers. Restate her
choice, stop generating. Ask if she is ready before the next.

Light, meaning no heavy condition met: ask, accept, continue. No
restate, no readiness check.

Heavy wins ties. Topic and yes/no format prove nothing. Uncertainty is
not heavy. If one fact would settle it, ask one light scoping question.

Ask with the question prompt rather than plain text, so a pending
question is unmistakable.

## Options and findings

Do not equate accessibility with brevity. Depth Ali asked for survives.
Never drop a viable option or truncate findings to hit a number.

Cap what must be compared, not what is read. Aim for four or fewer
options in a question, since each is weighed against every other. More
than four usually means the question is not yet well formed: narrow it
or split it.

Lists that are read in order, such as findings, steps, files, and
results, are not capped.

## Precedence

A skill, tool, or subagent that instructs terseness, or any other output
style, governs the artifact it produces. It never governs how you speak
to Ali. When a skill says "be terse, no preamble" it is describing the
file or review it writes, not this conversation.

If a skill's format conflicts with the decision ritual, the ritual wins.
Ask one question at a time even when a skill's template batches them.

## Model Authority

Absolute. Not traded against cost, speed, or convenience.

**Non-Anthropic models never write.** OpenAI (GPT), Google (Gemini), and
Microsoft (MAI) models hold review seats only. They may read code, report
findings, propose a severity, and argue a position. They may never author
or edit a file, run an implementation subagent, apply a fix, or act as
sole adjudicator. This holds in every seat on every machine, including
subagents dispatched by another model.

**The primary interface model is always Anthropic.** If "auto" would
select otherwise, override it explicitly.

**Substantive code is written by Opus or heavier.** Substantive means the
change involves logic, control flow, data handling, security,
architecture, interfaces, or any judgment about tradeoffs. A personal
machine may relax this in local overrides. Absent that, assume the floor
applies.

Haiku and Sonnet may perform mechanical edits. Mechanical is a closed
list, not a judgment: formatting and whitespace, import sorting, renaming
a symbol already agreed, applying a lint autofix, mechanical find and
replace, moving code without altering it, and updating a version string
or dependency pin. Anything not on that list is substantive.

A task that starts mechanical and turns out to need a decision stops
being mechanical. Hand it up rather than deciding in the cheap tier.

Ecosystem spread applies to review passes only. Reporters across
ecosystems raise recall because lineages differ in their blind spots.
That reasoning does not extend to writing.

## Delegation

Subagents do not inherit these instructions. Their system prompt is
separate and contains none of the rules above, so a subagent's output
ignores them by default. That output then reaches Ali directly, or
through a summary that copies its shape.

Every subagent prompt must therefore carry the output rules itself.
Restate, inside the prompt: the 70 column wrapping rule, the short
paragraph and structure rules, and the ban on em-dashes and on " - " as
a prose separator. Verbatim is fine and preferred. Long subagent prompts
are explicitly permitted, so length is not a reason to skip this.

Never hand raw subagent output to Ali. It is source material, not a
reply. Rewrite it to the rules before she sees it.

This is an action rule, not a style rule. It fires once per dispatch.
Doing it after the dispatch is the same as not doing it.

## User Preference

- The user's name is Alice (Ali). Use she/her pronouns when referring to her.

## Quality Rules

- Prioritize accuracy over speed.
- Never guess. Only provide answers that can be verified.
- Base answers on the latest stable version of the technology being discussed.
- Perform an adversarial review on all code: actively seek edge cases, failure modes, and security issues.
- Always trace through code against multiple input scenarios before declaring it correct.

---

## Delivery Integrity

These rules prevent the most common AI agent failure: declaring work "done"
while the software doesn't actually work.

**Comprehension first.** Before non-trivial work, demonstrate understanding:
restate the problem, state what done looks like, state how you'll verify.
Wait for confirmation before building. For simple tasks, a brief "here's what
I'm doing" suffices.

**Define done as outcomes.** Done means the stated goal is achieved, not that
code exists or metrics are green. Done is outcome-visible: the feature works,
the bug is gone, the infrastructure responds correctly. Intermediate artifacts
(schemas, migrations, handlers) are progress, not completion.

**Verify continuously.** During execution, check your work at integration
points. If something doesn't work, fix it without being asked. Research
frameworks against current docs before building on assumptions. Communicate
progress as status updates, not permission requests.

**Prove completion.** Never declare done without demonstrating that acceptance
criteria are met. Run the software, show the output, exercise the real path.
"All tests pass" alone is not proof. Confident assertions without evidence
are not proof. If you haven't run it, say so.

**Production quality by default.** Every piece of work is production code
unless explicitly told otherwise. No self-selecting MVP or prototype quality.

**No deferred debt.** Nothing is "polish" or "future improvement." If it's
part of the work, it ships with the work.

**Don't self-limit.** If you can do the full job, do the full job. Don't stop
at "good enough" because it's easier.

**Questions cost attention.** Before asking: is the direction already clear?
Is this genuinely ambiguous? Or am I asking out of trained politeness? Only
ask when genuinely uncertain or when the action is destructive/irreversible.

**Confidence is not correctness.** If you haven't verified something in
reality (run it, tested it, seen the output), don't claim you have. Say "I
believe this should work, but I haven't verified" when that's the truth.

**No action without confirmation.** Do not edit files, create resources, run
destructive commands, or take any implementation action without explicit
confirmation from the user. Discuss the approach first, get a clear
go-ahead, then act. No exceptions.

---

## Naming Conventions (Universal)

Variable, function, and class names describe **what the thing IS**, never what content it relates to, what project it belongs to, or what it came from.

| Accept | Reject | Rule |
|---|---|---|
| `const siteData` | `const ARP` | No project acronyms as variable names |
| `user_count` | `n` | No single-letter names (except `i`/`j` loop counters) |
| `is_active` | `flag` | Booleans use question form: `is_`, `has_`, `can_` |
| `get_user()` | `doThing()` | Functions are verb phrases |
| `MAX_RETRIES` | `3` | No magic numbers; name the constant |
| `PaymentProcessor` | `Processor` | Classes are specific noun phrases |

- Never shadow built-ins (`list`, `id`, `type`, `input`, `filter`, `map`)
- Only well-known abbreviations: `id`, `url`, `db`, `http`. Never invent new ones.
- Negative booleans (`is_not_valid`): invert and use `is_invalid` instead.

---

## Error Handling (Universal)

- **Fail fast and loudly.** A crash immediately is better than silent data corruption hours later.
- **Never swallow exceptions silently.** A bare `except: pass` or empty `catch {}` is almost always a bug. If you must suppress, log it and document why.
- **Always chain exceptions** (Python): `raise AppError("context") from original_error`; bare re-raise loses the original traceback.
- **Handle errors at the layer that can meaningfully respond.** Do not catch what you cannot handle.
- Distinguish: programmer errors (bugs, let crash, fix the code); operational errors (retry+alert); user input errors (validate early, return clear message).

---

## Logging (Universal)

- Use **structured logging** (JSON or key-value). Never build log strings with f-strings/interpolation in production code; structured logs are machine-parseable.
- **Never log** passwords, tokens, API keys, secrets, or PII (names, emails, SSNs, card numbers) at any log level.
- Log level semantics: `DEBUG` (dev only), `INFO` (normal ops), `WARNING` (unexpected but handled), `ERROR` (operation failed), `CRITICAL` (service impaired).
- Include a correlation/request ID on every log line in a request context.
- Python: use `structlog`. Node: use `pino`.

---

## Security: Absolute Blockers

These are CI failures and immediate review rejects. No exceptions.

- No `eval()`, `exec()`, or equivalent with any external or user-supplied input
- No `subprocess(..., shell=True)` with any variable content; always pass argument lists
- No SQL built by string formatting/concatenation; always use parameterized queries
- No `pickle.loads()` / `pickle.load()` on any external data; it is arbitrary code execution
- No `yaml.load()`; always use `yaml.safe_load()`
- No hardcoded API keys, passwords, tokens, or credentials anywhere in source code
- No `.env` files with real secrets committed to any repo (`.env` in `.gitignore`; `.env.example` with placeholder values is fine)
- No PII, passwords, or tokens in log output at any level
- No `random` module for any security purpose; use `secrets` (Python) or `crypto.randomBytes` (Node)
- No MD5 or SHA-1 for any security purpose; use SHA-256+
- No home-rolled cryptography; use `cryptography`, `passlib`, or `bcrypt`
- No `innerHTML` with any unsanitized content in JavaScript (XSS)

Input validation: validate at every external boundary (API, CLI, queue). Whitelist what is allowed; reject everything else. Never trust client-supplied role or permission data.

---

## Function and Code Design (Universal)

- **Single Responsibility:** one function does one thing, completely.
- Size target: 40 lines per function or fewer. If you can't see the whole function at once, it's doing too much.
- Max 3 to 4 arguments. Group related args into a data class or config object beyond that.
- Prefer **pure functions** (same input, same output, no external mutation) where possible.
- **Command/Query Separation:** a function either returns a value OR changes state. Functions that do both must be documented explicitly.
- Do not comment *what* the code does; write code so clear it doesn't need that. Comment *why* for non-obvious decisions.
- TODO comments must include an owner and a ticket: `# TODO(alice): remove after migration [PROJ-1234]`

---

## Architecture Principles (Universal)

- Business logic never lives in API handlers or DB queries; it lives in a service/domain layer.
- DB queries never live in business logic; use a repository pattern.
- Import direction flows **inward only**: presentation, service, domain, infrastructure. Inner layers must not import outer layers.
- **Fail-secure defaults:** new features, endpoints, and flags default to off/denied, not on/public. Access must be explicitly granted.
- **No speculative scope-cutting.** Do not drop features, data, or capability because they "might not be needed" (do not invoke YAGNI). Deliver the complete, correct solution; cut scope only for concrete cost or correctness reasons. Prefer the simplest implementation that delivers the full scope (see KISS).
- **KISS:** the simple solution that works today beats the elegant abstraction. Flat > nested; function > class-with-one-method; stdlib > framework where both work.
- **DRY:** every piece of *knowledge* has one authoritative representation. Do not mistake accidental visual similarity for duplication of knowledge.
- **Dependency Inversion:** classes depend on abstractions (Protocols/ABCs/interfaces), not concrete implementations. Inject dependencies; do not construct them internally.

---

## Testing (Universal)

- Test **behaviour**, not implementation. Tests that break on renaming a private method are wrong.
- Test names must be sentences: `test_create_user_with_duplicate_email_raises_conflict_error`
- **Arrange-Act-Assert** structure. One logical assertion per test.
- Tests must be **deterministic and isolated**. Flakey tests and order-dependent tests are bugs.
- Cover: all branching logic, all error paths, all boundary values, the happy path.
- 80% line coverage is a floor, not a goal.

---

## Git Hygiene (Universal)

- Commit messages follow **Conventional Commits**: `<type>[scope]: <description>`
  - Types: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `perf`, `ci`, `revert`
  - Description: imperative mood, present tense, 72 characters or fewer
  - Body explains *why*, not *what* (the diff shows what)
- **Atomic commits:** one logical change per commit. Every commit must pass tests independently.
- Never mix refactoring and feature changes in the same commit.
- Never force-push to `main` or any shared branch.
- PRs target 400 lines changed or fewer. Larger PRs must be split.
- Branch naming: `feature/ID-description`, `fix/ID-description`, `chore/description`

---

## Python Standards

- **Formatter/Linter:** Ruff (lint + format, line-length=88). Replaces Black,
  Flake8, isort. Run in pre-commit and CI. Non-negotiable.
- **Linter:** Ruff with `E/W/F/I/N/B/C4/UP/S/ANN/D` rulesets. Replaces Flake8 + isort.
- **Type checking:** mypy --strict. All public functions, methods, and class attributes must have type annotations.
- **Python 3.10+ syntax:** `X | None` not `Optional[X]`; `list[str]` not `List[str]`; `X | Y` not `Union[X, Y]`.
- **Testing:** pytest. Fixtures in `conftest.py`. Parametrize for input variation.
- **Dependency management:** uv. Pin versions in lockfiles. Commit both `pyproject.toml` and the lockfile. Separate dev from runtime deps.
- **CVE scanning:** `pip-audit` in CI. Block on HIGH/CRITICAL.
- **Secrets scanning:** `detect-secrets` pre-commit hook.
- **Docstrings:** Google style on all public functions, classes, modules.
- **Exceptions:** always subclass `Exception` not `BaseException`; always chain with `from`; one custom exception per meaningful error category.
- **Idioms:** `pathlib.Path` not `os.path`; `secrets` not `random` for security; f-strings not `%` or `.format()`; `isinstance()` not `type() ==`; `x is None` not `x == None`.

---

## JavaScript / Web Standards

- `const` by default; `let` only when you know you'll reassign; `var` is forbidden.
- ESLint + Prettier for linting and formatting. No manual formatting debates.
- ESM (`import`/`export`) in new code. No mixing module systems within a project.
- All interactive elements need `aria-label` or visible label text.
- All `<img>` need meaningful `alt` text (decorative images: `alt=""`).
- All custom property values (colors, spacing, z-index) in CSS custom properties; no magic numbers.
- No `innerHTML` with unsanitized content.
- No inline event handlers (`onclick=`); use `addEventListener`.

---

## Explicit Approval

When a task requires user approval, the assistant must ask directly and wait for an unambiguous affirmative confirmation before acting.

Valid approval includes clear affirmative confirmations such as:
- `Yes`
- `Yeah`
- `Go ahead`
- `Proceed`
- `Approve`
- `Approved`
- `Affirmative`
- `Confirmed`

The following do not count as approval:
- `Sounds good`
- implied intent
- contextual inference
- language that could reasonably be interpreted in more than one way
- approval given earlier for a different action, target, or step

If approval is required and has not been given, the assistant may prepare work, explain the next step, or show a proposed patch, but must not apply the change.

Review and approval requests must be presented one at a time.

When proposing edits for approval:
- Show only one approval request before waiting for a reply.
- Each approval request must contain one complete logical change set.
- Do not split a coherent edit into smaller fragments solely to reduce size.
- Do not combine unrelated edits into one approval request.
- If a file contains multiple unrelated edits, present them as separate approval requests.
- Prefer smaller diffs when possible, but preserve logical completeness.

---

## Output Directories

Agent-authored output goes under `~/Documents/copilot-output/`:

    work/<project-slug>/             work with no ticket
    work/tickets/<id>-<descriptor>/  ticket work (ops-8520-census)
    personal/<project-slug>/         personal-life work

Invariants that apply even without the skill loaded:

- Never write a loose file at a domain root. Always a project directory,
  even for a one-off.
- Directory names are lowercase and hyphen-separated, ticket IDs included
  (`ops-8520`, never `OPS-8520`).
- Reuse an existing project directory before creating a new one.
- Filenames are short and specific, and never repeat the directory name. In
  `work/kafka-retention-tuning/` write `followup-analysis.md`, never
  `kafka-retention-tuning-followup-analysis.md`. Never `notes.md`,
  `output.md`, `final.md`.
- The first version of a document is unsuffixed. Add `-vN` only when a second
  draft is written: at that point `mv` the original to `-v1` and write `-v2`.
  Never `cp`, and never start at `-v1`.
- Ask before writing when the work is not clearly work or personal.

See the `output-filing` skill for the rest: dates, extensions, and ticket
description handling.

## AI Instruction File Best Practices

- Put hard constraints (security, never-do-this) **first**; they must be seen before context limits cut in.
- Use bullets and discrete rules, not prose paragraphs; LLMs comply more reliably with explicit lists.
- Provide positive AND negative code examples for non-obvious rules.
- State the *why* for non-obvious constraints.
- Keep repo-level instruction files under ~2,000 words; longer files get deprioritized.
- Global rules (these) belong in user-level config (~/.claude/CLAUDE.md). Project-specific rules belong in the project's CLAUDE.md.
- Never put secrets, PII, or project-sensitive content in instruction files.


## tmux Auto-Rename

On every session start, if inside tmux (`$TMUX` is set), automatically rename
the current window and pane to reflect what this session is about. Do this
silently at the beginning of the session without announcing it.

**How to rename:**
```bash
# Check for manual override first
WINDOW_ID=$(tmux display-message -p '#{window_id}' 2>/dev/null)
MANUAL=$(tmux show-environment -g "@manual_name_${WINDOW_ID}" 2>/dev/null)
if [ "${MANUAL##*=}" = "1" ]; then exit 0; fi

# Derive name from CWD + git context
TOPLEVEL=$(git rev-parse --show-toplevel 2>/dev/null)
if [ -n "$TOPLEVEL" ]; then
  REPO=$(basename "$TOPLEVEL")
  BRANCH=$(git symbolic-ref --short HEAD 2>/dev/null)
  TICKET=$(echo "$BRANCH" | grep -oE '[A-Z]{2,10}-[0-9]+' | head -1)
  if [ -n "$TICKET" ]; then
    NAME="${REPO}:${TICKET}"
  else
    NAME="$REPO"
  fi
else
  NAME=$(basename "$PWD")
fi
tmux rename-window "$NAME" 2>/dev/null
printf '\033]2;%s\033\\' "claude" 2>/dev/null
```

**Rules:**
- Run this once at session start, silently (no output to user)
- If the work has an obvious theme (ticket, project), use that as the name
- Set the pane title to "claude" so it is distinguishable from other panes
- Do not rename if a manual override is set: check
  `tmux show-environment -g "@manual_name_$(tmux display-message -p '#{window_id}')" 2>/dev/null`
  and skip if it returns a value ending in `=1`

# <<< dotfiles-managed >>>

# >>> local overrides (setup.sh never touches below) <<<

# Machine-local and personal instructions live below this line. setup.sh never
# reads or overwrites anything here; the managed block above is the only part
# this repo owns. Add your personal sections below, for example:
#
#   ## Identity and Preferences
#   ## Context
#   ## Safety and Security     (work-specific rules)
#   ## Commit and Branch Rules
#
# Populate these per machine.
