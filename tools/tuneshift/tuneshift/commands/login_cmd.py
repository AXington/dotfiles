"""Login command: authenticate with a streaming platform."""

import sys


def _session_is_valid(client) -> bool:
    """Return True only if a loaded session is actually usable.

    ``load_session()`` can succeed on a structurally-valid but expired/revoked
    token. Concrete clients expose ``_ensure_session()``, which performs the
    real validity check (e.g. ``check_login()``) and raises ``RuntimeError``
    when the session is not usable. If a client has no such check, we cannot
    prove validity, so treat it as valid to preserve existing behaviour.
    """
    ensure = getattr(client, "_ensure_session", None)
    if ensure is None:
        return True
    try:
        ensure()
    except RuntimeError:
        return False
    return True


def handle_login(args, db) -> int:
    """Authenticate with a streaming platform."""
    from tuneshift.commands.ingest_cmd import _load_client

    client = _load_client(args.platform)
    if client is None:
        print(f"Unknown platform: {args.platform}", file=sys.stderr)
        return 1

    # Check if already logged in — load_session() only confirms a token file
    # loads structurally, so validate the session is actually usable before
    # short-circuiting. An expired/revoked session falls through to re-auth.
    if client.load_session():
        if _session_is_valid(client):
            print(f"Already authenticated with {args.platform}.")
            return 0
        print(f"Saved {args.platform} session is expired; re-authenticating...")

    print(f"Logging in to {args.platform}...")

    # Tidal uses a two-step flow: login() returns URL, login_wait() polls
    # YT Music uses a single-step flow: login() runs interactively and returns bool
    if hasattr(client, "login_wait"):
        url = client.login()
        print(f"\nOpen this URL to authenticate:\n  {url}\n")
        print("Waiting for authorization...")
        if client.login_wait(timeout=300.0):
            print(f"Authenticated with {args.platform}.")
            return 0
        else:
            print(f"Authentication timed out for {args.platform}.", file=sys.stderr)
            return 1
    else:
        result = client.login()
        if result:
            print(f"Authenticated with {args.platform}.")
            return 0
        else:
            print(f"Authentication failed for {args.platform}.", file=sys.stderr)
            return 1
