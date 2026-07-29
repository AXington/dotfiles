"""The resolve quarantine gate must not fire on a preference (BUG-13 commit 3).

``RESOLVE_ACCEPT_FLOOR`` exists to catch "the resolver found only wrong
candidates". A preference penalty is not evidence of that, so the floor reads
``quality_score``. Everything else (ranking, hydration, confidence tier) keeps
reading ``match_score``, because deciding WHICH candidate wins is exactly what
a preference is for.
"""

import pytest

from tuneshift.db import Database
from tuneshift.library.worker import ResolutionWorker, ResolvedCandidate
from tuneshift.models import Track


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "test.db")


def _track(db):
    return db.insert_track(Track(title="Vogue", artist="Madonna"))


def _resolve(db, candidates):
    tid = _track(db)
    ResolutionWorker(db, lambda _t: candidates).resolve_tracks([tid], force=True)
    return tid


def test_preference_alone_never_quarantines(db):
    """The BUG-13 harm mechanism, at the gate that caused it.

    Ranked below the floor only because the sole candidate carries the
    non-preferred lyric rating. Nothing is wrong with the recording, so the
    track must resolve rather than land in quarantine.
    """
    tid = _resolve(
        db, [ResolvedCandidate("tidal", "1", {"match_score": 45, "quality_score": 100})]
    )

    assert db.get_resolution_queue_state(tid) == "resolved"


def test_real_defect_still_quarantines(db):
    """Anti-forgiveness: a quality_score is not a free pass."""
    tid = _resolve(
        db, [ResolvedCandidate("tidal", "1", {"match_score": 20, "quality_score": 30})]
    )

    assert db.get_resolution_queue_state(tid) == "quarantined"
    assert "no_confident_match" in (db.get_track(tid).quarantine_reason or "")


def test_legacy_candidate_without_quality_score_falls_back(db):
    """Every candidate row persisted before this change lacks quality_score.

    Falling back to match_score keeps the floor firing on the existing library.
    Treating a missing value as "no quality concern" would silently disable
    the quarantine gate everywhere, which is a worse bug than the one being
    fixed here.
    """
    tid = _resolve(db, [ResolvedCandidate("tidal", "1", {"match_score": 20})])

    assert db.get_resolution_queue_state(tid) == "quarantined"


def test_ranking_still_uses_match_score(db):
    """Preference must keep deciding the winner.

    The explicit release ranks higher (match 90) while the clean one is the
    better pure-quality match (quality 100). Hydration must follow match_score,
    otherwise the preference has been neutered instead of relocated.
    """
    tid = _resolve(
        db,
        [
            ResolvedCandidate(
                "tidal",
                "clean",
                {"match_score": 80, "quality_score": 100, "album": "Clean Album"},
            ),
            ResolvedCandidate(
                "tidal",
                "explicit",
                {"match_score": 90, "quality_score": 90, "album": "Explicit Album"},
            ),
        ],
    )

    assert db.get_resolution_queue_state(tid) == "resolved"
    assert db.get_track(tid).album == "Explicit Album"


def test_floor_reads_quality_of_the_best_ranked_candidate(db):
    """The floor asks about the candidate that actually won, not the best
    quality_score in the set. A high-quality also-ran must not rescue a run
    whose winner is genuinely poor."""
    tid = _resolve(
        db,
        [
            ResolvedCandidate("tidal", "winner", {"match_score": 30, "quality_score": 30}),
            ResolvedCandidate("tidal", "loser", {"match_score": 10, "quality_score": 99}),
        ],
    )

    assert db.get_resolution_queue_state(tid) == "quarantined"
