"""Alias command: manage artist-alias equivalence classes.

An alias class is a set of artist surface forms that denote the *same act* but
are spelled differently across platforms (``98 Degrees`` / ``98\u00b0`` /
``98\u00ba``; ``Ke$ha`` / ``Kesha``). The matcher uses these both to score an
aliased artist as an exact match and to expand retrieval so a track indexed
under a variant spelling still surfaces (see :mod:`tuneshift.matching.aliases`
and :mod:`tuneshift.reconcile`). This command is the user-facing surface for the
DB override layer that stacks on top of the built-in seed.

Grammar::

    tuneshift alias list
    tuneshift alias show ARTIST
    tuneshift alias add MEMBER MEMBER [MEMBER ...]
    tuneshift alias remove MEMBER
"""

from __future__ import annotations

from tuneshift.db import Database
from tuneshift.matching.aliases import AliasResolver, default_resolver
from tuneshift.matching.normalize import normalize_artist


def _seed_classes() -> list[frozenset[str]]:
    """The static seed classes (raw surface forms), independent of the DB."""
    return default_resolver().raw_classes()


def _tag(members: frozenset[str], seed_keys: set[str], db_keys: set[str]) -> str:
    """Label a class by whether its members come from the seed, the DB, or both."""
    keys = {normalize_artist(m) for m in members}
    in_seed = bool(keys & seed_keys)
    in_db = bool(keys & db_keys)
    if in_seed and in_db:
        return "seed+user"
    if in_seed:
        return "seed"
    return "user"


def handle_alias(args, db: Database) -> int:
    """Dispatch the ``alias`` subcommands."""
    if args.action == "list":
        return _handle_list(db)
    if args.action == "show":
        return _handle_show(args, db)
    if args.action == "add":
        return _handle_add(args, db)
    if args.action == "remove":
        return _handle_remove(args, db)
    print(f"Unknown action: {args.action}")
    return 1


def _merged_classes(db: Database) -> list[frozenset[str]]:
    """Seed + DB classes, merged by the resolver's own union-on-key logic."""
    resolver = AliasResolver(db_classes=db.get_artist_alias_classes())
    return resolver.raw_classes()


def _handle_list(db: Database) -> int:
    seed_keys = {normalize_artist(m) for c in _seed_classes() for m in c}
    db_keys = {normalize_artist(m) for c in db.get_artist_alias_classes() for m in c}
    classes = _merged_classes(db)
    if not classes:
        print("No artist alias classes.")
        return 0
    print("Artist alias classes:")
    for members in sorted(classes, key=lambda c: sorted(c)[0].lower()):
        tag = _tag(members, seed_keys, db_keys)
        print(f"    [{tag}] {', '.join(sorted(members))}")
    return 0


def _handle_show(args, db: Database) -> int:
    query_norm = normalize_artist(args.artist)
    for members in _merged_classes(db):
        if any(normalize_artist(m) == query_norm for m in members):
            print(f'"{args.artist}" belongs to an alias class:')
            print(f"    {', '.join(sorted(members))}")
            return 0
    print(f'"{args.artist}" is not in any alias class.')
    return 0


def _handle_add(args, db: Database) -> int:
    members = [m.strip() for m in args.members if m and m.strip()]
    if len(set(members)) < 2:
        print("alias add needs at least two distinct members.")
        return 1
    db.add_artist_alias(members)
    print(f"Added alias class: {', '.join(sorted(set(members)))}")
    return 0


def _handle_remove(args, db: Database) -> int:
    if db.remove_artist_alias(args.member):
        print(f'Removed "{args.member}" from its alias class.')
        return 0
    # Distinguish a seed-only member (read-only) from an absent one.
    query_norm = normalize_artist(args.member)
    for members in _seed_classes():
        if any(normalize_artist(m) == query_norm for m in members):
            print(f'"{args.member}" is a built-in seed alias and cannot be removed.')
            return 1
    print(f'"{args.member}" is not in any user-defined alias class.')
    return 1
