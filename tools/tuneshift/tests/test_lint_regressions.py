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
