"""Guard that tests never open the committed production DB (PROC-M1).

The repo tracks a real ``tuneshift.db`` (the curation session's live library).
Opening it inside a test runs ``_ensure_schema()`` and could mutate a 22MB
production artifact. The autouse guard in ``conftest.py`` must both redirect the
default path to a temp file and hard-fail any code that still resolves the
tracked artifact.
"""

from pathlib import Path

import pytest

import tuneshift.db as _db

TRACKED_DB = (Path(_db.__file__).parent.parent / "tuneshift.db").resolve()


def test_default_db_path_redirected_away_from_tracked_artifact() -> None:
    """Under the autouse guard the default DB path is a temp file, not the artifact."""
    assert _db.get_default_db_path().resolve() != TRACKED_DB


def test_opening_tracked_db_is_blocked() -> None:
    """Explicitly constructing Database on the tracked artifact fails loudly."""
    with pytest.raises(RuntimeError, match="committed production database"):
        _db.Database(TRACKED_DB)


def test_temp_db_still_allowed(tmp_path: Path) -> None:
    """The guard does not block legitimate temp databases (proves it has teeth)."""
    db = _db.Database(tmp_path / "ok.db")
    assert db.path.resolve() != TRACKED_DB
