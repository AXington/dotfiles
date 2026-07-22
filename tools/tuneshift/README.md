# tuneshift

Canonical playlist manager with cross-platform distribution and a
best-in-class, source-aware version-matching engine.

TuneShift maintains a local SQLite library of tracks and playlists as the single
source of truth, then distributes them to **Tidal**, **Spotify**, and **YouTube
Music**. It picks the *right version* of every track (studio vs. live vs. remaster
vs. Atmos, etc.), never guesses when it cannot be sure, and applies every mutation
through a reviewable, rollback-able plan.

## Install

```bash
cd tools/tuneshift
uv venv && uv pip install -e ".[dev]"
```

## Core model

TuneShift is **canonical-first and push-only**:

- **`tuneshift.db` is the product.** The SQLite database (committed to git) is the
  single source of truth for playlists, tracks, metadata, platform mappings,
  preferences, and locks. It is intentionally version-controlled for cross-machine
  sync; do not gitignore it or treat it as ephemeral.
- **Platforms are distribution targets.** Changes flow one direction: edit locally,
  push to platforms. Platforms never overwrite local state. `ingest` is the only
  read-from-platform path and is a one-time import.
- **Credentials never touch the repo.** Auth tokens live in
  `~/.local/share/tuneshift/` with `0700` permissions.
- **Mutations go through plan/apply.** Sync, rematch, lock, map, migrate and
  self-heal all default to writing a *reviewable plan* that changes nothing until
  applied, and every apply is journaled for one-step rollback.
- **Schema migrations are automatic.** The schema version is stored in the
  `schema_meta` table (key `version`) and drives in-DB migrations on open, one
  versioned module per step under `persistence/migrations/` (`vNNN.py`). Check the
  current version with
  `sqlite3 tuneshift.db "SELECT value FROM schema_meta WHERE key='version'"`.

## Quickstart

```bash
# 1. Authenticate
tuneshift login tidal
tuneshift login spotify
tuneshift login ytmusic

# 2. Get tracks into the canonical library
tuneshift ingest tidal <playlist-id>          # one-time import from a platform
tuneshift import-text "Road Trip" tracks.txt   # library-first: enqueues resolution
tuneshift add "Road Trip" "Heroes" "David Bowie" --album "\"Heroes\""

# 3. Resolve identities + hydrate metadata (ISRC, duration, album, Atmos, ...)
tuneshift resolve "Road Trip" --all
tuneshift resolve --status                     # coverage / quarantine stats
# Tip: a full-library resolve takes hours. Keep the machine awake:
#   caffeinate -i tuneshift resolve --all

# 4. Inspect and steer matching
tuneshift explain <track-id> "Road Trip"       # why this version was chosen
tuneshift triage                               # tracks needing human review
tuneshift prefs set --playlist "Road Trip" spatial prefer atmos
tuneshift lock "Road Trip" "Heroes" --tidal 12345678   # pin the right version
tuneshift map "Road Trip" "Heroes" --tidal 12345678    # or map a track triage surfaced

# 5. Order and ship
tuneshift order "Road Trip" --arc wave
tuneshift sync "Road Trip" tidal               # writes a plan (changes nothing)
tuneshift plan show <plan-id>
tuneshift sync "Road Trip" tidal --apply       # apply + push
```

## Command surface

TuneShift exposes ~45 subcommands. Grouped by workflow:

| Group | Commands |
|-------|----------|
| **Library** | `add`, `rm`, `edit`, `import-text`, `ingest`, `list`, `status`, `merge` |
| **Resolution / enrichment** | `resolve`, `enrich`, `map`, `unmap`, `alias` |
| **Version control (matching)** | `prefs`, `lock`, `unlock`, `explain` (`why` = deprecated alias), `triage` |
| **Plan / apply** | `plan {sync,rematch,migrate,heal,list,show,reject,apply,rollback}`, `sync`, `diff`, `batch` |
| **Sequencing** | `order`, `pin`, `narrative`, `weights`, `goal`, `concept` |
| **Curation** | `curate`, `compose`, `review`, `analyze`, `audit`, `ban` |
| **Organization** | `tag`, `untag`, `collections`, `folders`, `link`, `share` |
| **Platform / config** | `login`, `export`, `doctor` (plan/apply-routed), `config` |

Full flag-by-flag reference: [`docs/CLI.md`](docs/CLI.md).

Global flags: `--db PATH` (override DB), `-v`/`-q` (verbose/quiet logging to
stderr), `--print-completion {bash,zsh,tcsh}`.

## Feature guides

Start at the [documentation index](docs/index.md) for a one-line map of every
guide, or jump to [common workflows](docs/workflows.md) for end-to-end recipes.
The major subsystems each have a dedicated guide under [`docs/`](docs/):

- **[Version-selection engine](docs/version-selection.md)**: 15 criteria axes,
  require/prefer/avoid/forbid strengths, source-aware recording verdicts,
  confidence tiers, deterministic tie-breaks, and ambiguity surfacing.
- **[Preferences](docs/preferences.md)**: typed `(criterion, strength, target)`
  prefs at global / playlist / playlist-track scope, precedence, and multi-target
  axes.
- **[Identity locks](docs/locks.md)**: two-level composite locks, precedence,
  self-heal, and version-downgrade flagging.
- **[Plan / apply](docs/plan-apply.md)**: durable plans, the journaled apply
  engine, rollback, and the sync/rematch/migrate/heal routes.
- **[Resolution & enrichment](docs/resolution-enrichment.md)**: library-first
  add/import, the resumable resolution-queue worker, candidate persistence,
  metadata hydration, coverage/quarantine, and the enrichment layer.
- **[Matching known limits](docs/matching-known-limits.md)**: the honest
  register of what matching deliberately does *not* auto-resolve.
- **[Roadmap](docs/roadmap.md)**: known future work, deferred improvements, and
  intentionally-unbuilt limits.


## Architecture

### Module layout

```
tuneshift/
  cli.py              # Argument parsing, command dispatch
  db.py               # Public Database facade over the persistence/ mixins
  persistence/        # Persistence mixins (base, schema, migrations/, tracks,
                      #   resolution, playlists, platform, meta, artists,
                      #   collections); the only place raw SQL lives
  models.py           # Shared dataclasses (Track, Playlist, etc.)
  commands/           # One file per CLI subcommand (~35 modules)
  platforms/          # Platform clients + auth + rate limiting
                      #   (tidal, spotify, ytmusic, protocol, rate_limiter)
  matching/           # Version-selection engine (criteria, registry, engine,
                      #   selection, version, confidence, precedence, tiebreak,
                      #   preferences, normalize, aliases, similarity, review)
  identity/           # Cross-platform identity resolution + locks
                      #   (resolver, matching, confidence, models)
  library/            # Library-first resolution worker + enrichment glue
                      #   (worker, resolvers, enrichment)
  enrichment/         # Metadata enrichment (pipeline, platform_metadata,
                      #   audio_features, genius, lastfm, retry)
  planapply/          # Plan model + journaled apply engine
                      #   (models, plan, apply, builders, sync, rematch,
                      #   migrate, heal)
  sequencer/          # Track ordering (energy arcs, pinning, 2-opt, narrative)
  composer/           # Narrative-driven playlist composition
  curation/           # Playlist trim / gap-fill / scoring
  doctor/             # Mapping-issue scan + repair
```

### Key DB tables

| Table | Purpose |
|-------|---------|
| `tracks` | Canonical track metadata (title, artist, album, ISRC, energy, ...) |
| `playlists` / `playlist_tracks` | Playlist definitions and ordered membership |
| `playlist_pins` | Sequencer constraints (opener, closer, position, anchor) |
| `platform_tracks` / `platform_playlists` | Platform IDs + match metadata |
| `playlist_track_mappings` | Per-playlist track-to-platform mappings (`user_approved`) |
| `track_platform_metadata` | Native platform metadata (Atmos/quality, year, genres) |
| `resolution_queue` | Library-first resolution work queue (state, attempts) |
| `track_candidates` | Persisted top-N discovery candidates per track |
| `match_audits` | Per-(playlist,track,platform) match decision + reason |
| `playlist_track_prefs` | Typed version prefs (criterion, strength, target) |
| `apply_journal` | Forward-only journal of applied plan changes (for rollback) |
| `artist_aliases` | Artist equivalence classes (e.g. Ke$ha / Kesha) |
| `evidence` | Identity-resolution evidence from MusicBrainz/Discogs |
| `collections` / `tidal_folders` | Playlist grouping + Tidal folder mirror |
| `sync_log` | Sync history |

## Platform integration

| Platform | Client | Session file | Notes |
|----------|--------|--------------|-------|
| Tidal | `tidalapi` | `~/.local/share/tidal-importer/session.json` | Full CRUD + reorder; source of truth for availability + Atmos |
| YouTube Music | `ytmusicapi` | `~/.local/share/tuneshift/ytmusic_oauth.json` | Push works; reorder returns 403 (scope); match by video ID |
| Spotify | `spotipy` (PKCE) | `~/.local/share/tuneshift/spotify.json` | Track/album/artist search + enrich; `client_id` via env or 1Password |

## Testing & lint

```bash
.venv/bin/python -m pytest tests/ -x -q   # full suite
.venv/bin/ruff check .                     # lint
```

Tests use a real SQLite DB for integration and mock the platform clients. Bug
fixes require a regression test; new features require unit + integration coverage
(the suite includes gold winner-parity tests under `tests/gold/`).

## Shell completions

```bash
tuneshift --print-completion bash >> ~/.bash_completion
tuneshift --print-completion zsh  >> ~/.zshrc
```

## Database & credentials

The DB lives at `tools/tuneshift/tuneshift.db` and is committed for cross-machine
sync. It is currently about 11 MB (roughly 2,700 tracks across 62 playlists) and
grows slowly: expect a few KB per added track, plus candidate and metadata rows as
tracks resolve and enrich. Its WAL sidecars (`*.db-shm`, `*.db-wal`) and coverage
artifacts are gitignored. Auth tokens live in `~/.local/share/tuneshift/` (mode
`0700`) and are never committed.

Because the DB is committed and shared, two sessions writing it concurrently can
clobber each other (last writer wins). Guardrails: `resolve` takes a PID
single-flight lock (`.tuneshift/resolve.lock`) so only one resolve runs at a time,
and `export --format json` + `import-json` gives a per-playlist backup/restore
round-trip to recover a clobbered playlist. Operationally, prefer one DB writer at
a time.
