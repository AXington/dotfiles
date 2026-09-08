"""Tests for the todos section and the sync pointer.

Run with: python3 -m unittest discover -s . -p 'test_*.py'

These exercise wwm_ledger against a real temporary session directory and a
real sqlite store, because both bugs under test were in the seam between the
ledger file and the store rather than in either one alone.
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import wwm_ledger
import wwm_session
from wwm_ledger import Thread, Todo, load, parse, record, serialize

SESSION = "test-session"


class LedgerHarness(unittest.TestCase):
    """Point the module at a throwaway state directory and store."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

        self.ledger_file = self.root / "ledger.md"
        self.store = self.root / "store.db"

        patch_ledger = mock.patch.object(
            wwm_session, "ledger_path", lambda _sid: self.ledger_file
        )
        patch_store = mock.patch.object(wwm_session, "store_path", lambda: self.store)
        patch_lock = mock.patch.object(wwm_ledger, "_locked", self._null_lock)
        for patch in (patch_ledger, patch_store, patch_lock):
            patch.start()
            self.addCleanup(patch.stop)

    @staticmethod
    def _null_lock(_session_id: str):  # type: ignore[no-untyped-def]
        """Replace the flock context manager.

        The real one locks a path under the live session state directory,
        which these tests deliberately do not have.
        """
        import contextlib

        return contextlib.nullcontext()

    def seed_store(self, max_turn: int) -> None:
        """Create a store whose highest turn index is max_turn."""
        with sqlite3.connect(self.store) as conn:
            conn.execute("CREATE TABLE turns (session_id TEXT, turn_index INTEGER)")
            conn.executemany(
                "INSERT INTO turns VALUES (?, ?)",
                [(SESSION, i) for i in range(max_turn + 1)],
            )


class TestSyncPointer(LedgerHarness):
    """The pointer must only advance on evidence that covers the turns.

    Regression for the live corruption: a single throwaway `--kind thread`
    write moved last_synced_turn from 9 to 34 while state and next still held
    three-week-old content. Reconciliation selects turn_index > the pointer,
    so those 25 turns could never surface again.
    """

    def test_item_append_does_not_advance_pointer(self) -> None:
        self.seed_store(max_turn=34)
        self.ledger_file.write_text(
            "# where-were-we\n\ngoal: something\nlast_synced_turn: 9\n\n"
        )

        for kind in ("decision", "thread", "todo", "blocker"):
            with self.subTest(kind=kind):
                led = record(SESSION, kind, f"a {kind}")
                self.assertEqual(
                    led.last_synced_turn,
                    9,
                    f"recording one {kind} claimed 25 unreviewed turns were reconciled",
                )

    def test_state_and_next_do_advance_pointer(self) -> None:
        """Both describe the whole session, so both are honest evidence."""
        for kind in ("state", "next"):
            with self.subTest(kind=kind):
                self.ledger_file.write_text(
                    "# where-were-we\n\ngoal: g\nlast_synced_turn: 9\n\n"
                )
                self.seed_store(max_turn=34)
                led = record(SESSION, kind, "a whole-session summary")
                self.assertEqual(led.last_synced_turn, 34)
                self.store.unlink()

    def test_pointer_never_moves_backwards(self) -> None:
        """A missing store must not erase a pointer that was true.

        _max_turn_index returns -1 on every failure path. Assigning that
        unconditionally turned a transient read failure into permanent loss
        of the sync boundary.
        """
        self.ledger_file.write_text(
            "# where-were-we\n\ngoal: g\nlast_synced_turn: 34\n\n"
        )
        self.assertFalse(self.store.exists())

        led = record(SESSION, "state", "written while the store is gone")
        self.assertEqual(led.last_synced_turn, 34)

    def test_goal_does_not_advance_pointer(self) -> None:
        """Conservative on purpose.

        Under-claiming sync costs a replay of history the user has seen.
        Over-claiming costs silent, permanent loss. The costs are not
        symmetric, so a marginal kind stays out.
        """
        self.seed_store(max_turn=34)
        self.ledger_file.write_text(
            "# where-were-we\n\ngoal: old\nlast_synced_turn: 9\n\n"
        )
        led = record(SESSION, "goal", "new goal")
        self.assertEqual(led.last_synced_turn, 9)


class TestTodosSection(LedgerHarness):
    def test_record_todo_round_trips(self) -> None:
        record(SESSION, "todo", "file the feature request")
        led = load(SESSION)
        self.assertEqual([t.text for t in led.todos], ["file the feature request"])
        self.assertEqual(led.threads, [])

    def test_todos_and_threads_stay_separate(self) -> None:
        record(SESSION, "thread", "an open question")
        record(SESSION, "todo", "a deferred commitment")
        led = load(SESSION)
        self.assertEqual([t.text for t in led.threads], ["an open question"])
        self.assertEqual([t.text for t in led.todos], ["a deferred commitment"])
        self.assertIsInstance(led.threads[0], Thread)
        self.assertIsInstance(led.todos[0], Todo)

    def test_checked_box_parses_as_done(self) -> None:
        led = parse("# where-were-we\n\n## todos\n- [x] shipped\n- [ ] pending\n")
        self.assertEqual([t.done for t in led.todos], [True, False])
        self.assertEqual(led.damaged, [])

    def test_heading_absent_when_no_todos(self) -> None:
        """Existing ledgers must not gain an empty heading on the next write.

        Follows the goal history precedent: emitting an unconditional heading
        rewrites every ledger on disk for no content.
        """
        record(SESSION, "decision", "some decision")
        self.assertNotIn("## todos", self.ledger_file.read_text())

    def test_heading_present_once_a_todo_exists(self) -> None:
        record(SESSION, "todo", "something deferred")
        text = self.ledger_file.read_text()
        self.assertIn("## todos", text)
        self.assertIn("- [ ] something deferred", text)

    def test_todos_render_above_decisions(self) -> None:
        """Forward-looking content is read before the record of the past."""
        record(SESSION, "decision", "a decision")
        record(SESSION, "todo", "a todo")
        text = self.ledger_file.read_text()
        self.assertLess(text.index("## todos"), text.index("## decisions"))

    def test_unknown_kind_still_refused(self) -> None:
        with self.assertRaises(ValueError):
            record(SESSION, "reminder", "not a real kind")


class TestTodoPreservation(unittest.TestCase):
    """Round-trip and damage behaviour, no store or session dir needed."""

    def test_unparsable_todo_line_is_kept_verbatim(self) -> None:
        """A typo must be reported and preserved, never silently dropped."""
        led = parse("# where-were-we\n\n## todos\n- [ ] good one\n* bad shape\n")
        self.assertEqual([t.text for t in led.todos], ["good one"])
        self.assertIn("todos", led.damaged)
        self.assertIn("* bad shape", led.unparsed["todos"])
        self.assertIn("* bad shape", serialize(led))

    def test_serialize_parse_round_trip(self) -> None:
        led = wwm_ledger.Ledger(
            goal="g",
            todos=[Todo(False, "open"), Todo(True, "closed")],
            threads=[Thread(False, "a thread")],
        )
        again = parse(serialize(led))
        self.assertEqual(again.todos, led.todos)
        self.assertEqual(again.threads, led.threads)
        self.assertEqual(again.damaged, [])

    def test_has_content_sees_a_todo_only_ledger(self) -> None:
        """Adoption must not overwrite a ledger holding only todos."""
        led = wwm_ledger.Ledger(todos=[Todo(False, "the only content")])
        self.assertTrue(wwm_ledger._has_content(led))

    def test_has_content_still_sees_unparsed_only_ledger(self) -> None:
        """Guards a line dropped while editing this function.

        A ledger whose every section failed to parse still holds the user's
        words. Losing this check let adopt() overwrite it.
        """
        led = wwm_ledger.Ledger(unparsed={"decisions": ["- garbled"]})
        self.assertTrue(wwm_ledger._has_content(led))

    def test_empty_ledger_has_no_content(self) -> None:
        self.assertFalse(wwm_ledger._has_content(wwm_ledger.Ledger()))


class TestCollectSurfacing(unittest.TestCase):
    def test_open_todos_surface_and_done_ones_do_not(self) -> None:
        import wwm_collect

        led = wwm_ledger.Ledger(
            todos=[Todo(False, "still owed"), Todo(True, "already done")]
        )
        labels = wwm_collect._recorded_items(led)
        todos = [i["text"] for i in labels if i["label"] == "Todo"]
        self.assertEqual(todos, ["still owed"])

    def test_todo_is_a_renderable_section(self) -> None:
        import wwm_render

        self.assertIn("Todo", wwm_render.SECTIONS["todos"])
        self.assertIn("Todo", wwm_render.TLDR_PRIORITY)
        self.assertIn("[todos]", wwm_render.MENU)

    def test_todo_outranks_thread_in_tldr(self) -> None:
        import wwm_render

        order = wwm_render.TLDR_PRIORITY
        self.assertLess(order.index("Todo"), order.index("Thread"))


if __name__ == "__main__":
    unittest.main()
