"""Configurable named penalties, the beets-style scoring primitives.

Each scoring signal (title, artist, album, isrc, version keywords, duration)
is expressed as a :class:`SignalPenalty` carrying two projections of the same
judgement:

* ``penalty`` (0.0-1.0) and ``weight``, the *distance* view: 0.0 = perfect,
  1.0 = worst; distance = sum(penalty*weight) / sum(weight) (see ``engine.py``).
* ``points``, the *legacy-score* view: the exact signed integer this signal
  contributed under the historical additive scorer, so the split preserves
  byte-for-byte parity with ``score_match`` / ``score_match_with_version``.

All magic numbers live in :class:`Weights`; its defaults reproduce the current
scorer exactly. Callers can pass a customised ``Weights`` (or per-playlist
preferences, later) without touching this code.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from tuneshift.matching.aliases import AliasResolver, default_resolver
from tuneshift.matching.similarity import ratio

if TYPE_CHECKING:
    from tuneshift.matching.version import VersionProfile


@dataclass(frozen=True)
class VersionWeights:
    """Point penalties for undesirable version keywords (all cumulative)."""

    karaoke: int = 50
    instrumental: int = 50
    live: int = 20
    remix: int = 20
    tribute: int = 20
    radio_edit: int = 20
    compilation: int = 15
    acoustic: int = 10
    remaster: int = 10
    deluxe: int = 5
    # Preference-grade down-rank for the lyric (explicit/clean) RATING. This is
    # a preference between available alternatives, not a defect in the
    # recording, so it must stay small and must never reuse ``substitute``.
    # Reusing substitute-grade 40 here quarantined every clean track (BUG-13).
    lyric_preference: int = 10
    # Source-aware recording-verdict magnitudes (Chunk 4). ``reject`` is large
    # enough to floor the 0-100 legacy score; ``substitute`` down-ranks a
    # fallback recording (e.g. studio master for a requested live take) without
    # hiding it; a soft remaster reuses ``remaster``.
    substitute: int = 40
    reject: int = 100


@dataclass(frozen=True)
class Weights:
    """All tunable magic numbers. Defaults == the historical scorer."""

    # Title bonus tiers (ratio thresholds are exclusive/inclusive as noted).
    title_exact: int = 50
    title_high: int = 30  # ratio > title_high_ratio
    title_mid: int = 15  # ratio >= title_mid_ratio
    title_high_ratio: float = 0.85
    title_mid_ratio: float = 0.70

    # Artist bonus/penalty tiers.
    artist_exact: int = 30
    artist_high: int = 25  # ratio > artist_high_ratio
    artist_mid: int = 15  # ratio > artist_mid_ratio
    artist_low_penalty: int = 15  # ratio > artist_low_ratio -> -artist_low_penalty
    artist_heavy_base: int = 30  # else -> -int(base * (1 - 2*ratio))
    artist_high_ratio: float = 0.85
    artist_mid_ratio: float = 0.70
    artist_low_ratio: float = 0.50

    # Album bonus tiers (only scored when a source album is present).
    album_exact: int = 20
    album_high: int = 10  # ratio >= album_high_ratio
    album_high_ratio: float = 0.75

    # ISRC exact-match bonus.
    isrc_bonus: int = 15

    # Duration band penalties (require a reference >= duration_ref_floor).
    duration_ref_floor: int = 60
    duration_long_max: int = 20  # ratio > 2.0
    duration_long_high: int = 15  # ratio > 1.6
    duration_long_mid: int = 10  # ratio > 1.4
    duration_short_max: int = 20  # ratio < 0.5
    duration_short_high: int = 15  # ratio < 0.65
    duration_short_mid: int = 10  # ratio < 0.75

    version: VersionWeights = field(default_factory=VersionWeights)

    def with_overrides(self, **kwargs: object) -> Weights:
        """Return a copy with the given top-level fields replaced."""
        return replace(self, **kwargs)


DEFAULT_WEIGHTS = Weights()


@dataclass(frozen=True)
class SignalPenalty:
    """One scoring signal, in both the distance and legacy-score projections.

    ``points`` is the exact signed integer the signal contributes to the
    legacy 0-100 score. ``penalty`` (0.0-1.0) and ``weight`` drive the
    normalized distance model.
    """

    name: str
    points: int
    penalty: float
    weight: int


def _clamp01(value: float) -> float:
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value


def _bonus_penalty(points: int, budget: int) -> float:
    """Normalized penalty for a *bonus* signal: full budget -> 0, none -> 1."""
    if budget <= 0:
        return 0.0
    return _clamp01(1.0 - points / budget)


# Signals in this class express what the LISTENER wanted, not what is wrong with
# the recording. They belong in ranking (which candidate wins) and must stay out
# of every absolute accept floor (was anything found at all), because a
# preference is only meaningful when a preferred alternative actually exists.
PREFERENCE_SIGNAL_PREFIX = "pref:"


def is_preference_signal(name: str) -> bool:
    """True when a signal is preference-grade rather than quality-grade.

    The single definition of the distinction. The scorer, the resolve
    quarantine floor and the reconcile not-found floor all consult this rather
    than testing the prefix themselves: a second copy of this rule is precisely
    how the explicit/clean axis drifted out of agreement with itself (BUG-13).
    """
    return name.startswith(PREFERENCE_SIGNAL_PREFIX)


def title_signal(
    source_title: str, result_title: str, weights: Weights = DEFAULT_WEIGHTS
) -> SignalPenalty:
    """Score title similarity. Empty on either side yields no signal."""
    budget = weights.title_exact
    if not source_title or not result_title:
        return SignalPenalty("title", 0, 1.0, budget)
    if source_title == result_title:
        points = weights.title_exact
    else:
        r = ratio(source_title, result_title)
        if r > weights.title_high_ratio:
            points = weights.title_high
        elif r >= weights.title_mid_ratio:
            points = weights.title_mid
        else:
            points = 0
    return SignalPenalty("title", points, _bonus_penalty(points, budget), budget)


def artist_signal(
    source_artist: str,
    result_artist: str,
    weights: Weights = DEFAULT_WEIGHTS,
    *,
    resolver: AliasResolver | None = None,
) -> SignalPenalty:
    """Score artist similarity. Empty on either side yields no signal.

    ``source_artist``/``result_artist`` are expected already normalized. Each is
    mapped through the alias resolver's canonical key before comparison so that
    equivalent surface forms (``98\u00ba`` / ``98 Degrees``, ``Ke$ha`` / ``Kesha``)
    score as an exact artist match. ``resolver`` defaults to the seed-only
    :func:`default_resolver`; reconcile passes a seed+DB resolver so user-curated
    alias classes also apply. Non-member artists map to themselves, so the score
    is byte-identical to the historical scorer for every un-aliased artist.
    """
    budget = weights.artist_exact
    if not source_artist or not result_artist:
        return SignalPenalty("artist", 0, 1.0, budget)
    resolver = resolver or default_resolver()
    source_artist = resolver.canonical(source_artist)
    result_artist = resolver.canonical(result_artist)
    if source_artist == result_artist:
        points = weights.artist_exact
    else:
        r = ratio(source_artist, result_artist)
        if r > weights.artist_high_ratio:
            points = weights.artist_high
        elif r > weights.artist_mid_ratio:
            points = weights.artist_mid
        elif r > weights.artist_low_ratio:
            points = -weights.artist_low_penalty
        else:
            points = -int(weights.artist_heavy_base * (1.0 - r * 2))
    return SignalPenalty("artist", points, _bonus_penalty(points, budget), budget)


def artist_overlap_absent(
    source_artist: str,
    result_artist: str,
    weights: Weights = DEFAULT_WEIGHTS,
    *,
    resolver: AliasResolver | None = None,
) -> bool:
    """True only when two NON-EMPTY artists share no plausible identity.

    Hard-reject gate (BUG-3): a same-title candidate by a clearly different
    artist must not win. Inputs are expected already normalized (same contract
    as :func:`artist_signal`); this function only alias-canonicalizes, so it
    never double-normalizes. Returns False (no reject) whenever:

    * either side is empty (missing data is not a mismatch),
    * alias-resolved forms are equal,
    * one token set is a subset of the other (featured-artist containment,
      e.g. "drake" within "drake feat future"), or
    * fuzzy ratio clears ``artist_low_ratio``.

    Covers/tributes that share a name are handled by the source-aware version
    axis (a studio source rejects a tribute take), not here.
    """
    if not source_artist or not result_artist:
        return False
    resolver = resolver or default_resolver()
    src = resolver.canonical(source_artist)
    res = resolver.canonical(result_artist)
    if src == res:
        return False
    src_tokens = set(src.split())
    res_tokens = set(res.split())
    if (
        src_tokens
        and res_tokens
        and (src_tokens <= res_tokens or res_tokens <= src_tokens)
    ):
        return False  # directional containment (featured artist)
    return ratio(src, res) <= weights.artist_low_ratio


def album_signal(
    source_album: str | None,
    result_album: str | None,
    weights: Weights = DEFAULT_WEIGHTS,
    *,
    source_present: bool = True,
) -> SignalPenalty:
    """Score album similarity.

    ``source_present`` mirrors the legacy scorer's gate on the *raw* source
    album being truthy: when False the signal is neutral (no source album to
    compare). When True, inputs are expected already normalized and equal
    strings score an exact match (including the empty/empty case, preserving
    legacy behavior for albums whose names are pure edition tags).
    """
    budget = weights.album_exact
    if not source_present:
        return SignalPenalty("album", 0, 0.0, budget)
    src = source_album or ""
    res = result_album or ""
    if src == res:
        points = weights.album_exact
    elif src and res:
        points = (
            weights.album_high if ratio(src, res) >= weights.album_high_ratio else 0
        )
    else:
        points = 0
    return SignalPenalty("album", points, _bonus_penalty(points, budget), budget)


def isrc_signal(
    source_isrc: str | None, result_isrc: str | None, weights: Weights = DEFAULT_WEIGHTS
) -> SignalPenalty:
    """Exact-ISRC bonus. No signal unless both ISRCs are present and equal."""
    budget = weights.isrc_bonus
    if source_isrc and result_isrc and source_isrc.upper() == result_isrc.upper():
        return SignalPenalty("isrc", weights.isrc_bonus, 0.0, budget)
    return SignalPenalty("isrc", 0, 1.0, budget)


# Keyword -> (VersionWeights attribute, regex attribute in matching.normalize).
_VERSION_KEYWORDS: tuple[tuple[str, str, str], ...] = (
    ("karaoke", "karaoke", "_KARAOKE_RE"),
    ("instrumental", "instrumental", "_INSTRUMENTAL_RE"),
    ("live", "live", "_LIVE_RE"),
    ("remix", "remix", "_REMIX_RE"),
    ("tribute", "tribute", "_TRIBUTE_RE"),
    ("radio_edit", "radio_edit", "_RADIO_EDIT_RE"),
    ("compilation", "compilation", "_COMPILATION_RE"),
    ("acoustic", "acoustic", "_ACOUSTIC_RE"),
    ("remaster", "remaster", "_REMASTER_RE"),
    ("deluxe", "deluxe", "_DELUXE_RE"),
)


def version_signals(
    title: str, album: str, weights: Weights = DEFAULT_WEIGHTS
) -> list[SignalPenalty]:
    """Return one penalty signal per undesirable version keyword present.

    Each keyword is an independent penalty (fully present -> penalty 1.0,
    weighted by its point cost). Summed ``points`` equal the legacy
    ``version_penalty``.
    """
    from tuneshift.matching import normalize as _norm

    combined = f"{title} {album}"
    signals: list[SignalPenalty] = []
    for name, attr, regex_name in _VERSION_KEYWORDS:
        regex = getattr(_norm, regex_name)
        if regex.search(combined):
            cost = getattr(weights.version, attr)
            signals.append(SignalPenalty(f"version:{name}", -cost, 1.0, cost))
    return signals


# Keywords that are NOT recording-identity classes: they describe packaging or
# an edit of the *same* recording, so they layer on top of the source-aware
# recording verdict rather than being folded into it.
_RESIDUAL_KEYWORDS: tuple[tuple[str, str, str], ...] = (
    ("radio_edit", "radio_edit", "_RADIO_EDIT_RE"),
    ("compilation", "compilation", "_COMPILATION_RE"),
    ("deluxe", "deluxe", "_DELUXE_RE"),
)


def _residual_version_signals(
    source_title: str,
    source_album: str,
    cand_title: str,
    cand_album: str,
    weights: Weights = DEFAULT_WEIGHTS,
    *,
    prefer: frozenset[str] = frozenset(),
    avoid: frozenset[str] = frozenset(),
    owned: frozenset[str] = frozenset(),
) -> list[SignalPenalty]:
    """Score packaging/edit (edition) keywords on the candidate.

    These buckets (radio/single edit, compilation, deluxe/expanded/anniversary)
    do not change *which recording* a track is, so they sit on top of the
    source-aware recording verdict. There are three regimes per bucket, keyed on
    the effective per-playlist preferences (``prefer``/``avoid`` carry the
    edition bucket names produced by
    :func:`tuneshift.matching.preferences.scoring_intent`):

    * **avoided**, the candidate carries an edition the playlist wants to steer
      away from: down-rank it (substitute-grade) so a cleaner alternative wins,
      while keeping it findable when it is the only option.
    * **preferred**, the playlist wants this edition: candidates *lacking* it
      are treated as a substitute for the intent (substitute-grade penalty), so
      the preferred edition wins even at the score ceiling; the edition-bearing
      candidate keeps its full score.
    * **default** (neither preferred nor avoided), the historical asymmetric
      minor down-rank: penalise only when the candidate carries the marker and
      the source does not. With no configured preferences every bucket falls
      here, preserving byte-for-byte parity.

    ``owned`` names buckets whose axis is governed by an active *typed* criterion
    (e.g. the ``edit`` axis owns ``radio_edit``). Those buckets are skipped
    entirely so the typed criterion is the single source of truth for that axis
    and its default down-rank cannot double-count against, or fight, an
    explicit typed preference (M7). ``owned`` is empty for default prefs, so
    byte-parity is preserved.
    """
    from tuneshift.matching import normalize as _norm

    src_combined = f"{source_title} {source_album}"
    cand_combined = f"{cand_title} {cand_album}"
    sub = weights.version.substitute
    signals: list[SignalPenalty] = []
    for name, attr, regex_name in _RESIDUAL_KEYWORDS:
        if name in owned:
            continue
        regex = getattr(_norm, regex_name)
        cand_has = bool(regex.search(cand_combined))
        if name in avoid:
            if cand_has:
                signals.append(SignalPenalty(f"version:{name}", -sub, 0.55, sub))
        elif name in prefer:
            if not cand_has:
                signals.append(SignalPenalty(f"version:{name}", -sub, 0.55, sub))
        elif cand_has and not regex.search(src_combined):
            cost = getattr(weights.version, attr)
            signals.append(SignalPenalty(f"version:{name}", -cost, 1.0, cost))
    return signals


def _lyric_preference_signals(
    source: VersionProfile,
    candidate: VersionProfile,
    weights: Weights,
    *,
    prefer: frozenset[str],
) -> list[SignalPenalty]:
    """Down-rank the non-preferred lyric RATING between real alternatives.

    A content rating is a descriptor, not a defect in the recording, so this
    is preference-grade and is named ``pref:`` so the acceptance floor can
    ignore it. A preference must never quarantine a track that had no
    preferred alternative to begin with (BUG-13).

    Fires only when the candidate's STRUCTURED rating is known. Sources that
    carry an affirmative lyric variant marker are governed by the recording
    verdict instead, so they are skipped here to avoid double-charging.
    """
    if candidate.explicit_rating is None:
        return []
    if source.is_clean or source.is_explicit:
        return []
    # An affirmatively marked clean edit is a recording variant and is already
    # charged by the version verdict; that axis owns it, so do not double-count
    # (same ``owned`` discipline as the residual edition buckets).
    if candidate.is_clean:
        return []
    prefers_clean = "clean" in prefer
    wanted = False if prefers_clean else True
    if candidate.explicit_rating is wanted:
        return []
    cost = weights.version.lyric_preference
    return [SignalPenalty("pref:lyric", -cost, 0.15, cost)]


def is_lyric_non_preferred(
    source_title: str | None,
    source_album: str | None,
    cand_title: str | None,
    cand_album: str | None,
    cand_version: str | None = None,
    *,
    cand_explicit: bool | None = None,
    prefer: frozenset[str] = frozenset(),
    weights: Weights = DEFAULT_WEIGHTS,
) -> bool:
    """True when the candidate carries the NON-preferred lyric rating.

    Exposed so callers that gate on a score threshold (e.g. the ISRC
    short-circuit) can honour the lyric preference without re-implementing it.
    Duplicating this rule into a second engine is what let the explicit/clean
    axis drift out of agreement with itself in the first place.
    """
    from tuneshift.matching.version import infer_version

    src = infer_version(source_title, source_album)
    cand = infer_version(cand_title, cand_album, cand_version, explicit=cand_explicit)
    return bool(_lyric_preference_signals(src, cand, weights, prefer=prefer))


def source_aware_version_signals(
    source_title: str,
    source_album: str,
    cand_title: str,
    cand_album: str,
    *,
    source_version: str | None = None,
    cand_version: str | None = None,
    source_explicit: bool | None = None,
    cand_explicit: bool | None = None,
    prefer: frozenset[str] = frozenset(),
    avoid: frozenset[str] = frozenset(),
    owned: frozenset[str] = frozenset(),
    weights: Weights = DEFAULT_WEIGHTS,
) -> list[SignalPenalty]:
    """Source-aware version scoring (Chunk 4).

    Combines the recording-identity verdict (``compare_version``) with the
    residual packaging/edit penalties. The recording verdict is emitted as a
    single ``version:<verdict>`` signal whose name drives the engine's
    recommendation cap (REJECT / SUBSTITUTE), so callers get correct intent
    fidelity in both the distance and legacy-point projections.

    ``source_version`` / ``cand_version`` are the platforms' STRUCTURED version
    fields (Tidal ``tidal_version``); they let a controlled-vocabulary marker
    (e.g. a continuous-mix "Mixed") classify the recording even when the free
    title does not carry it (M1).

    ``source_explicit`` / ``cand_explicit`` are the platforms' STRUCTURED explicit
    booleans. When provided they drive the lyric (explicit/clean) axis
    authoritatively, so a structurally-clean candidate is down-ranked toward the
    explicit release by default even without a title marker. ``None`` preserves
    the prior title-regex behaviour (byte parity).
    """
    from tuneshift.matching.version import (
        VersionVerdict,
        compare_version,
        infer_version,
    )

    vw = weights.version
    src = infer_version(
        source_title, source_album, source_version, explicit=source_explicit
    )
    cand = infer_version(cand_title, cand_album, cand_version, explicit=cand_explicit)
    verdict = compare_version(src, cand, prefer=prefer, avoid=avoid)

    signals: list[SignalPenalty] = []
    if verdict is VersionVerdict.MATCH:
        signals.append(SignalPenalty("version:match", 0, 0.0, 0))
    elif verdict is VersionVerdict.SOFT:
        signals.append(SignalPenalty("version:soft", -vw.remaster, 0.15, vw.remaster))
    elif verdict is VersionVerdict.SUBSTITUTE:
        signals.append(
            SignalPenalty("version:substitute", -vw.substitute, 0.55, vw.substitute)
        )
    else:  # REJECT
        signals.append(SignalPenalty("version:reject", -vw.reject, 1.0, vw.reject))

    signals.extend(_lyric_preference_signals(src, cand, weights, prefer=prefer))
    signals.extend(
        _residual_version_signals(
            source_title,
            source_album,
            cand_title,
            cand_album,
            weights,
            prefer=prefer,
            avoid=avoid,
            owned=owned,
        )
    )
    return signals


def duration_signal(
    candidate_duration: int | None,
    reference_duration: int | None = None,
    all_durations: list[int] | None = None,
    weights: Weights = DEFAULT_WEIGHTS,
) -> SignalPenalty:
    """Penalize durations far from the expected reference.

    Mirrors the legacy tiered bands. ``penalty`` is normalized against the
    maximum band (20) so a full band -> 1.0.
    """
    max_band = max(weights.duration_long_max, weights.duration_short_max)
    if candidate_duration is None:
        return SignalPenalty("duration", 0, 0.0, max_band)

    if reference_duration is None and all_durations:
        valid = [d for d in all_durations if d and d > weights.duration_ref_floor]
        if valid:
            reference_duration = min(valid)

    if reference_duration is None or reference_duration < weights.duration_ref_floor:
        return SignalPenalty("duration", 0, 0.0, max_band)

    r = candidate_duration / reference_duration
    if r > 2.0:
        cost = weights.duration_long_max
    elif r > 1.6:
        cost = weights.duration_long_high
    elif r > 1.4:
        cost = weights.duration_long_mid
    elif r < 0.5:
        cost = weights.duration_short_max
    elif r < 0.65:
        cost = weights.duration_short_high
    elif r < 0.75:
        cost = weights.duration_short_mid
    else:
        cost = 0

    penalty = _clamp01(cost / max_band) if max_band > 0 else 0.0
    return SignalPenalty("duration", -cost, penalty, max_band)
