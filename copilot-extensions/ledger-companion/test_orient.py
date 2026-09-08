"""Tests for the sessionStart orientation hook.

Run from this directory with:
    python3 -m unittest test_orient -v

Every case here is a regression for a defect found by running the hook
against a real ledger, not one imagined from reading the code.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import orient

HOOK = Path(__file__).with_name("orient.py")

LEDGER = """# where-were-we

goal: Codify the communication rules
started: 2026-08-11
updated: 2026-09-08
last_synced_turn: 34

## state
Both commits pushed. Tests pass.

## next
Write side of the tracker, then rewrap the configs.

## todos
- [ ] first todo
- [ ] second todo
- [x] a finished one that must not show
- [ ] third todo
- [ ] fourth todo

## decisions
- 2026-08-11 Something was decided

## threads
- [ ] an open question

## blockers
- a blocker
"""


class TestNextRenders(unittest.TestCase):
    """Regression: Next was parsed as bullets, always written as prose.

    fields["next"] came back empty for every ledger the skill had ever
    produced, so the most action-relevant line never reached the card.
    """

    def test_prose_next_is_not_empty(self) -> None:
        fields = orient.parse_ledger(LEDGER)
        self.assertEqual(
            fields["next"],
            ["Write side of the tracker, then rewrap the configs."],
        )

    def test_next_appears_on_the_card(self) -> None:
        card = orient.build_card(orient.parse_ledger(LEDGER), 0)
        assert card is not None
        self.assertIn("Next:", card)
        self.assertIn("Write side of the tracker", card)

    def test_next_outranks_blocked_on_the_card(self) -> None:
        """The card is read to resume, so what to do next comes first."""
        card = orient.build_card(orient.parse_ledger(LEDGER), 0)
        assert card is not None
        self.assertLess(card.index("Next:"), card.index("Blocked:"))

    def test_bullet_shape_still_works(self) -> None:
        """A hand-edited section must not regress when the shape changes."""
        text = "# l\n\ngoal: g\n\n## next\n- one\n- two\n"
        self.assertEqual(orient.parse_ledger(text)["next"], ["one", "two"])

    def test_bullets_win_over_prose_when_both_present(self) -> None:
        text = "# l\n\ngoal: g\n\n## next\nintro line\n- one\n"
        self.assertEqual(orient.parse_ledger(text)["next"], ["one"])


class TestTodosOnCard(unittest.TestCase):
    def test_only_open_todos_are_parsed(self) -> None:
        fields = orient.parse_ledger(LEDGER)
        self.assertEqual(
            fields["todos"],
            ["first todo", "second todo", "third todo", "fourth todo"],
        )

    def test_todos_reach_the_card(self) -> None:
        card = orient.build_card(orient.parse_ledger(LEDGER), 0)
        assert card is not None
        self.assertIn("Todo:", card)
        self.assertIn("first todo", card)

    def test_finished_todo_never_shows(self) -> None:
        card = orient.build_card(orient.parse_ledger(LEDGER), 0)
        assert card is not None
        self.assertNotIn("a finished one", card)

    def test_truncation_says_what_it_hid(self) -> None:
        """A cut list that looks whole tells the reader the rest is gone."""
        card = orient.build_card(orient.parse_ledger(LEDGER), 0)
        assert card is not None
        self.assertIn("and 1 more", card)
        self.assertNotIn("fourth todo", card)

    def test_no_truncation_notice_when_nothing_hidden(self) -> None:
        text = "# l\n\ngoal: g\n\n## todos\n- [ ] only one\n"
        card = orient.build_card(orient.parse_ledger(text), 0)
        assert card is not None
        self.assertNotIn("more, see the ledger", card)

    def test_threads_are_not_shown_as_todos(self) -> None:
        """The two sections are distinct; the card must not merge them."""
        card = orient.build_card(orient.parse_ledger(LEDGER), 0)
        assert card is not None
        self.assertNotIn("an open question", card)


class TestWorktreeLookup(unittest.TestCase):
    """Regression: the card went silent in any linked git worktree.

    All feature work happens in worktrees, so the hook said nothing during
    exactly the sessions that most needed orienting.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_worktree_resolves_to_the_main_clone(self) -> None:
        main = self.root / "project"
        (main / ".git" / "worktrees" / "featx").mkdir(parents=True)
        tree = self.root / "parallels" / "featx"
        tree.mkdir(parents=True)
        (tree / ".git").write_text(f"gitdir: {main / '.git' / 'worktrees' / 'featx'}\n")
        self.assertEqual(orient.main_worktree(str(tree)), str(main))

    def test_ordinary_clone_returns_none(self) -> None:
        """A real .git directory is not a worktree pointer."""
        clone = self.root / "clone"
        (clone / ".git").mkdir(parents=True)
        self.assertIsNone(orient.main_worktree(str(clone)))

    def test_missing_directory_returns_none(self) -> None:
        self.assertIsNone(orient.main_worktree(str(self.root / "nope")))

    def test_submodule_gitdir_is_not_treated_as_a_worktree(self) -> None:
        """A submodule .git file points at modules/, not worktrees/."""
        sub = self.root / "sub"
        sub.mkdir()
        (sub / ".git").write_text("gitdir: /somewhere/.git/modules/sub\n")
        self.assertIsNone(orient.main_worktree(str(sub)))

    def test_garbage_pointer_returns_none(self) -> None:
        odd = self.root / "odd"
        odd.mkdir()
        (odd / ".git").write_text("not a gitdir line at all\n")
        self.assertIsNone(orient.main_worktree(str(odd)))


class TestNeverBreaksTheSession(unittest.TestCase):
    """A hook that fails must never stop a session from starting."""

    def run_hook(self, payload: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(HOOK)],
            input=payload,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )

    def test_garbage_stdin_exits_zero_with_valid_json(self) -> None:
        proc = self.run_hook("this is not json at all")
        self.assertEqual(proc.returncode, 0)
        json.loads(proc.stdout)

    def test_empty_stdin_exits_zero(self) -> None:
        proc = self.run_hook("")
        self.assertEqual(proc.returncode, 0)
        json.loads(proc.stdout)

    def test_unknown_source_stays_silent(self) -> None:
        proc = self.run_hook(json.dumps({"source": "compaction"}))
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(json.loads(proc.stdout), {})


class TestCardShape(unittest.TestCase):
    def test_empty_ledger_produces_no_card(self) -> None:
        self.assertIsNone(orient.build_card(orient.parse_ledger("# l\n"), 0))

    def test_header_warns_the_card_is_recall_not_instruction(self) -> None:
        card = orient.build_card(orient.parse_ledger(LEDGER), 0)
        assert card is not None
        self.assertIn("recall, not instruction", card)
        self.assertIn("never authorises an action", card)

    def test_long_field_is_clamped_with_an_ellipsis(self) -> None:
        text = "# l\n\ngoal: " + "x" * 900 + "\n"
        card = orient.build_card(orient.parse_ledger(text), 0)
        assert card is not None
        self.assertIn("...", card)
        self.assertLess(len(card.splitlines()[2]), orient.MAX_FIELD_CHARS + 10)

    def test_age_is_stated_so_staleness_is_visible(self) -> None:
        card = orient.build_card(orient.parse_ledger(LEDGER), 5)
        assert card is not None
        self.assertIn("5 day(s) ago", card)


class TestAgainstTheRealLedger(unittest.TestCase):
    """Exercise the shipped path end to end, not a fixture of it."""

    def test_live_ledger_renders_every_section(self) -> None:
        live = (
            Path.home()
            / ".copilot/session-state"
            / "3e21189d-423c-49ce-917d-b2cca812bd1c/files/ledger.md"
        )
        if not live.exists():
            self.skipTest("live ledger not present on this machine")
        card = orient.build_card(orient.parse_ledger(live.read_text()), 0)
        assert card is not None
        for label in ("Goal:", "Now:", "Next:", "Todo:"):
            self.assertIn(label, card, f"{label} missing from the real card")


if __name__ == "__main__":
    unittest.main()
