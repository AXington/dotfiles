"""Goal command: set/show/clear playlist goal."""

from tuneshift.db import Database


def handle_goal(args, db: Database) -> int:
    """Set, show, or clear a playlist's goal."""
    playlists = db.list_playlists()
    matches = [p for p in playlists if p.name == args.playlist]
    if not matches:
        print(f'Playlist "{args.playlist}" not found.')
        return 1

    pid = matches[0].id

    if args.clear:
        db.set_goal(pid, None)
        print(f'Cleared goal for "{args.playlist}".')
        return 0

    if args.text:
        db.set_goal(pid, args.text)
        print(f'Set goal for "{args.playlist}".')
        return 0

    # Show current goal
    goal = db.get_goal(pid)
    if goal:
        print(f'Goal for "{args.playlist}":\n\n{goal}')
    else:
        print(
            f'No goal set for "{args.playlist}". Set one with: tuneshift goal "{args.playlist}" "<text>"'  # noqa: E501
        )
    return 0
