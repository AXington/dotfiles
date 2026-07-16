"""Artist-alias equivalence classes.

Some artists publish the *same act* under different surface names — a stylized
glyph ("98\u00b0" / "98\u00ba" / "98 Degrees"), a rebrand ("Ke$ha" -> "Kesha"), and so on.
Plain normalization cannot bridge every such case: ``normalize_artist`` maps
"98\u00b0" (U+00B0 degree sign) to "98 degrees", but "98\u00ba" (U+00BA masculine ordinal
indicator) is a legitimate Spanish/Portuguese ordinal, so it must *not* be
folded globally. A curated equivalence class bridges the gap safely.

Two representations per class, and they are NOT interchangeable:

* **Equivalence keys** -- each member run through ``normalize_artist`` -- decide
  "same act?" for *scoring*. "98\u00b0" and "98 Degrees" share the key
  "98 degrees".
* **Raw surface forms** -- the strings as a platform actually publishes them --
  drive *retrieval* query expansion. "98\u00b0", "98\u00ba" and "98 Degrees" are three
  distinct query spellings; a platform may index a track under only one.

Members are therefore stored as raw surface strings; the resolver derives the
normalized equivalence key internally. ``canonical``/``same_class`` operate on
already-normalized names (scoring); ``variants_for_query`` takes a raw name and
returns the other raw forms (retrieval).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from tuneshift.matching.normalize import normalize_artist

# Curated seed classes, as RAW surface forms. Conservative on purpose: a bad
# entry risks a false-positive merge for every user.
#   * 98 Degrees family: "98\u00b0" and "98 Degrees" already share the normalized
#     key "98 degrees"; the class is what bridges the "98\u00ba" (U+00BA) glyph that
#     normalize_artist alone cannot, and it lets retrieval query all three
#     surface spellings.
#   * Ke$ha / Kesha: a documented, unambiguous rebrand.
# "P!nk"/"Pink" is deliberately excluded -- "pink" is a common word and risks
# merging a different artist. Users can add it per-library via ``alias add``.
_SEED_CLASSES: tuple[frozenset[str], ...] = (
    frozenset({"98 Degrees", "98\u00b0", "98\u00ba"}),
    frozenset({"Ke$ha", "Kesha"}),
)


def canonicalize_raw(member: str) -> str:
    """The stored identity of a raw surface form: surrounding whitespace only.

    Case and glyphs are preserved, so "98\u00b0" and "98 Degrees" stay distinct while
    " 98\u00b0 " and "98\u00b0" collapse to the same stored member.
    """
    return member.strip()


def _merge_classes(raw_classes: Iterable[Iterable[str]]) -> list[frozenset[str]]:
    """Union raw-form classes whose normalized keys overlap.

    ``{a, b}`` and ``{b, c}`` (by normalized key) merge to ``{a, b, c}``.
    Classes with fewer than two distinct raw members are dropped. Raw surface
    forms are always retained; the normalized keys only decide *which* classes
    merge.
    """
    # Each pending class carries its raw members and the set of normalized keys.
    pending: list[tuple[set[str], set[str]]] = []
    for raw_class in raw_classes:
        raws = {canonicalize_raw(m) for m in raw_class if canonicalize_raw(m)}
        if not raws:
            continue
        keys = {normalize_artist(m) for m in raws}
        keys.discard("")
        if not keys:
            continue
        pending.append((raws, keys))

    merged: list[tuple[set[str], set[str]]] = []
    for raws, keys in pending:
        target: tuple[set[str], set[str]] | None = None
        for existing in merged:
            if existing[1] & keys:
                target = existing
                break
        if target is None:
            merged.append((set(raws), set(keys)))
        else:
            target[0].update(raws)
            target[1].update(keys)
            # A newly-merged class can now bridge others; fold any that overlap.
            _absorb_overlaps(merged, target)

    return [frozenset(raws) for raws, _keys in merged if len(raws) >= 2]


def _absorb_overlaps(
    merged: list[tuple[set[str], set[str]]],
    target: tuple[set[str], set[str]],
) -> None:
    """Fold any class whose keys now overlap ``target`` into ``target``."""
    changed = True
    while changed:
        changed = False
        for other in list(merged):
            if other is target:
                continue
            if other[1] & target[1]:
                target[0].update(other[0])
                target[1].update(other[1])
                merged.remove(other)
                changed = True


class AliasResolver:
    """Resolves artist names to curated equivalence classes.

    Built from a static seed plus optional DB-provided classes, all merged by
    union on their normalized keys. Deterministic and order-independent.
    """

    def __init__(
        self,
        seed: Sequence[Iterable[str]] = _SEED_CLASSES,
        db_classes: Sequence[Iterable[str]] | None = None,
    ) -> None:
        all_classes: list[Iterable[str]] = list(seed)
        if db_classes:
            all_classes.extend(db_classes)
        self._classes: list[frozenset[str]] = _merge_classes(all_classes)

        # Map each normalized key -> its class canonical key (lex-smallest key).
        self._key_to_canonical: dict[str, str] = {}
        # Map each normalized key -> the class's raw members (for expansion).
        self._key_to_raws: dict[str, frozenset[str]] = {}
        for raws in self._classes:
            keys = sorted(k for k in (normalize_artist(m) for m in raws) if k)
            if not keys:
                continue
            canonical = keys[0]
            for key in keys:
                self._key_to_canonical[key] = canonical
                self._key_to_raws[key] = raws

    def raw_classes(self) -> list[frozenset[str]]:
        """All merged classes as frozensets of raw surface members."""
        return list(self._classes)

    def canonical(self, normalized_name: str) -> str:
        """Canonical key for an ALREADY-normalized name.

        Returns the class's canonical key (lexicographically-smallest normalized
        member) or the input unchanged when it belongs to no class -- so a
        non-member compares byte-identically to today.
        """
        return self._key_to_canonical.get(normalized_name, normalized_name)

    def same_class(self, normalized_a: str, normalized_b: str) -> bool:
        """True iff two already-normalized names share a canonical key."""
        return self.canonical(normalized_a) == self.canonical(normalized_b)

    def variants_for_query(self, raw_name: str) -> list[str]:
        """OTHER raw surface members of ``raw_name``'s class, for retrieval.

        Takes a RAW artist name (e.g. ``track.artist``). Returns the class's
        other raw forms (deterministically sorted), excluding any that share the
        queried name's own surface spelling, or ``[]`` when the name is in no
        class.
        """
        key = normalize_artist(raw_name)
        raws = self._key_to_raws.get(key)
        if not raws:
            return []
        queried = canonicalize_raw(raw_name)
        return sorted(m for m in raws if m != queried)


_DEFAULT_RESOLVER: AliasResolver | None = None


def default_resolver() -> AliasResolver:
    """A shared seed-only resolver (pure, no DB), for scoring without context."""
    global _DEFAULT_RESOLVER
    if _DEFAULT_RESOLVER is None:
        _DEFAULT_RESOLVER = AliasResolver()
    return _DEFAULT_RESOLVER
