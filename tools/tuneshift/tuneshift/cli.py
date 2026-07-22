"""Command-line entry point for tuneshift."""

import argparse
import logging
import os
import sys

from tuneshift import TuneShiftError, __version__
from tuneshift.db import Database


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level CLI parser with all subcommands."""
    parser = argparse.ArgumentParser(
        prog="tuneshift",
        description="Canonical playlist manager with cross-platform distribution",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "--db",
        type=str,
        default=None,
        help="Path to database file (default: auto-detect)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Show per-source detail (MB / Last.fm / Genius / LLM); repeatable",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Suppress progress output; show only warnings and errors",
    )

    sub = parser.add_subparsers(dest="command")

    # ingest
    p_ingest = sub.add_parser("ingest", help="Import a playlist from a platform")
    p_ingest.add_argument("platform", choices=["tidal", "spotify", "ytmusic"])
    p_ingest.add_argument("playlist_id", help="Platform-specific playlist ID or URL")

    # sync
    p_sync = sub.add_parser(
        "sync", help="Plan (or --apply) a routed push of a playlist to a platform"
    )
    p_sync.add_argument("playlist", nargs="?", help="Playlist name")
    p_sync.add_argument("platform", nargs="?", help="Target platform")
    p_sync.add_argument("--all", action="store_true", help="Sync all playlists")
    p_sync.add_argument(
        "--reconcile", action="store_true", help="Force re-reconciliation"
    )
    p_sync.add_argument(
        "--apply",
        action="store_true",
        help="Build and apply the push in one step (default writes a plan and "
        "pushes nothing - AC-P1)",
    )
    p_sync.add_argument(
        "--interactive",
        action="store_true",
        help="With --apply, step through each push before applying (AC-P2)",
    )

    # diff
    p_diff = sub.add_parser("diff", help="Show what would change on sync")
    p_diff.add_argument("playlist", help="Playlist name")
    p_diff.add_argument("platform", nargs="?", help="Target platform")

    # add
    p_add = sub.add_parser("add", help="Add a track to a playlist")
    p_add.add_argument("playlist", help="Playlist name (created if new)")
    p_add.add_argument("title", help="Track title")
    p_add.add_argument("artist", help="Artist name")
    p_add.add_argument("--album", help="Album name")
    p_add.add_argument(
        "--replace", help="Title of track to replace (inherits position and pins)"
    )

    # rm
    p_rm = sub.add_parser("rm", help="Remove a track from a playlist")
    p_rm.add_argument("playlist", help="Playlist name")
    p_rm.add_argument("target", help="Position number or title substring")

    # login
    p_login = sub.add_parser("login", help="Authenticate with a platform")
    p_login.add_argument("platform", choices=["tidal", "spotify", "ytmusic"])

    # status
    p_status = sub.add_parser("status", help="Show playlist status")
    p_status.add_argument("playlist", nargs="?", help="Playlist name (all if omitted)")

    # list
    sub.add_parser("list", help="List all playlists")

    # order
    p_order = sub.add_parser("order", help="Reorder playlist by energy arc")
    p_order.add_argument("playlist", help="Playlist name")
    p_order.add_argument("--arc", default="wave", help="Arc shape (default: wave)")
    p_order.add_argument("--weights", help="Weight preset name or JSON dict")
    p_order.add_argument(
        "--dry-run", action="store_true", help="Show proposed order without applying"
    )
    p_order.add_argument(
        "--no-sync", action="store_true", help="Skip pushing to platforms"
    )
    p_order.add_argument(
        "--auto-on", action="store_true", help="Enable auto-reorder on sync"
    )
    p_order.add_argument(
        "--auto-off", action="store_true", help="Disable auto-reorder on sync"
    )

    # pin
    p_pin = sub.add_parser(
        "pin", help="Pin tracks as openers, closers, or adjacency groups"
    )
    p_pin.add_argument("playlist", help="Playlist name")
    p_pin.add_argument("--opener", metavar="TITLE", help="Pin track as opener")
    p_pin.add_argument("--closer", metavar="TITLE", help="Pin track as closer")
    p_pin.add_argument(
        "--position",
        nargs=2,
        metavar=("INDEX", "TITLE"),
        help="Pin track to specific position (0-based)",
    )
    p_pin.add_argument(
        "--adjacent",
        nargs="+",
        metavar="TITLE",
        help="Pin tracks as adjacent group (in order)",
    )
    p_pin.add_argument(
        "--group", metavar="NAME", help="Name for adjacency group (default: auto)"
    )
    p_pin.add_argument(
        "--moment",
        metavar="TITLE",
        help="Pin track as a narrative moment (placed at climax)",
    )
    p_pin.add_argument("--remove", metavar="TITLE", help="Remove pin from track")
    p_pin.add_argument(
        "--list", action="store_true", dest="list_pins", help="Show current pins"
    )

    # resolve
    p_resolve = sub.add_parser(
        "resolve", help="Resolve tracks to platform candidates + hydrate metadata"
    )
    p_resolve.add_argument("playlist", nargs="?", help="Playlist name to resolve")
    p_resolve.add_argument(
        "--track", nargs=2, metavar=("TITLE", "ARTIST"), help="Resolve single track"
    )
    p_resolve.add_argument(
        "--all", action="store_true", help="Resolve all unresolved tracks"
    )
    p_resolve.add_argument(
        "--platform",
        default="tidal",
        help="Platform to resolve against (default: tidal)",
    )
    p_resolve.add_argument(
        "--upgrade", action="store_true", help="Re-resolve tracks below CONFIRMED"
    )
    p_resolve.add_argument(
        "--force", action="store_true", help="Re-resolve tracks already resolved"
    )
    p_resolve.add_argument(
        "--status", action="store_true", help="Show resolution statistics"
    )
    p_resolve.add_argument(
        "--verbose", "-v", action="store_true", help="Show skipped tracks"
    )
    p_resolve.add_argument(
        "--throttle",
        type=float,
        default=None,
        metavar="OPS_PER_SEC",
        help="Cap resolve to N operations/second (default: 3.0)",
    )

    # import-text
    p_import_text = sub.add_parser(
        "import-text", help="Import playlist from a text file"
    )
    p_import_text.add_argument("file", help="Path to playlist text file")
    p_import_text.add_argument("--name", help="Override playlist name")
    p_import_text.add_argument(
        "--force", action="store_true", help="Overwrite existing playlist"
    )

    # enrich
    p_enrich = sub.add_parser(
        "enrich", help="Fetch audio metadata and/or classify tracks"
    )
    p_enrich.add_argument(
        "playlist",
        nargs="?",
        default=None,
        help="Playlist name (omit with --all to enrich every playlist)",
    )
    p_enrich.add_argument(
        "--all",
        action="store_true",
        help="Enrich Tidal catalog metadata for every playlist (slow, retries on rate limits)",  # noqa: E501
    )
    p_enrich.add_argument(
        "--catalog",
        action="store_true",
        help="Fetch Tidal catalog metadata (Atmos, release year, genres, quality) with retry",  # noqa: E501
    )
    p_enrich.add_argument(
        "--platform", default=None, help="Source platform for audio metadata (BPM, key)"
    )
    p_enrich.add_argument(
        "--classify",
        action="store_true",
        help="Run LLM classification for narrative fields",
    )
    p_enrich.add_argument(
        "--reclassify",
        action="store_true",
        help="Force re-classify all tracks (overwrites existing)",
    )
    p_enrich.add_argument("--model", help="Override LLM model for classification")
    p_enrich.add_argument(
        "--max-retries",
        type=int,
        default=3,
        help="Max retry attempts per track on rate limit/transient errors (default: 3, 0 = skip on error)",  # noqa: E501
    )
    p_enrich.add_argument(
        "--refresh", action="store_true", help="Re-fetch even if metadata is cached"
    )
    p_enrich.add_argument(
        "--dry-run",
        action="store_true",
        help="With --all: show what would be enriched without making API calls",
    )

    # doctor
    p_doctor = sub.add_parser(
        "doctor", help="Scan playlists for mapping issues and apply fixes"
    )
    p_doctor.add_argument(
        "playlist",
        nargs="?",
        default=None,
        help="Playlist name (omit with --all to scan every playlist)",
    )
    p_doctor.add_argument(
        "--all", action="store_true", help="Scan every playlist in the database"
    )
    p_doctor.add_argument(
        "--orphans",
        action="store_true",
        help="List orphaned tracks (no mapping, no queue, not quarantined)",
    )
    p_doctor.add_argument(
        "--enqueue-orphans",
        action="store_true",
        help="Enqueue orphaned tracks for resolution",
    )
    p_doctor.add_argument(
        "--apply",
        action="store_true",
        help="Apply the previously written plan instead of scanning",
    )
    p_doctor.add_argument(
        "--only",
        type=int,
        action="append",
        metavar="ITEM_ID",
        help="With --apply: only apply the given plan item id(s)",
    )
    p_doctor.add_argument(
        "--override",
        action="append",
        metavar="ITEM_ID=TIDAL_ID",
        help="With --apply: override an item's fix (remap id, or keep-track id for duplicates)",  # noqa: E501
    )
    p_doctor.add_argument(
        "--no-sync",
        action="store_true",
        help="With --apply: skip the best-effort Tidal re-sync",
    )
    p_doctor.add_argument(
        "--dry-run",
        action="store_true",
        help="With --apply: preview what would change without applying",
    )
    p_doctor.add_argument(
        "--max-retries",
        type=int,
        default=3,
        help="Max retry attempts per track on rate limit/transient errors (default: 3)",
    )
    p_doctor.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="With --apply: skip the confirmation prompt",
    )
    p_doctor.add_argument(
        "--quiet", action="store_true", help="Suppress progress output"
    )

    # narrative
    p_narrative = sub.add_parser(
        "narrative", help="Set or show the intended narrative arc for a playlist"
    )
    p_narrative.add_argument("playlist", help="Playlist name")
    p_narrative.add_argument(
        "text", nargs="?", help="Narrative description (omit to show current)"
    )
    p_narrative.add_argument("-f", "--file", help="Read narrative from file")
    p_narrative.add_argument("--clear", action="store_true", help="Remove narrative")

    # export
    p_export = sub.add_parser("export", help="Export playlist to file")
    p_export.add_argument("playlist", help="Playlist name")
    p_export.add_argument(
        "-f",
        "--format",
        default="text",
        choices=["text", "csv", "json", "soundiiz", "tunemymusic"],
        help="Output format (default: text)",
    )
    p_export.add_argument(
        "-o", "--output", default="-", help="Output file (default: stdout)"
    )

    # import-json
    p_import_json = sub.add_parser(
        "import-json", help="Restore a playlist from an exported JSON snapshot"
    )
    p_import_json.add_argument(
        "file", help="Path to a JSON file from `export --format json`"
    )
    p_import_json.add_argument(
        "--into", default=None, help="Restore under a different playlist name"
    )

    # map
    p_map = sub.add_parser("map", help="Manually map a track to a platform ID")
    p_map.add_argument(
        "playlist", nargs="?", help="Playlist name (omit when using --track-id)"
    )
    p_map.add_argument(
        "title",
        nargs="?",
        help="Track title, substring match (omit when using --track-id)",
    )
    p_map.add_argument(
        "--track-id", type=int, help="Canonical track id (maps without playlist/title)"
    )
    p_map.add_argument("--tidal", help="Tidal track ID")
    p_map.add_argument("--ytmusic", help="YouTube Music video ID")
    p_map.add_argument(
        "--verify", action="store_true", help="Verify ID exists on platform"
    )
    p_map.add_argument(
        "--dry-run",
        action="store_true",
        help="Show the intended mapping without writing",
    )

    # unmap
    p_unmap = sub.add_parser("unmap", help="Remove a manual platform mapping")
    p_unmap.add_argument("playlist", help="Playlist name")
    p_unmap.add_argument("title", help="Track title (substring match)")
    p_unmap.add_argument("--tidal", action="store_true", help="Remove Tidal mapping")
    p_unmap.add_argument(
        "--ytmusic", action="store_true", help="Remove YouTube Music mapping"
    )

    # lock - routed identity lock (global default or per-playlist override)
    p_lock = sub.add_parser(
        "lock",
        help="Lock a track to a specific platform release (routed via plan/apply)",
    )
    p_lock.add_argument(
        "playlist", nargs="?", help="Playlist name (track lookup / --scope playlist)"
    )
    p_lock.add_argument(
        "title", nargs="?", help="Track title, substring match (omit with --track-id)"
    )
    p_lock.add_argument(
        "--track-id", type=int, help="Canonical track id (locks without playlist/title)"
    )
    p_lock.add_argument("--tidal", help="Tidal track ID to lock to")
    p_lock.add_argument("--ytmusic", help="YouTube Music video ID to lock to")
    p_lock.add_argument(
        "--scope",
        choices=["global", "playlist"],
        default="global",
        help="global default lock (default) or per-playlist override",
    )
    p_lock.add_argument(
        "--list",
        action="store_true",
        dest="list_locks",
        help="List effective locks with precedence (optionally scoped "
        "to the positional playlist); ignores other lock args",
    )
    p_lock.add_argument(
        "--apply",
        action="store_true",
        help="Apply immediately instead of writing a plan",
    )
    p_lock.add_argument(
        "--interactive",
        action="store_true",
        help="Step through the change before applying",
    )

    # unlock - release an identity lock (global default or per-playlist override)
    p_unlock = sub.add_parser(
        "unlock", help="Release an identity lock (routed via plan/apply)"
    )
    p_unlock.add_argument(
        "playlist", nargs="?", help="Playlist name (track lookup / --scope playlist)"
    )
    p_unlock.add_argument(
        "title", nargs="?", help="Track title, substring match (omit with --track-id)"
    )
    p_unlock.add_argument(
        "--track-id",
        type=int,
        help="Canonical track id (unlocks without playlist/title)",
    )
    p_unlock.add_argument("--tidal", action="store_true", help="Release the Tidal lock")
    p_unlock.add_argument(
        "--ytmusic", action="store_true", help="Release the YouTube Music lock"
    )
    p_unlock.add_argument(
        "--scope",
        choices=["global", "playlist"],
        default="global",
        help="global default lock (default) or per-playlist override",
    )
    p_unlock.add_argument(
        "--apply",
        action="store_true",
        help="Apply immediately instead of writing a plan",
    )
    p_unlock.add_argument(
        "--interactive",
        action="store_true",
        help="Step through the change before applying",
    )

    # edit
    p_edit = sub.add_parser("edit", help="Edit track metadata (title/artist/album)")
    p_edit.add_argument(
        "track_id", nargs="?", type=int, help="Canonical track id to edit"
    )
    p_edit.add_argument(
        "--playlist", help="Playlist name (for batch --strip-album-from-title)"
    )
    p_edit.add_argument("--title", help="New track title")
    p_edit.add_argument("--artist", help="New track artist")
    p_edit.add_argument("--album", help="New track album")
    p_edit.add_argument(
        "--energy", type=float, help="Set energy 0.0-1.0 (manual override, AC8)"
    )
    p_edit.add_argument(
        "--valence", type=float, help="Set valence 0.0-1.0 (manual override, AC8)"
    )
    p_edit.add_argument(
        "--strip-album-from-title",
        action="store_true",
        help="Remove a trailing parenthetical that repeats the album name",
    )
    p_edit.add_argument(
        "--dry-run", action="store_true", help="Show changes without writing"
    )

    # explain (formerly `why`)
    p_explain = sub.add_parser(
        "explain",
        help="Explain a track's match decision (criteria, breakdown, rejections)",
    )
    p_explain.add_argument("track_id", type=int, help="Canonical track id to explain")
    p_explain.add_argument(
        "playlist",
        nargs="?",
        help="Playlist to scope the explanation to (default: global decision)",
    )
    p_explain.add_argument(
        "--platform",
        choices=["spotify", "tidal", "ytmusic"],
        help="Limit to one platform (default: all with a stored decision)",
    )
    p_explain.add_argument(
        "--live",
        action="store_true",
        help="Reconcile now against the platform(s) instead of reading the "
        "stored decision (requires login)",
    )

    # why (deprecated alias for explain)
    p_why = sub.add_parser("why", help="[deprecated] alias for `explain`")
    p_why.add_argument("track_id", type=int, help="Canonical track id to explain")
    p_why.add_argument(
        "playlist",
        nargs="?",
        help="Playlist to scope the explanation to (default: global decision)",
    )
    p_why.add_argument(
        "--platform",
        choices=["spotify", "tidal", "ytmusic"],
        help="Limit to one platform (default: all with a stored decision)",
    )
    p_why.add_argument(
        "--live",
        action="store_true",
        help="Reconcile now against the platform(s) instead of reading the "
        "stored decision (requires login)",
    )

    # triage
    p_triage = sub.add_parser(
        "triage",
        help="Cluster tracks needing attention for bulk review + show review burden",
    )
    p_triage.add_argument(
        "playlist", nargs="?", help="Limit to one playlist (default: all playlists)"
    )
    p_triage.add_argument(
        "--platform",
        choices=["spotify", "tidal", "ytmusic"],
        help="Limit to one platform (default: all)",
    )

    # goal
    p_goal = sub.add_parser("goal", help="Set or show playlist goal/theme")
    p_goal.add_argument("playlist", help="Playlist name")
    p_goal.add_argument("text", nargs="?", help="Goal text to set")
    p_goal.add_argument("--clear", action="store_true", help="Clear the goal")

    # weights
    p_weights = sub.add_parser("weights", help="Manage sequencing weight presets")
    p_weights.add_argument(
        "action", nargs="?", default="list", choices=["list", "set", "show"]
    )
    p_weights.add_argument("playlist", nargs="?", help="Playlist name")
    p_weights.add_argument("--preset", help="Named preset to apply")
    p_weights.add_argument("values", nargs="*", help="dimension=value pairs")

    # curate
    p_curate = sub.add_parser("curate", help="Curate playlist (trim/analyze/fill)")
    p_curate.add_argument("playlist", help="Playlist name")
    p_curate.add_argument(
        "mode", choices=["trim", "analyze", "fill"], help="Curation mode"
    )
    p_curate.add_argument(
        "--dry-run", action="store_true", help="Preview without applying"
    )
    p_curate.add_argument(
        "--strategy",
        default="quick",
        choices=["quick", "hybrid", "deep"],
        help="Curation strategy",
    )
    p_curate.add_argument(
        "--target-tracks", type=int, help="Target track count for trim"
    )
    p_curate.add_argument("--hard-limit", type=int, help="Hard limit track count")

    # prefs
    p_prefs = sub.add_parser(
        "prefs",
        help="Manage version preferences (typed criterion/strength/target at "
        "global/playlist/playlist-track scope)",
    )
    p_prefs.add_argument(
        "action",
        choices=["show", "set", "unset", "list", "clear"],
        help="set/unset/list typed prefs; show/clear the legacy keyword model",
    )
    p_prefs.add_argument(
        "key",
        nargs="?",
        help="criterion axis (spatial/mix/fidelity/performance/content/edit/"
        "production) - or legacy version.<field>",
    )
    p_prefs.add_argument(
        "value",
        nargs="?",
        help="strength (require/prefer/avoid/forbid) - or legacy value",
    )
    p_prefs.add_argument(
        "target",
        nargs="?",
        help="target token for a typed pref (e.g. atmos, live, remaster)",
    )
    p_prefs.add_argument(
        "--global",
        dest="global_scope",
        action="store_true",
        help="Target global defaults (default when no scope flag is given)",
    )
    p_prefs.add_argument("--playlist", help="Target a playlist by name")
    p_prefs.add_argument(
        "--track",
        type=int,
        help="Target a track (typed prefs: combine with --playlist for "
        "playlist-track scope)",
    )

    # alias
    p_alias = sub.add_parser(
        "alias", help="Manage artist-alias equivalence classes (98\u00b0 / 98 Degrees)"
    )
    alias_sub = p_alias.add_subparsers(dest="action", required=True)
    alias_sub.add_parser("list", help="List all alias classes")
    p_alias_show = alias_sub.add_parser(
        "show", help="Show the class an artist belongs to"
    )
    p_alias_show.add_argument("artist", help="Artist name to look up")
    p_alias_add = alias_sub.add_parser(
        "add", help="Create/extend a class from >=2 members"
    )
    p_alias_add.add_argument(
        "members", nargs="+", help="Two or more artist surface forms"
    )
    p_alias_remove = alias_sub.add_parser(
        "remove", help="Remove a member from its class"
    )
    p_alias_remove.add_argument("member", help="Artist surface form to remove")

    # share
    p_share = sub.add_parser("share", help="Generate shareable links for a playlist")
    p_share.add_argument("name", help="Playlist name")
    p_share.add_argument(
        "--format",
        choices=["plain", "markdown", "slack", "discord", "urls"],
        default="plain",
        help="Output format (default: plain)",
    )

    # link
    p_link = sub.add_parser(
        "link", help="Link platform playlist IDs (auto-discover or manual)"
    )
    p_link.add_argument("platform", choices=["tidal", "spotify", "ytmusic"])
    p_link.add_argument("name", nargs="?", help="Playlist name (for manual link)")
    p_link.add_argument(
        "url", nargs="?", help="Platform URL or playlist ID (for manual link)"
    )
    p_link.add_argument(
        "--quiet", "-q", action="store_true", help="Suppress 'not found' messages"
    )

    # compose
    p_compose = sub.add_parser("compose", help="Narrative-driven playlist composition")
    p_compose.add_argument("playlist", help="Playlist name")
    p_compose.add_argument(
        "--analyze", action="store_true", help="Analyze only (gap report)"
    )
    p_compose.add_argument("--reorder", action="store_true", help="Reorder only")
    p_compose.add_argument(
        "--fill-gaps", action="store_true", help="Find candidates for gaps"
    )
    p_compose.add_argument(
        "--dry-run", action="store_true", help="Preview without applying"
    )
    p_compose.add_argument(
        "--apply", action="store_true", help="Apply changes to playlist"
    )

    # concept
    p_concept = sub.add_parser("concept", help="Set or show playlist concept/theme")
    p_concept.add_argument("playlist", help="Playlist name")
    p_concept.add_argument("--theme", help="Playlist theme")
    p_concept.add_argument("--require", help="Add a hard rule")
    p_concept.add_argument("--prefer", help="Add a soft rule")
    p_concept.add_argument("--show", action="store_true", help="Show current concept")
    p_concept.add_argument("--clear", action="store_true", help="Clear concept")

    # review
    p_review = sub.add_parser("review", help="Review playlist for concept compliance")
    p_review.add_argument("playlist", help="Playlist name")
    p_review.add_argument(
        "--fix", action="store_true", help="Remove tracks that violate hard rules"
    )
    p_review.add_argument(
        "--accept-track",
        type=int,
        metavar="TRACK_ID",
        help="Accept (suppress) concept findings for this track id across runs",
    )
    p_review.add_argument(
        "--rule",
        metavar="RULE",
        help="With --accept-track: accept only this specific rule (default: all "
        "current findings for the track)",
    )
    p_review.add_argument(
        "--list-accepted",
        action="store_true",
        help="List accepted (track, rule) pairs for the playlist and exit",
    )

    # config
    p_config = sub.add_parser("config", help="Configure TuneShift settings")
    p_config.add_argument(
        "key",
        nargs="?",
        help="Config key (e.g., anthropic-key, openai-key, llm-backend)",
    )
    p_config.add_argument("value", nargs="?", help="Config value")
    p_config.add_argument(
        "--show", action="store_true", help="Show current LLM configuration"
    )

    # batch
    p_batch = sub.add_parser(
        "batch", help="Batch playlist operations (plan/apply model)"
    )
    p_batch.add_argument("playlist", nargs="?", help="Playlist name")
    p_batch.add_argument(
        "--dedupe", action="store_true", help="Flag artists with more than --cap tracks"
    )
    p_batch.add_argument(
        "--cap",
        type=int,
        default=1,
        help="Max tracks per artist for --dedupe (default: 1)",
    )
    p_batch.add_argument(
        "--rm-artist", help="Remove all tracks by an artist (including features)"
    )
    p_batch.add_argument(
        "--rm",
        action="append",
        help="Remove a track (repeatable: --rm 'Title - Artist')",
    )
    p_batch.add_argument(
        "--add",
        action="append",
        help="Add a track (repeatable: --add 'Title - Artist')",
    )
    p_batch.add_argument(
        "--review-findings", action="store_true", help="Plan fixes from concept review"
    )
    p_batch.add_argument(
        "--sweep-banned", action="store_true", help="Sweep for banned artists"
    )
    p_batch.add_argument(
        "--split", help="Split matching tracks into a new playlist (name)"
    )
    p_batch.add_argument(
        "--filter",
        action="append",
        help="Filter for --split (artist:X, vibe:X, energy:<0.4)",
    )
    p_batch.add_argument(
        "--rebuild", action="store_true", help="Concept-driven rebuild (review + fill)"
    )
    p_batch.add_argument(
        "--count", type=int, default=50, help="Target track count for --rebuild"
    )
    p_batch.add_argument(
        "--fresh", action="store_true", help="Rebuild from scratch (clear all first)"
    )
    p_batch.add_argument(
        "--structure", action="store_true", help="Retroactive narrative structuring"
    )
    p_batch.add_argument(
        "--narrative-file",
        help="Narrative file for --structure (user-provided sections)",
    )
    p_batch.add_argument(
        "--plan", action="store_true", help="Generate plan (no changes)"
    )
    p_batch.add_argument("--plan-file", help="Load operations from a plan file")
    p_batch.add_argument(
        "--from-stdin", action="store_true", help="Read operations from stdin"
    )
    p_batch.add_argument("--show-plan", action="store_true", help="Show current plan")
    p_batch.add_argument("--apply", action="store_true", help="Apply current plan")
    p_batch.add_argument("--discard", action="store_true", help="Discard current plan")
    p_batch.add_argument(
        "--undo", action="store_true", help="Undo a batch (operation-based)"
    )
    p_batch.add_argument("--id", type=int, help="History ID for --undo")
    p_batch.add_argument("--history", nargs="?", const=True, help="Show batch history")
    p_batch.add_argument(
        "--interactive",
        action="store_true",
        help="Walk through decisions one at a time",
    )

    # plan (terraform-style plan/apply engine: sync/rematch/migrate routes)
    p_plan = sub.add_parser(
        "plan", help="Generate, inspect, apply, or roll back a plan/apply plan"
    )
    plan_sub = p_plan.add_subparsers(dest="action", required=True)

    p_plan_sync = plan_sub.add_parser("sync", help="Plan a playlist push to a platform")
    p_plan_sync.add_argument("playlist", help="Playlist name")
    p_plan_sync.add_argument(
        "--platform", default="tidal", help="Target platform (default: tidal)"
    )
    p_plan_sync.add_argument(
        "--reconcile", action="store_true", help="Force re-reconcile (ignore cache)"
    )

    p_plan_rematch = plan_sub.add_parser(
        "rematch", help="Plan re-matching a playlist's tracks"
    )
    p_plan_rematch.add_argument("playlist", help="Playlist name")
    p_plan_rematch.add_argument(
        "--platform", default="tidal", help="Target platform (default: tidal)"
    )
    p_plan_rematch.add_argument(
        "--reconcile", action="store_true", help="Force re-reconcile (ignore cache)"
    )

    p_plan_migrate = plan_sub.add_parser(
        "migrate", help="Plan migration of stale global mappings"
    )
    p_plan_migrate.add_argument(
        "--platform", default="tidal", help="Target platform (default: tidal)"
    )

    p_plan_heal = plan_sub.add_parser(
        "heal", help="Plan a routed self-heal of dead identity locks (AC-L3)"
    )
    p_plan_heal.add_argument(
        "--platform", default="tidal", help="Target platform (default: tidal)"
    )
    p_plan_heal.add_argument(
        "--playlist", help="Limit to one playlist's override locks"
    )

    plan_sub.add_parser("list", help="List saved plans")

    p_plan_show = plan_sub.add_parser("show", help="Show a saved plan")
    p_plan_show.add_argument("plan_id", help="Plan id")

    p_plan_reject = plan_sub.add_parser(
        "reject", help="Reject a single change in a plan"
    )
    p_plan_reject.add_argument("plan_id", help="Plan id")
    p_plan_reject.add_argument("change_id", type=int, help="Change id to reject")

    p_plan_apply = plan_sub.add_parser("apply", help="Apply a saved plan")
    p_plan_apply.add_argument("plan_id", help="Plan id")
    p_plan_apply.add_argument(
        "--include-locked", action="store_true", help="Also apply locked changes"
    )
    p_plan_apply.add_argument(
        "--interactive", action="store_true", help="Step through changes accept/reject"
    )

    p_plan_rollback = plan_sub.add_parser("rollback", help="Roll back an applied plan")
    p_plan_rollback.add_argument("plan_id", help="Plan id")

    # ban
    p_ban = sub.add_parser("ban", help="Manage the global banned artist list")
    p_ban.add_argument("artist", nargs="?", help="Artist name to ban")
    p_ban.add_argument("--reason", help="Reason for banning")
    p_ban.add_argument("--list", action="store_true", help="List all banned artists")
    p_ban.add_argument("--remove", help="Remove an artist from the ban list")

    # merge (separate command since it takes multiple playlist args)
    p_merge = sub.add_parser("merge", help="Merge playlists into one")
    p_merge.add_argument("sources", nargs="+", help="Source playlist names")
    p_merge.add_argument("--into", required=True, help="Target playlist name")
    p_merge.add_argument(
        "--plan", action="store_true", help="Generate plan (no changes)"
    )
    p_merge.add_argument(
        "--delete-sources",
        action="store_true",
        help="Delete source playlists after merge",
    )

    # audit
    p_audit = sub.add_parser("audit", help="Full playlist health audit (all checks)")
    p_audit.add_argument("playlist", nargs="?", help="Playlist name (omit for all)")
    p_audit.add_argument(
        "--matching-only",
        action="store_true",
        help="Only check platform mapping quality",
    )
    p_audit.add_argument(
        "--vibes-only", action="store_true", help="Only check vibe outliers"
    )
    p_audit.add_argument(
        "--concept-only", action="store_true", help="Only check concept rules"
    )
    p_audit.add_argument(
        "--fix", action="store_true", help="Generate batch plan from findings"
    )

    # tag / untag (playlist collections + track tags)
    p_tag = sub.add_parser("tag", help="Tag playlists or tracks")
    p_tag_sub = p_tag.add_subparsers(dest="tag_action")
    # Default: tag playlist with collection (backward compat via positional args)
    p_tag.add_argument(
        "target",
        nargs="?",
        help="Playlist name (or 'track'/'query'/'derive'/'list-tags')",
    )
    p_tag.add_argument(
        "value", nargs="?", help="Collection name (for playlist tagging)"
    )
    # Track subcommand
    p_tag_track = p_tag_sub.add_parser("track", help="Tag a track")
    p_tag_track.add_argument("title", help="Track title")
    p_tag_track.add_argument("artist", help="Artist name")
    p_tag_track.add_argument("--add", nargs="+", help="Tags to add")
    p_tag_track.add_argument("--rm", nargs="+", help="Tags to remove")
    # Query subcommand
    p_tag_query = p_tag_sub.add_parser("query", help="Find tracks by tags")
    p_tag_query.add_argument(
        "--filter", action="append", required=True, help="Tag to filter by (AND)"
    )
    # Derive subcommand
    p_tag_derive = p_tag_sub.add_parser("derive", help="Auto-derive tags from metadata")
    p_tag_derive.add_argument("playlist", nargs="?", help="Playlist name (or --all)")
    p_tag_derive.add_argument(
        "--all", action="store_true", help="Derive for all tracks"
    )
    # List-tags subcommand
    p_tag_sub.add_parser("list-tags", help="List all tags with counts")

    p_untag = sub.add_parser("untag", help="Remove a collection tag from a playlist")
    p_untag.add_argument("playlist", help="Playlist name")
    p_untag.add_argument("collection", help="Collection name")

    # analyze
    p_analyze = sub.add_parser("analyze", help="Analyze playlist metadata")
    p_analyze.add_argument("playlist", help="Playlist name")

    # collections
    p_collections = sub.add_parser("collections", help="List or manage collections")
    p_collections.add_argument(
        "collection", nargs="?", help="Show playlists in this collection"
    )
    p_collections.add_argument(
        "--create", dest="create_name", help="Create a collection"
    )
    p_collections.add_argument(
        "--delete", dest="delete_name", help="Delete a collection"
    )

    # folders
    p_folders = sub.add_parser("folders", help="Manage Tidal folders")
    p_folders_sub = p_folders.add_subparsers(dest="action")
    p_folders_sub.add_parser("list", help="List Tidal folders")
    p_folders_sub.add_parser("import", help="Import existing Tidal structure")
    p_fc = p_folders_sub.add_parser("create", help="Create folder on Tidal")
    p_fc.add_argument("name", help="Folder name")
    p_fr = p_folders_sub.add_parser("rename", help="Rename folder on Tidal")
    p_fr.add_argument("old_name", help="Current name")
    p_fr.add_argument("new_name", help="New name")
    p_fd = p_folders_sub.add_parser("delete", help="Delete folder on Tidal")
    p_fd.add_argument("name", help="Folder name")
    p_fm = p_folders_sub.add_parser("move", help="Assign playlist to folder")
    p_fm.add_argument("playlist", help="Playlist name")
    p_fm.add_argument("--to", required=True, help="Target folder name")
    p_fu = p_folders_sub.add_parser("unassign", help="Remove folder assignment")
    p_fu.add_argument("playlist", help="Playlist name")
    p_folders_sub.add_parser("sync", help="Push folder assignments to Tidal")
    p_folders_sub.add_parser("pull", help="Update local from Tidal state")
    p_folders_sub.add_parser("status", help="Show folder assignment status")

    # Shell completions via shtab
    try:
        import shtab

        shtab.add_argument_to(parser)
    except ImportError:
        pass

    return parser


def _handle_config(args) -> int:
    """Handle the config command."""
    import sys

    from tuneshift.sequencer.classifier import (
        _TOKEN_DIR,
        _load_stored_key,
        detect_backend,
        store_llm_key,
    )

    if getattr(args, "show", False) or not args.key:
        name, _backend = detect_backend()
        print("LLM Configuration:")
        if name:
            from tuneshift.sequencer.classifier import TrackClassifier

            classifier = TrackClassifier()
            print(f"  Backend: {classifier.backend_info}")
        else:
            print("  Backend: none configured")
        print()
        print(f"  Stored keys: {_TOKEN_DIR}")
        for key_name in ("anthropic", "openai"):
            stored = _load_stored_key(key_name)
            if stored:
                print(f"    {key_name}: {stored[:8]}...")
            else:
                print(f"    {key_name}: not set")
        print()
        print("  To configure: tuneshift config anthropic-key <your-key>")
        print("                tuneshift config openai-key <your-key>")
        print("                tuneshift config lastfm-key <your-key>")
        return 0

    key_map = {
        "anthropic-key": "anthropic",
        "openai-key": "openai",
        "lastfm-key": "lastfm",
    }

    if args.key not in key_map:
        print(f"Unknown config key: {args.key}", file=sys.stderr)
        print(f"Valid keys: {', '.join(key_map.keys())}", file=sys.stderr)
        return 1

    if not args.value:
        print(f"Usage: tuneshift config {args.key} <value>", file=sys.stderr)
        return 1

    backend_name = key_map[args.key]
    store_llm_key(backend_name, args.value)
    print(f"Stored {backend_name} API key in {_TOKEN_DIR / f'{backend_name}_key'}")

    # Verify it works
    name, _backend = detect_backend()
    if name:
        print(f"Backend now active: {name}")
    return 0


def _handle_tag_dispatch(args, db) -> int:
    """Route tag command to playlist tagging or track tagging."""
    import sys

    tag_action = getattr(args, "tag_action", None)

    if tag_action == "track":
        track = db.find_track(args.title, args.artist, None)
        if not track:
            tracks = db.find_tracks_by_title_artist(args.title, args.artist)
            if not tracks:
                print(
                    f"Track not found: {args.title} by {args.artist}", file=sys.stderr
                )
                return 1
            track = tracks[0]
        if getattr(args, "add", None):
            for tag in args.add:
                db.add_track_tag(track.id, tag)
            print(f'Tagged "{track.title}" by {track.artist}: +{", ".join(args.add)}')
        if getattr(args, "rm", None):
            for tag in args.rm:
                db.remove_track_tag(track.id, tag)
            print(f'Untagged "{track.title}": -{", ".join(args.rm)}')
        if not getattr(args, "add", None) and not getattr(args, "rm", None):
            tags = db.get_track_tags(track.id)
            print(f'Tags for "{track.title}": {tags or "(none)"}')
        return 0

    elif tag_action == "query":
        filters = getattr(args, "filter", [])
        tracks = db.find_tracks_by_tag(*filters)
        print(f"Tracks matching [{', '.join(filters)}]: {len(tracks)}")
        for t in tracks[:20]:
            print(f"  - {t.title} - {t.artist}")
        if len(tracks) > 20:
            print(f"  ... +{len(tracks) - 20} more")
        return 0

    elif tag_action == "derive":
        from tuneshift.enrichment.platform_metadata import derive_tags

        if getattr(args, "all", False):
            track_ids = db.all_track_ids()
        elif getattr(args, "playlist", None):
            playlist = db.find_playlist_by_name(args.playlist)
            if not playlist:
                print(f"Playlist not found: {args.playlist}", file=sys.stderr)
                return 1
            track_ids = [t.id for t in db.get_playlist_tracks(playlist.id)]
        else:
            print("Usage: tuneshift tag derive <playlist> or --all", file=sys.stderr)
            return 1
        total_tags = 0
        for tid in track_ids:
            tags = derive_tags(db, tid)
            total_tags += len(tags)
        print(f"Derived {total_tags} tags across {len(track_ids)} tracks")
        return 0

    elif tag_action == "list-tags":
        tags = db.list_all_track_tags()
        if not tags:
            print("No track tags.")
            return 0
        print("Track tags:")
        for tag, count in tags:
            print(f"  {tag}: {count} tracks")
        return 0

    else:
        # Default: playlist collection tagging (backward compat)
        target = getattr(args, "target", None)
        value = getattr(args, "value", None)
        if target and value:
            from tuneshift.commands.folders_cmd import handle_tag

            # Reconstruct args for the old handler
            args.playlist = target
            args.collection = value
            return handle_tag(args, db)
        print("Usage: tuneshift tag <playlist> <collection>", file=sys.stderr)
        print("       tuneshift tag track <title> <artist> --add/--rm", file=sys.stderr)
        return 1


def _handle_analyze(args, db) -> int:
    """Handle the analyze command."""
    import sys

    from tuneshift.enrichment.platform_metadata import analyze_playlist

    playlist = db.find_playlist_by_name(args.playlist)
    if not playlist:
        print(f"Playlist not found: {args.playlist}", file=sys.stderr)
        return 1

    result = analyze_playlist(db, playlist.id)

    print(f"=== {playlist.name} ({result['total_tracks']} tracks) ===")
    print(
        f"  Enriched: {result['enriched_tracks']}/{result['total_tracks']} tracks have platform metadata"  # noqa: E501
    )
    if result["era"]:
        print(f"  Era: {result['era']}")
    if result["top_genres"]:
        genre_str = ", ".join(f"{g} ({c})" for g, c in result["top_genres"])
        print(f"  Genres: {genre_str}")
    print(
        f"  Quality: {result['atmos_pct']:.0f}% Atmos, {result['lossless_pct']:.0f}% lossless"  # noqa: E501
    )
    if result["top_tags"]:
        tag_str = ", ".join(f"{t} ({c})" for t, c in result["top_tags"][:5])
        print(f"  Tags: {tag_str}")

    return 0


def _handle_ban(args, db) -> int:
    """Handle the ban command."""
    import sys

    if getattr(args, "list", False):
        banned = db.get_banned_artists()
        if not banned:
            print("No banned artists.")
            return 0
        print("Banned artists:")
        for name, reason in banned:
            reason_str = f" ({reason})" if reason else ""
            print(f"  - {name}{reason_str}")
        return 0

    if getattr(args, "remove", None):
        if db.unban_artist(args.remove):
            print(f'Removed "{args.remove}" from ban list.')
        else:
            print(f'"{args.remove}" not found on ban list.', file=sys.stderr)
            return 1
        return 0

    if not args.artist:
        print("Usage: tuneshift ban <artist> [--reason <reason>]", file=sys.stderr)
        print("       tuneshift ban --list", file=sys.stderr)
        return 1

    reason = getattr(args, "reason", None)
    db.ban_artist(args.artist, reason)
    print(
        f'Banned "{args.artist}"{f" ({reason})" if reason else ""}. '
        f"Future adds will be blocked. Run --sweep-banned to check existing playlists."
    )
    return 0


def _configure_logging(args) -> None:
    """Route enrichment/progress logs to stderr at the requested verbosity (AC6).

    Enrichment was silent because nothing configured the root logger. Progress
    (INFO) is visible by default; ``-v`` drops to DEBUG and annotates each line
    with its source logger; ``-q`` suppresses everything below WARNING.
    """
    verbose = getattr(args, "verbose", 0)
    if getattr(args, "quiet", False):
        level = logging.WARNING
    elif verbose:
        level = logging.DEBUG
    else:
        level = logging.INFO
    fmt = "%(levelname)s %(name)s: %(message)s" if verbose else "%(message)s"
    logging.basicConfig(level=level, stream=sys.stderr, format=fmt, force=True)


def main(argv: list[str] | None = None) -> int:
    """Run the tuneshift CLI."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 0

    _configure_logging(args)

    from pathlib import Path

    db_path = Path(args.db) if args.db else None
    db = Database(db_path)

    try:
        if args.command == "ingest":
            from tuneshift.commands.ingest_cmd import handle_ingest

            return handle_ingest(args, db)
        elif args.command == "sync":
            from tuneshift.commands.sync_cmd import handle_sync

            return handle_sync(args, db)
        elif args.command == "diff":
            from tuneshift.commands.diff_cmd import handle_diff

            return handle_diff(args, db)
        elif args.command == "add":
            from tuneshift.commands.add_cmd import handle_add

            return handle_add(args, db)
        elif args.command == "rm":
            from tuneshift.commands.rm_cmd import handle_rm

            return handle_rm(args, db)
        elif args.command == "login":
            from tuneshift.commands.login_cmd import handle_login

            return handle_login(args, db)
        elif args.command == "status":
            from tuneshift.commands.status_cmd import handle_status

            return handle_status(args, db)
        elif args.command == "list":
            from tuneshift.commands.status_cmd import handle_list

            return handle_list(args, db)
        elif args.command == "order":
            from tuneshift.commands.order_cmd import handle_order

            return handle_order(args, db)
        elif args.command == "pin":
            from tuneshift.commands.pin_cmd import handle_pin

            return handle_pin(args, db)
        elif args.command == "resolve":
            from tuneshift.commands.resolve import run_resolve

            run_resolve(args, db)
            return 0
        elif args.command == "import-text":
            from tuneshift.commands.import_text_cmd import handle_import_text

            return handle_import_text(args, db)
        elif args.command == "enrich":
            from tuneshift.commands.enrich_cmd import handle_enrich

            return handle_enrich(args, db)
        elif args.command == "doctor":
            from tuneshift.commands.doctor_cmd import handle_doctor

            return handle_doctor(args, db)
        elif args.command == "narrative":
            from tuneshift.commands.narrative_cmd import handle_narrative

            return handle_narrative(args, db)
        elif args.command == "export":
            from tuneshift.commands.export_cmd import handle_export

            return handle_export(args, db)
        elif args.command == "import-json":
            from tuneshift.commands.import_json_cmd import handle_import_json

            return handle_import_json(args, db)
        elif args.command == "map":
            from tuneshift.commands.map_cmd import handle_map

            return handle_map(args, db)
        elif args.command == "unmap":
            from tuneshift.commands.map_cmd import handle_unmap

            return handle_unmap(args, db)
        elif args.command == "lock":
            from tuneshift.commands.lock_cmd import handle_lock, handle_lock_list

            if getattr(args, "list_locks", False):
                return handle_lock_list(args, db)
            return handle_lock(args, db)
        elif args.command == "unlock":
            from tuneshift.commands.lock_cmd import handle_unlock

            return handle_unlock(args, db)
        elif args.command == "edit":
            from tuneshift.commands.edit_cmd import handle_edit

            return handle_edit(args, db)
        elif args.command == "explain":
            from tuneshift.commands.explain_cmd import handle_explain

            return handle_explain(args, db)
        elif args.command == "why":
            from tuneshift.commands.explain_cmd import handle_why

            return handle_why(args, db)
        elif args.command == "triage":
            from tuneshift.commands.triage_cmd import handle_triage

            return handle_triage(args, db)
        elif args.command == "goal":
            from tuneshift.commands.goal_cmd import handle_goal

            return handle_goal(args, db)
        elif args.command == "weights":
            from tuneshift.commands.weights_cmd import handle_weights

            return handle_weights(args, db)
        elif args.command == "curate":
            from tuneshift.commands.curate_cmd import handle_curate

            return handle_curate(args, db)
        elif args.command == "prefs":
            from tuneshift.commands.prefs_cmd import handle_prefs

            return handle_prefs(args, db)
        elif args.command == "alias":
            from tuneshift.commands.alias_cmd import handle_alias

            return handle_alias(args, db)
        elif args.command == "share":
            from tuneshift.commands.share_cmd import handle_share

            return handle_share(args, db)
        elif args.command == "link":
            from tuneshift.commands.link_cmd import handle_link

            return handle_link(args, db)
        elif args.command == "compose":
            from tuneshift.commands.compose_cmd import handle_compose

            return handle_compose(args, db)
        elif args.command == "concept":
            from tuneshift.commands.compose_cmd import handle_concept

            return handle_concept(args, db)
        elif args.command == "review":
            from tuneshift.commands.compose_cmd import handle_review

            return handle_review(args, db)
        elif args.command == "config":
            return _handle_config(args)
        elif args.command == "batch":
            from tuneshift.commands.batch_cmd import handle_batch

            return handle_batch(args, db)
        elif args.command == "plan":
            from tuneshift.commands.plan_cmd import handle_plan

            return handle_plan(args, db)
        elif args.command == "ban":
            return _handle_ban(args, db)
        elif args.command == "merge":
            from tuneshift.commands.batch_cmd import handle_merge

            return handle_merge(args, db)
        elif args.command == "audit":
            from tuneshift.commands.audit_cmd import handle_audit

            return handle_audit(args, db)
        elif args.command == "tag":
            return _handle_tag_dispatch(args, db)
        elif args.command == "untag":
            from tuneshift.commands.folders_cmd import handle_untag

            return handle_untag(args, db)
        elif args.command == "analyze":
            return _handle_analyze(args, db)
        elif args.command == "collections":
            from tuneshift.commands.folders_cmd import handle_collections

            return handle_collections(args, db)
        elif args.command == "folders":
            from tuneshift.commands.folders_cmd import handle_folders

            return handle_folders(args, db)
        else:
            parser.print_help()
            return 1
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130
    except TuneShiftError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"Unexpected error: {type(exc).__name__}: {exc}", file=sys.stderr)
        print("Run with TUNESHIFT_DEBUG=1 for full traceback.", file=sys.stderr)
        if os.environ.get("TUNESHIFT_DEBUG"):
            import traceback

            traceback.print_exc()
        return 2
    finally:
        db.close()
