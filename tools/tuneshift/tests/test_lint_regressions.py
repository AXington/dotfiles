"""Lint-level regression guards enforced as tests.

These run ruff for a specific rule across the package so a regression fails the
suite even before CI. Kept narrow (single rules) so they are fast and stable.
"""

import re
import subprocess
import sys
from pathlib import Path

_PKG_ROOT = Path(__file__).resolve().parent.parent


def _ruff_select(rule: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "ruff", "check", "tuneshift", "--select", rule],
        cwd=_PKG_ROOT,
        capture_output=True,
        text=True,
    )


def test_no_f821_undefined_names() -> None:
    """No undefined-name references anywhere in the package (guards BUG-L1)."""
    result = _ruff_select("F821")
    assert result.returncode == 0, result.stdout + result.stderr


def test_source_is_ascii_only() -> None:
    """All package source is pure ASCII (house rule: no non-ASCII, no em-dashes).

    Walks ``tuneshift/**/*.py`` and asserts every character is ord < 0x80,
    reporting ``file:line`` and the offending character(s) on failure.
    """
    offenders: list[str] = []
    for path in sorted((_PKG_ROOT / "tuneshift").rglob("*.py")):
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            bad = [(char, hex(ord(char))) for char in line if ord(char) > 0x7F]
            if bad:
                rel = path.relative_to(_PKG_ROOT)
                offenders.append(f"{rel}:{lineno}: {bad}")
    assert not offenders, "Non-ASCII characters found:\n" + "\n".join(offenders)


def test_no_e501() -> None:
    """No lines exceed the configured length limit (guards the E501 cleanup)."""
    result = _ruff_select("E501")
    assert result.returncode == 0, result.stdout + result.stderr


def test_no_s113_request_without_timeout() -> None:
    """Every requests/urllib call sets an explicit timeout (guards B113/S113)."""
    result = _ruff_select("S113")
    assert result.returncode == 0, result.stdout + result.stderr


def test_no_s310_unguarded_urlopen() -> None:
    """Every urlopen/Request site is scheme-guarded or justified (guards S310)."""
    result = _ruff_select("S310")
    assert result.returncode == 0, result.stdout + result.stderr


def test_no_ble001_blind_except() -> None:
    """No un-justified blind ``except Exception`` (guards ERR-M1).

    Every remaining broad catch is a genuine boundary handler carrying a
    justified ``# noqa: BLE001``; all others were narrowed to specific types.
    """
    result = _ruff_select("BLE001")
    assert result.returncode == 0, result.stdout + result.stderr


# Raw-SQL access is confined to the persistence layer. Every other module must
# route through named Database methods so the SQL surface stays auditable and
# swappable (guards ARCH-M3). ``.conn.commit()`` stays allowed everywhere: it is
# a legitimate caller-managed transaction boundary, not a query.
_CONN_LEAK_RE = re.compile(r"\.conn\.(execute|executescript|executemany|cursor)\b")
_PERSISTENCE_ALLOWLIST = (
    Path("tuneshift") / "db.py",
    Path("tuneshift") / "persistence",
)


def _is_persistence_path(rel: Path) -> bool:
    return any(
        rel == allowed or allowed in rel.parents for allowed in _PERSISTENCE_ALLOWLIST
    )


def test_no_conn_execute_leak() -> None:
    """No raw ``.conn.execute*/cursor`` outside the persistence layer (ARCH-M3).

    Walks ``tuneshift/**/*.py`` and fails if any module other than ``db.py`` or
    ``persistence/`` touches the sqlite connection's query API directly. This is
    a structural leak-gate: new command/library code must call a named Database
    method instead of embedding SQL.
    """
    offenders: list[str] = []
    for path in sorted((_PKG_ROOT / "tuneshift").rglob("*.py")):
        rel = path.relative_to(_PKG_ROOT)
        if _is_persistence_path(rel):
            continue
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            if _CONN_LEAK_RE.search(line):
                offenders.append(f"{rel}:{lineno}: {line.strip()}")
    assert not offenders, (
        "Raw .conn query access outside the persistence layer "
        "(route through a named Database method instead):\n" + "\n".join(offenders)
    )
