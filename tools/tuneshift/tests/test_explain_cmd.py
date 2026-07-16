"""E2E tests for the ``tuneshift explain`` command (renamed from ``why``).

Covers the AC-CLI3 match explanation (playlist-scoped; criteria hard/soft,
weighted breakdown, tie-break) and the AC-CLI5 failed-match explanation (every
rejected candidate with its per-candidate rejection reason), plus the deprecated
``why`` alias.
"""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from tuneshift.commands.explain_cmd import handle_explain, handle_why
from tuneshift.db import Database
from tuneshift.matching import (
    Availability,
    CriterionOutcome,
    MatchAudit,
    ReasonCode,
    RejectedCandidate,
    SignalContribution,
)
from tuneshift.models import Track, TrackResult
from tuneshift.reconcile import reconcile_track


def _args(track_id, *, playlist=None, platform=None, live=False):
    return SimpleNamespace(
        track_id=track_id, playlist=playlist, platform=platform, live=live
    )


def _client(results: list[TrackResult]) -> MagicMock:
    client = MagicMock()
    client.platform_name = "tidal"
    client.search_isrc.return_value = None
    client.search_track.return_value = results
    client.search_album.return_value = []
    client.get_album_tracks.return_value = []
    client.search_artist.return_value = []
    client.get_artist_albums.return_value = []
    return client


# --- Playlist scoping (AC-CLI3: selection is per-playlist) ---


def test_explain_reads_playlist_scoped_audit(tmp_db: Path, capsys) -> None:
    db = Database(tmp_db)
    track_id = db.add_track(Track(title="Song", artist="Artist", album="Album"))
    playlist_id = db.create_playlist("Road Trip")
    db.add_track_to_playlist(playlist_id, track_id, 0)
    # A global decision and a DIFFERENT per-playlist decision for the same track.
    db.save_match_audit(track_id, "tidal", MatchAudit(
        availability=Availability.EXACT_AVAILABLE, reason_code=ReasonCode.MATCHED,
        chosen_platform_id="global_pick", chosen_score=90))
    db.save_match_audit(track_id, "tidal", MatchAudit(
        availability=Availability.EXACT_AVAILABLE, reason_code=ReasonCode.MATCHED,
        chosen_platform_id="playlist_pick", chosen_score=95), playlist_id=playlist_id)

    assert handle_explain(_args(track_id, playlist="Road Trip"), db) == 0
    out = capsys.readouterr().out
    assert "playlist: Road Trip" in out
    assert "playlist_pick" in out
    assert "global_pick" not in out


def test_explain_unknown_playlist_returns_1(tmp_db: Path, capsys) -> None:
    db = Database(tmp_db)
    track_id = db.add_track(Track(title="Song", artist="Artist", album="Album"))
    assert handle_explain(_args(track_id, playlist="Nope"), db) == 1
    assert "Playlist not found: Nope" in capsys.readouterr().err


# --- AC-CLI3: full match explanation is rendered ---


def test_explain_renders_criteria_breakdown_and_tie_break(tmp_db: Path, capsys) -> None:
    db = Database(tmp_db)
    track_id = db.add_track(Track(title="Song", artist="Artist", album="Album"))
    db.save_match_audit(track_id, "tidal", MatchAudit(
        availability=Availability.EXACT_AVAILABLE, reason_code=ReasonCode.MATCHED,
        chosen_platform_id="atmos", chosen_score=96, decisive_signal="isrc",
        criteria=[
            CriterionOutcome("spatial", "prefer", "soft", "atmos", fired=True),
            CriterionOutcome("performance", "require", "hard", "studio", fired=False),
        ],
        signal_breakdown=[
            SignalContribution("duration", 0.12, 2.0),
            SignalContribution("title", 0.0, 3.0),
        ],
        tie_break="spatial",
    ))
    assert handle_explain(_args(track_id), db) == 0
    out = capsys.readouterr().out
    assert "criteria:" in out
    assert "[soft] spatial=atmos (prefer) - fired" in out
    assert "[hard] performance=studio (require) - in force, no effect" in out
    assert "weighted breakdown" in out
    assert "duration: 0.12 (weight 2)" in out
    assert "tie-break: resolved by 'spatial' (precedence)" in out


def test_explain_mono_preference_shows_soft_not_hard(tmp_db: Path, capsys) -> None:
    # AC-CLI3 gold: a mono/stereo preference is rendered as a SOFT criterion, not
    # a hard filter — the "mono demoted to soft" transparency. Driven end-to-end
    # through a real reconcile so the rendering reflects the live engine decision.
    db = Database(tmp_db)
    track_id = db.add_track(
        Track(title="God Only Knows", artist="The Beach Boys", album="Pet Sounds")
    )
    playlist_id = db.create_playlist("Mono Mixes")
    db.add_track_to_playlist(playlist_id, track_id, 0)
    db.set_preferences(playlist_id, {"prefer": ["mono"]})
    candidates = [
        TrackResult(platform_id="stereo", title="God Only Knows",
                    artist="The Beach Boys", album="Pet Sounds (Stereo Mix)"),
        TrackResult(platform_id="mono", title="God Only Knows",
                    artist="The Beach Boys", album="Pet Sounds (Mono)"),
    ]
    result = reconcile_track(db, track_id, _client(candidates), playlist_id=playlist_id)
    db.save_match_audit(track_id, "tidal", result.audit, playlist_id=playlist_id)
    assert result.platform_track_id == "mono"

    assert handle_explain(_args(track_id, playlist="Mono Mixes"), db) == 0
    out = capsys.readouterr().out
    assert "chosen: mono" in out
    # The mono/mix preference appears as a SOFT criterion (demoted, not a filter).
    assert "[soft]" in out
    assert "[hard]" not in out


# --- AC-CLI5: failed-match explanation lists per-candidate rejection reasons ---


def test_explain_failed_match_lists_rejection_reasons(tmp_db: Path, capsys) -> None:
    db = Database(tmp_db)
    track_id = db.add_track(Track(title="Song", artist="Artist", album="Album"))
    db.save_match_audit(track_id, "tidal", MatchAudit(
        availability=Availability.NOT_FOUND, reason_code=ReasonCode.VERSION_REJECTED,
        rejected=[
            RejectedCandidate("c1", "Song (Live)", "Artist", "Live Album", 40,
                              decisive_signal="version:reject",
                              rejection="below_threshold"),
            RejectedCandidate("c2", "Song", "Artist", "Atmos Album", 0,
                              rejection="hard_filter", rejection_detail="spatial=dolbyatmos"),
            RejectedCandidate("c3", "Song", "Artist", "Blocked Album", 0,
                              rejection="unavailable"),
        ],
    ))
    assert handle_explain(_args(track_id), db) == 0
    out = capsys.readouterr().out
    assert "rejected:" in out
    assert "Song (Live)" in out and "below match threshold" in out
    assert "failed hard filter (spatial=dolbyatmos)" in out
    assert "unavailable here" in out


def test_explain_failed_match_returns_1_but_prints(tmp_db: Path, capsys) -> None:
    # A genuine no-candidates miss still returns nonzero, but the explanation is
    # printed (the user asked "why didn't this match?").
    db = Database(tmp_db)
    track_id = db.add_track(Track(title="Song", artist="Artist", album="Album"))
    db.save_match_audit(track_id, "tidal", MatchAudit(
        availability=Availability.NOT_FOUND, reason_code=ReasonCode.NO_CANDIDATES))
    rc = handle_explain(_args(track_id), db)
    assert rc == 0  # a stored audit exists, so the explain succeeds
    assert "not found" in capsys.readouterr().out.lower()


# --- Deprecated alias ---


def test_why_alias_warns_and_delegates(tmp_db: Path, capsys) -> None:
    db = Database(tmp_db)
    track_id = db.add_track(Track(title="Song", artist="Artist", album="Album"))
    db.save_match_audit(track_id, "tidal", MatchAudit(
        availability=Availability.EXACT_AVAILABLE, reason_code=ReasonCode.MATCHED,
        chosen_platform_id="p1", chosen_score=90))
    rc = handle_why(_args(track_id), db)
    captured = capsys.readouterr()
    assert rc == 0
    assert "deprecated" in captured.err
    assert "p1" in captured.out
