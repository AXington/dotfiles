"""Preferences command: manage DB-backed version preferences.

This is the user-facing control surface for the general metadata-driven
preference model (AC-CLI1): a preference is ``(criterion, strength, target)``
settable on ANY criterion at three scopes, and the matcher reads exactly what
this command writes, so a configured preference takes effect on the next
sync/add/doctor run.

Typed grammar (the general model)::

    tuneshift prefs set  <criterion> <strength> <target> [--playlist NAME] [--track ID]
    tuneshift prefs unset <criterion>                     [--playlist NAME] [--track ID]
    tuneshift prefs list                                  [--playlist NAME] [--track ID]

* ``criterion`` is an axis: spatial / mix / fidelity (structured audio) or
  performance / content / edit / production (title-derived).
* ``strength`` is one of require / prefer / avoid / forbid. ``require``/``forbid``
  are HARD filters (a candidate is eliminated); ``prefer``/``avoid`` are SOFT.
* ``target`` is a token in any surface form (``atmos`` / ``Dolby Atmos`` /
  ``dolby_atmos``); it is canonicalised against the whitelist.

Scope is chosen by the flags present: none = global, ``--playlist`` = that
playlist, ``--playlist`` + ``--track`` = that track on that playlist
(most-specific). Precedence when resolving a match is global < playlist <
playlist-track.

Legacy grammar (recording-class / lyric / edition soft intent, retained for
backward compatibility)::

    tuneshift prefs set   version.<field> <value> [--global | --playlist NAME | --track ID]
    tuneshift prefs show                           [--global | --playlist NAME | --track ID]
    tuneshift prefs clear                          [--global | --playlist NAME | --track ID]

where ``version.<field>`` is ``prefer`` / ``avoid`` / ``tiebreak_order``
(comma lists), ``duration_tolerance_percent`` (float) or ``min_lead`` (int).
"""

from __future__ import annotations

from collections.abc import Callable

from tuneshift.db import Database
from tuneshift.matching.criteria import (
    DurationCriterion,
    Strength,
    load_token_whitelist,
)
from tuneshift.matching.preferences import Preferences, resolve_preferences
from tuneshift.matching.registry import KNOWN_AXES

_LIST_KEYS = {"prefer", "avoid", "tiebreak_order"}
_FLOAT_KEYS = {"duration_tolerance_percent"}
_INT_KEYS = {"min_lead"}
_VALID_KEYS = _LIST_KEYS | _FLOAT_KEYS | _INT_KEYS

_STRENGTHS = {s.value for s in Strength}
_STRUCTURED_AXES = {"spatial", "mix", "fidelity"}

# Preference polarity: require/prefer pull a candidate up, avoid/forbid push it
# down. Two prefs on the SAME (criterion, canonical target) at the SAME scope with
# OPPOSITE polarity are contradictory (you cannot both prefer and avoid Atmos on
# one playlist) and are rejected at write time (FL3 conflict rule). Cross-scope
# opposite polarity is an intentional override, not a conflict.
_POSITIVE_STRENGTHS = {"require", "prefer"}
_NEGATIVE_STRENGTHS = {"avoid", "forbid"}


def _polarity(strength: str) -> str:
    """Return ``"positive"`` for require/prefer, ``"negative"`` for avoid/forbid."""
    return "positive" if strength in _POSITIVE_STRENGTHS else "negative"


# --------------------------------------------------------------------------- #
# Typed (criterion, strength, target) model — the general AC-CLI1 interface.  #
# --------------------------------------------------------------------------- #


def _resolve_typed_scope(args, db: Database):
    """Resolve the typed-pref scope from the flags present.

    Returns ``(label, scope, playlist_id, track_id)`` where ``scope`` is one of
    ``global`` / ``playlist`` / ``track`` / ``playlist-track``:

    * no flags .................. ``global``
    * ``--playlist`` ............ ``playlist``
    * ``--track`` ............... ``track`` (playlist-agnostic per-track; applies
      to the track on every playlist — stored with a NULL ``playlist_id``)
    * ``--playlist`` ``--track``  ``playlist-track`` (that track on that playlist,
      most specific)

    Returns ``None`` (after printing an error) for an unknown playlist/track.
    """
    playlist = getattr(args, "playlist", None)
    track = getattr(args, "track", None)

    pid = None
    if playlist is not None:
        matches = [p for p in db.list_playlists() if p.name == playlist]
        if not matches:
            print(f'Playlist "{playlist}" not found.')
            return None
        pid = matches[0].id

    if track is not None:
        if db.get_track(track) is None:
            print(f"Track {track} not found.")
            return None
        if pid is not None:
            return (
                f'playlist "{playlist}" track {track}',
                "playlist-track",
                pid,
                track,
            )
        return (f"track {track}", "track", None, track)

    if pid is not None:
        return (f'playlist "{playlist}"', "playlist", pid, None)
    return ("global", "global", None, None)


def _db_playlist_id(scope: str, pid):
    """The ``playlist_id`` argument for the ``playlist_track_prefs`` store.

    Both DB-backed scopes live in that one table: ``playlist-track`` under a
    concrete playlist id, ``track`` (playlist-agnostic) under a NULL id.
    """
    return pid if scope == "playlist-track" else None


def _get_typed_criteria(db: Database, scope: str, pid, tid) -> list[dict]:
    """Read the stored typed criteria for a scope as a list of dicts."""
    if scope in ("playlist-track", "track"):
        return db.get_playlist_track_prefs(_db_playlist_id(scope, pid), tid)
    if scope == "playlist":
        return list((db.get_preferences(pid) or {}).get("criteria") or [])
    return list((db.get_global_preferences() or {}).get("criteria") or [])


def _write_criterion(
    db: Database, scope: str, pid, tid, criterion: str, strength: str, target: str
) -> None:
    """Upsert one typed criterion at ``scope``.

    De-duplication is by ``(criterion, canonical target)`` — NOT by criterion
    alone — so distinct targets on the same axis coexist (``content avoid
    karaoke`` and ``content avoid instrumental``), while a new strength for the
    same canonical target replaces the old entry. Different surface forms of one
    canonical target (``atmos`` / ``Dolby Atmos``) collapse to a single row.
    """
    whitelist = load_token_whitelist()
    canonical = whitelist.canonical(target)

    if scope in ("playlist-track", "track"):
        db_pid = _db_playlist_id(scope, pid)
        # Drop any stored surface form of the same canonical target first so a
        # re-spelled target does not leave a duplicate row behind.
        for row in db.get_playlist_track_prefs(db_pid, tid):
            if (
                row.get("criterion") == criterion
                and whitelist.canonical(row.get("target")) == canonical
                and row.get("target") != target
            ):
                db.remove_playlist_track_pref(db_pid, tid, criterion, row.get("target"))
        db.set_playlist_track_pref(db_pid, tid, criterion, strength, target)
        return

    # JSON-backed scopes (global / playlist): replace the entry for this
    # (criterion, canonical target), keeping every other target on the axis.
    blob = (
        db.get_preferences(pid) if scope == "playlist" else db.get_global_preferences()
    ) or {}
    criteria = [
        c
        for c in (blob.get("criteria") or [])
        if not (
            c.get("criterion") == criterion
            and whitelist.canonical(c.get("target")) == canonical
        )
    ]
    criteria.append({"criterion": criterion, "strength": strength, "target": target})
    blob["criteria"] = criteria
    if scope == "playlist":
        db.set_preferences(pid, blob)
    else:
        db.set_global_preferences(blob)


def _remove_criterion(
    db: Database, scope: str, pid, tid, criterion: str, target: str | None = None
) -> bool:
    """Delete typed criterion rows at ``scope``. Returns True if any was removed.

    With ``target`` omitted, every target on the criterion is removed; with a
    ``target`` given, only that ``(criterion, canonical target)`` entry.
    """
    whitelist = load_token_whitelist()
    if scope in ("playlist-track", "track"):
        db_pid = _db_playlist_id(scope, pid)
        if target is None:
            return db.remove_playlist_track_pref(db_pid, tid, criterion)
        canonical = whitelist.canonical(target)
        removed = False
        for row in db.get_playlist_track_prefs(db_pid, tid):
            if (
                row.get("criterion") == criterion
                and whitelist.canonical(row.get("target")) == canonical
            ):
                removed |= db.remove_playlist_track_pref(
                    db_pid, tid, criterion, row.get("target")
                )
        return removed

    blob = (
        db.get_preferences(pid) if scope == "playlist" else db.get_global_preferences()
    ) or {}
    criteria = blob.get("criteria") or []
    if target is None:
        kept = [c for c in criteria if c.get("criterion") != criterion]
    else:
        canonical = whitelist.canonical(target)
        kept = [
            c
            for c in criteria
            if not (
                c.get("criterion") == criterion
                and whitelist.canonical(c.get("target")) == canonical
            )
        ]
    if len(kept) == len(criteria):
        return False
    if kept:
        blob["criteria"] = kept
    else:
        blob.pop("criteria", None)
    if scope == "playlist":
        db.set_preferences(pid, blob or None)
    else:
        db.set_global_preferences(blob or None)
    return True


def _conflicting_pref(
    db: Database, scope: str, pid, tid, criterion: str, strength: str, target: str
) -> dict | None:
    """Return an existing SAME-scope pref that contradicts this one, else None.

    A contradiction is the same ``(criterion, canonical target)`` set to the
    OPPOSITE polarity at the same scope (e.g. ``spatial prefer atmos`` already
    stored, now setting ``spatial forbid atmos``). Re-setting the same polarity
    (``prefer`` -> ``require``) is a replacement, not a conflict.
    """
    whitelist = load_token_whitelist()
    canonical = whitelist.canonical(target)
    want = _polarity(strength)
    for c in _get_typed_criteria(db, scope, pid, tid):
        if (
            c.get("criterion") == criterion
            and whitelist.canonical(c.get("target")) == canonical
            and _polarity(c.get("strength")) != want
        ):
            return c
    return None


def _handle_typed_set(
    args, db: Database, criterion: str, strength: str, target: str
) -> int:
    """Set a typed (criterion, strength, target) preference at the flagged scope."""
    if criterion not in KNOWN_AXES:
        print(f'Unknown criterion "{criterion}". Valid: {sorted(KNOWN_AXES)}')
        return 1
    if strength not in _STRENGTHS:
        print(f'Unknown strength "{strength}". Valid: {sorted(_STRENGTHS)}')
        return 1
    if not target:
        print("A target token is required: prefs set <criterion> <strength> <target>")
        return 1
    if criterion == "duration" and not DurationCriterion.is_valid_target(target):
        print(
            f'Invalid duration tolerance "{target}". '
            'Use an absolute ("3s" / "3") or relative ("5%") tolerance.'
        )
        return 1

    resolved = _resolve_typed_scope(args, db)
    if resolved is None:
        return 1
    label, scope, pid, tid = resolved

    # Reject a same-scope contradiction (opposite polarity on the same
    # (criterion, canonical target)) loudly rather than silently overwriting.
    conflict = _conflicting_pref(db, scope, pid, tid, criterion, strength, target)
    if conflict is not None:
        print(
            f'Conflict ({label}): "{criterion} {conflict.get("strength")} '
            f'{conflict.get("target")}" is already set and contradicts '
            f'"{criterion} {strength} {target}". '
            f"Unset it first (prefs unset {criterion} {conflict.get('target')}) "
            "or set the opposite polarity at a different scope."
        )
        return 1

    # Warn (do not fail) when a STRUCTURED-audio target is not a known token: it
    # would never match a real candidate. Title axes tolerate free tokens (the
    # whitelist confidence gate demotes an unknown one to a soft signal).
    whitelist = load_token_whitelist()
    if criterion in _STRUCTURED_AXES and whitelist.axis(target) is None:
        print(
            f'Warning: "{target}" is not a known {criterion} token '
            "— it may never match a candidate."
        )

    _write_criterion(db, scope, pid, tid, criterion, strength, target)
    print(f"Set {criterion} {strength} {target} ({label}).")
    return 0


def _handle_typed_unset(
    args, db: Database, criterion: str, target: str | None = None
) -> int:
    """Remove a typed preference for ``criterion`` at the flagged scope.

    With ``target`` given, only that ``(criterion, target)`` entry is removed;
    otherwise every target on the criterion at the scope is removed.
    """
    if not criterion:
        print(
            "Usage: prefs unset <criterion> [<target>] [--playlist NAME] [--track ID]"
        )
        return 1
    resolved = _resolve_typed_scope(args, db)
    if resolved is None:
        return 1
    label, scope, pid, tid = resolved
    if _remove_criterion(db, scope, pid, tid, criterion, target):
        what = f"{criterion} {target}" if target else criterion
        print(f"Unset {what} ({label}).")
    else:
        what = f"{criterion} {target}" if target else criterion
        print(f'No "{what}" preference set ({label}).')
    return 0


def _handle_typed_list(args, db: Database) -> int:
    """List effective typed preferences for the flagged scope, in precedence order.

    Precedence is global < playlist < playlist-track (most specific last / wins).
    Prints each scope's stored criteria and marks the effective winner per axis.
    """
    resolved = _resolve_typed_scope(args, db)
    if resolved is None:
        return 1
    label, scope, pid, tid = resolved

    layers: list[tuple[str, list[dict]]] = [
        ("global", _get_typed_criteria(db, "global", None, None))
    ]
    if scope in ("playlist", "playlist-track"):
        layers.append(("playlist", _get_typed_criteria(db, "playlist", pid, None)))
    if scope in ("track", "playlist-track"):
        layers.append(("track", _get_typed_criteria(db, "track", None, tid)))
    if scope == "playlist-track":
        layers.append(
            ("playlist-track", _get_typed_criteria(db, "playlist-track", pid, tid))
        )

    whitelist = load_token_whitelist()
    # Effective winner per (axis, canonical target): most specific layer wins.
    effective: dict[tuple, tuple[str, int]] = {}
    for layer_name, criteria in layers:
        for idx, c in enumerate(criteria):
            key = (c.get("criterion"), whitelist.canonical(c.get("target")))
            effective[key] = (layer_name, idx)

    print(
        f"Effective version preferences ({label}), precedence "
        "global < playlist < track < playlist-track:"
    )
    any_shown = False
    for layer_name, criteria in layers:
        if not criteria:
            continue
        any_shown = True
        print(f"  [{layer_name}]")
        for idx, c in enumerate(criteria):
            key = (c.get("criterion"), whitelist.canonical(c.get("target")))
            active = effective.get(key) == (layer_name, idx)
            marker = "*" if active else " "
            note = "" if active else "  (overridden)"
            print(
                f"    {marker} {c.get('criterion')} {c.get('strength')} "
                f"{c.get('target')}{note}"
            )
    if not any_shown:
        print("    (none set)")
    return 0


# --------------------------------------------------------------------------- #
# Legacy version.<field> keyword-list model (backward compatible).            #
# --------------------------------------------------------------------------- #


def _parse_value(key: str, raw: str):
    """Parse a raw legacy CLI value into the stored type for ``key``."""
    if key in _LIST_KEYS:
        return [t.strip().lower() for t in raw.split(",") if t.strip()]
    if key in _FLOAT_KEYS:
        return float(raw)
    return int(raw)


def _resolve_scope(
    args, db: Database
) -> tuple[str, Callable[[], dict | None], Callable[[dict | None], None], str] | None:
    """Resolve the selected legacy preference layer.

    Returns ``(label, getter, setter, layer)`` where ``layer`` is one of
    ``global``/``playlist``/``track``. Returns ``None`` after printing an error
    when the target playlist/track does not exist.
    """
    playlist = getattr(args, "playlist", None)
    track = getattr(args, "track", None)
    if playlist is not None:
        matches = [p for p in db.list_playlists() if p.name == playlist]
        if not matches:
            print(f'Playlist "{playlist}" not found.')
            return None
        pid = matches[0].id
        return (
            f'playlist "{playlist}"',
            lambda: db.get_preferences(pid),
            lambda d: db.set_preferences(pid, d),
            "playlist",
        )
    if track is not None:
        print(
            "Per-track legacy keyword preferences have been retired. Use the "
            "typed model instead, e.g. "
            "prefs set <criterion> <strength> <target> --track "
            f"{track} (add --playlist NAME for a playlist-specific override)."
        )
        return None
    return (
        "global",
        db.get_global_preferences,
        db.set_global_preferences,
        "global",
    )


def _print_preferences(prefs: Preferences) -> None:
    print(f"    prefer                      = {', '.join(prefs.prefer) or '(none)'}")
    print(f"    avoid                       = {', '.join(prefs.avoid) or '(none)'}")
    print(f"    duration_tolerance_percent  = {prefs.duration_tolerance_percent}")
    print(
        f"    tiebreak_order              = {', '.join(prefs.tiebreak_order) or '(none)'}"
    )
    print(f"    min_lead                    = {prefs.min_lead}")


def _effective(db: Database, stored: dict | None, layer: str) -> Preferences:
    """Cascade-resolve the effective legacy prefs for the selected layer."""
    global_prefs = db.get_global_preferences()
    if layer == "global":
        return resolve_preferences(stored, None, None)
    if layer == "playlist":
        return resolve_preferences(global_prefs, stored, None)
    return resolve_preferences(global_prefs, None, stored)


def _handle_legacy_set(args, db: Database) -> int:
    parts = args.key.split(".", 1)
    if len(parts) != 2 or parts[0] != "version":
        print(f'Key must be "version.<field>": {args.key}')
        return 1
    field = parts[1]
    if field not in _VALID_KEYS:
        print(f'Unknown field "{field}". Valid: {sorted(_VALID_KEYS)}')
        return 1
    try:
        value = _parse_value(field, args.value)
    except ValueError:
        print(f'Invalid value for {args.key}: "{args.value}"')
        return 1
    resolved = _resolve_scope(args, db)
    if resolved is None:
        return 1
    label, getter, setter, _ = resolved
    stored = getter() or {}
    stored[field] = value
    setter(stored)
    shown = ", ".join(value) if isinstance(value, list) else value
    print(f"Set {args.key} = {shown} ({label}).")
    return 0


def handle_prefs(args, db: Database) -> int:
    """Show, set, unset, list or clear DB-backed version preferences."""
    criterion = getattr(args, "key", None)
    strength = getattr(args, "value", None)
    target = getattr(args, "target", None)

    if args.action == "set":
        if not criterion:
            print(
                "Usage: prefs set <criterion> <strength> <target>  "
                "(or legacy prefs set version.<field> <value>)"
            )
            return 1
        # Typed grammar when the 2nd token names a strength; legacy otherwise.
        if strength in _STRENGTHS:
            return _handle_typed_set(args, db, criterion, strength, target)
        if criterion.startswith("version."):
            if strength is None:
                print("Usage: prefs set version.<field> <value>")
                return 1
            return _handle_legacy_set(args, db)
        print(
            f'Unknown strength "{strength}". '
            f"Use: prefs set <criterion> <{'|'.join(sorted(_STRENGTHS))}> <target>, "
            'or the legacy "prefs set version.<field> <value>".'
        )
        return 1

    if args.action == "unset":
        return _handle_typed_unset(args, db, criterion, target=strength)

    if args.action == "list":
        return _handle_typed_list(args, db)

    track_flag = getattr(args, "track", None) is not None

    if args.action == "show":
        # The typed cascade is the authoritative model — always show it (this is
        # what the matcher reads, and it renders playlist-track + track scopes,
        # fixing the historical `show --track` gap). For global/playlist we ALSO
        # render any legacy keyword blob for backward compatibility.
        rc = _handle_typed_list(args, db)
        if rc != 0:
            return rc
        if not track_flag:
            resolved = _resolve_scope(args, db)
            if resolved is None:
                return 1
            label, getter, setter, layer = resolved
            stored = getter()
            legacy_keys = [
                k
                for k in (
                    "prefer",
                    "avoid",
                    "tiebreak_order",
                    "duration_tolerance_percent",
                    "min_lead",
                )
                if stored and k in stored
            ]
            if legacy_keys:
                print(f"\nLegacy keyword preferences ({label}):")
                for key in legacy_keys:
                    val = stored[key]
                    shown = ", ".join(val) if isinstance(val, list) else val
                    print(f"    {key} = {shown}")
                print(f"\nEffective legacy preferences ({label}, cascade-resolved):")
                _print_preferences(_effective(db, stored, layer))
        return 0

    if args.action == "clear":
        if track_flag:
            print(
                "Per-track 'clear' is retired — use "
                "prefs unset <criterion> [<target>] --track ID "
                "(add --playlist NAME for a playlist-specific override)."
            )
            return 1
        resolved = _resolve_scope(args, db)
        if resolved is None:
            return 1
        label, getter, setter, layer = resolved
        # Clear only the legacy keyword keys; preserve typed `criteria` and the
        # `concept` blob so a legacy clear never silently drops typed prefs.
        stored = getter() or {}
        for key in (
            "prefer",
            "avoid",
            "tiebreak_order",
            "duration_tolerance_percent",
            "min_lead",
        ):
            stored.pop(key, None)
        setter(stored or None)
        print(f"Cleared legacy keyword preferences ({label}).")
        return 0

    print(f"Unknown action: {args.action}")
    return 1
