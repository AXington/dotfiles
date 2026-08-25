---
name: where-were-we
description: Use when the user asks where were we, what were we doing, where am I, catch me up, remind me, what is the state of this, what did we decide, tl;dr, summarize this session, or returns to a session after a gap and needs orientation. Also use when the user asks what other sessions are open.
---

# where-were-we

Reconstructs session state with sources shown, in 8 lines or fewer.

## Absolute rules

1. Never compose the summary yourself. The scripts render it.
2. If a script fails, report the failure. Do not format output by hand. A
   wrong-shaped answer teaches the user to distrust the shape.
3. Never present inferred content as decided. `Decided` comes only from the
   ledger.
4. Never write `ledger.md` directly. Use `wwm.py record`.

## Default flow

```bash
S=~/.copilot/skills/where-were-we/scripts
python3 $S/wwm.py collect
```

Read the JSON. Then write one hedged prose sentence that orients without
asserting any fact absent from `items`. If any item has `"source": "inf"`, the
prose must be hedged ("looks like", "appears to be"). Then:

```bash
python3 $S/wwm.py render --level tldr --prose "<your sentence>"
```

Show the output verbatim. Do not add commentary above or below it.

## Routing natural language

| The user says | Run |
| --- | --- |
| where were we / catch me up / tl;dr | `--level tldr` |
| more detail / summary | `--level summary` |
| everything / full | `--level full` |
| what did we decide | `--section decisions` |
| what is left / open threads | `--section threads` |
| what happened / timeline | `--section timeline` |
| which files | `--section files` |
| what is blocking | `--section blockers` |
| what other sessions / what else is open | `wwm.py sessions` |

If the request is ambiguous, ask which one. Do not guess.

## Recording milestones

Call this in-turn when a decision is made, a commit lands, or a blocker
appears. Do not batch it to the end of a session; that is what fails today.

```bash
python3 $S/wwm.py record --kind decision \
  --text "Approach B, scripts plus skill" \
  --why "budget and format must be deterministic" \
  --rejected "pure prompt (drifts), full CLI (too heavy)"
```

Kinds: `decision`, `thread`, `blocker`, `state`, `next`, `goal`.

Record a new `goal` whenever the session's purpose actually shifts. It
replaces the current goal on purpose, and the one it replaced is filed into
`## goal history` automatically, so nothing is lost by re-recording it. The
current goal shows at every level; superseded ones show only in `full`.

## When there is no ledger

`collect` reports `has_ledger: false`. Offer adoption only if a ledger from
another session in the same directory is plausible, and name it explicitly:

```bash
python3 $S/wwm.py adopt --source <other-session-id>
```

Never adopt silently. A silently adopted ledger is indistinguishable from a
fabricated one.
