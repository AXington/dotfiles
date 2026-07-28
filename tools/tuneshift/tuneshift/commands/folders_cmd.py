"""Tidal folder management and collection tagging commands."""

from __future__ import annotations

import sys

from tuneshift.db import Database

# Bound every Tidal folder API call so a hung connection cannot stall the CLI
# indefinitely (B113/S113). Matches the timeout used by other platform clients.
_REQUEST_TIMEOUT = 30

# Tidal's v2 API reports folders as TRNs ("trn:folder:<uuid>") and that is the
# form cached in tidal_folders.tidal_id. tidalapi's Folder lookup, however,
# matches against the bare ``data.id`` UUID, so a TRN never matches and raises
# ObjectNotFound. Normalise at the tidalapi boundary rather than at write time:
# rewriting stored ids would insert bare-uuid rows beside the existing
# TRN-keyed ones (tidal_id is UNIQUE), duplicating every cached folder.
_TRN_FOLDER_PREFIX = "trn:folder:"


def _bare_folder_id(folder_id: str) -> str:
    """Return the bare folder UUID, accepting either a TRN or an already-bare id."""
    if folder_id.startswith(_TRN_FOLDER_PREFIX):
        return folder_id[len(_TRN_FOLDER_PREFIX) :]
    return folder_id


def handle_tag(args, db: Database) -> int:
    """Tag a playlist with a collection."""
    playlist = db.find_playlist_by_name(args.playlist)
    if not playlist:
        print(f"Playlist not found: {args.playlist}", file=sys.stderr)
        return 1
    db.tag_playlist(playlist.id, args.collection)
    print(f'Tagged "{playlist.name}" with "{args.collection}"')
    return 0


def handle_untag(args, db: Database) -> int:
    """Remove a collection tag from a playlist."""
    playlist = db.find_playlist_by_name(args.playlist)
    if not playlist:
        print(f"Playlist not found: {args.playlist}", file=sys.stderr)
        return 1
    if db.untag_playlist(playlist.id, args.collection):
        print(f'Removed "{args.collection}" from "{playlist.name}"')
    else:
        print(
            f'"{playlist.name}" is not tagged with "{args.collection}"', file=sys.stderr
        )
        return 1
    return 0


def handle_collections(args, db: Database) -> int:
    """List collections or show playlists in a collection."""
    if getattr(args, "create_name", None):
        db.create_collection(args.create_name)
        print(f'Created collection "{args.create_name}"')
        return 0

    if getattr(args, "delete_name", None):
        if db.delete_collection(args.delete_name):
            print(f'Deleted collection "{args.delete_name}"')
        else:
            print(f'Collection not found: "{args.delete_name}"', file=sys.stderr)
            return 1
        return 0

    collection_name = getattr(args, "collection", None)
    if collection_name:
        playlists = db.get_collection_playlists(collection_name)
        if not playlists:
            print(f'No playlists in "{collection_name}" (or collection does not exist)')
            return 0
        print(f'Collection "{collection_name}" ({len(playlists)} playlists):')
        for p in playlists:
            print(f"  - {p.name}")
        return 0

    # List all collections
    collections = db.list_collections_with_counts()
    if not collections:
        print("No collections. Create one with: tuneshift collections create <name>")
        return 0
    print("Collections:")
    for name, count in collections:
        print(f"  {name} ({count} playlists)")
    return 0


def handle_folders(args, db: Database) -> int:
    """Manage Tidal folders."""
    action = getattr(args, "action", None)

    if action == "list":
        return _folders_list(db)
    elif action == "import":
        return _folders_import(db)
    elif action == "create":
        return _folders_create(db, args.name)
    elif action == "rename":
        return _folders_rename(db, args.old_name, args.new_name)
    elif action == "delete":
        return _folders_delete(db, args.name)
    elif action == "move":
        return _folders_move(db, args.playlist, args.to)
    elif action == "unassign":
        return _folders_unassign(db, args.playlist)
    elif action == "sync":
        return _folders_sync(db)
    elif action == "pull":
        return _folders_pull(db)
    elif action == "status":
        return _folders_status(db)
    else:
        print(
            "Usage: tuneshift folders <list|import|create|rename|delete|move|unassign|sync|pull|status>",  # noqa: E501
            file=sys.stderr,
        )
        return 1


def _get_tidal_client():
    """Load and authenticate Tidal client."""
    from tuneshift.commands.ingest_cmd import _load_client

    client = _load_client("tidal")
    if not client or not client.load_session():
        print("Not logged in to Tidal. Run: tuneshift login tidal", file=sys.stderr)
        return None
    return client


def _folders_list(db: Database) -> int:
    """List Tidal folders and their contents."""
    client = _get_tidal_client()
    if not client:
        return 1

    import requests

    session = client._session
    headers = {"Authorization": f"Bearer {session.access_token}"}
    params = {
        "folderId": "root",
        "countryCode": session.country_code,
        "limit": "50",
        "offset": "0",
        "order": "NAME",
        "includeOnly": "",
    }

    resp = requests.get(
        "https://api.tidal.com/v2/my-collection/playlists/folders",
        headers=headers,
        params=params,
        timeout=_REQUEST_TIMEOUT,
    )
    if resp.status_code != 200:
        print(f"Tidal API error: {resp.status_code}", file=sys.stderr)
        return 1

    data = resp.json()
    items = data.get("items", [])

    print("Tidal folders:")
    root_playlists = []
    for item in items:
        trn = item.get("trn", "")
        name = item.get("name", "?")
        if "folder" in trn:
            db.cache_tidal_folder(trn, name)
            # Fetch folder contents
            sub_resp = requests.get(
                "https://api.tidal.com/v2/my-collection/playlists/folders",
                headers=headers,
                params={**params, "folderId": _bare_folder_id(trn)},
                timeout=_REQUEST_TIMEOUT,
            )
            sub_items = (
                sub_resp.json().get("items", []) if sub_resp.status_code == 200 else []
            )
            print(f"\n  [{name}] ({len(sub_items)} items)")
            for s in sub_items:
                print(f"    - {s.get('name', '?')}")
        else:
            root_playlists.append(name)

    if root_playlists:
        print(f"\n  (root) ({len(root_playlists)} playlists)")
        for p in root_playlists[:10]:
            print(f"    - {p}")
        if len(root_playlists) > 10:
            print(f"    ... +{len(root_playlists) - 10} more")

    return 0


def _folders_import(db: Database) -> int:
    """Import existing Tidal folder structure."""
    client = _get_tidal_client()
    if not client:
        return 1

    import requests

    session = client._session
    headers = {"Authorization": f"Bearer {session.access_token}"}
    params = {
        "folderId": "root",
        "countryCode": session.country_code,
        "limit": "50",
        "offset": "0",
        "order": "NAME",
        "includeOnly": "",
    }

    resp = requests.get(
        "https://api.tidal.com/v2/my-collection/playlists/folders",
        headers=headers,
        params=params,
        timeout=_REQUEST_TIMEOUT,
    )
    if resp.status_code != 200:
        print(f"Tidal API error: {resp.status_code}", file=sys.stderr)
        return 1

    data = resp.json()
    items = data.get("items", [])

    imported_folders = 0
    assigned_playlists = 0

    for item in items:
        trn = item.get("trn", "")
        name = item.get("name", "?")
        if "folder" not in trn:
            continue

        db.cache_tidal_folder(trn, name)
        imported_folders += 1
        print(f"  Folder: {name} ({trn})")

        # Fetch folder contents
        sub_resp = requests.get(
            "https://api.tidal.com/v2/my-collection/playlists/folders",
            headers=headers,
            params={**params, "folderId": _bare_folder_id(trn)},
            timeout=_REQUEST_TIMEOUT,
        )
        if sub_resp.status_code != 200:
            continue

        sub_items = sub_resp.json().get("items", [])
        for s in sub_items:
            s_name = s.get("name", "")
            if not s_name:
                continue
            local = db.find_playlist_by_name(s_name)
            if local:
                db.set_playlist_tidal_folder(local.id, trn)
                assigned_playlists += 1
                print(f"    -> {s_name} (linked)")
            else:
                print(f"    -> {s_name} (no local match)")

    print(
        f"\nImported {imported_folders} folders, linked {assigned_playlists} playlists"
    )

    create = (
        input("Create local collections matching folder names? [y/N] ").strip().lower()
    )
    if create in ("y", "yes"):
        for folder in db.get_cached_tidal_folders():
            db.create_collection(folder["name"])
            for p in db.get_playlists_by_tidal_folder(folder["tidal_id"]):
                db.tag_playlist(p.id, folder["name"])
        print("Collections created and playlists tagged.")

    return 0


def _folders_create(db: Database, name: str) -> int:
    """Create a folder on Tidal."""
    client = _get_tidal_client()
    if not client:
        return 1

    # tidalapi creates folders by adding items; we need to use the API directly
    # The folder API requires creating via the session
    try:
        import requests

        session = client._session
        resp = requests.put(
            "https://api.tidal.com/v2/my-collection/playlists/folders/create-folder",
            headers={"Authorization": f"Bearer {session.access_token}"},
            params={
                "folderId": "root",
                "name": name,
                "countryCode": session.country_code,
            },
            timeout=_REQUEST_TIMEOUT,
        )
        if resp.status_code in (200, 201):
            data = resp.json()
            folder_id = data.get("data", {}).get("trn", "")
            if folder_id:
                db.cache_tidal_folder(folder_id, name)
                print(f'Created folder "{name}" on Tidal (id: {folder_id})')
                return 0
        # Fallback: try via tidalapi if available
        print(
            f"Failed to create folder: {resp.status_code} {resp.text}", file=sys.stderr
        )
        return 1
    except (OSError, ValueError, KeyError) as exc:
        print(f"Failed to create folder: {exc}", file=sys.stderr)
        return 1


def _folders_rename(db: Database, old_name: str, new_name: str) -> int:
    """Rename a folder on Tidal."""
    folder = db.get_tidal_folder_by_name(old_name)
    if not folder:
        print(
            f'Folder not found in cache: "{old_name}". Run: tuneshift folders list',
            file=sys.stderr,
        )
        return 1

    client = _get_tidal_client()
    if not client:
        return 1

    from tidalapi.exceptions import TidalAPIError

    try:
        tidal_folder = client._session.folder(_bare_folder_id(folder["tidal_id"]))
        tidal_folder.rename(new_name)
        db.cache_tidal_folder(
            folder["tidal_id"], new_name, folder.get("parent_tidal_id")
        )
        print(f'Renamed "{old_name}" to "{new_name}" on Tidal')
        return 0
    except (OSError, RuntimeError, TidalAPIError) as exc:
        print(f"Failed to rename: {exc}", file=sys.stderr)
        return 1


def _folders_delete(db: Database, name: str) -> int:
    """Delete a folder on Tidal."""
    folder = db.get_tidal_folder_by_name(name)
    if not folder:
        print(
            f'Folder not found in cache: "{name}". Run: tuneshift folders list',
            file=sys.stderr,
        )
        return 1

    # Show affected playlists
    affected = db.get_playlists_by_tidal_folder(folder["tidal_id"])
    if affected:
        print(f'Deleting "{name}" will unassign {len(affected)} playlists:')
        for p in affected:
            print(f"  - {p.name}")

    confirm = input(f'Delete folder "{name}" on Tidal? [y/N] ').strip().lower()
    if confirm not in ("y", "yes"):
        print("Cancelled.")
        return 0

    client = _get_tidal_client()
    if not client:
        return 1

    from tidalapi.exceptions import TidalAPIError

    try:
        tidal_folder = client._session.folder(_bare_folder_id(folder["tidal_id"]))
        tidal_folder.remove()
        count = db.clear_tidal_folder_assignments(folder["tidal_id"])
        db.remove_tidal_folder_cache(folder["tidal_id"])
        print(f'Deleted "{name}" on Tidal. {count} playlists moved to root.')
        return 0
    except (OSError, RuntimeError, TidalAPIError) as exc:
        print(f"Failed to delete: {exc}", file=sys.stderr)
        return 1


def _folders_move(db: Database, playlist_name: str, folder_name: str) -> int:
    """Assign a playlist to a Tidal folder."""
    playlist = db.find_playlist_by_name(playlist_name)
    if not playlist:
        print(f"Playlist not found: {playlist_name}", file=sys.stderr)
        return 1

    folder = db.get_tidal_folder_by_name(folder_name)
    if not folder:
        print(
            f'Folder not found in cache: "{folder_name}". Run: tuneshift folders list',
            file=sys.stderr,
        )
        return 1

    db.set_playlist_tidal_folder(playlist.id, folder["tidal_id"])
    print(
        f'Assigned "{playlist.name}" to folder "{folder_name}". Run: tuneshift folders sync'  # noqa: E501
    )
    return 0


def _folders_unassign(db: Database, playlist_name: str) -> int:
    """Remove folder assignment from a playlist."""
    playlist = db.find_playlist_by_name(playlist_name)
    if not playlist:
        print(f"Playlist not found: {playlist_name}", file=sys.stderr)
        return 1

    db.set_playlist_tidal_folder(playlist.id, None)
    print(f'Unassigned "{playlist.name}" from folder. Will move to root on next sync.')
    return 0


def _folders_sync(db: Database) -> int:
    """Push all folder assignments to Tidal."""
    client = _get_tidal_client()
    if not client:
        return 1

    import requests
    from tidalapi.exceptions import TidalAPIError

    session = client._session
    headers = {"Authorization": f"Bearer {session.access_token}"}

    # Refresh folder cache
    resp = requests.get(
        "https://api.tidal.com/v2/my-collection/playlists/folders",
        headers=headers,
        params={
            "folderId": "root",
            "countryCode": session.country_code,
            "limit": "50",
            "offset": "0",
            "order": "NAME",
            "includeOnly": "",
        },
        timeout=_REQUEST_TIMEOUT,
    )
    if resp.status_code == 200:
        for item in resp.json().get("items", []):
            trn = item.get("trn", "")
            if "folder" in trn:
                db.cache_tidal_folder(trn, item.get("name", ""))

    # Get all playlists with folder assignments
    all_playlists = db.list_playlists()
    moved = 0
    errors = 0

    for playlist in all_playlists:
        if not playlist.tidal_folder_id:
            continue

        platform_id = db.get_platform_playlist_id(playlist.id, "tidal")
        if not platform_id:
            continue

        try:
            target_folder = session.folder(_bare_folder_id(playlist.tidal_folder_id))
            playlist_trn = f"trn:playlist:{platform_id}"
            target_folder.add_items([playlist_trn])
            moved += 1
            folder_info = next(
                (
                    f
                    for f in db.get_cached_tidal_folders()
                    if f["tidal_id"] == playlist.tidal_folder_id
                ),
                None,
            )
            folder_name = (
                folder_info["name"] if folder_info else playlist.tidal_folder_id
            )
            print(f"  {playlist.name} -> {folder_name}")
        except (OSError, RuntimeError, ValueError, TidalAPIError) as exc:
            print(f"  {playlist.name}: failed ({exc})", file=sys.stderr)
            errors += 1

    print(f"\nSynced: {moved} moved, {errors} errors")
    return 0


def _folders_pull(db: Database) -> int:
    """Update local folder assignments from Tidal state."""
    client = _get_tidal_client()
    if not client:
        return 1

    import requests

    session = client._session
    headers = {"Authorization": f"Bearer {session.access_token}"}
    params = {
        "folderId": "root",
        "countryCode": session.country_code,
        "limit": "50",
        "offset": "0",
        "order": "NAME",
        "includeOnly": "",
    }

    resp = requests.get(
        "https://api.tidal.com/v2/my-collection/playlists/folders",
        headers=headers,
        params=params,
        timeout=_REQUEST_TIMEOUT,
    )
    if resp.status_code != 200:
        print(f"Tidal API error: {resp.status_code}", file=sys.stderr)
        return 1

    # Clear all local assignments
    db.clear_all_tidal_folder_assignments(commit=False)
    updated = 0

    for item in resp.json().get("items", []):
        trn = item.get("trn", "")
        name = item.get("name", "")
        if "folder" not in trn:
            continue

        db.cache_tidal_folder(trn, name)

        # Fetch folder contents
        sub_resp = requests.get(
            "https://api.tidal.com/v2/my-collection/playlists/folders",
            headers=headers,
            params={**params, "folderId": _bare_folder_id(trn)},
            timeout=_REQUEST_TIMEOUT,
        )
        if sub_resp.status_code != 200:
            continue

        for s in sub_resp.json().get("items", []):
            s_name = s.get("name", "")
            if s_name:
                local = db.find_playlist_by_name(s_name)
                if local:
                    db.set_playlist_tidal_folder(local.id, trn)
                    updated += 1

    db.conn.commit()
    print(f"Pulled Tidal folder state: {updated} playlists updated")
    return 0


def _folders_status(db: Database) -> int:
    """Show local vs Tidal folder state."""
    all_playlists = db.list_playlists()
    unassigned = [p for p in all_playlists if not p.tidal_folder_id]

    folders = db.get_cached_tidal_folders()

    print("Folder assignments:")
    for folder in folders:
        playlists = db.get_playlists_by_tidal_folder(folder["tidal_id"])
        print(f"\n  [{folder['name']}] ({len(playlists)} playlists)")
        for p in playlists:
            print(f"    - {p.name}")

    if unassigned:
        print(f"\n  (root) ({len(unassigned)} playlists)")
        for p in unassigned[:10]:
            print(f"    - {p.name}")
        if len(unassigned) > 10:
            print(f"    ... +{len(unassigned) - 10} more")

    return 0
