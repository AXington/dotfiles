"""Narrative composition commands."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Any

from tuneshift.composer import compose_playlist
from tuneshift.composer.candidate_finder import find_candidates
from tuneshift.composer.models import ComposeResult, PlaylistConcept
from tuneshift.composer.parser import parse_enhanced_narrative
from tuneshift.db import Database
from tuneshift.sequencer.metadata import track_to_metadata

if TYPE_CHECKING:
    from tuneshift.models import Artist


def _get_concept_store(db: Database, playlist_id: int) -> tuple[str, dict | None]:
    preferences = db.get_preferences(playlist_id)
    if preferences is not None:
        return "preferences", preferences

    constraints = db.get_constraints(playlist_id)
    if constraints is not None:
        return "constraints", constraints

    return "preferences", {}


def _get_concept(db: Database, playlist_id: int) -> PlaylistConcept | None:
    store_name, store = _get_concept_store(db, playlist_id)
    del store_name
    concept_data = (store or {}).get("concept")
    if not concept_data:
        return None
    return PlaylistConcept(
        theme=concept_data.get("theme", ""),
        hard_rules=list(concept_data.get("hard_rules", [])),
        soft_rules=list(concept_data.get("soft_rules", [])),
        genres=list(concept_data.get("genres", [])),
        era=concept_data.get("era"),
    )


def _save_concept(db: Database, playlist_id: int, concept_data: dict | None) -> None:
    store_name, store = _get_concept_store(db, playlist_id)
    updated_store = dict(store or {})
    if concept_data is None:
        updated_store.pop("concept", None)
    else:
        updated_store["concept"] = concept_data

    value = updated_store or None
    if store_name == "preferences":
        db.set_preferences(playlist_id, value)
        return
    db.set_constraints(playlist_id, value)


def _render_analysis(result: ComposeResult) -> None:
    print("Assignments:")
    for section_name, tracks in result.assignments.assignments.items():
        print(f"  {section_name}:")
        if not tracks:
            print("    (none)")
            continue
        for track in tracks:
            print(f"    - {track.artist} - {track.title}")

    print("\nGaps:")
    if not result.gaps:
        print("  (none)")
    for gap in result.gaps:
        print(f"  - [{gap.gap_type}] {gap.section_name}: {gap.description}")
        candidates = result.candidates.get(gap.section_name, [])
        for candidate in candidates:
            print(
                f"      candidate: {candidate.artist} - {candidate.title} "
                f"({candidate.fitness_score:.2f})"
            )


def _render_composition(result: ComposeResult, sections: list) -> None:
    print("Composed order:")
    position = 1

    # Build section label lookup from the ordered tracks.
    # When pins move tracks across section boundaries, we label by
    # the section the track was assigned to (for context), but render
    # in the actual pin-corrected order.
    track_section: dict[int, str] = {}
    for section in sections:
        for track in result.assignments.assignments.get(section.name, []):
            track_section[track.track_id] = section.name

    current_section = None
    for track in result.ordered_tracks:
        section_name = track_section.get(track.track_id, "UNASSIGNED")
        if section_name != current_section:
            print(f"\n[{section_name}]")
            current_section = section_name
        print(f"  {position:2d}. {track.artist} - {track.title}")
        position += 1

    if result.assignments.unassigned:
        unassigned_ids = {t.track_id for t in result.assignments.unassigned}
        remaining = [t for t in result.ordered_tracks if t.track_id in unassigned_ids]
        if remaining:
            print("\n[UNASSIGNED]")
            for track in remaining:
                print(f"  {position:2d}. {track.artist} - {track.title}")
                position += 1

    print("\nReview findings:")
    if not result.review_findings:
        print("  (none)")
        return

    for finding in result.review_findings:
        section_prefix = f"{finding.section_name}: " if finding.section_name else ""
        print(
            f"  - [{finding.category}] {section_prefix}{finding.description} "
            f"(severity={finding.severity:.2f})"
        )


def _build_artist_lookup(db: Database, playlist_id: int) -> dict[str, Artist]:
    """Build a name->Artist lookup for all artists in a playlist."""
    artists = db.get_artists_for_playlist(playlist_id)
    return {a.name.casefold(): a for a in artists}


def handle_compose(args, db: Database) -> int:
    """Compose or analyze a playlist from its narrative."""
    playlist = db.find_playlist_by_name(args.playlist)
    if not playlist:
        print(f"Playlist not found: {args.playlist}", file=sys.stderr)
        return 1

    narrative = db.get_narrative(playlist.id)
    if not narrative:
        print(f'No narrative set for "{playlist.name}".', file=sys.stderr)
        return 1

    sections = parse_enhanced_narrative(narrative)
    if not sections:
        print(
            f'No sections could be parsed from "{playlist.name}" narrative.',
            file=sys.stderr,
        )
        return 1

    concept = _get_concept(db, playlist.id)
    tracks = [track_to_metadata(track) for track in db.get_playlist_tracks(playlist.id)]
    pins = db.get_pins(playlist.id)
    artist_lookup = _build_artist_lookup(db, playlist.id)
    from tuneshift.composer.concept_llm import make_concept_judge

    result = compose_playlist(
        tracks,
        narrative,
        concept=concept,
        pins=pins,
        artist_lookup=artist_lookup,
        year_lookup=db.get_release_years_for_playlist(playlist.id),
        llm_judge=make_concept_judge(),
        accepted=db.get_concept_acceptances(playlist.id),
    )

    if getattr(args, "fill_gaps", False):
        used_ids = {track.track_id for track in result.ordered_tracks}
        result.candidates = {
            gap.section_name: find_candidates(
                gap.fill_spec,
                db=db,
                concept=concept,
                exclude_ids=used_ids,
            )
            for gap in result.gaps
            if gap.fill_spec is not None
        }
        if not getattr(args, "analyze", False):
            print(
                "Note: --fill-gaps only suggests candidates, which are shown in "
                "analysis mode. Re-run with --analyze to see them.",
                file=sys.stderr,
            )

    if getattr(args, "analyze", False):
        _render_analysis(result)
        return 0

    _render_composition(result, sections)

    if getattr(args, "apply", False):
        db.set_playlist_tracks(
            playlist.id, [track.track_id for track in result.ordered_tracks]
        )
        print(f'\nApplied composed order to "{playlist.name}".')
    elif getattr(args, "dry_run", False):
        print("\nDry run only, no changes applied.")

    return 0


def handle_concept(args, db: Database) -> int:
    """Show, update, or clear the playlist concept."""
    playlist = db.find_playlist_by_name(args.playlist)
    if not playlist:
        print(f"Playlist not found: {args.playlist}", file=sys.stderr)
        return 1

    if getattr(args, "clear", False):
        _save_concept(db, playlist.id, None)
        print(f'Cleared concept for "{playlist.name}"')
        return 0

    concept = _get_concept(db, playlist.id)
    if not any(
        (
            getattr(args, "theme", None),
            getattr(args, "require", None),
            getattr(args, "prefer", None),
        )
    ):
        if concept is None:
            print(f'No concept set for "{playlist.name}".')
            return 0
        print(f'Concept for "{playlist.name}":')
        print(f"  Theme: {concept.theme}")
        print(f"  Hard rules: {concept.hard_rules}")
        print(f"  Soft rules: {concept.soft_rules}")
        if concept.genres:
            print(f"  Genres: {concept.genres}")
        if concept.era:
            print(f"  Era: {concept.era}")
        return 0

    concept_data = {
        "theme": concept.theme if concept is not None else "",
        "hard_rules": list(concept.hard_rules) if concept is not None else [],
        "soft_rules": list(concept.soft_rules) if concept is not None else [],
        "genres": list(concept.genres) if concept is not None else [],
        "era": concept.era if concept is not None else None,
    }
    if getattr(args, "theme", None):
        concept_data["theme"] = args.theme
    if (
        getattr(args, "require", None)
        and args.require not in concept_data["hard_rules"]
    ):
        concept_data["hard_rules"].append(args.require)
    if getattr(args, "prefer", None) and args.prefer not in concept_data["soft_rules"]:
        concept_data["soft_rules"].append(args.prefer)

    _save_concept(db, playlist.id, concept_data)
    print(f'Updated concept for "{playlist.name}"')
    return 0


def _accept_concept_findings(
    db, playlist, concept, track_id: int, rule: str | None
) -> int:
    """Record acceptance of concept findings for one track.

    With ``rule`` set, accepts exactly that rule for the track. Without it,
    re-runs the review and accepts every hard rule currently producing a
    finding for that track, so the user can silence a track in one command.
    """
    import re as _re

    from tuneshift.composer.reviewer import review_playlist

    playlist_tracks = db.get_playlist_tracks(playlist.id)
    target = next((t for t in playlist_tracks if t.id == track_id), None)
    if target is None:
        print(f'Track {track_id} is not in "{playlist.name}".', file=sys.stderr)
        return 1

    if rule is not None:
        db.add_concept_acceptance(playlist.id, track_id, rule)
        print(f'Accepted rule "{rule}" for track {track_id} ({target.title}).')
        return 0

    tracks = [track_to_metadata(t) for t in playlist_tracks]
    artist_lookup = _build_artist_lookup(db, playlist.id)
    year_lookup = db.get_release_years_for_playlist(playlist.id)
    from tuneshift.composer.concept_llm import make_concept_judge

    findings = review_playlist(
        tracks,
        concept=concept,
        artist_lookup=artist_lookup,
        year_lookup=year_lookup,
        llm_judge=make_concept_judge(),
        accepted=db.get_concept_acceptances(playlist.id),
    )
    needle = f'"{target.title}" by {target.artist} '
    accepted_rules: set[str] = set()
    for finding in findings:
        if needle in finding.description:
            match = _re.search(r'Rule: "(.+?)" - ', finding.description)
            if match:
                accepted_rules.add(match.group(1))

    if not accepted_rules:
        print(f"No current concept findings for track {track_id} ({target.title}).")
        return 0

    for accepted_rule in sorted(accepted_rules):
        db.add_concept_acceptance(playlist.id, track_id, accepted_rule)
    print(
        f"Accepted {len(accepted_rules)} rule(s) for track {track_id} "
        f"({target.title}): {', '.join(sorted(accepted_rules))}"
    )
    return 0


def _review_list_accepted(db: Database, playlist: Any) -> int:
    accepted_rows = db.list_concept_acceptances(playlist.id)
    if not accepted_rows:
        print(f'No accepted concept findings for "{playlist.name}".')
        return 0
    print(f'Accepted concept findings for "{playlist.name}":')
    for track_id, rule_text in accepted_rows:
        print(f'  - track {track_id}: "{rule_text}"')
    return 0


def _print_review_header(
    playlist: Any, concept: Any, tracks: list, llm_judge: Any
) -> None:
    from tuneshift.composer.rules import RuleKind, classify_rule

    print(f'Review: "{playlist.name}" ({len(tracks)} tracks)')
    print(f"Concept: {concept.theme}")
    print(f"Hard rules: {concept.hard_rules}")
    print(f"Soft rules: {concept.soft_rules}")

    has_thematic = any(
        classify_rule(rule) is RuleKind.THEMATIC for rule in concept.hard_rules
    )
    if has_thematic:
        label = getattr(llm_judge, "model_label", None)
        if label:
            print(
                f"Thematic rules judged by: {label} "
                f"(verdict quality is model-dependent)"
            )
        else:
            print("Thematic rules: no LLM backend reachable (reported as unverified)")
    print()


def _print_findings_section(title: str, items: list) -> None:
    if not items:
        return
    print(f"{title} ({len(items)}):")
    for finding in items:
        print(f"  - {finding.description}")
    print()


def _apply_review_fixes(db: Database, playlist: Any, tracks: list, hard: list) -> None:
    """Remove tracks that violate hard concept rules."""
    import re as _re

    removed: list[str] = []
    for finding in hard:
        # Format: 'HARD: "Title" by Artist - Rule: ...'
        title_match = _re.search(r'"([^"]+)" by (.+?) - Rule:', finding.description)
        if not title_match:
            continue
        title = title_match.group(1)
        artist_name = title_match.group(2)
        for track in tracks:
            if track.title == title and track.artist == artist_name:
                db.remove_track_from_playlist(playlist.id, track.track_id)
                removed.append(f"{title} by {artist_name}")
                break

    if removed:
        print(f"REMOVED ({len(removed)} tracks):")
        for entry in removed:
            print(f"  - {entry}")
    else:
        print("No tracks could be matched for removal.")


def handle_review(args, db: Database) -> int:
    """Review a playlist for concept compliance (works with or without narrative)."""
    from tuneshift.composer.concept_llm import make_concept_judge
    from tuneshift.composer.reviewer import review_playlist

    playlist = db.find_playlist_by_name(args.playlist)
    if not playlist:
        print(f"Playlist not found: {args.playlist}", file=sys.stderr)
        return 1

    concept = _get_concept(db, playlist.id)
    if concept is None:
        print(
            f'No concept set for "{playlist.name}". Nothing to review.', file=sys.stderr
        )
        return 1

    if getattr(args, "list_accepted", False):
        return _review_list_accepted(db, playlist)

    accept_track = getattr(args, "accept_track", None)
    if accept_track is not None:
        return _accept_concept_findings(
            db, playlist, concept, accept_track, getattr(args, "rule", None)
        )

    tracks = [track_to_metadata(track) for track in db.get_playlist_tracks(playlist.id)]
    artist_lookup = _build_artist_lookup(db, playlist.id)
    year_lookup = db.get_release_years_for_playlist(playlist.id)
    llm_judge = make_concept_judge()
    accepted = db.get_concept_acceptances(playlist.id)

    findings = review_playlist(
        tracks,
        concept=concept,
        artist_lookup=artist_lookup,
        year_lookup=year_lookup,
        llm_judge=llm_judge,
        accepted=accepted,
    )

    _print_review_header(playlist, concept, tracks, llm_judge)

    if not findings:
        print("No issues found.")
        return 0

    hard = [f for f in findings if f.severity >= 0.8]
    soft = [f for f in findings if 0.3 < f.severity < 0.8]
    unknown = [f for f in findings if f.severity <= 0.3]

    _print_findings_section("VIOLATIONS", hard)
    _print_findings_section("WARNINGS", soft)
    _print_findings_section("UNVERIFIED", unknown)

    if getattr(args, "fix", False) and hard:
        _apply_review_fixes(db, playlist, tracks, hard)

    return 0
