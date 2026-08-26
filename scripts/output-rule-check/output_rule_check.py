#!/usr/bin/env python3
"""Check agent-authored terminal output against the output rules.

Self-measurement by the agent is not evidence: the same misreading that
produces a violation also writes a checker blind to it. This tool reads the
session records instead, and classifies every string an agent authored by
which field of which tool call it came from, never by a label the agent
supplied.

Stdlib only, so it can run from a hook without a virtualenv.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_WIDTH = 70
SESSION_ROOT = Path.home() / ".copilot" / "session-state"

# Surface categories.
PROSE = "prose"  # authored prose: width plus style checks
COMMAND = "command"  # authored command: width only, style is legitimate
PAYLOAD = "payload"  # authored file content: width per target language
SCHEMA = "schema"  # nested question-prompt schema, walked for prose
IDENTIFIER = "identifier"  # unbreakable token, exempt by breakage
QUOTED = "quoted"  # read from disk, exempt by provenance
IGNORE = "ignore"  # never rendered as prose
UNKNOWN = "unknown"  # not in the table: reported, and treated as prose

# Which field of which tool. Derived from the tool schema, not from the rule
# text, so a surface the prose forgets to name is still classified. Anything
# absent falls through to UNKNOWN and is reported for triage.
SURFACES: dict[str, dict[str, str]] = {
    "ask_user": {"message": PROSE, "requestedSchema": SCHEMA},
    "bash": {
        "command": COMMAND,
        "description": PROSE,
        "mode": IGNORE,
        "shellId": IDENTIFIER,
        "initial_wait": IGNORE,
        "detach": IGNORE,
    },
    "read_bash": {"shellId": IDENTIFIER, "delay": IGNORE},
    "stop_bash": {"shellId": IDENTIFIER},
    "list_bash": {},
    "create": {"path": IDENTIFIER, "file_text": PAYLOAD},
    "edit": {
        "path": IDENTIFIER,
        "new_str": PAYLOAD,
        "old_str": QUOTED,
    },
    "view": {
        "path": IDENTIFIER,
        "view_range": IGNORE,
        "forceReadLargeFiles": IGNORE,
    },
    "glob": {"pattern": IDENTIFIER, "paths": IDENTIFIER},
    "grep": {
        "pattern": IDENTIFIER,
        "paths": IDENTIFIER,
        "glob": IDENTIFIER,
        "type": IDENTIFIER,
        "output_mode": IGNORE,
        "head_limit": IGNORE,
        "multiline": IGNORE,
        "-i": IGNORE,
        "-n": IGNORE,
        "-A": IGNORE,
        "-B": IGNORE,
        "-C": IGNORE,
    },
    "task": {
        "prompt": PROSE,
        "description": PROSE,
        "name": IDENTIFIER,
        "agent_type": IGNORE,
        "model": IGNORE,
        "mode": IGNORE,
        "context_tier": IGNORE,
        "reasoning_effort": IGNORE,
    },
    "read_agent": {
        "agent_id": IDENTIFIER,
        "since_turn": IGNORE,
        "wait": IGNORE,
        "timeout": IGNORE,
    },
    "write_agent": {
        "message": PROSE,
        "agent_id": IDENTIFIER,
        "agent_ids": IDENTIFIER,
        "scope": IGNORE,
    },
    "list_agents": {"scope": IGNORE, "include_completed": IGNORE},
    "store_memory": {
        "fact": PROSE,
        "reason": PROSE,
        "citations": PROSE,
        "subject": PROSE,
        "scope": IGNORE,
    },
    "vote_memory": {
        "fact": PROSE,
        "reason": PROSE,
        "direction": IGNORE,
        "scope": IGNORE,
    },
    "sql": {"query": COMMAND, "description": PROSE},
    "session_store_sql": {
        "query": COMMAND,
        "description": PROSE,
        "source": IGNORE,
    },
    "web_fetch": {
        "url": IDENTIFIER,
        "raw": IGNORE,
        "max_length": IGNORE,
        "start_index": IGNORE,
    },
    "skill": {"skill": IDENTIFIER},
    "manage_schedule": {
        "prompt": PROSE,
        "reason": PROSE,
        "action": IGNORE,
        "interval": IDENTIFIER,
        "cron": IDENTIFIER,
        "id": IGNORE,
        "delaySeconds": IGNORE,
    },
    "tool_search_tool": {"pattern": IDENTIFIER, "limit": IGNORE},
    "fetch_copilot_cli_documentation": {},
    "open_canvas": {
        "canvasId": IDENTIFIER,
        "instanceId": IDENTIFIER,
        "extensionId": IDENTIFIER,
        "input": IGNORE,
    },
    "invoke_canvas_action": {
        "instanceId": IDENTIFIER,
        "actionName": IDENTIFIER,
        "input": IGNORE,
    },
    "list_canvas_capabilities": {
        "canvasId": IDENTIFIER,
        "extensionId": IDENTIFIER,
    },
}

# Search queries and repository coordinates are authored but unbreakable.
GITHUB_IDENTIFIER_FIELDS = {
    "query",
    "path",
    "paths",
    "sha",
    "ref",
    "repo",
    "owner",
    "branch",
    "name",
    "head",
    "base",
    "commit_sha",
    "ref_type",
    "tool_name",
}

# Width a language allows for authored file content. Prose files take the
# terminal limit; source files take the limit their formatter enforces.
PAYLOAD_WIDTH: dict[str, int] = {
    ".md": DEFAULT_WIDTH,
    ".markdown": DEFAULT_WIDTH,
    ".rst": DEFAULT_WIDTH,
    ".txt": DEFAULT_WIDTH,
    ".py": 88,
    ".pyi": 88,
    ".sh": 100,
    ".bash": 100,
    ".zsh": 100,
    ".bats": 100,
    ".js": 100,
    ".ts": 100,
    ".tsx": 100,
    ".jsx": 100,
    ".go": 120,
    ".rs": 100,
    ".toml": 100,
    ".yaml": 120,
    ".yml": 120,
    ".json": 200,
    ".lock": 200,
}

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
DASH_CHARS = {
    "\u2014": "em-dash",
    "\u2013": "en-dash",
    "\u2015": "horizontal bar",
}
SPACED_HYPHEN_RE = re.compile(r"(?<=\S) -{1,2} (?=\S)")
# Markdown bullets and CLI flags legitimately start a line with a hyphen.
BULLET_RE = re.compile(r"^\s*-\s")


def display_width(text: str) -> int:
    """Columns a terminal renders, not bytes or codepoints."""
    text = ANSI_RE.sub("", text).expandtabs(8)
    width = 0
    for ch in text:
        if unicodedata.combining(ch):
            continue
        width += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return width


def has_wrap_opportunity(line: str, limit: int) -> bool:
    """False when the first token alone overflows, so no break was possible."""
    stripped = line.lstrip()
    if not stripped:
        return False
    indent = line[: len(line) - len(stripped)]
    first = stripped.split(" ", 1)[0]
    return display_width(indent + first) <= limit


@dataclass
class Finding:
    kind: str
    surface: str
    category: str
    width: int
    limit: int
    line_no: int
    excerpt: str
    timestamp: str = ""

    @property
    def is_width(self) -> bool:
        return self.kind == "width"


@dataclass
class Report:
    session: str
    findings: list[Finding] = field(default_factory=list)
    unclassified: set[str] = field(default_factory=set)
    surfaces_seen: int = 0
    lines_checked: int = 0


def trim_to(text: str, room: int) -> str:
    """Trim to a display width, marking the cut."""
    if room < 4:
        return "..."
    if display_width(text) <= room:
        return text
    out = ""
    for ch in text:
        if display_width(out + ch) > room - 3:
            break
        out += ch
    return out + "..."


def excerpt_of(line: str, room: int = 200) -> str:
    """Flatten a line for reporting. Trimmed again at format time."""
    return trim_to(ANSI_RE.sub("", line).expandtabs(8).strip(), room)


def check_width(
    text: str,
    surface: str,
    category: str,
    limit: int,
    report: Report,
    ts: str,
) -> None:
    for idx, line in enumerate(text.split("\n"), start=1):
        report.lines_checked += 1
        width = display_width(line)
        if width <= limit:
            continue
        if not has_wrap_opportunity(line, limit):
            continue  # exempt by breakage: a single unbreakable token
        report.findings.append(
            Finding(
                kind="width",
                surface=surface,
                category=category,
                width=width,
                limit=limit,
                line_no=idx,
                excerpt=excerpt_of(line),
                timestamp=ts,
            )
        )


def _style_finding(kind: str, surface: str, line: str, idx: int, ts: str) -> Finding:
    return Finding(
        kind=kind,
        surface=surface,
        category=PROSE,
        width=display_width(line),
        limit=0,
        line_no=idx,
        excerpt=excerpt_of(line),
        timestamp=ts,
    )


def check_style(text: str, surface: str, report: Report, ts: str) -> None:
    for idx, line in enumerate(text.split("\n"), start=1):
        for ch, name in DASH_CHARS.items():
            if ch in line:
                report.findings.append(
                    _style_finding(name, surface, line, idx, ts)
                )
                break
        if SPACED_HYPHEN_RE.search(line) and not BULLET_RE.match(line):
            report.findings.append(
                _style_finding("spaced hyphen", surface, line, idx, ts)
            )
        non_ascii = {c for c in line if ord(c) > 127 and c not in DASH_CHARS}
        if non_ascii:
            glyphs = " ".join(sorted(non_ascii))
            report.findings.append(
                _style_finding(f"non-ascii ({glyphs})", surface, line, idx, ts)
            )


def walk_schema(node: object, path: str) -> Iterator[tuple[str, str]]:
    """Yield the user-visible strings of a question prompt schema."""
    visible = {"title", "description", "enumNames"}
    if isinstance(node, dict):
        for key, value in node.items():
            here = f"{path}.{key}"
            if key in visible:
                if isinstance(value, str):
                    yield here, value
                elif isinstance(value, list):
                    for i, item in enumerate(value):
                        if isinstance(item, str):
                            yield f"{here}[{i}]", item
            else:
                yield from walk_schema(value, here)
    elif isinstance(node, list):
        for i, item in enumerate(node):
            yield from walk_schema(item, f"{path}[{i}]")


def payload_limit(args: dict[str, Any], default: int) -> int:
    target = args.get("path") or args.get("file_path") or ""
    suffix = Path(str(target)).suffix.lower()
    return PAYLOAD_WIDTH.get(suffix, default)


def classify(tool: str, key: str) -> str:
    if tool.startswith("github-mcp-server-"):
        if key in GITHUB_IDENTIFIER_FIELDS:
            return IDENTIFIER
        if key in {"body", "title", "message", "description"}:
            return PROSE
        return UNKNOWN
    table = SURFACES.get(tool)
    if table is None:
        return UNKNOWN
    return table.get(key, UNKNOWN)


def decode_arguments(raw: object) -> dict[str, Any]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            return {}
    if isinstance(raw, dict):
        return {str(k): v for k, v in raw.items()}
    return {}


def check_tool_call(event: dict[str, Any], limit: int, report: Report) -> None:
    data = event.get("data") or {}
    tool = str(data.get("toolName") or "?")
    ts = str(event.get("timestamp") or "")
    args = decode_arguments(data.get("arguments"))
    for key, value in args.items():
        category = classify(tool, key)
        surface = f"{tool}.{key}"
        if category == SCHEMA:
            for sub_path, text in walk_schema(value, surface):
                report.surfaces_seen += 1
                check_width(text, sub_path, PROSE, limit, report, ts)
                check_style(text, sub_path, report, ts)
            continue
        if not isinstance(value, str) or not value:
            continue
        report.surfaces_seen += 1
        if category in (IDENTIFIER, QUOTED, IGNORE):
            continue
        if category == PAYLOAD:
            target = payload_limit(args, limit)
            check_width(value, surface, PAYLOAD, target, report, ts)
            continue
        if category == UNKNOWN:
            report.unclassified.add(surface)
        effective = PROSE if category == UNKNOWN else category
        check_width(value, surface, effective, limit, report, ts)
        if effective == PROSE:
            check_style(value, surface, report, ts)


def message_text(data: dict[str, Any]) -> str:
    content = data.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts)
    return ""


def check_session(path: Path, limit: int) -> Report:
    report = Report(session=path.parent.name)
    with path.open(encoding="utf-8", errors="replace") as handle:
        for raw in handle:
            raw = raw.strip()
            if not raw:
                continue
            try:
                event = json.loads(raw)
            except ValueError:
                continue
            kind = event.get("type")
            if kind == "tool.execution_start":
                check_tool_call(event, limit, report)
            elif kind == "assistant.message":
                data = event.get("data") or {}
                text = message_text(data)
                if not text:
                    continue
                report.surfaces_seen += 1
                ts = str(event.get("timestamp") or "")
                surface = "assistant.message"
                check_width(text, surface, PROSE, limit, report, ts)
                check_style(text, surface, report, ts)
            # tool.execution_complete, user.message and system.message are
            # exempt by provenance: Ali or a process wrote those bytes.
    return report


def find_sessions(args: argparse.Namespace) -> list[Path]:
    root = Path(args.root).expanduser()
    if args.session:
        candidate = Path(args.session).expanduser()
        if candidate.is_dir():
            return [candidate / "events.jsonl"]
        if candidate.is_file():
            return [candidate]
        return [root / args.session / "events.jsonl"]
    found = sorted(
        root.glob("*/events.jsonl"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if args.all:
        return found
    return found[:1]


def format_report(report: Report, limit: int, show: int) -> list[str]:
    lines: list[str] = []
    lines.append(f"session {report.session}")
    lines.append(
        f"  {report.surfaces_seen} authored surfaces, "
        f"{report.lines_checked} lines, limit {limit}"
    )
    if not report.findings and not report.unclassified:
        lines.append("  clean")
        return lines
    by_surface: dict[str, list[Finding]] = {}
    for finding in report.findings:
        by_surface.setdefault(finding.surface, []).append(finding)
    order = sorted(
        by_surface.items(),
        key=lambda kv: max(f.width for f in kv[1]),
        reverse=True,
    )
    for surface, items in order:
        worst = max(f.width for f in items)
        kinds = ", ".join(sorted({f.kind for f in items}))
        count = len(items)
        noun = "finding" if count == 1 else "findings"
        # A surface path is one unbreakable token, so it gets its own line.
        lines.append(f"  {surface}")
        lines.append(f"    {count} {noun}, worst {worst} cols")
        lines.append(trim_to(f"    kinds: {kinds}", limit))
        ranked = sorted(items, key=lambda f: f.width, reverse=True)
        for finding in ranked[:show]:
            if finding.is_width:
                tag = f"{finding.width}>{finding.limit}"
            else:
                tag = finding.kind
            prefix = f"    [{tag}] "
            room = limit - display_width(prefix)
            lines.append(prefix + trim_to(finding.excerpt, room))
    if report.unclassified:
        lines.append("  unclassified surfaces, add them to SURFACES:")
        for surface in sorted(report.unclassified):
            lines.append(f"    {surface}")
    return lines


def as_json(reports: list[Report]) -> list[dict[str, Any]]:
    return [
        {
            "session": r.session,
            "surfaces": r.surfaces_seen,
            "lines": r.lines_checked,
            "unclassified": sorted(r.unclassified),
            "findings": [
                {
                    "kind": f.kind,
                    "surface": f.surface,
                    "category": f.category,
                    "width": f.width,
                    "limit": f.limit,
                    "line": f.line_no,
                    "excerpt": f.excerpt,
                    "timestamp": f.timestamp,
                }
                for f in r.findings
            ],
        }
        for r in reports
    ]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="output-rule-check",
        description="Measure agent-authored output against the output rules.",
    )
    parser.add_argument(
        "session",
        nargs="?",
        help="session id, session directory, or events.jsonl path",
    )
    parser.add_argument(
        "--all", action="store_true", help="check every recorded session"
    )
    parser.add_argument(
        "--root", default=str(SESSION_ROOT), help="session-state directory"
    )
    parser.add_argument(
        "--width", type=int, default=DEFAULT_WIDTH, help="display column limit"
    )
    parser.add_argument(
        "--show", type=int, default=3, help="example findings per surface"
    )
    parser.add_argument(
        "--json", action="store_true", help="emit machine output"
    )
    parser.add_argument(
        "--quiet", action="store_true", help="suppress clean sessions"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    paths = [p for p in find_sessions(args) if p.is_file()]
    if not paths:
        print("no session records found", file=sys.stderr)
        return 2

    reports = [check_session(path, args.width) for path in paths]
    total = sum(len(r.findings) for r in reports)

    if args.json:
        json.dump(as_json(reports), sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 1 if total else 0

    for report in reports:
        if args.quiet and not report.findings and not report.unclassified:
            continue
        for line in format_report(report, args.width, args.show):
            print(line)
    print(f"\n{total} findings across {len(reports)} session(s)")
    return 1 if total else 0


if __name__ == "__main__":
    raise SystemExit(main())
