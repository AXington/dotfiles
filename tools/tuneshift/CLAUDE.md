# CLAUDE.md

Agent instructions for TuneShift.

## Commands

```bash
cd tools/tuneshift
.venv/bin/python -m pytest tests/ -x -q   # run tests
.venv/bin/ruff check .                     # lint
.venv/bin/python -m tuneshift --help       # CLI help
.venv/bin/python -m tuneshift list         # list all playlists
.venv/bin/python -m tuneshift status <name> # playlist sync status
.venv/bin/python -m tuneshift pin <name> --list  # show pins
```

## Architecture

TuneShift is a canonical playlist manager. The local SQLite database is the
single source of truth; platform services (Tidal, Spotify, YTM) are distribution
targets that receive pushes from the canonical state.

### Critical Design Decisions

**The database is committed to git.** `tuneshift.db` is intentionally tracked in
version control. It IS the product: the curated playlist collection, track
metadata, platform mappings, and sequencer state. This is not an accident or
oversight. Do not suggest gitignoring it, moving it to an external store, or
treating it as generated/ephemeral data.

**Canonical-first, push-only model.** Changes flow one direction: local DB is
edited, then pushed to platforms. Platforms never overwrite local state. The
`ingest` command is the only path that reads from platforms into local state,
and it is a one-time import operation.

**Platform credentials live outside the repo.** Auth tokens are stored in
`~/.local/share/tuneshift/` with restricted permissions. They are never committed.
The separation between committed data (DB) and uncommitted secrets (tokens) is
load-bearing.

**Schema migrations are in-DB.** The schema version is stored in the `schema_meta`
table (key `version`), not the `user_version` pragma. Migrations run automatically
on open. Each migration is one versioned module in `persistence/migrations/`
(`vNNN.py`, each exposing `apply(conn)`); to add one, create the next `vNNN.py`,
register it in `persistence/migrations/__init__.py`, and bump `_SCHEMA_VERSION` in
`persistence/base.py`. `SchemaMixin._migrate_schema()` delegates to the ordered
chain via `run_migrations()`.

**Raw SQL is confined to the persistence layer.** Only `db.py` and modules under
`persistence/` may call `.conn.execute*` / `.conn.cursor()`. Every other module
goes through a named `Database` method. This boundary is enforced by the
`test_no_conn_execute_leak` gate in `tests/test_lint_regressions.py` (a bare
`.conn.commit()` for caller-managed transactions is the only exception).

### Module Layout

`matching` is a package; `reconcile.py` and `models.py` remain flat modules.

| Module | Responsibility |
|--------|---------------|
| `db.py` | Public `Database` facade composing the `persistence/` mixins; re-exports the stable public surface (schema version stored in `schema_meta`) |
| `persistence/` | Persistence mixins split by area (`base`, `schema`, `migrations/`, `tracks`, `resolution`, `playlists`, `platform`, `meta`, `artists`, `collections`); the only place raw SQL lives |
| `cli.py` | Argument parsing, command dispatch |
| `models.py` | Shared dataclasses |
| `reconcile.py` | Track reconciliation: match canonical tracks to platform IDs (wraps `matching/`) |
| `commands/` | One file per CLI subcommand (~35 modules) |
| `platforms/` | Platform clients + `auth`, `protocol`, `rate_limiter` |
| `matching/` | Version-selection engine (`registry`, `criteria`, `engine`, `selection`, `version`, `confidence`, `precedence`, `tiebreak`, `preferences`, `normalize`, `aliases`, `similarity`, `review`, `audit`) |
| `identity/` | Cross-platform identity resolution + locks (`resolver`, `matching`, `confidence`, `models`) |
| `library/` | Library-first resolution worker + enrichment glue (`worker`, `resolvers`, `enrichment`) |
| `enrichment/` | Metadata enrichment (`pipeline`, `platform_metadata`, `audio_features`, `genius`, `lastfm`, `retry`) |
| `planapply/` | Plan model + journaled apply engine (`models`, `plan`, `apply`, `builders`, `sync`, `rematch`, `migrate`, `heal`) |
| `sequencer/` | Track ordering (energy arcs, pinning, 2-opt, narrative) |
| `composer/` | Narrative-driven playlist composition |
| `curation/` | Playlist trim / gap-fill / scoring |
| `doctor/` | Mapping-issue scan + repair |

### Feature guides

Deep documentation for each major subsystem lives in `docs/`:

| Guide | Covers |
|-------|--------|
| `docs/version-selection.md` | Criteria axes, strengths, source-aware verdicts, confidence tiers, tie-breaks, ambiguity |
| `docs/preferences.md` | Typed `(criterion, strength, target)` prefs, scopes, precedence, multi-target |
| `docs/locks.md` | Two-level composite locks, precedence, self-heal, downgrade flagging |
| `docs/plan-apply.md` | Plan model, journaled apply engine, rollback, routing |
| `docs/resolution-enrichment.md` | Library-first queue, worker, candidate persistence, coverage/quarantine, enrichment |
| `docs/CLI.md` | Complete flag-by-flag command reference |
| `docs/matching-known-limits.md` | What matching deliberately does not auto-resolve |

### DB Schema (key tables)

| Table | Purpose |
|-------|---------|
| `tracks` | Canonical track metadata (title, artist, album, ISRC, energy, quarantine_state) |
| `playlists` | Playlist definitions (name, auto_reorder flag, reorder_arc) |
| `playlist_tracks` | Track membership and position (playlist_id, track_id, position) |
| `playlist_pins` | Sequencer constraints (opener, closer, position, anchor groups) |
| `platform_tracks` | Platform-specific track IDs and match metadata (global lock: `user_approved=1`) |
| `platform_playlists` | Links canonical playlists to platform playlist IDs |
| `playlist_track_mappings` | Per-playlist track-to-platform mappings (playlist-override lock: `user_approved=1`) |
| `track_platform_metadata` | Native platform metadata (Atmos/quality, release year, genres) |
| `resolution_queue` | Library-first resolution work queue (state, attempts, transient_attempts) |
| `track_candidates` | Persisted top-N discovery candidates per track (discovery_rank) |
| `match_audits` | Per-(playlist,track,platform) match decision + reason_code |
| `playlist_track_prefs` | Typed version prefs; unique per `(scope, criterion, target)` (multi-target) |
| `apply_journal` | Forward-only journal of applied plan changes (for rollback) |
| `artist_aliases` | Artist equivalence classes (Ke$ha / Kesha) |
| `evidence` | Identity resolution evidence from MusicBrainz/Discogs |
| `collections` / `tidal_folders` | Playlist grouping + Tidal folder mirror |
| `sync_log` | Sync history |

## Pin System

The sequencer respects four pin types:

| Pin Type | Effect | CLI |
|----------|--------|-----|
| `opener` | Track is always first | `--opener TITLE` |
| `closer` | Track is always last | `--closer TITLE` |
| `position` | Track locked to specific 0-based index | `--position INDEX TITLE` |
| `anchor` | Group of tracks stays together in order | `--adjacent T1 T2 T3 [--group NAME]` |

Position 0 overrides opener; last-index position overrides closer. The optimizer
removes pinned tracks from the free pool, builds the sequence, then inserts
position-pinned tracks at their target indices. Post-optimization (2-opt, artist
distribution) protects all pinned positions.

## Version Matching Rules

The reconcile pipeline selects platform tracks in this priority:
1. **ISRC lookup** is a *discovery strategy*, not ground truth. An ISRC hit
   surfaces a candidate, but that candidate is still scored like any other and
   can be down-ranked or rejected; ISRCs are shared/mislabelled often enough
   (clean vs. explicit, single vs. album edit) that a blind accept is unsafe.
   The short-circuit only fires when the found candidate *also* scores a clean
   title+artist+album match.
2. **Title+artist search** scored with `score_match_with_version`:
   - Base similarity (title: 0-50, artist: 0-30, album: 0-20)
   - **Source-aware recording verdict** (see `matching/version.py`): the
     candidate's recording class is compared to the SOURCE's, not judged in
     isolation. A studio source REJECTs a live/karaoke/instrumental/tribute/
     cover take (score floored to 0); a live source MATCHes a live take; a live
     source falling back to the studio master is a SUBSTITUTE (down-ranked,
     never auto-selected); a remaster of the same recording is a SOFT match; a
     clean candidate never satisfies an explicit source (REJECT).
   - **Version-intent override** from per-playlist prefs (`version_intent`):
     `prefer:[live]` elevates a live candidate to a MATCH; `avoid:[live]` hard-
     rejects live regardless of source. Default prefs map to *no* intent, so
     scoring stays purely source-aware.
   - Residual candidate-only penalties for packaging keywords that are not
     recording classes (radio-edit, deluxe, compilation), applied only when the
     candidate has them and the source does not.
   - Duration penalty (>1.4x shortest: -10, >1.6x: -15, >2.0x: -20)
3. **User-approved mappings** are never re-reconciled.

The legacy candidate-only `version_penalty`/`version_signals` are retained for
byte-parity golden tests; the live scoring path uses the source-aware signals.

Key recording classes distinguished (distinct performances, not packaging):
- Live recordings / concert versions
- Remixes (Performance/Extended/Club Mix, 12" versions), dub mixes
- Instrumentals, karaoke
- Acoustic re-recordings
- Tributes / covers / reimaginings

### Known Tidal Pitfalls

- "Deluxe Remastered" albums bundle BBC/live recordings on bonus discs that share
  the same album name in metadata. The ISRC differs but album field is identical.
  Don't trust album name alone for studio vs. live determination.
- Track numbers matter: same album can have the single edit (track 1) and an
  extended mix (track 6) under the same album name (e.g., Youthquake).
- tidalapi returns whatever version Tidal's search ranks highest, which is often
  the extended/deluxe version. Duration comparison is essential.

## Platform Integration

| Platform | Client | Session File | Notes |
|----------|--------|-------------|-------|
| Tidal | `tidalapi` | `~/.local/share/tidal-importer/session.json` | Full CRUD, reorder works |
| YouTube Music | `ytmusicapi` | `~/.local/share/tuneshift/ytmusic_oauth.json` | Push works, reorder gets 403 (scope issue) |
| Spotify | `spotipy` (PKCE) | `~/.local/share/tuneshift/spotify.json` | Track/album/artist search + enrich; client_id via env or 1Password |

### YTM API Quirks
- Reorder requires `playlistItems.delete` permission which returns 403
- Track matching is by video ID, not ISRC
- Some tracks unavailable due to regional/licensing differences

## Testing

- **Run:** `.venv/bin/python -m pytest tests/ -x -q`
- **Lint:** `.venv/bin/ruff check .`
- Tests use real DB for integration, mocks for platform clients
- Bug fixes require regression tests
- New features require unit tests for the core logic

## Sequencer

The sequencer orders tracks by energy arc profiles (wave, narrative, descending).
It uses greedy nearest-neighbor construction, 2-opt local optimization, and
artist redistribution. Tracks can be pinned (opener, closer, position, adjacency
groups) which constrain the optimizer.

Key functions in `sequencer/optimizer.py`:
- `_resolve_pins`: Parse pins into opener/closer/position/anchor data
- `_select_endpoints`: Choose opener/closer (pinned or auto-selected)
- `_prepare_free_pool`: Separate free tracks from adjacency blocks
- `_greedy_build`: Construct sequence via nearest-neighbor with energy targets
- `optimize_sequence`: Top-level orchestrator
- `sequence_playlist`: DB-aware entry point (loads pins, metadata, calls optimizer)

## Error Handling Contract

All commands that push to platforms propagate failures as non-zero exit codes.
Platform errors go to stderr. The CLI top-level handler catches `TuneShiftError`
for clean messages and unexpected exceptions with opt-in traceback
(`TUNESHIFT_DEBUG=1`).

Exception handlers use specific exception types (not bare `except Exception`):
- Platform API errors: `OSError`, `RuntimeError`, `ValueError`, `KeyError`
- MusicBrainz: `MusicBrainzError` (from musicbrainzngs)
- Anthropic (classifier): `APIError` via `self._api_errors` tuple
