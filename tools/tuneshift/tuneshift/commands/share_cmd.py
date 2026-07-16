"""Share command: generate shareable URLs for a playlist across platforms."""

# Platform URL templates (playlist ID -> public URL)
PLATFORM_URL_TEMPLATES = {
    "tidal": "https://tidal.com/playlist/{id}",
    "ytmusic": "https://music.youtube.com/playlist?list={id}",
    "spotify": "https://open.spotify.com/playlist/{id}",
}

PLATFORM_DISPLAY_NAMES = {
    "tidal": "Tidal",
    "ytmusic": "YouTube Music",
    "spotify": "Spotify",
}


def handle_share(args, db) -> int:
    """Generate shareable links for a playlist."""
    playlist = db.find_playlist_by_name(args.name)
    if not playlist:
        print(f"Playlist not found: {args.name}")
        return 1

    linked = db.get_linked_platforms(playlist.id)
    if not linked:
        print(f"No platform links found for '{playlist.name}'")
        print("Sync the playlist first: tuneshift sync <name> <platform>")
        return 1

    links: list[tuple[str, str]] = []
    for platform in linked:
        pid = db.get_platform_playlist_id(playlist.id, platform)
        if not pid:
            continue
        template = PLATFORM_URL_TEMPLATES.get(platform)
        if template:
            url = template.format(id=pid)
            links.append((platform, url))
        else:
            links.append((platform, f"(no URL template for {platform}, ID: {pid})"))

    if not links:
        print("No shareable links available.")
        return 1

    fmt = getattr(args, "format", "plain")

    if fmt == "markdown":
        print(f"**{playlist.name}**\n")
        for platform, url in links:
            name = PLATFORM_DISPLAY_NAMES.get(platform, platform)
            print(f"- [{name}]({url})")
    elif fmt == "slack":
        print(f"*{playlist.name}*\n")
        for platform, url in links:
            name = PLATFORM_DISPLAY_NAMES.get(platform, platform)
            print(f":headphones: <{url}|{name}>")
    elif fmt == "discord":
        print(f"**{playlist.name}**\n")
        for platform, url in links:
            name = PLATFORM_DISPLAY_NAMES.get(platform, platform)
            print(f":headphones: [{name}](<{url}>)")
    elif fmt == "urls":
        for _platform, url in links:
            print(url)
    else:
        # plain
        print(f"{playlist.name}\n")
        for platform, url in links:
            name = PLATFORM_DISPLAY_NAMES.get(platform, platform)
            print(f"  {name}: {url}")

    return 0
