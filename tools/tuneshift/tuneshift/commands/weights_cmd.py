"""Weights command: manage sequencing weight vectors."""

from tuneshift.db import Database
from tuneshift.sequencer.weights import PRESETS

VALID_DIMENSIONS = {
    "narrative_arc",
    "energy_flow",
    "mood_continuity",
    "sonic_texture",
    "lyrical_thread",
    "emotional_arc",
    "groove_coherence",
    "era_mood",
    "variety",
    "artist_separation",
}


def handle_weights(args, db: Database) -> int:
    """Manage sequencing weight vectors."""
    if args.action == "list":
        print("Available weight presets:\n")
        for name, weights in PRESETS.items():
            top3 = sorted(weights.items(), key=lambda x: x[1], reverse=True)[:3]
            summary = ", ".join(f"{k}={v}" for k, v in top3)
            print(f"  {name}: {summary} ...")
        return 0

    if not args.playlist:
        print("Playlist name required for set/show.")
        return 1

    playlists = db.list_playlists()
    matches = [p for p in playlists if p.name == args.playlist]
    if not matches:
        print(f'Playlist "{args.playlist}" not found.')
        return 1

    pid = matches[0].id

    if args.action == "show":
        weights = db.get_weights(pid)
        if weights:
            print(f'Weights for "{args.playlist}":')
            for dim, val in sorted(weights.items(), key=lambda x: x[1], reverse=True):
                bar = "#" * int(val * 10)
                print(f"  {dim:20s} {val:.1f} {bar}")
        else:
            print(f'No weights set for "{args.playlist}". Using default (energy-wave).')
        return 0

    # action == "set"
    if args.preset:
        if args.preset not in PRESETS:
            print(f'Unknown preset "{args.preset}". Use `tuneshift weights list`.')
            return 1
        db.set_weights(pid, PRESETS[args.preset])
        print(f'Set weights for "{args.playlist}" to preset "{args.preset}".')
        return 0

    if args.values:
        weights = db.get_weights(pid) or {}
        for pair in args.values:
            if "=" not in pair:
                print(f'Invalid format: "{pair}". Use dimension=value.')
                return 1
            dim, val_str = pair.split("=", 1)
            if dim not in VALID_DIMENSIONS:
                print(f'Unknown dimension: "{dim}". Valid: {sorted(VALID_DIMENSIONS)}')
                return 1
            weights[dim] = float(val_str)
        db.set_weights(pid, weights)
        print(f'Updated weights for "{args.playlist}".')
        return 0

    print("Specify --preset or dimension=value pairs.")
    return 1
