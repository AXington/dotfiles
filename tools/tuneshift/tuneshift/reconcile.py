"""Track reconciliation: match canonical tracks to platform-specific IDs."""

import json
import logging
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from tuneshift.db import Database
from tuneshift.matching import (
    Availability,
    CriterionOutcome,
    MatchAudit,
    ReasonCode,
    RejectedCandidate,
    SignalContribution,
    TrackFingerprint,
    build_fingerprint,
    classify_album_results,
    classify_artist_results,
    classify_scores,
    duration_proximity_bonus,
    edition_cost,
    fingerprint_equal,
    is_remaster,
    normalize_title,
    preference_sort_bias,
    resolve_preferences,
    score_album_match,
    score_artist_match,
    score_match_with_version,
    score_track_match,
    scoring_intent,
)
from tuneshift.matching.aliases import AliasResolver
from tuneshift.matching.criteria import (
    CriterionValue,
    Strength,
    Verdict,
    load_token_whitelist,
)
from tuneshift.matching.registry import (
    STRUCTURED_AXIS_FIELDS,
    PreferenceSpec,
    resolve_active_preferences,
    resolve_scoped_specs,
)
from tuneshift.matching.selection import (
    AMBIGUITY_DELTA,
    ActivePreference,
    IdentityLock,
    SelectionResult,
    select_version,
)
from tuneshift.models import (
    AlbumResult,
    ArtistResult,
    EffectiveLock,
    PlatformMapping,
    TrackResult,
    capture_candidate_metadata,
    trackresult_from_metadata,
)

logger = logging.getLogger(__name__)

# Operational/platform errors that mean "this strategy could not produce
# results" and should degrade to the next strategy. OSError covers
# ConnectionError, TimeoutError, and requests.RequestException (which subclasses
# IOError/OSError); RuntimeError covers not-logged-in and retry exhaustion;
# ValueError covers response parsing failures. Programming errors
# (AttributeError, TypeError, KeyError, IndexError, ...) are intentionally NOT
# caught so genuine bugs propagate instead of silently becoming not_found.
_PLATFORM_ERRORS = (RuntimeError, OSError, ValueError)


@dataclass
class ReconcileResult:
    """Result of reconciling a track against a platform."""

    platform_track_id: str = ""
    platform_title: str = ""
    platform_artist: str = ""
    platform_album: str = ""
    score: int = 0
    confidence: str = "not_found"
    is_divergent: bool = False
    divergence_note: str | None = None
    alternatives: list[TrackResult] = field(default_factory=list)
    from_cache: bool = False
    match_type: str = ""
    availability: str = "not_found"
    reason_code: str | None = None
    album_confidence: str | None = None
    artist_confidence: str | None = None
    audit: MatchAudit | None = None


# --- Availability + audit helpers (Chunk 5 explainability) ---

# Platforms whose "no result" cannot be trusted as "does not exist": their APIs
# cannot distinguish region/tier-blocked from genuinely absent (see the
# availability spike). A miss on these degrades to AMBIGUOUS, never NOT_FOUND.
_UNTRUSTED_ABSENCE = frozenset({"ytmusic"})


def _decisive_signal(
    track,
    candidate: TrackResult,
    prefer: frozenset[str],
    avoid: frozenset[str],
    resolver: AliasResolver | None = None,
) -> str | None:
    """Name the signal that most drove a candidate's distance (worst-first).

    Reuses the engine-native scorer so the reason shown to a human matches the
    real scoring, e.g. ``version:reject`` for a wrong recording or ``duration``
    for a suspicious length.
    """
    distance = score_track_match(
        track,
        candidate,
        prefer=prefer,
        avoid=avoid,
        alias_resolver=resolver,
    )
    rows = distance.breakdown
    return rows[0].name if rows else None


def _candidate_blocked(result: TrackResult) -> str | None:
    """Return a blocked reason code if the candidate is known-but-unplayable.

    Uses the availability signal we now retain on ``TrackResult`` (``available``
    from Spotify ``is_playable``/Tidal ``allowStreaming``; ``tier_restricted``
    for premium-only). ``None`` means "available or unknown", never a guess.
    """
    if getattr(result, "tier_restricted", False):
        return ReasonCode.TIER_RESTRICTED
    available = getattr(result, "available", None)
    if available is False:
        return ReasonCode.BLOCKED_IN_MARKET
    return None


def _typed_active_prefs(
    prefs,
    resolver: AliasResolver | None = None,  # noqa: ARG001 - optional param accepted for caller compatibility
) -> list[ActivePreference]:
    """Bridge free-text audio-format preferences onto the engine's typed criteria.

    A stored ``prefer atmos`` / ``prefer hi-res`` (whatever surface form) becomes
    a real :class:`~tuneshift.matching.selection.ActivePreference` on the
    ``spatial`` / ``mix`` / ``fidelity`` axes, so the two-phase engine actually
    selects the Atmos (or hi-res) release from a playlist's preferences (AC-S5),
    rather than the axis being dead config.

    Only the STRUCTURED audio axes are bridged here. Recording-class, lyric and
    edition tokens (live/remix/clean/deluxe/...) are already applied through
    ``prefer_classes``/``avoid_classes`` in the base scorer; forwarding them again
    as typed criteria would double-count them.
    """
    if prefs.is_default():
        return []
    whitelist = load_token_whitelist()
    specs: list[PreferenceSpec] = []
    for token in prefs.prefer:
        if whitelist.axis(token) in STRUCTURED_AXIS_FIELDS:
            specs.append(
                PreferenceSpec(
                    axis=whitelist.axis(token), target=token, strength=Strength.PREFER
                )
            )
    for token in prefs.avoid:
        if whitelist.axis(token) in STRUCTURED_AXIS_FIELDS:
            specs.append(
                PreferenceSpec(
                    axis=whitelist.axis(token), target=token, strength=Strength.AVOID
                )
            )
    if not specs:
        return []
    return resolve_active_preferences(specs, whitelist=whitelist)


def _scoped_active_prefs(
    db: Database,
    track_id: int,
    playlist_id: int | None,
    prefs,
) -> list[ActivePreference]:
    """All active typed preferences for this track+playlist, across every scope.

    Reads the general ``(criterion, strength, target)`` model (AC-CLI1) stored at
    three scopes, global (``schema_meta`` ``criteria``), playlist
    (``playlists.preferences`` ``criteria``) and per-track
    (``playlist_track_prefs``: playlist-specific rows plus playlist-agnostic
    NULL-playlist rows), cascades them most-specific-wins via
    :func:`resolve_scoped_specs`, and turns them into engine-ready
    :class:`ActivePreference` objects the two-phase engine fires on.

    The legacy free-text ``prefer``/``avoid`` audio-format bridge is merged in for
    backward compatibility, but only for an ``(axis, target)`` a typed criterion
    has not already claimed (the typed model is authoritative). Returns an empty
    list when nothing is configured, preserving byte-parity (AC-C5): with no
    ``criteria`` rows and default ``prefs`` the engine sees no active preference
    and cannot perturb the ranking.
    """
    global_criteria = (db.get_global_preferences() or {}).get("criteria")
    playlist_criteria = None
    playlist_track_rows: list[dict] = []
    if playlist_id is not None:
        playlist_criteria = (db.get_preferences(playlist_id) or {}).get("criteria")
        playlist_track_rows = db.get_playlist_track_prefs(playlist_id, track_id)

    # Playlist-agnostic per-track preferences (NULL playlist) are read
    # unconditionally - they apply to the track on every playlist and are the
    # folded successor to the retired ``tracks.preferences`` blob (FL3 #4). They
    # live in the most-specific "track" layer alongside the playlist-track rows;
    # a playlist-specific row for the same ``(axis, canonical target)`` wins
    # because it is appended later (``resolve_scoped_specs`` keeps the last row
    # per key within a layer).
    track_global_rows = db.get_track_global_prefs(track_id)
    merged_track_rows = [*track_global_rows, *playlist_track_rows]

    specs = resolve_scoped_specs(global_criteria, playlist_criteria, merged_track_rows)
    active = resolve_active_preferences(specs)

    typed_pairs = {(ap.ref.criterion, ap.ref.target) for ap in active}
    for ap in _typed_active_prefs(prefs):
        key = (ap.ref.criterion, ap.ref.target)
        if key not in typed_pairs:
            active.append(ap)
            typed_pairs.add(key)
    return active


def _legacy_intent(prefs, active: list[ActivePreference]):
    """Free-text prefer/avoid intent, minus tokens a typed criterion already owns.

    The legacy keyword lists feed the source-aware recording-class / lyric /
    edition scoring (:func:`scoring_intent`). A token that is ALSO expressed as a
    typed criterion (e.g. ``performance avoid live`` and a legacy ``avoid: live``)
    would otherwise be counted twice, once as a typed soft signal and once as a
    recording-class penalty. Drop such tokens here so the typed criterion is the
    single source of truth. Returns empty sets for default prefs (byte-parity).
    """
    if prefs.is_default():
        return frozenset(), frozenset()
    prefer_classes, avoid_classes = scoring_intent(prefs.prefer, prefs.avoid)
    if not active:
        return prefer_classes, avoid_classes
    whitelist = load_token_whitelist()
    covered = {ap.ref.target for ap in active}
    prefer_classes = frozenset(
        t for t in prefer_classes if whitelist.canonical(t) not in covered
    )
    avoid_classes = frozenset(
        t for t in avoid_classes if whitelist.canonical(t) not in covered
    )
    return prefer_classes, avoid_classes


def _criteria_outcomes(
    active: list[ActivePreference] | None,
    selection: SelectionResult | None,
) -> list[CriterionOutcome]:
    """Record the active user-preference criteria and how each acted (AC-CLI3).

    A hard (require/forbid) criterion ``fired`` when it eliminated >=1 candidate
    in Phase 1; a soft (prefer/avoid) criterion ``fired`` when it broke the
    winning within-delta tie by precedence. A hard criterion that eliminated
    nothing is recorded ``fired=False``, in force, yet inert (the transparency
    behind "mono demoted to soft").
    """
    if not active:
        return []
    filtered_hard: set[str] = set()
    if selection is not None:
        for fc in selection.filtered:
            reason = fc.reason or ""
            if reason.startswith("hard:"):
                filtered_hard.add(reason[len("hard:") :])
    tie = selection.decided_by if selection is not None else None
    outcomes: list[CriterionOutcome] = []
    for ap in active:
        ref = ap.ref
        is_hard = ref.strength.is_hard
        if is_hard:
            fired = f"{ref.criterion}={ref.target}" in filtered_hard
        else:
            fired = tie == ref.criterion
        outcomes.append(
            CriterionOutcome(
                criterion=ref.criterion,
                strength=ref.strength.value,
                kind="hard" if is_hard else "soft",
                target=ref.target,
                fired=fired,
            )
        )
    return outcomes


def _signal_contributions(
    selection: SelectionResult | None,
) -> list[SignalContribution]:
    """The winner's weighted per-signal distance breakdown (AC-CLI3)."""
    if selection is None or selection.winner_distance is None:
        return []
    return [
        SignalContribution(
            name=sb.name, contribution=round(sb.contribution, 4), weight=sb.weight
        )
        for sb in selection.winner_distance.breakdown
    ]


def _filtered_rejections(
    selection: SelectionResult | None, already: set[str]
) -> list[RejectedCandidate]:
    """Phase-1 eliminations rendered as rejected candidates (AC-CLI5).

    These never appear in the scored list, a candidate that failed a hard filter
    or is unavailable was dropped before scoring, so the failed-match explain
    surface would otherwise be silent about *why* they were rejected.
    """
    if selection is None:
        return []
    out: list[RejectedCandidate] = []
    for fc in selection.filtered:
        cand = fc.candidate
        pid = getattr(cand, "platform_id", None)
        if pid is None or pid in already:
            continue
        already.add(pid)
        reason = fc.reason or ""
        if reason.startswith("hard:"):
            rejection, detail = "hard_filter", reason[len("hard:") :]
        else:
            rejection, detail = "unavailable", None
        out.append(
            RejectedCandidate(
                platform_id=pid,
                title=getattr(cand, "title", "") or "",
                artist=getattr(cand, "artist", "") or "",
                album=getattr(cand, "album", "") or "",
                score=0,
                decisive_signal=reason or None,
                rejection=rejection,
                rejection_detail=detail,
            )
        )
    return out


def _build_audit(
    *,
    track,
    platform_name: str,
    scored: list[tuple[int, int, TrackResult]],
    confidence: str,
    prefer: frozenset[str],
    avoid: frozenset[str],
    resolver: AliasResolver | None = None,
    selection: SelectionResult | None = None,
    active: list[ActivePreference] | None = None,
) -> MatchAudit:
    """Construct the explainable audit + availability verdict for a reconcile.

    ``scored`` is the fully ranked ``(score, edition_penalty, result)`` list
    (best first). Empty ``scored`` means no candidates were produced at all.

    When ``selection``/``active`` are supplied (the live reconcile path) the audit
    is enriched with the criteria that fired (hard vs soft), the winner's weighted
    signal breakdown, the precedence tie-break, and the Phase-1 eliminations with
    per-candidate rejection reasons, the AC-CLI3 / AC-CLI5 explain data. Callers
    that omit them (direct unit tests) get the unenriched availability verdict.
    """
    untrusted = platform_name in _UNTRUSTED_ABSENCE

    criteria = _criteria_outcomes(active, selection)
    signal_breakdown = _signal_contributions(selection)
    tie_break = selection.decided_by if selection is not None else None

    if not scored:
        availability = Availability.AMBIGUOUS if untrusted else Availability.NOT_FOUND
        reason = (
            ReasonCode.PLATFORM_CANNOT_DISTINGUISH
            if untrusted
            else ReasonCode.NO_CANDIDATES
        )
        return MatchAudit(
            availability=availability,
            reason_code=reason,
            criteria=criteria,
            rejected=_filtered_rejections(selection, set()),
            tie_break=tie_break,
        )

    best_score, _, best = scored[0]
    seen_ids: set[str] = {best.platform_id}
    version_lost = confidence == "not_found"
    rejected = [
        RejectedCandidate(
            platform_id=r.platform_id,
            title=r.title,
            artist=r.artist,
            album=r.album,
            score=s,
            decisive_signal=_decisive_signal(track, r, prefer, avoid, resolver),
            rejection="below_threshold" if version_lost else "lost",
        )
        for s, _, r in scored[1:4]
    ]
    seen_ids.update(r.platform_id for r in rejected)
    # Phase-1 eliminations (hard-filter failures / unavailable) never reach the
    # scored list; append them so the failed-match explain is complete (AC-CLI5).
    rejected.extend(_filtered_rejections(selection, seen_ids))
    distance = round((100 - best_score) / 100, 4)
    best_signal = _decisive_signal(track, best, prefer, avoid, resolver)

    # Known-but-blocked takes precedence: the exact recording exists, just not
    # playable here. Surface it as held, never as a silent miss.
    blocked_reason = _candidate_blocked(best)
    if blocked_reason is not None:
        return MatchAudit(
            availability=Availability.EXACT_UNAVAILABLE,
            reason_code=blocked_reason,
            chosen_platform_id=best.platform_id,
            chosen_score=best_score,
            decisive_signal=best_signal,
            distance=distance,
            rejected=rejected,
            criteria=criteria,
            signal_breakdown=signal_breakdown,
            tie_break=tie_break,
        )

    if confidence == "not_found":
        # Candidates existed but none cleared the bar. Distinguish a
        # version-class rejection (wrong recording) from a plain low score.
        version_rejected = best_signal is not None and best_signal.startswith(
            "version:"
        )
        if untrusted:
            availability, reason = (
                Availability.AMBIGUOUS,
                ReasonCode.PLATFORM_CANNOT_DISTINGUISH,
            )
        elif version_rejected:
            availability, reason = Availability.NOT_FOUND, ReasonCode.VERSION_REJECTED
        else:
            availability, reason = (
                Availability.NOT_FOUND,
                ReasonCode.ALL_BELOW_THRESHOLD,
            )
        return MatchAudit(
            availability=availability,
            reason_code=reason,
            chosen_score=best_score,
            decisive_signal=best_signal,
            distance=distance,
            rejected=rejected,
            criteria=criteria,
            signal_breakdown=signal_breakdown,
            tie_break=tie_break,
        )

    if confidence == "ambiguous":
        return MatchAudit(
            availability=Availability.AMBIGUOUS,
            reason_code=ReasonCode.AMBIGUOUS_TOP,
            chosen_platform_id=best.platform_id,
            chosen_score=best_score,
            decisive_signal=best_signal,
            distance=distance,
            rejected=rejected,
            criteria=criteria,
            signal_breakdown=signal_breakdown,
            tie_break=tie_break,
        )

    # confidence == "high": clear pick. Distinguish an exact recording from an
    # accepted *substitute* version (e.g. the only available copy is a different
    # but acceptable master) so callers can tell "we got the thing you asked
    # for" from "we got a stand-in". Metadata only - the chosen match is
    # identical either way.
    is_substitute = best_signal == "version:substitute"
    return MatchAudit(
        availability=(
            Availability.SUBSTITUTE_AVAILABLE
            if is_substitute
            else Availability.EXACT_AVAILABLE
        ),
        reason_code=ReasonCode.SUBSTITUTED if is_substitute else ReasonCode.MATCHED,
        chosen_platform_id=best.platform_id,
        chosen_score=best_score,
        decisive_signal=best_signal,
        distance=distance,
        rejected=rejected,
        criteria=criteria,
        signal_breakdown=signal_breakdown,
        tie_break=tie_break,
    )


# --- Album/artist selection helpers (shared scorers, no blind [0]) ---


def _rank_albums(track, albums: list[AlbumResult]) -> list[tuple[float, AlbumResult]]:
    """Rank candidate albums by match distance (best/smallest first)."""
    scored = [
        (
            score_album_match(
                track.album,
                track.artist,
                album,
                source_track_count=None,
                source_year=None,
            ).total,
            album,
        )
        for album in albums
    ]
    scored.sort(key=lambda pair: pair[0])
    return scored


def _acceptable_albums(track, albums: list[AlbumResult]) -> list[AlbumResult]:
    """Return candidate albums whose best match is not classified not_found.

    Preserves ranking order and drops the trailing candidates only once the
    classifier rejects the whole pool (empty -> []).
    """
    ranked = _rank_albums(track, albums)
    if not ranked:
        return []
    if classify_album_results([d for d, _ in ranked]) == "not_found":
        return []
    return [album for _, album in ranked]


def _best_artist(track, artists: list[ArtistResult]) -> ArtistResult | None:
    """Pick the best-matching artist, or None if none is acceptable."""
    if not artists:
        return None
    scored = sorted(
        ((score_artist_match(track.artist, a).total, a) for a in artists),
        key=lambda pair: pair[0],
    )
    if classify_artist_results([d for d, _ in scored]) == "not_found":
        return None
    return scored[0][1]


# --- Strategy functions ---


def _strategy_album_lookup(track, client) -> list[TrackResult]:
    """Search for the album, get tracklists of the best-matching candidates."""
    if not track.album:
        return []
    try:
        query = f"{track.album} {track.artist}"
        albums: list[AlbumResult] = client.search_album(query, limit=5)
        results: list[TrackResult] = []
        for album in _acceptable_albums(track, albums)[:3]:
            tracklist = client.get_album_tracks(album.platform_id)
            results.extend(tracklist)
        return results
    except _PLATFORM_ERRORS as exc:
        logger.warning("album_lookup strategy failed: %s", exc)
        return []


def _strategy_album_tracklist(track, client) -> list[TrackResult]:
    """Search for the album, then fetch the best candidate's tracklist."""
    if not track.album:
        return []
    try:
        query = f"{track.album} {track.artist}"
        albums: list[AlbumResult] = client.search_album(query, limit=5)
        if not albums:
            # Fallback: album name only
            albums = client.search_album(track.album, limit=5)

        acceptable = _acceptable_albums(track, albums)
        if not acceptable:
            return []

        best_album = acceptable[0]
        return client.get_album_tracks(best_album.platform_id)
    except _PLATFORM_ERRORS as exc:
        logger.warning("album_tracklist strategy failed: %s", exc)
        return []


def _strategy_isrc(track, client) -> list[TrackResult]:
    """Direct ISRC lookup."""
    if not track.isrc:
        return []
    try:
        result = client.search_isrc(track.isrc)
        return [result] if result else []
    except _PLATFORM_ERRORS as exc:
        logger.warning("isrc strategy failed: %s", exc)
        return []


def _strategy_title_artist(track, client) -> list[TrackResult]:
    """Standard title + artist text search."""
    try:
        return client.search_track(f"{track.title} {track.artist}", limit=10)
    except _PLATFORM_ERRORS as exc:
        logger.warning("title_artist strategy failed: %s", exc)
        return []


def _strategy_title_only(track, client) -> list[TrackResult]:
    """Broader title-only search."""
    try:
        return client.search_track(track.title, limit=10)
    except _PLATFORM_ERRORS as exc:
        logger.warning("title_only strategy failed: %s", exc)
        return []


def _strategy_album_in_query(track, client) -> list[TrackResult]:
    """Search with title + album name."""
    if not track.album:
        return []
    try:
        return client.search_track(f"{track.title} {track.album}", limit=10)
    except _PLATFORM_ERRORS as exc:
        logger.warning("album_in_query strategy failed: %s", exc)
        return []


def _strategy_artist_browse(track, client) -> list[TrackResult]:
    """Browse the best-matching artist's discography for the right album."""
    if not track.album:
        return []
    try:
        artists: list[ArtistResult] = client.search_artist(track.artist, limit=3)
        artist = _best_artist(track, artists)
        if artist is None:
            return []
        albums: list[AlbumResult] = client.get_artist_albums(
            artist.platform_id, limit=20
        )
        acceptable = _acceptable_albums(track, albums)
        if not acceptable:
            return []
        return client.get_album_tracks(acceptable[0].platform_id)
    except _PLATFORM_ERRORS as exc:
        logger.warning("artist_browse strategy failed: %s", exc)
        return []


# Strategy execution order with short-circuit thresholds
_STRATEGIES = [
    (_strategy_isrc, 100),
    (_strategy_title_artist, 90),
    (_strategy_album_tracklist, None),
    (_strategy_album_lookup, 90),
    (_strategy_title_only, None),
    (_strategy_album_in_query, None),
    (_strategy_artist_browse, None),
]


def build_alias_resolver(db: Database) -> AliasResolver:
    """Build the seed + DB artist-alias resolver for a reconcile run.

    Reads the user-curated alias classes from the database and merges them with
    the static seed. Called once per reconcile so a run sees a consistent alias
    view; artists in no class map to themselves (zero behavioural change).
    """
    return AliasResolver(db_classes=db.get_artist_alias_classes())


def _alias_expanded_candidates(
    track, client, resolver: AliasResolver
) -> list[TrackResult]:
    """Retrieve candidates under each equivalent artist surface form.

    Platform search indexes a specific spelling, so an artist published as
    ``98\u00ba`` on one platform never surfaces when we query ``98 Degrees``.
    For an artist that belongs to an alias class this issues one extra
    ``search_track`` per *raw* variant spelling (the exact strings the platform
    may have indexed) and returns the merged candidates. Gated on the artist
    having variants, so a non-aliased artist costs zero extra API calls.
    """
    variants = resolver.variants_for_query(track.artist)
    if not variants:
        return []
    results: list[TrackResult] = []
    for variant in variants:
        try:
            results.extend(client.search_track(f"{track.title} {variant}", limit=10))
        except _PLATFORM_ERRORS as exc:
            logger.warning("alias_expansion strategy failed for %r: %s", variant, exc)
    return results


def gather_candidates(
    track,
    client,
    resolver: AliasResolver,
    *,
    prefer_classes: frozenset[str] | set[str] | None = None,
    avoid_classes: frozenset[str] | set[str] | None = None,
) -> tuple[list[TrackResult], dict[str, str]]:
    """Run the multi-strategy platform search and return deduped candidates.

    This is the single candidate-discovery pass shared by ``reconcile_track``
    (which then scores + selects a winner) and the library-first resolver
    (which persists the top-N for later scoring). Keeping one implementation
    guarantees selection and resolution see the *same* candidate set, a
    prerequisite for AC-X3 (selection reads persisted candidates, not a
    divergent live search) and plan reproducibility (AC-P4).

    Returns ``(candidates, strategy_by_platform_id)`` in discovery order.
    """
    prefer_classes = prefer_classes or frozenset()
    avoid_classes = avoid_classes or frozenset()

    all_candidates: list[TrackResult] = []
    candidate_strategies: dict[str, str] = {}  # platform_id -> strategy_name
    seen_ids: set[str] = set()

    for strategy_fn, threshold in _STRATEGIES:
        new_candidates = strategy_fn(track, client)
        strategy_name = strategy_fn.__name__.replace("_strategy_", "")
        for c in new_candidates:
            if c.platform_id not in seen_ids:
                seen_ids.add(c.platform_id)
                all_candidates.append(c)
                candidate_strategies[c.platform_id] = strategy_name

        # Short-circuit: only on ISRC match (score 100). Never short-circuit
        # on text matches because a later strategy might find a better version.
        if threshold is not None and threshold >= 100 and all_candidates:
            top_score = _quick_top_score(
                track,
                all_candidates,
                prefer=prefer_classes,
                avoid=avoid_classes,
                resolver=resolver,
            )
            if top_score >= threshold:
                break

    # Alias-expanded retrieval: for an artist in an alias class, query each
    # equivalent surface spelling so a track indexed under a variant (e.g. the
    # platform lists "98\u00ba" while the source says "98 Degrees") still
    # surfaces. Runs once, after the cascade, and is a no-op for non-aliased
    # artists (zero extra API calls).
    for c in _alias_expanded_candidates(track, client, resolver):
        if c.platform_id not in seen_ids:
            seen_ids.add(c.platform_id)
            all_candidates.append(c)
            candidate_strategies[c.platform_id] = "alias_expansion"

    return all_candidates, candidate_strategies


def _load_persisted_candidates(
    db: Database, track_id: int, platform: str
) -> list[TrackResult]:
    """Rebuild the persisted candidate set for ``(track, platform)``.

    Returned in discovery order (``track_candidates.discovery_rank``) so
    selection's default band-tie behaviour is identical to a live gather, the
    parity guarantee behind AC-P4.
    """
    rows = db.get_track_candidates(track_id, platform=platform)
    return [
        trackresult_from_metadata(row["platform_track_id"], row["captured_metadata"])
        for row in rows
    ]


def _persist_candidates(
    db: Database, track_id: int, platform: str, candidates: list[TrackResult]
) -> None:
    """Replace the persisted candidate set with ``candidates`` in discovery order.

    Clears first so a refresh REPLACES the prior set (no stale rows / ranks).
    """
    db.clear_track_candidates(track_id, platform)
    for rank, candidate in enumerate(candidates):
        db.upsert_track_candidate(
            track_id,
            platform,
            candidate.platform_id,
            capture_candidate_metadata(candidate),
            discovery_rank=rank,
        )


def acquire_candidates(
    db: Database,
    track,
    client,
    resolver: AliasResolver,
    *,
    prefer_classes: frozenset[str] | set[str] | None = None,
    avoid_classes: frozenset[str] | set[str] | None = None,
    force: bool = False,
) -> tuple[list[TrackResult], dict[str, str]]:
    """Obtain the candidate set for selection, reading the cache when possible.

    Implements spec section 4.1a: for STEADY-STATE selection the live candidate gather
    is retired in favour of the persisted ``track_candidates`` set (AC-X3, no
    resolution API calls during interactive matching). A live gather runs only
    when there is no persisted set (cold cache) or when the caller explicitly
    requests a refresh (``force=True``, e.g. ``explain``/self-heal), and the
    fresh set is persisted so the next run is reproducible with no live search
    (AC-P4).

    Returns ``(candidates, strategy_by_platform_id)`` in discovery order, matching
    :func:`gather_candidates`. On a cache hit the strategy is reported as
    ``"cached"`` for every candidate (the original per-strategy attribution is not
    persisted); on a live gather the real per-strategy names are returned.
    """
    platform = client.platform_name
    if not force:
        cached = _load_persisted_candidates(db, track.id, platform)
        if cached:
            return cached, dict.fromkeys((c.platform_id for c in cached), "cached")

    candidates, strategies = gather_candidates(
        track,
        client,
        resolver,
        prefer_classes=prefer_classes,
        avoid_classes=avoid_classes,
    )
    _persist_candidates(db, track.id, platform, candidates)
    return candidates, strategies


def _fingerprint_for_track(track, mapping: PlatformMapping) -> TrackFingerprint:
    """Resolve the target fingerprint for a locked mapping.

    Prefer the fingerprint captured when the lock was approved; fall back to
    building one from the canonical track for locks made before fingerprints
    were stored (schema < v13).
    """
    if mapping.fingerprint:
        try:
            return TrackFingerprint.from_dict(json.loads(mapping.fingerprint))
        except (ValueError, TypeError, KeyError):
            logger.warning(
                "corrupt stored fingerprint for track %s; rebuilding", mapping.track_id
            )
    return build_fingerprint(
        title=track.title,
        artist=track.artist,
        album=track.album,
        isrc=track.isrc,
        duration_seconds=track.duration_seconds,
    )


def _locked_id_alive(client, platform_track_id: str) -> bool | None:
    """Return True/False if the locked id is alive, or None if undeterminable.

    Liveness is checked via the platform's ``get_track``; clients that don't
    expose one (e.g. Spotify) return None, meaning "cannot verify", the caller
    then trusts the lock as-is rather than guessing.
    """
    get_track = getattr(client, "get_track", None)
    if not callable(get_track):
        return None
    try:
        result = get_track(platform_track_id)
    except _PLATFORM_ERRORS as exc:
        logger.warning("lock liveness check failed for %s: %s", platform_track_id, exc)
        return None
    if result is None:
        return False
    if result.available is False:
        return False
    return True


def _find_equivalent_candidate(
    track, client, target_fp: TrackFingerprint
) -> TrackResult | None:
    """Search the platform for a live candidate that is the SAME recording.

    Runs the full strategy cascade and returns the first candidate whose
    fingerprint equals ``target_fp`` (ISRC match, or normalized title/artist +
    version class + duration bucket) and that is not explicitly blocked. Returns
    None when no equivalent recording is available, the lock is then held, never
    swapped to a different recording.
    """
    seen: set[str] = set()
    for strategy_fn, _ in _STRATEGIES:
        for cand in strategy_fn(track, client):
            if cand.platform_id in seen:
                continue
            seen.add(cand.platform_id)
            if cand.available is False:
                continue
            cand_fp = build_fingerprint(
                title=cand.title,
                artist=cand.artist,
                album=cand.album,
                isrc=cand.isrc,
                duration_seconds=cand.duration_seconds,
            )
            if fingerprint_equal(target_fp, cand_fp):
                return cand
    return None


def _locked_available_result(
    mapping: PlatformMapping, reason_code: str
) -> ReconcileResult:
    audit = MatchAudit(
        availability=Availability.EXACT_AVAILABLE,
        reason_code=reason_code,
        chosen_platform_id=mapping.platform_track_id,
        chosen_score=mapping.match_score or 100,
        locked=True,
    )
    return ReconcileResult(
        platform_track_id=mapping.platform_track_id,
        score=mapping.match_score or 100,
        confidence="high",
        is_divergent=mapping.is_divergent,
        divergence_note=mapping.divergence_note,
        from_cache=True,
        availability=audit.availability,
        reason_code=audit.reason_code,
        audit=audit,
    )


def _locked_unavailable_result(reason_code: str) -> ReconcileResult:
    audit = MatchAudit(
        availability=Availability.EXACT_UNAVAILABLE,
        reason_code=reason_code,
        locked=True,
    )
    return ReconcileResult(
        confidence="not_found",
        from_cache=True,
        availability=audit.availability,
        reason_code=audit.reason_code,
        audit=audit,
    )


def _live_locked_track(client, platform_track_id: str) -> TrackResult | None:
    """Fetch the CURRENT metadata for a locked id, or None if gone/undeterminable.

    Used by the AC-L5 downgrade check: unlike :func:`_locked_id_alive` it returns
    the full live :class:`TrackResult` so its structured fields (``audio_modes``
    ...) can be re-evaluated against active preferences. A missing/blocked id or
    a client without ``get_track`` yields ``None``, disappearance is AC-L3's
    concern, not L5's, so the downgrade check simply stands down.
    """
    get_track = getattr(client, "get_track", None)
    if not callable(get_track):
        return None
    try:
        result = get_track(platform_track_id)
    except _PLATFORM_ERRORS as exc:
        logger.warning("lock downgrade check failed for %s: %s", platform_track_id, exc)
        return None
    if result is None or result.available is False:
        return None
    return result


@dataclass(frozen=True)
class LockDowngrade:
    """An active preference the STILL-LIVE locked release no longer satisfies (AC-L5)."""  # noqa: E501

    axis: str
    target: str
    strength: str

    def describe(self) -> str:
        return (
            f"locked track no longer satisfies preference "
            f"{self.axis}={self.target} ({self.strength})"
        )


def check_lock_downgrade(
    db: Database,
    track_id: int,
    client,
    *,
    platform: str,  # noqa: ARG001 - keyword-only arg reserved; passed by callers
    playlist_id: int | None,
    locked_id: str,
) -> list[LockDowngrade]:
    """Report active preferences the still-live locked release fails (AC-L5).

    Distinct from AC-L3 (disappearance): the locked id still exists, but its
    current metadata may have degraded (e.g. Tidal dropped the Atmos mode) so it
    no longer satisfies an active ``prefer``/``require`` on a structured audio
    axis. The lock is NEVER changed here, the caller surfaces the returned
    downgrades in a plan for the user to decide (re-pin or accept). Pure query:
    no writes.

    Returns an empty list when the lock is gone/undeterminable (AC-L3, not L5),
    when no structured audio preferences are set (byte-parity for default prefs),
    or when the live release still satisfies every such preference.
    """
    prefs = resolve_preferences(
        db.get_global_preferences(),
        db.get_preferences(playlist_id) if playlist_id is not None else None,
        None,
    )
    active = _scoped_active_prefs(db, track_id, playlist_id, prefs)
    if not active:
        return []
    live = _live_locked_track(client, locked_id)
    if live is None:
        return []
    downgrades: list[LockDowngrade] = []
    for pref in active:
        # An absent field can't satisfy a prefer/require, so treat it as an empty
        # (structured) value - the criterion's verdict routing then flags it.
        value = pref.criterion.extract(live) or CriterionValue(
            raw=None, tokens=frozenset(), structured=True
        )
        verdict = pref.criterion.compare(value, value, pref.ref.strength)
        if verdict in (Verdict.SOFT_PENALTY, Verdict.HARD_REJECT):
            downgrades.append(
                LockDowngrade(
                    pref.ref.criterion, pref.ref.target, pref.ref.strength.value
                )
            )
    return downgrades


def _mapping_from_effective(
    track_id: int, platform: str, eff: EffectiveLock
) -> PlatformMapping:
    """Synthesize a :class:`PlatformMapping` view of an effective lock so the
    shared locked-result / self-heal helpers can consume either scope uniformly."""
    return PlatformMapping(
        track_id=track_id,
        platform=platform,
        platform_track_id=eff.platform_track_id,
        match_score=eff.match_score,
        is_divergent=eff.is_divergent,
        divergence_note=eff.divergence_note,
        status=eff.status,
        user_approved=True,
        fingerprint=eff.fingerprint,
    )


def _identity_lock_from_effective(eff: EffectiveLock, track) -> IdentityLock:
    """Build the engine-level composite lock (platform-id + ISRC + fingerprint)
    from an effective lock so forced/verify selection paths honour it (AC-L1/L2)."""
    fingerprint: TrackFingerprint | None = None
    if eff.fingerprint:
        try:
            fingerprint = TrackFingerprint.from_dict(json.loads(eff.fingerprint))
        except (ValueError, TypeError, KeyError):
            logger.warning(
                "corrupt effective-lock fingerprint; ignoring the fingerprint axis"
            )
    return IdentityLock(
        platform_id=eff.platform_track_id,
        isrc=eff.isrc or (track.isrc if track is not None else None),
        fingerprint=fingerprint,
    )


def _verify_lock(
    db: Database,  # noqa: ARG001 - uniform _verify_lock signature
    track,  # noqa: ARG001 - uniform _verify_lock signature
    client,
    mapping: PlatformMapping,
) -> ReconcileResult:
    """Verify a user lock's liveness WITHOUT mutating anything (AC-L3, section 7.1).

    - Locked id alive          -> keep it (LOCKED, available).
    - Liveness undeterminable  -> trust the lock as-is (per stored status).
    - Locked id dead           -> report HELD (EXACT_UNAVAILABLE / LOCK_HELD),
                                  surfaced for review. The actual self-heal
                                  (re-bind to an equivalent recording) is a
                                  ROUTED operation proposed by
                                  ``planapply.heal.build_heal_plan``, never a
                                  silent inline swap.
    """
    alive = _locked_id_alive(client, mapping.platform_track_id)

    if alive is True:
        return _locked_available_result(mapping, ReasonCode.LOCKED)

    if alive is None:
        # Cannot verify (client has no liveness probe) - honour stored status.
        if mapping.status == "unavailable":
            return _locked_unavailable_result(ReasonCode.LOCKED)
        return _locked_available_result(mapping, ReasonCode.LOCKED)

    # Locked id is dead - hold and surface for a routed heal; never swap inline.
    return _locked_unavailable_result(ReasonCode.LOCK_HELD)


def reconcile_track(
    db: Database,
    track_id: int,
    client: object,
    force: bool = False,
    cached_mapping: PlatformMapping | None = None,
    playlist_id: int | None = None,
    verify_locked: bool = False,
) -> ReconcileResult:
    """Reconcile a canonical track to a platform ID using multi-strategy cascade.

    When ``playlist_id`` is given, per-playlist version preferences cascade over
    the account-wide defaults and bias candidate ordering. With no configured
    preferences (or ``playlist_id=None``) the cascade resolves to the built-in
    defaults, which is a strict no-op, identical to the pre-preferences
    behaviour.

    ``verify_locked`` controls whether a durable user lock is actively checked
    for liveness on this run. Default ``False`` trusts the lock without an API
    call (fast, no rate-limit pressure). When ``True``, e.g. a periodic
    integrity sync, a lock whose platform id has gone dead is self-healed to an
    equivalent live id for the *same* recording, or held as unavailable if the
    recording is genuinely gone. It is never silently swapped to a different
    recording.
    """
    track = db.get_track(track_id)
    if track is None:
        return ReconcileResult(confidence="not_found")

    platform_name = client.platform_name

    prefs = resolve_preferences(
        db.get_global_preferences(),
        db.get_preferences(playlist_id) if playlist_id is not None else None,
        None,
    )
    # Typed preferences (the general (criterion, strength, target) model) resolved
    # across every scope - global < playlist < playlist-track. These drive the
    # two-phase engine's hard filters (require/forbid) and soft precedence.
    active_prefs = _scoped_active_prefs(db, track_id, playlist_id, prefs)
    # Combined scoring intent from the effective prefs: recording classes and
    # lyric axis feed the source-aware version verdict; edition buckets
    # (radio/single, deluxe/expanded/anniversary, compilation) feed the residual
    # edition penalties. Empty when prefs are the built-in defaults, so scoring
    # stays purely source-aware (a live source is not rejected as "avoided").
    # Any token already expressed as a typed criterion is dropped here so it is
    # not counted twice (see _legacy_intent).
    prefer_classes, avoid_classes = _legacy_intent(prefs, active_prefs)

    # Two-level composite identity lock (AC-L1/L4): a per-playlist override wins
    # over the library-wide default lock. Resolved regardless of ``force`` so the
    # forced/verify selection paths below can honour it via ``select_version``.
    effective_lock = db.get_effective_lock(track_id, platform_name, playlist_id)

    # Cache/mapping checks
    if not force:
        mapping = cached_mapping or db.get_platform_mapping(track_id, platform_name)
        # A lock (either scope) is authoritative on every non-forced run - it must
        # be consulted BEFORE the auto-match cache, so a per-playlist override is
        # never shadowed by the global cached mapping.
        if effective_lock is not None:
            if (
                verify_locked
                and effective_lock.scope == "global"
                and mapping is not None
            ):
                return _verify_lock(db, track, client, mapping)
            # Per-playlist self-heal is routed through the plan/apply engine
            # (Task 5.3); until then a per-playlist lock is trusted without a
            # liveness probe rather than mutated inline.
            if effective_lock.status == "unavailable":
                return _locked_unavailable_result(ReasonCode.LOCKED)
            return _locked_available_result(
                _mapping_from_effective(track_id, platform_name, effective_lock),
                ReasonCode.LOCKED,
            )
        tier, _, _ = db.get_resolution_state(track_id)
        if tier is not None and mapping is not None:
            if mapping.status == "unavailable":
                audit = MatchAudit(
                    availability=Availability.EXACT_UNAVAILABLE,
                    reason_code=ReasonCode.BLOCKED_IN_MARKET,
                )
                return ReconcileResult(
                    confidence="not_found",
                    from_cache=True,
                    availability=audit.availability,
                    reason_code=audit.reason_code,
                    audit=audit,
                )
            audit = MatchAudit(
                availability=Availability.EXACT_AVAILABLE,
                reason_code=ReasonCode.MATCHED,
                chosen_platform_id=mapping.platform_track_id,
                chosen_score=mapping.match_score or 100,
            )
            return ReconcileResult(
                platform_track_id=mapping.platform_track_id,
                score=mapping.match_score or 100,
                confidence="high",
                is_divergent=mapping.is_divergent,
                divergence_note=mapping.divergence_note,
                from_cache=True,
                availability=audit.availability,
                reason_code=audit.reason_code,
                audit=audit,
            )

    # Alias resolver (seed + user-curated DB classes), built once per run so an
    # aliased artist scores and is retrieved under every equivalent surface form.
    resolver = build_alias_resolver(db)

    # Candidate acquisition: steady-state selection reads the persisted
    # candidate set (AC-X3, no live search); a cold cache or an explicit refresh
    # (force) does one live gather and persists it (AC-P4). Shared with the
    # library-first resolver via gather_candidates so both see an identical set.
    all_candidates, candidate_strategies = acquire_candidates(
        db,
        track,
        client,
        resolver,
        prefer_classes=prefer_classes,
        avoid_classes=avoid_classes,
        force=force,
    )

    if not all_candidates:
        audit = _build_audit(
            track=track,
            platform_name=platform_name,
            scored=[],
            confidence="not_found",
            prefer=prefer_classes,
            avoid=avoid_classes,
            resolver=resolver,
            active=active_prefs,
        )
        return ReconcileResult(
            confidence="not_found",
            availability=audit.availability,
            reason_code=audit.reason_code,
            audit=audit,
        )

    # Score all candidates uniformly
    all_durations = [r.duration_seconds for r in all_candidates if r.duration_seconds]

    def _int_score(r: TrackResult) -> int:
        s = score_match_with_version(
            track.title,
            track.artist,
            track.album,
            r.title,
            r.artist,
            r.album,
            result_duration=r.duration_seconds,
            reference_duration=track.duration_seconds,
            all_durations=all_durations,
            prefer=prefer_classes,
            avoid=avoid_classes,
            cand_explicit=getattr(r, "explicit", None),
            alias_resolver=resolver,
        )
        return min(
            100,
            s + duration_proximity_bonus(r.duration_seconds, track.duration_seconds),
        )

    # --- Selection: the single two-phase engine owns the winner pick (AC-C5) ---
    # select_version applies the availability filter, hard-preference filter,
    # source-aware version-mismatch guard, ambiguity-delta review flag and the
    # per-playlist soft-preference precedence (including typed audio-format
    # prefs like "prefer atmos"), then ranks the available survivors by the
    # shared Distance scorer. This retires the old integer loop as the SELECTOR;
    # winner-parity with the retired ranking is gated on the gold corpus
    # (tests/gold/test_winner_parity.py). The integer score is retained only to
    # drive the confidence bar + reported score + audit, so the accept /
    # not_found / high boundaries stay byte-identical for default preferences
    # (classify_scores reads the score multiset, independent of order).
    selection = select_version(
        track,
        all_candidates,
        active=active_prefs,
        lock=_identity_lock_from_effective(effective_lock, track)
        if effective_lock
        else None,
        prefer=prefer_classes,
        avoid=avoid_classes,
        all_durations=all_durations or None,
        alias_resolver=resolver,
    )

    survivors = [c for c, _ in selection.ranked] if selection.winner is not None else []
    if survivors:
        # The engine ranked available survivors by Distance (and any typed/soft
        # preference). When it resolved the winner via a preference or precedence
        # (decided_by set - e.g. a "prefer atmos" spatial criterion), that choice
        # is authoritative and must not be second-guessed. Only when the weighted
        # score alone left the top band unresolved (decided_by is None, an
        # effective tie) do the per-playlist free-text keyword bias + the
        # standard-edition tiebreak decide the pick - the legacy keyword-
        # preference behaviour. The sort is stable, so a band with no keyword/
        # edition signal keeps the engine's order.
        winner_result = selection.winner
        if selection.decided_by is None:
            base_distance = selection.winner_distance.total
            band = [
                cand
                for cand, dist in selection.ranked
                if dist.total - base_distance <= AMBIGUITY_DELTA
            ]
            if len(band) > 1:
                band.sort(
                    key=lambda c: (
                        -preference_sort_bias(c.album or "", prefs),
                        edition_cost(c.album or ""),
                    )
                )
                winner_result = band[0]
        ordered = [winner_result, *[c for c in survivors if c is not winner_result]]
    else:
        ordered = []
    scored: list[tuple[int, int, TrackResult]] = [  # (score, edition_penalty, result)
        (_int_score(r), edition_cost(r.album or ""), r) for r in ordered
    ]
    confidence = (
        classify_scores([s for s, _, _ in scored], min_lead=prefs.min_lead)
        if scored
        else "not_found"
    )
    # The engine refuses to confidently commit a near-tie or an unresolved
    # version mismatch; surface it for review rather than silently guessing
    # (AC-S3/AC-S4). Never elevate a below-floor not_found - two poor matches
    # are still "not found", not "review these two".
    if confidence != "not_found" and selection.needs_review:
        confidence = "ambiguous"

    if confidence == "not_found":
        # No confident *available* winner. Rank ALL candidates with the integer
        # path so an exact-but-unavailable release still surfaces as held
        # (EXACT_UNAVAILABLE), never a silent miss.
        scored_all = [
            (_int_score(r), edition_cost(r.album or ""), r) for r in all_candidates
        ]
        scored_all.sort(
            key=lambda x: (
                -(x[0] + preference_sort_bias(x[2].album or "", prefs)),
                x[1],
            )
        )
        fallback_conf = (
            classify_scores([s for s, _, _ in scored_all], min_lead=prefs.min_lead)
            if scored_all
            else "not_found"
        )
        audit = _build_audit(
            track=track,
            platform_name=platform_name,
            scored=scored_all,
            confidence=fallback_conf,
            prefer=prefer_classes,
            avoid=avoid_classes,
            resolver=resolver,
            selection=selection,
            active=active_prefs,
        )
        if audit.availability == Availability.EXACT_UNAVAILABLE and scored_all:
            # The exact recording exists but is unplayable here: report it as
            # held (its id + score), flagged via availability so callers never
            # treat it as a live, selectable match.
            held_score, _, held = scored_all[0]
            return ReconcileResult(
                platform_track_id=held.platform_id,
                platform_title=held.title,
                platform_artist=held.artist,
                platform_album=held.album,
                score=held_score,
                confidence=fallback_conf,
                alternatives=[r for _, _, r in scored_all[1:4]],
                availability=audit.availability,
                reason_code=audit.reason_code,
                audit=audit,
            )
        return ReconcileResult(
            confidence="not_found",
            alternatives=[r for _, _, r in scored_all[:3]],
            availability=audit.availability,
            reason_code=audit.reason_code,
            audit=audit,
        )

    audit = _build_audit(
        track=track,
        platform_name=platform_name,
        scored=scored,
        confidence=confidence,
        prefer=prefer_classes,
        avoid=avoid_classes,
        resolver=resolver,
        selection=selection,
        active=active_prefs,
    )
    best_score, _, best_result = scored[0]
    is_div = _check_divergence(track.album, best_result.album)
    div_note = f"Version differs: {best_result.album}" if is_div else None

    # Duration sanity check
    if (
        track.duration_seconds
        and best_result.duration_seconds
        and best_result.duration_seconds > track.duration_seconds * 1.6
    ):
        is_div = True
        div_note = (
            f"Duration suspicious: "
            f"{best_result.duration_seconds}s vs expected ~{track.duration_seconds}s"
        )

    # Artist mismatch check: if best result has a completely different artist, flag it.
    # Canonicalize through the alias resolver first so equivalent surface forms
    # (98\u00ba / 98 Degrees) are not falsely flagged as a mismatch.
    from difflib import SequenceMatcher as _SM

    from tuneshift.matching import normalize_artist as _norm_artist

    src_artist_norm = resolver.canonical(_norm_artist(track.artist))
    res_artist_norm = (
        resolver.canonical(_norm_artist(best_result.artist))
        if best_result.artist
        else ""
    )
    if src_artist_norm and res_artist_norm:
        artist_ratio = _SM(None, src_artist_norm, res_artist_norm).ratio()
        if artist_ratio < 0.4:
            is_div = True
            div_note = (
                f'Artist mismatch: expected "{track.artist}", '
                f'got "{best_result.artist}"'
            )

    match_type = candidate_strategies.get(best_result.platform_id, "")

    return ReconcileResult(
        platform_track_id=best_result.platform_id,
        platform_title=best_result.title,
        platform_artist=best_result.artist,
        platform_album=best_result.album,
        score=best_score,
        confidence=confidence,
        is_divergent=is_div,
        divergence_note=div_note,
        alternatives=[r for _, _, r in scored[1:4]],
        match_type=match_type,
        availability=audit.availability,
        reason_code=audit.reason_code,
        audit=audit,
    )


def _quick_top_score(
    track,
    candidates: list[TrackResult],
    *,
    prefer: frozenset[str] = frozenset(),
    avoid: frozenset[str] = frozenset(),
    resolver: AliasResolver | None = None,
) -> int:
    """Quick score check for short-circuit decision."""
    best = 0
    for c in candidates:
        s = score_match_with_version(
            track.title,
            track.artist,
            track.album,
            c.title,
            c.artist,
            c.album,
            result_duration=c.duration_seconds,
            reference_duration=track.duration_seconds,
            prefer=prefer,
            avoid=avoid,
            cand_explicit=getattr(c, "explicit", None),
            alias_resolver=resolver,
        )
        s = min(
            100,
            s + duration_proximity_bonus(c.duration_seconds, track.duration_seconds),
        )
        if s > best:
            best = s
    return best


def _check_divergence(source_album: str | None, result_album: str) -> bool:
    """Check if the result is a different version/remaster."""
    if not source_album:
        return False
    norm_src = normalize_title(source_album)
    norm_res = normalize_title(result_album)
    if norm_src == norm_res:
        return False
    if is_remaster(result_album) != is_remaster(source_album or ""):
        return True
    ratio = SequenceMatcher(None, norm_src, norm_res).ratio()
    return ratio < 0.7
