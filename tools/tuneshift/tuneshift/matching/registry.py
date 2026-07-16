"""Default criterion registry + preference resolution (AC-C1).

The GOVERNING requirement is a single general model: a preference is
``(criterion, strength, target, scope)`` settable on *any* metadata axis, and
the matcher builds confidence from every available field. This module is the
seam that turns a stored typed preference into the concrete
:class:`~tuneshift.matching.criteria.Criterion` the two-phase selection engine
fires on, so a user's ``prefer spatial=atmos`` actually selects the Atmos
release, rather than the axis being handled by bespoke one-off code (the
per-axis build-and-abandon pattern this design exists to end).

Two families of axis:

* **Structured** axes read a typed list field the platform captured on the
  candidate (Tidal ``audio_modes`` / ``media_metadata_tags``). These drive
  confident matches and hard filters directly.
* **Title-derived** axes (performance/content/edit/production) are parsed from
  the free-text title through the committed whitelist confidence gate.
"""

from __future__ import annotations

from dataclasses import dataclass

from tuneshift.matching.criteria import (
    ArtistRoleCriterion,
    ComposerCriterion,
    Criterion,
    DateCriterion,
    DurationCriterion,
    EditAxisCriterion,
    Strength,
    TitleTokenCriterion,
    TokenCriterion,
    TokenWhitelist,
    WorkCriterion,
    load_token_whitelist,
)
from tuneshift.matching.precedence import PreferenceRef
from tuneshift.matching.selection import ActivePreference

#: Whitelist axes backed by a STRUCTURED list field on the candidate metadata
#: (populated from the platform search - Tidal ``audio_modes`` covers both the
#: spatial mix and the mono/stereo channel layout; ``media_metadata_tags``
#: carries the encoding-fidelity tier).
STRUCTURED_AXIS_FIELDS: dict[str, str] = {
    "spatial": "audio_modes",
    "mix": "audio_modes",
    "fidelity": "media_metadata_tags",
}

#: Whitelist axes whose evidence is PARSED from the free-text title (no reliable
#: structured field exists for them), gated by the committed whitelist.
TITLE_AXES: frozenset[str] = frozenset({"performance", "content", "edit", "production"})

#: Date/year axes (M3). Each maps a preference axis to the candidate metadata
#: attribute the :class:`~tuneshift.matching.criteria.DateCriterion` reads; the
#: target is a four-digit year or the literal ``"original"`` (field absent).
DATE_AXIS_FIELDS: dict[str, str] = {
    "recording_year": "recording_date",
    "release_year": "release_date",
    "remaster_year": "remaster_year",
}


@dataclass(frozen=True)
class PreferenceSpec:
    """A typed preference the user set at some scope (AC-C1 general model).

    ``target`` is the desired token in any surface form (``"atmos"``,
    ``"Dolby Atmos"``, ``"dolby_atmos"``); it is canonicalised against the
    whitelist when the criterion is built.
    """

    axis: str
    target: str
    strength: Strength
    scope: str = "global"


def criterion_for(
    axis: str,
    target: str,
    whitelist: TokenWhitelist | None = None,
) -> Criterion:
    """Build the concrete criterion for ``axis``/``target``.

    ``target`` is canonicalised through the whitelist alias table so an alias
    surface form (``"atmos"``) matches the candidate's canonical token
    (``DOLBY_ATMOS``). Raises :class:`ValueError` for an unknown axis so a
    typo'd preference fails loudly rather than silently never firing.
    """

    wl = whitelist or load_token_whitelist()
    canonical = wl.canonical(target)

    if axis in DATE_AXIS_FIELDS:
        # Date axes carry a year / "original" target, not a whitelist token, so
        # the raw target is passed through unfolded.
        return DateCriterion(
            name=axis, date_field=DATE_AXIS_FIELDS[axis], target=target
        )
    if axis == "duration":
        # The duration axis target is a tolerance (e.g. "3s" / "5%"), not a
        # whitelist token - pass the raw target through unfolded (M4).
        return DurationCriterion(name=axis, target=target)
    if axis == "artist_role":
        # Role-aware artist-set match; target selects the role ("main"). Raw
        # target, not a whitelist token (M5).
        return ArtistRoleCriterion(name=axis, target=target)
    if axis == "language":
        # Scalar language-equality; the target is a language code/name (not a
        # whitelist token). TokenCriterion folds it and the candidate uniformly
        # so "en"/"EN" match (M6). Structured -> a require may hard-filter.
        return TokenCriterion(
            name=axis, field_name="language", target=target, structured=True
        )
    if axis == "composer":
        # Source-vs-candidate composer identity match; target is a mode selector
        # ("match"), not a whitelist token (M6).
        return ComposerCriterion(name=axis, target=target)
    if axis == "work":
        # MB work-entity: original vs cover/re-recording. Target is "original"
        # or a re-recording marker ("taylors version"), not a whitelist token
        # (M2).
        return WorkCriterion(name=axis, target=target)
    field = STRUCTURED_AXIS_FIELDS.get(axis)
    if field is not None:
        return TokenCriterion(
            name=axis, field_name=field, target=canonical, structured=True
        )
    if axis == "edit":
        # M7: dual-source (title + structured version) with album_version as the
        # unmarked default - a plain album track carries no "album version" text.
        return EditAxisCriterion(whitelist=wl, target=canonical)
    if axis in TITLE_AXES:
        return TitleTokenCriterion(name=axis, target=canonical, whitelist=wl)
    raise ValueError(f"unknown preference axis {axis!r}")


#: All criterion axes a preference may target (structured + title-derived + date
#: + duration-tolerance + role-aware artist + language/composer + work-entity axes).
KNOWN_AXES: frozenset[str] = (
    frozenset(STRUCTURED_AXIS_FIELDS)
    | TITLE_AXES
    | frozenset(DATE_AXIS_FIELDS)
    | frozenset({"duration", "artist_role", "language", "composer", "work"})
)

#: Scope name each cascade layer maps onto for engine precedence
#: (:data:`tuneshift.matching.precedence.SCOPE_RANK`). The most-specific
#: playlist-track layer maps onto ``"track"`` (rank 0, resolved first).
_SCOPE_NAMES = ("global", "playlist", "track")


def resolve_scoped_specs(
    global_prefs: list[dict] | tuple[dict, ...] | None,
    playlist_prefs: list[dict] | tuple[dict, ...] | None,
    playlist_track_prefs: list[dict] | tuple[dict, ...] | None,
    *,
    whitelist: TokenWhitelist | None = None,
) -> list[PreferenceSpec]:
    """Cascade typed preferences from three scopes into engine-ready specs.

    Each input is a list of ``{"criterion", "strength", "target"}`` dicts read
    from a storage layer (``criterion`` names the axis; ``target`` a token in any
    surface form). Precedence is ``global < playlist < playlist-track``, the
    most specific scope wins (AC-CLI1). Two preferences that address the *same*
    ``(axis, canonical-target)`` collapse to the most-specific scope's entry, so
    a per-playlist-track ``spatial require atmos`` overrides a global ``spatial
    avoid atmos`` rather than fighting it in phase-1 as contradictory hard
    filters. Different targets on the same axis (e.g. ``content avoid karaoke``
    and ``content avoid instrumental``) coexist.

    Unknown axes and unknown strengths are skipped defensively (the CLI validates
    loudly at set-time; this keeps a stray stored row from crashing a match).
    """

    wl = whitelist or load_token_whitelist()
    # Keyed by (axis, canonical target); a later (more specific) scope overwrites,
    # giving most-specific-wins collapse while preserving distinct targets.
    collapsed: dict[tuple[str, str], PreferenceSpec] = {}
    for scope, layer in zip(
        _SCOPE_NAMES, (global_prefs, playlist_prefs, playlist_track_prefs), strict=True
    ):
        for row in layer or ():
            axis = row.get("criterion")
            target = row.get("target")
            raw_strength = row.get("strength")
            if axis not in KNOWN_AXES or not target:
                continue
            try:
                strength = Strength(raw_strength)
            except ValueError:
                continue
            canonical = wl.canonical(target)
            collapsed[(axis, canonical)] = PreferenceSpec(
                axis=axis, target=target, strength=strength, scope=scope
            )
    return list(collapsed.values())


def resolve_active_preferences(
    specs: list[PreferenceSpec] | tuple[PreferenceSpec, ...],
    *,
    whitelist: TokenWhitelist | None = None,
) -> list[ActivePreference]:
    """Resolve stored typed preferences into engine-ready active preferences.

    Each spec becomes an :class:`ActivePreference` pairing the concrete
    criterion (which knows how to read the candidate field) with a
    :class:`PreferenceRef` (which carries the strength/target/scope the engine's
    precedence resolution keys on). The order of ``specs`` is preserved, which
    also fixes the per-playlist precedence order.
    """

    wl = whitelist or load_token_whitelist()
    active: list[ActivePreference] = []
    for spec in specs:
        criterion = criterion_for(spec.axis, spec.target, wl)
        ref = PreferenceRef(
            spec.axis, spec.strength, wl.canonical(spec.target), spec.scope
        )
        active.append(ActivePreference(criterion, ref))
    return active


__all__ = [
    "KNOWN_AXES",
    "STRUCTURED_AXIS_FIELDS",
    "TITLE_AXES",
    "PreferenceSpec",
    "criterion_for",
    "resolve_active_preferences",
    "resolve_scoped_specs",
]
