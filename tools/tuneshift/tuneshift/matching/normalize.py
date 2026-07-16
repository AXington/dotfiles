"""Normalization primitives and version-keyword regexes.

This module holds the string-normalization functions (`normalize_title`,
`normalize_artist`, `is_remaster`) and every compiled regex used by the
matching layer: the edition/feat-stripping patterns consumed by normalization
and the version-keyword patterns (live/remix/karaoke/...) consumed by the
scorers in `track.py`. Keeping the patterns in one place prevents the
"four copies of edition knowledge" drift the overhaul is undoing.
"""

import re
import unicodedata
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tuneshift.matching.aliases import AliasResolver

_EDITION_PARENS_RE = re.compile(
    r"\s*\("
    r"(?:\d{4}\s*)?"
    r"(?:\d+(?:st|nd|rd|th)\s+)?"
    r"(?:Remastered|Remaster|Deluxe Edition|Deluxe Version|Deluxe|"
    r"Digital Deluxe|Mono|Stereo|"
    r"Expanded Edition|Expanded|Anniversary Edition|Anniversary|"
    r"Super Deluxe|Special Edition|"
    r"\d{4}\s+(?:Re)?[Mm]ix|Stereo Mix|Mono Mix|"
    r"Taylor's Version)[^)]*\)",
    re.IGNORECASE,
)
_THE_PREFIX_RE = re.compile(r"^the\s+", re.IGNORECASE)
_FEAT_PAREN_RE = re.compile(
    r"\s*[\(\[]\s*(?:feat\.?|ft\.?|featuring|with)\s+[^\)\]]+[\)\]]",
    re.IGNORECASE,
)
_FEAT_BARE_RE = re.compile(
    r"\s+(?:feat\.?|ft\.?|featuring)\s+.+$",
    re.IGNORECASE,
)
# Explicit/clean *labels* are version-axis markers, not part of the title.
# Version detection reads the raw title/album (infer_version), so stripping the
# label here only cleans the string used for title *similarity*.
_EXPLICIT_CLEAN_LABEL_RE = re.compile(
    r"\s*[\(\[]\s*(?:explicit|clean)(?:\s+version)?\s*[\)\]]",
    re.IGNORECASE,
)
_PUNCT_NORMALIZE = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2026": "...",
        "\u2013": "-",
        "\u2014": "-",
        "\u2015": "-",  # en/em/horizontal dashes
        "\u00a0": " ",  # non-breaking space
        "\u00b0": " degrees ",  # degree sign: e.g. "98 deg" -> "98 degrees" (band names)  # noqa: E501
    }
)
_WHITESPACE_RE = re.compile(r"\s+")
# Expand "Pt." / "Pt" part-abbreviations to "Part" so multi-part titles match.
# Word-boundaried on both sides so "Ptolemy" is untouched.
_PART_ABBREV_RE = re.compile(r"\bpt\.?(?=\s|$)", re.IGNORECASE)
# Latin combining diacritical marks only (U+0300-U+036F). Deliberately excludes
# CJK/kana combining marks (e.g. Japanese dakuten U+3099-U+309A) so folding never
# alters a non-Latin script.
_LATIN_COMBINING_RE = re.compile(r"[\u0300-\u036f]")
# Separators that join collaborating artists in a single credit string.
_ARTIST_SPLIT_RE = re.compile(
    r"\s*(?:,|&|\band\b|\bfeaturing\b|\bfeat\.?(?=\s)|\bft\.?(?=\s)|"
    r"\bwith\b|\bx\b|\u00d7|/)\s*",
    re.IGNORECASE,
)
_BOUNDARY_PUNCT_RE = re.compile(
    r"(?:^[^\w]+|[^\w]+$)"
)  # Leading/trailing non-word chars
_STANDALONE_PUNCT_RE = re.compile(
    r"\s+[^\w\s]+\s+"
)  # Standalone punctuation between words
# The FEATURED-credit boundary: everything after an explicit feat/ft/featuring
# marker is a featured artist, not a main one (M5). Deliberately does NOT include
# "with"/"&"/","/"x" - those join co-billed MAIN artists and stay in the main set.
_FEAT_ROLE_SPLIT_RE = re.compile(
    r"\s+(?:feat\.?|ft\.?|featuring)\s+",
    re.IGNORECASE,
)


def fold_accents(text: str) -> str:
    """Strip Latin diacritics while leaving non-Latin scripts intact.

    Decomposes to NFD, removes only the Latin combining-mark range
    (U+0300-U+036F), and recomposes. "Beyonce" (e-acute) -> "Beyonce" and
    "Motorhead" (o-umlaut) -> "Motorhead", but CJK, kana (including
    dakuten-bearing kana such as GA), and Hangul pass through unchanged: we
    never transliterate or romanize a non-Latin script.
    """
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFD", text)
    stripped = _LATIN_COMBINING_RE.sub("", decomposed)
    return unicodedata.normalize("NFC", stripped)


def split_artists(name: str, *, resolver: "AliasResolver | None" = None) -> set[str]:
    """Split a multi-artist credit into a set of normalized artist tokens.

    "Jay-Z & Alicia Keys" and "Alicia Keys & Jay-Z" both yield
    {"jay-z", "alicia keys"}, so collaborator order does not matter when
    comparing. Splits on commas, ampersands, "and", feat/ft/featuring/with, "x",
    "\u00d7", and slashes. Each token is accent-folded and casefolded.

    Each token is then mapped through the alias resolver's canonical key so an
    aliased collaborator (e.g. ``98\u00ba``) collapses onto its canonical form
    before set comparison. ``resolver`` defaults to the seed-only resolver; a
    non-member token maps to itself, leaving un-aliased credits unchanged.
    """
    if not name:
        return set()
    from tuneshift.matching.aliases import default_resolver

    resolver = resolver or default_resolver()
    folded = fold_accents(name).translate(_PUNCT_NORMALIZE)
    parts = _ARTIST_SPLIT_RE.split(folded)
    return {resolver.canonical(p.strip().casefold()) for p in parts if p and p.strip()}


def artist_set_overlap(
    source_name: str,
    candidate_name: str,
    *,
    resolver: "AliasResolver | None" = None,
) -> float:
    """Fraction of the source's artists also credited on the candidate.

    Returns overlap of the source artist set with the candidate set, in
    [0.0, 1.0]. 1.0 means every source artist appears on the candidate
    regardless of order; 0.0 means no shared artist. Order-independent. Tokens
    are alias-canonicalized (see :func:`split_artists`) so a credit that names an
    aliased collaborator still overlaps its equivalent surface form.
    """
    src = split_artists(source_name, resolver=resolver)
    cand = split_artists(candidate_name, resolver=resolver)
    if not src or not cand:
        return 0.0
    return len(src & cand) / len(src)


def split_artist_roles(
    name: str, *, resolver: "AliasResolver | None" = None
) -> tuple[set[str], set[str]]:
    """Split a credit into ``(main_artists, featured_artists)`` sets (M5).

    The portion before an explicit ``feat.``/``ft.``/``featuring`` marker is the
    MAIN credit; everything after is FEATURED. Co-billed main artists joined by
    ``&``/``and``/``,``/``x``/``with`` stay together in the main set, only the
    feat marker separates roles. Each side is normalized/alias-canonicalized via
    :func:`split_artists` so role sets compare on the same basis as the flat
    overlap. A credit with no feat marker has an empty featured set.

    KNOWN COVERAGE GAP (spec section 11 M5): reliable role data ultimately wants the
    MusicBrainz artist-credit ``joinphrase``/role fields, which are inconsistent
    across releases, many carry flat artist strings with no role breakdown. This
    string-marker heuristic is the best signal available from the platform credit
    alone; a low fire-rate on role distinctions is expected, not a bug.
    """
    if not name:
        return set(), set()
    parts = _FEAT_ROLE_SPLIT_RE.split(name, maxsplit=1)
    main = split_artists(parts[0], resolver=resolver)
    featured = split_artists(parts[1], resolver=resolver) if len(parts) > 1 else set()
    # A featured artist is never also counted as main.
    return main, featured - main


# Re-recording markers a title may carry (M2, AC-M2). Matched on the RAW title
# BEFORE `_EDITION_PARENS_RE` strips "(Taylor's Version)", so the deliberate
# re-recording stays selectable. Each pattern maps to a canonical marker token
# a preference can target (e.g. ``prefer work=taylors version``).
_RERECORDING_MARKERS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"taylor'?s\s+version", re.IGNORECASE), "taylors version"),
    (re.compile(r"re-?recorded", re.IGNORECASE), "re-recorded"),
    (re.compile(r"re-?recording", re.IGNORECASE), "re-recorded"),
)


def extract_rerecording_marker(title: str | None) -> str | None:
    """Return the canonical re-recording marker in a raw title, else ``None`` (M2).

    Scans the *raw* title for deliberate re-recording markers ("Taylor's
    Version", "Re-Recorded", "Re-Recording") and returns a canonical token
    (``"taylors version"`` / ``"re-recorded"``) that a ``work``-axis preference
    can target. Runs on the unmodified title so the marker is captured before
    :data:`_EDITION_PARENS_RE` strips the parenthetical during title
    normalization. A title with no such marker is an original recording and
    returns ``None``.
    """
    if not title:
        return None
    for pattern, marker in _RERECORDING_MARKERS:
        if pattern.search(title):
            return marker
    return None


# A parenthetical or bracketed group anywhere in a title.
_PAREN_GROUP_RE = re.compile(r"\s*[\(\[]([^\)\]]*)[\)\]]")


def strip_album_from_title(title: str, album: str | None) -> str:
    """Remove a parenthetical from a title when it merely repeats the album.

    Some sources corrupt a track title by appending the album name in
    parentheses, e.g. ``"Femininomenon (The Rise and Fall of a Midwest
    Princess)"`` where the album *is* "The Rise and Fall of a Midwest Princess".
    That parenthetical is noise that tanks title similarity against the clean
    catalogue title and pollutes the search query.

    A parenthetical group is removed only when its normalized contents closely
    match the normalized album (ratio >= 0.9), so genuine subtitles like
    ``"(You Drive Me) Crazy"`` are preserved. If nothing matches, the title is
    returned unchanged. This is album-aware and therefore lives outside
    ``normalize_title`` (which has no album context).
    """
    if not title or not album:
        return title
    from tuneshift.matching.similarity import ratio

    norm_album = normalize_title(album)
    if not norm_album:
        return title

    def _maybe_strip(match: re.Match[str]) -> str:
        inner = normalize_title(match.group(1))
        if inner and ratio(inner, norm_album) >= 0.9:
            return ""
        return match.group(0)

    cleaned = _PAREN_GROUP_RE.sub(_maybe_strip, title)
    return _WHITESPACE_RE.sub(" ", cleaned).strip() or title


def normalize_title(title: str) -> str:
    """Normalize a track/album title for comparison."""
    if not title:
        return ""
    title = unicodedata.normalize("NFC", title)
    title = fold_accents(title)
    title = title.translate(_PUNCT_NORMALIZE)
    title = _PART_ABBREV_RE.sub("Part", title)
    title = _EDITION_PARENS_RE.sub("", title)
    title = _EXPLICIT_CLEAN_LABEL_RE.sub("", title)
    title = _FEAT_PAREN_RE.sub("", title)
    title = _FEAT_BARE_RE.sub("", title)
    return _WHITESPACE_RE.sub(" ", title).strip().casefold()


def normalize_artist(artist: str) -> str:
    """Normalize an artist name for comparison.

    Strips feat text, punctuation, articles, and casefolds so that
    legitimate variants (P!nk/Pink, feat. suffix) become identical.
    """
    if not artist:
        return ""
    artist = unicodedata.normalize("NFC", artist)
    artist = fold_accents(artist)
    artist = artist.translate(_PUNCT_NORMALIZE)
    artist = _FEAT_PAREN_RE.sub("", artist)
    artist = _FEAT_BARE_RE.sub("", artist)
    artist = artist.strip()
    artist = _THE_PREFIX_RE.sub("", artist)
    artist = artist.replace("&", "and")
    artist = artist.replace(",", "")
    # Asterisk is a stylized space/nothing in band names (B*Witched, *NSYNC),
    # never a meaningful mid-word glyph like P!nk's "!" or A$AP's "$".
    artist = artist.replace("*", " ")
    # Strip boundary punctuation but preserve mid-word chars (P!nk stays as p!nk)
    artist = _BOUNDARY_PUNCT_RE.sub("", artist)
    artist = _STANDALONE_PUNCT_RE.sub(" ", artist)
    artist = _WHITESPACE_RE.sub(" ", artist)
    return artist.casefold().strip()


def is_remaster(album: str) -> bool:
    """Check if an album name indicates a remaster."""
    if not album:
        return False
    return "remaster" in album.casefold()


_LIVE_RE = re.compile(
    r"\b(live|concert|in concert|unplugged|mtv unplugged|"
    r"here and there|one night only|"
    r"17-11-70|11-17-70)\b",
    re.IGNORECASE,
)
_REMIX_RE = re.compile(
    r"\b(remix|remixed|performance mix|extended mix|"
    r"extended version|dub mix|club mix|12[\"'] mix|"
    r"12 inch|maxi|"
    # Named/qualified reworks: a distinct reworking (often by a named producer),
    # NOT the album master. Deliberately narrow and unambiguous so it never
    # catches packaging edits (radio/single/album edit or version, handled by
    # _RADIO_EDIT_RE) or album-edition names ("Deluxe Edition": "edit" only
    # appears inside "edition", which has no trailing word boundary).
    r"re-?edit|rework|new edit|new mix|special mix|vocal mix|"
    r"bootleg|mash-?up)\b",
    re.IGNORECASE,
)
_REMASTER_RE = re.compile(r"\b(remaster(?:ed)?)\b", re.IGNORECASE)
_DELUXE_RE = re.compile(
    r"\b(deluxe|expanded|anniversary|special edition|bonus track|"
    r"celebration)\b",
    re.IGNORECASE,
)
_COMPILATION_RE = re.compile(
    r"\b(greatest hits|best of|essentials|collection|anthology|"
    r"now that's what|various artists|soundtrack|love songs|"
    r"to be continued|diamonds|the definitive|rocket man:\s|"
    r"the very best)\b",
    re.IGNORECASE,
)
_TRIBUTE_RE = re.compile(r"\b(tribute|reimagin|covers?|revamp)\b", re.IGNORECASE)
_KARAOKE_RE = re.compile(r"\b(karaoke)\b", re.IGNORECASE)
_INSTRUMENTAL_RE = re.compile(r"\b(instrumental)\b", re.IGNORECASE)
_ACOUSTIC_RE = re.compile(r"\b(acoustic|stripped)\b", re.IGNORECASE)
_RADIO_EDIT_RE = re.compile(
    r"\b(radio edit|radio version|single edit|single version)\b",
    re.IGNORECASE,
)
_SPED_UP_RE = re.compile(
    r"\b(sped[\s-]?up|slowed(?:\s+down)?|nightcore|daycore)\b",
    re.IGNORECASE,
)
# M1 - DJ/continuous-mix markers safe to read from FREE TEXT (title/album):
# only unambiguous phrases, never a bare "mixed" (which is a song word, e.g.
# "Mixed Emotions"). A crossfaded/beatmatched mix track ruins standalone
# playback, so it is a distinct recording that must be avoided by default.
_CONTINUOUS_MIX_RE = re.compile(
    r"\b(continuous(?:\s+dj)?\s+mix|dj\s+mix|non[\s-]?stop\s+mix|"
    r"mega\s?mix|gapless(?:\s+mix)?|mixed\s+by)\b",
    re.IGNORECASE,
)
# The Tidal `version` field is a controlled vocabulary where a bare "Mixed" or
# "Continuous" denotes a continuous DJ mix. It is a STRUCTURED field, so these
# broader tokens are trusted from it (but never inferred from a free-text
# title/album - see ``infer_version``).
_CONTINUOUS_MIX_VERSION_RE = re.compile(
    r"\b(mixed|continuous|dj\s+mix|non[\s-]?stop|mega\s?mix|gapless)\b",
    re.IGNORECASE,
)

# Recording/edition version markers whose presence in a *title* should not
# reduce title similarity: the source-aware version axis (which reads the raw
# title via ``infer_version``) owns the "same version?" judgement, so the
# similarity title only needs to answer "same song?".
_VERSION_MARKER_REGEXES = (
    _LIVE_RE,
    _REMIX_RE,
    _REMASTER_RE,
    _ACOUSTIC_RE,
    _KARAOKE_RE,
    _INSTRUMENTAL_RE,
    _TRIBUTE_RE,
    _RADIO_EDIT_RE,
    _DELUXE_RE,
    _COMPILATION_RE,
    _SPED_UP_RE,
    _CONTINUOUS_MIX_RE,
)
_DASH_SUFFIX_RE = re.compile(r"\s+[-\u2013\u2014]\s+([^-\u2013\u2014]+)$")
_TRAILING_PAREN_RE = re.compile(r"\s*[\(\[][^\(\)\[\]]*[\)\]]\s*$")


def strip_version_markers(title: str) -> str:
    """Remove recording/edition version markers from a title for *similarity*.

    Removes any parenthetical/bracket group or a trailing dash-suffix whose text
    matches a known version marker (live, remix, remaster, acoustic, karaoke,
    instrumental, tribute, radio/single edit, deluxe/expanded/anniversary,
    compilation). Genuine subtitles (e.g. "(You Drive Me) Crazy", "(All I Wanna
    Do)") are preserved because their text matches no marker.

    Used only by the version-aware scorers to decouple "same song?" (title
    similarity) from "same version?" (the source-aware version axis, which reads
    the raw, unstripped title). Returns the original title if stripping would
    empty it.
    """
    if not title:
        return title

    def _is_marker(text: str) -> bool:
        return any(rx.search(text) for rx in _VERSION_MARKER_REGEXES)

    def _maybe_strip_paren(match: "re.Match[str]") -> str:
        return "" if _is_marker(match.group(1)) else match.group(0)

    cleaned = _PAREN_GROUP_RE.sub(_maybe_strip_paren, title)
    dash = _DASH_SUFFIX_RE.search(cleaned)
    if dash and _is_marker(dash.group(1)):
        cleaned = cleaned[: dash.start()]
    return _WHITESPACE_RE.sub(" ", cleaned).strip() or title


def base_title(title: str) -> str:
    """Return the title with *trailing* descriptive subtitles removed.

    Peels any trailing parenthetical/bracket group or trailing dash-suffix
    regardless of its content, so a regional/edition retitle that differs only
    in a descriptive subtitle reduces to the same base title
    (e.g. both "Come On Over Baby (All I Wanna Do)" and "... (All I Want Is
    You)" collapse to "Come On Over Baby"). Unlike :func:`strip_version_markers`
    this does not gate on known markers.

    Only *trailing* groups are removed: leading or embedded parentheticals that
    are integral to the song name, "(You Drive Me) Crazy", "(Sittin' On) The
    Dock of the Bay", are preserved. Returns the original title if stripping
    would empty it.

    Used only by the version-aware scorers as the second leg of a blended title
    similarity, so a true retitle is not penalised as a divergent title while a
    residual gap still guards against merging genuinely different songs that
    happen to share a base title.
    """
    if not title:
        return title
    cleaned = title
    while True:
        before = cleaned
        cleaned = _TRAILING_PAREN_RE.sub("", cleaned)
        dash = _DASH_SUFFIX_RE.search(cleaned)
        if dash:
            cleaned = cleaned[: dash.start()]
        cleaned = cleaned.strip()
        if cleaned == before:
            break
    return _WHITESPACE_RE.sub(" ", cleaned).strip() or title
