"""Regression tests for the output rule checker.

The fixtures are seeded with real widths measured from a live session, not
invented numbers. If this suite ever passes while reporting those fixtures
clean, the checker has regained the blind spot it was built to remove.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import output_rule_check as orc  # noqa: E402

# Widths observed in session 3e21189d, the session that motivated this tool.
REAL_COMMAND_WIDTH = 988
REAL_ASK_USER_WIDTH = 314
REAL_PROMPT_WIDTH = 602


def event(kind: str, data: dict) -> str:
    return json.dumps({"type": kind, "timestamp": "2026-08-25T00:00:00Z", "data": data})


def tool_call(tool: str, args: dict) -> str:
    return event("tool.execution_start", {"toolName": tool, "arguments": args})


def write_session(tmp_path: Path, lines: list[str]) -> Path:
    session = tmp_path / "session-under-test"
    session.mkdir()
    target = session / "events.jsonl"
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def run(tmp_path: Path, lines: list[str], width: int = 70) -> orc.Report:
    return orc.check_session(write_session(tmp_path, lines), width)


def surfaces(report: orc.Report) -> set[str]:
    return {f.surface for f in report.findings}


def widths(report: orc.Report, surface: str) -> list[int]:
    return [f.width for f in report.findings if f.surface == surface]


class TestDisplayWidth:
    def test_ascii_is_one_column_each(self):
        assert orc.display_width("hello") == 5

    def test_wide_glyphs_are_two_columns(self):
        assert orc.display_width("差") == 2

    def test_combining_marks_are_zero_width(self):
        assert orc.display_width("e\u0301") == 1

    def test_ansi_escapes_do_not_count(self):
        assert orc.display_width("\x1b[31mred\x1b[0m") == 3

    def test_tabs_expand(self):
        assert orc.display_width("\tx") == 9


class TestBreakageExemption:
    def test_bare_long_token_has_no_wrap_opportunity(self):
        url = "https://example.com/" + "a" * 200
        assert not orc.has_wrap_opportunity(url, 70)

    def test_prose_before_a_long_token_has_a_wrap_opportunity(self):
        url = "see https://example.com/" + "a" * 200
        assert orc.has_wrap_opportunity(url, 70)

    def test_indented_long_token_is_still_unbreakable(self):
        line = "    " + "a" * 200
        assert not orc.has_wrap_opportunity(line, 70)


class TestGoldenViolations:
    """Reproduce the failures that self-measurement missed."""

    def test_long_bash_command_is_caught(self, tmp_path):
        command = ("cd ~/dotfiles && " + "echo x && " * 200)[:REAL_COMMAND_WIDTH]
        assert orc.display_width(command) == REAL_COMMAND_WIDTH
        report = run(tmp_path, [tool_call("bash", {"command": command})])
        assert widths(report, "bash.command") == [REAL_COMMAND_WIDTH]

    def test_long_question_message_is_caught(self, tmp_path):
        message = "word " * 63
        message = message[:REAL_ASK_USER_WIDTH]
        report = run(tmp_path, [tool_call("ask_user", {"message": message})])
        assert widths(report, "ask_user.message") == [REAL_ASK_USER_WIDTH]

    def test_long_subagent_prompt_is_caught(self, tmp_path):
        prompt = ("instruction text " * 40)[:REAL_PROMPT_WIDTH]
        report = run(tmp_path, [tool_call("task", {"prompt": prompt})])
        assert widths(report, "task.prompt") == [REAL_PROMPT_WIDTH]

    def test_short_progress_note_with_an_em_dash_is_caught(self, tmp_path):
        note = "Decisive \u2014 that rejects the retry hypothesis."
        report = run(tmp_path, [event("assistant.message", {"content": note})])
        kinds = {f.kind for f in report.findings}
        assert "em-dash" in kinds

    def test_short_note_with_an_arrow_glyph_is_caught(self, tmp_path):
        note = "softirq \u2192 net_rx dominates."
        report = run(tmp_path, [event("assistant.message", {"content": note})])
        kinds = {f.kind for f in report.findings}
        assert any(k.startswith("non-ascii") for k in kinds)

    def test_short_note_with_a_spaced_hyphen_is_caught(self, tmp_path):
        note = "Side finding - flag this later."
        report = run(tmp_path, [event("assistant.message", {"content": note})])
        assert "spaced hyphen" in {f.kind for f in report.findings}

    def test_a_narrow_command_is_clean(self, tmp_path):
        report = run(tmp_path, [tool_call("bash", {"command": "ls -1"})])
        assert report.findings == []


class TestQuestionPromptSchema:
    """Option labels render to Ali, so they are covered."""

    def test_option_titles_are_measured(self, tmp_path):
        schema = {
            "properties": {
                "mode": {
                    "type": "string",
                    "oneOf": [
                        {"const": "a", "title": "A1 - instruction rule only"},
                        {"const": "b", "title": "short"},
                    ],
                }
            }
        }
        report = run(
            tmp_path,
            [tool_call("ask_user", {"message": "pick", "requestedSchema": schema})],
        )
        assert "spaced hyphen" in {f.kind for f in report.findings}

    def test_option_descriptions_are_measured(self, tmp_path):
        schema = {
            "properties": {
                "mode": {
                    "type": "boolean",
                    "description": "x " * 60,
                }
            }
        }
        report = run(
            tmp_path,
            [tool_call("ask_user", {"message": "pick", "requestedSchema": schema})],
        )
        assert any("description" in f.surface for f in report.findings)


class TestProvenanceExemptions:
    def test_process_output_is_never_measured(self, tmp_path):
        wide = "x" * 500 + " " + "y" * 200
        line = event(
            "tool.execution_complete",
            {"toolName": "bash", "result": {"content": wide}},
        )
        assert run(tmp_path, [line]).findings == []

    def test_ali_input_is_never_measured(self, tmp_path):
        wide = "word " * 200
        assert run(tmp_path, [event("user.message", {"content": wide})]).findings == []

    def test_text_read_from_a_file_is_exempt(self, tmp_path):
        wide = "word " * 200
        call = tool_call(
            "edit",
            {"path": "notes.md", "old_str": wide, "new_str": "short"},
        )
        assert "edit.old_str" not in surfaces(run(tmp_path, [call]))

    def test_text_authored_into_a_file_is_not_exempt(self, tmp_path):
        wide = "word " * 200
        call = tool_call(
            "edit",
            {"path": "notes.md", "old_str": "short", "new_str": wide},
        )
        assert "edit.new_str" in surfaces(run(tmp_path, [call]))


class TestLaunderingPaths:
    """The evasions two cold readers independently invented."""

    def test_wide_prose_written_to_a_file_is_still_measured(self, tmp_path):
        wide = "word " * 40
        call = tool_call("create", {"path": "report.md", "file_text": wide})
        assert "create.file_text" in surfaces(run(tmp_path, [call]))

    def test_a_heredoc_inside_a_command_is_still_measured(self, tmp_path):
        body = "word " * 40
        command = f"cat <<'EOF'\n{body}\nEOF"
        assert "bash.command" in surfaces(run(tmp_path, [tool_call("bash", {"command": command})]))

    def test_a_fenced_block_in_a_reply_is_still_measured(self, tmp_path):
        text = "```\n" + "word " * 40 + "\n```"
        report = run(tmp_path, [event("assistant.message", {"content": text})])
        assert "assistant.message" in surfaces(report)


class TestPayloadWidthPerLanguage:
    def test_python_is_allowed_its_formatter_width(self, tmp_path):
        line = "x = " + "1 + " * 20 + "1"  # 84 columns
        call = tool_call("create", {"path": "mod.py", "file_text": line})
        assert run(tmp_path, [call]).findings == []

    def test_python_beyond_its_formatter_width_is_caught(self, tmp_path):
        line = "x = " + "1 + " * 30 + "1"
        call = tool_call("create", {"path": "mod.py", "file_text": line})
        assert widths(run(tmp_path, [call]), "create.file_text") == [
            orc.display_width(line)
        ]

    def test_markdown_takes_the_terminal_width(self, tmp_path):
        line = "word " * 20  # 100 columns
        call = tool_call("create", {"path": "doc.md", "file_text": line})
        assert run(tmp_path, [call]).findings != []

    def test_an_unknown_extension_falls_back_to_the_terminal_width(self, tmp_path):
        line = "word " * 20
        call = tool_call("create", {"path": "thing.unknownext", "file_text": line})
        assert run(tmp_path, [call]).findings != []


class TestFailLoudDefault:
    def test_an_unknown_tool_is_reported_not_ignored(self, tmp_path):
        call = tool_call("brand_new_tool", {"note": "word " * 40})
        report = run(tmp_path, [call])
        assert "brand_new_tool.note" in report.unclassified
        assert "brand_new_tool.note" in surfaces(report)

    def test_an_unknown_field_on_a_known_tool_is_reported(self, tmp_path):
        call = tool_call("bash", {"command": "ls", "brand_new_field": "word " * 40})
        report = run(tmp_path, [call])
        assert "bash.brand_new_field" in report.unclassified

    def test_identifiers_are_exempt_by_breakage(self, tmp_path):
        call = tool_call("view", {"path": "/" + "a" * 300})
        assert run(tmp_path, [call]).findings == []


class TestArgumentDecoding:
    def test_arguments_encoded_as_a_json_string_are_decoded(self, tmp_path):
        args = json.dumps({"command": "echo " + "x " * 60})
        line = event("tool.execution_start", {"toolName": "bash", "arguments": args})
        assert "bash.command" in surfaces(run(tmp_path, [line]))

    def test_malformed_records_do_not_abort_the_run(self, tmp_path):
        lines = ["{not json", tool_call("bash", {"command": "echo " + "x " * 60})]
        assert "bash.command" in surfaces(run(tmp_path, [lines[0]] + [lines[1]]))


class TestExitCode:
    def test_a_dirty_session_exits_nonzero(self, tmp_path, capsys):
        path = write_session(tmp_path, [tool_call("bash", {"command": "x " * 60})])
        code = orc.main([str(path)])
        capsys.readouterr()
        assert code == 1

    def test_a_clean_session_exits_zero(self, tmp_path, capsys):
        path = write_session(tmp_path, [tool_call("bash", {"command": "ls -1"})])
        code = orc.main([str(path)])
        capsys.readouterr()
        assert code == 0

    def test_report_lines_stay_inside_the_margin(self, tmp_path):
        path = write_session(tmp_path, [tool_call("bash", {"command": "x " * 200})])
        report = orc.check_session(path, 70)
        for line in orc.format_report(report, 70, 3):
            assert orc.display_width(line) <= 70, line

    def test_a_long_surface_path_does_not_widen_the_report(self, tmp_path):
        schema = {
            "properties": {
                "a_rather_long_property_name_here": {
                    "type": "string",
                    "oneOf": [
                        {"const": "x", "title": "A1 - a label with a spaced hyphen"}
                    ],
                }
            }
        }
        path = write_session(
            tmp_path,
            [tool_call("ask_user", {"message": "pick", "requestedSchema": schema})],
        )
        report = orc.check_session(path, 70)
        assert report.findings
        for line in orc.format_report(report, 70, 3):
            if orc.has_wrap_opportunity(line, 70):
                assert orc.display_width(line) <= 70, line

    def test_unclassified_surfaces_are_listed(self, tmp_path, capsys):
        path = write_session(tmp_path, [tool_call("rg", {"pattern": "x"})])
        orc.main([str(path)])
        assert "rg.pattern" in capsys.readouterr().out


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
