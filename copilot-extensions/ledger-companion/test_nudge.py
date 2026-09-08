"""Tests for the agentStop ledger reminder hook.

Run from this directory with:
    python3 -m unittest test_nudge -v

The hook decides whether to interrupt the end of a turn, so the cases that
matter are the ones where it must stay quiet. A reminder that fires on a
guess trains the reader to ignore every reminder after it.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import nudge

HOOK = Path(__file__).with_name("nudge.py")
SESSION = "abc-123"


class Harness(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.files = self.home / "session-state" / SESSION / "files"
        self.files.mkdir(parents=True)
        self.store = self.home / "session-store.db"

    def write_ledger(self, synced: str | int = 9) -> None:
        (self.files / "ledger.md").write_text(
            f"# where-were-we\n\ngoal: g\nlast_synced_turn: {synced}\n\n"
            "## state\nsomething\n"
        )

    def write_store(self, max_turn: int, session: str = SESSION) -> None:
        with sqlite3.connect(self.store) as con:
            con.execute(
                "CREATE TABLE IF NOT EXISTS turns (session_id TEXT, turn_index INTEGER)"
            )
            con.executemany(
                "INSERT INTO turns VALUES (?, ?)",
                [(session, i) for i in range(max_turn + 1)],
            )


class TestDriftMeasurement(Harness):
    def test_drift_is_the_gap_between_ledger_and_store(self) -> None:
        self.write_ledger(synced=9)
        self.write_store(max_turn=34)
        self.assertEqual(nudge.drift(self.home, SESSION), 25)

    def test_current_ledger_has_no_drift(self) -> None:
        self.write_ledger(synced=34)
        self.write_store(max_turn=34)
        self.assertEqual(nudge.drift(self.home, SESSION), 0)

    def test_missing_ledger_is_unknown_not_zero(self) -> None:
        """No ledger means the user never opted in. Stay out of the way."""
        self.write_store(max_turn=34)
        self.assertIsNone(nudge.drift(self.home, SESSION))

    def test_missing_store_is_unknown(self) -> None:
        self.write_ledger(synced=9)
        self.assertIsNone(nudge.drift(self.home, SESSION))

    def test_damaged_marker_is_unknown(self) -> None:
        """A guess dressed as a measurement is worse than staying quiet."""
        self.write_ledger(synced="not-a-number")
        self.write_store(max_turn=34)
        self.assertIsNone(nudge.drift(self.home, SESSION))

    def test_absent_marker_is_unknown(self) -> None:
        (self.files / "ledger.md").write_text("# where-were-we\n\ngoal: g\n")
        self.write_store(max_turn=34)
        self.assertIsNone(nudge.drift(self.home, SESSION))

    def test_marker_after_a_heading_is_not_read_as_metadata(self) -> None:
        """Only the preamble carries metadata; body text must not be trusted."""
        (self.files / "ledger.md").write_text(
            "# where-were-we\n\ngoal: g\n\n## state\nlast_synced_turn: 0\n"
        )
        self.write_store(max_turn=34)
        self.assertIsNone(nudge.drift(self.home, SESSION))

    def test_another_sessions_turns_are_not_counted(self) -> None:
        self.write_ledger(synced=9)
        self.write_store(max_turn=9)
        self.write_store(max_turn=400, session="someone-else")
        self.assertEqual(nudge.drift(self.home, SESSION), 0)

    def test_ledger_ahead_of_the_store_reports_no_drift(self) -> None:
        """Never negative. A pointer past the store is a different fault."""
        self.write_ledger(synced=99)
        self.write_store(max_turn=34)
        self.assertEqual(nudge.drift(self.home, SESSION), 0)

    def test_empty_session_id_is_unknown(self) -> None:
        self.assertIsNone(nudge.drift(self.home, ""))


class TestHookDecision(Harness):
    def run_hook(self, payload: dict, drift_limit: str | None = None) -> dict:
        import os

        env = dict(os.environ, COPILOT_HOME=str(self.home))
        if drift_limit is not None:
            env["COPILOT_LEDGER_MAX_DRIFT"] = drift_limit
        proc = subprocess.run(
            [sys.executable, str(HOOK)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=20,
            env=env,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)

    def test_blocks_when_well_behind(self) -> None:
        self.write_ledger(synced=9)
        self.write_store(max_turn=34)
        out = self.run_hook({"sessionId": SESSION})
        self.assertEqual(out.get("decision"), "block")
        self.assertIn("25 turns behind", out["reason"])

    def test_silent_when_current(self) -> None:
        self.write_ledger(synced=34)
        self.write_store(max_turn=34)
        self.assertEqual(self.run_hook({"sessionId": SESSION}), {})

    def test_silent_just_under_the_threshold(self) -> None:
        self.write_ledger(synced=25)
        self.write_store(max_turn=34)
        self.assertEqual(self.run_hook({"sessionId": SESSION}), {})

    def test_fires_exactly_at_the_threshold(self) -> None:
        self.write_ledger(synced=24)
        self.write_store(max_turn=34)
        out = self.run_hook({"sessionId": SESSION})
        self.assertEqual(out.get("decision"), "block")

    def test_threshold_is_configurable(self) -> None:
        self.write_ledger(synced=31)
        self.write_store(max_turn=34)
        self.assertEqual(self.run_hook({"sessionId": SESSION}), {})
        out = self.run_hook({"sessionId": SESSION}, drift_limit="3")
        self.assertEqual(out.get("decision"), "block")

    def test_does_not_block_twice_in_a_row(self) -> None:
        """stopHookActive means we already interrupted this stop."""
        self.write_ledger(synced=0)
        self.write_store(max_turn=99)
        out = self.run_hook({"sessionId": SESSION, "stopHookActive": True})
        self.assertEqual(out, {})

    def test_silent_with_no_ledger(self) -> None:
        self.write_store(max_turn=99)
        self.assertEqual(self.run_hook({"sessionId": SESSION}), {})

    def test_silent_with_no_session_id(self) -> None:
        self.write_ledger(synced=0)
        self.write_store(max_turn=99)
        self.assertEqual(self.run_hook({}), {})


class TestNeverWedgesTheTurn(unittest.TestCase):
    def run_raw(self, payload: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(HOOK)],
            input=payload,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )

    def test_garbage_stdin_exits_zero_with_valid_json(self) -> None:
        proc = self.run_raw("]][[ not json")
        self.assertEqual(proc.returncode, 0)
        json.loads(proc.stdout)

    def test_empty_stdin_exits_zero(self) -> None:
        proc = self.run_raw("")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(json.loads(proc.stdout), {})

    def test_unknown_home_exits_zero(self) -> None:
        import os

        proc = subprocess.run(
            [sys.executable, str(HOOK)],
            input=json.dumps({"sessionId": "x"}),
            capture_output=True,
            text=True,
            timeout=20,
            env=dict(os.environ, COPILOT_HOME="/nonexistent/path/xyz"),
            check=False,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(json.loads(proc.stdout), {})


class TestReminderText(unittest.TestCase):
    """The reason is enqueued as a message, so it is read by a person too."""

    def test_no_line_exceeds_seventy_columns(self) -> None:
        for line in nudge.REMINDER.format(gap=25).splitlines():
            self.assertLessEqual(len(line), 70, line)

    def test_is_ascii(self) -> None:
        nudge.REMINDER.format(gap=25).encode("ascii")

    def test_uses_no_em_dash_or_spaced_hyphen(self) -> None:
        text = nudge.REMINDER.format(gap=25)
        self.assertNotIn("\u2014", text)
        # The command examples legitimately contain --flags, so only a hyphen
        # used as prose punctuation is checked for.
        for line in text.splitlines():
            if line.strip().startswith(("python3", "S=")):
                continue
            self.assertNotIn(" - ", line)

    def test_names_the_three_kinds_it_asks_for(self) -> None:
        text = nudge.REMINDER.format(gap=25)
        for kind in ("--kind state", "--kind next", "--kind todo"):
            self.assertIn(kind, text)

    def test_tells_the_agent_not_to_interrupt_the_user(self) -> None:
        self.assertIn("do not ask Ali to confirm", nudge.REMINDER)


if __name__ == "__main__":
    unittest.main()
