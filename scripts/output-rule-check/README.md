# output-rule-check

Measures agent-authored terminal output against the output
rules in `configs/copilot-instructions.md`.

## Why it exists

The rules were being followed in replies and broken almost
everywhere else. Every self-audit came back clean, because
an agent that misreads which surfaces are covered writes a
checker with the same blind spot. Self-measurement cannot
detect a scoping error in itself.

So this reads the session records instead, and decides what
is covered from the tool schema rather than from the rule
text. A surface the prose forgets to name is still checked.

## Usage

    ./output_rule_check.py                 # newest session
    ./output_rule_check.py SESSION_ID
    ./output_rule_check.py path/to/events.jsonl
    ./output_rule_check.py --all           # every session
    ./output_rule_check.py --json          # machine output

Exits `1` when it finds anything, so it can gate a hook.

Options: `--width` (default 70), `--show` examples per
surface, `--quiet` to hide clean sessions, `--root` to point
at a different session-state directory.

## How a string is classified

By which field of which tool call it came from. Never by a
label the agent supplies, because a label is the escape
hatch: "that block is command output" is unfalsifiable when
the agent wrote the block.

| Category | Checked | Example |
|---|---|---|
| prose | width, dashes, ASCII | `ask_user.message` |
| command | width only | `bash.command` |
| payload | width per language | `create.file_text` |
| identifier | exempt, unbreakable | `view.path` |
| quoted | exempt, read from disk | `edit.old_str` |
| unknown | reported, checked as prose | new tools |

`edit.old_str` and `edit.new_str` sit in the same call and
land on opposite sides. One is text read off disk, the other
is text the agent composed. That is the whole test, and it
needs no judgment.

Style checks apply to prose only. A `--flag` in a command is
not a prose separator.

## Exemptions that are real

A line is exempt by breakage when its first token alone
overflows, because there was no break to take. A bare URL
passes. The same URL with prose in front of it does not.

Process output, Ali's own messages and file contents read
from disk are exempt by provenance.

Authored file content is measured against the target file's
own convention, from its extension: 88 for Python, 70 for
markdown, and 70 for anything unrecognised, since an unnamed
reason to be exempt is not a reason.

## Failing loud

An unknown tool, or an unknown field on a known tool, is
reported under "unclassified surfaces" and measured as prose
anyway. Silence is never the default for something new.

## Tests

    python3 -m pytest -q
    uvx ruff check .
    uvx mypy --strict output_rule_check.py

The fixtures use widths measured from a real session: a 988
column command, a 602 column subagent prompt, a 314 column
question. If the suite ever reports those clean, the checker
has gone blind again and the suite is what tells you.

The negative cases matter as much. They cover the two
evasions that independent reviewers invented unprompted:
writing wide text to a file and displaying it, and burying
wide text in a heredoc.
