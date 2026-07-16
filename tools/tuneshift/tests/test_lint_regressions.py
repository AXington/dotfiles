"""Lint-level regression guards enforced as tests.

These run ruff for a specific rule across the package so a regression fails the
suite even before CI. Kept narrow (single rules) so they are fast and stable.
"""

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
