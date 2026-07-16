"""Typed criterion registry, the general metadata-driven matching model (section 5).

This module replaces the ad-hoc two-string-list (`prefer`/`avoid`) mechanism
with a registry of typed criteria. Each criterion is a self-contained unit:

* ``extract(meta)``, pull a typed :class:`CriterionValue` from a track-like
  metadata object (or ``None`` when the field is absent).
* ``compare(source, candidate, strength)``, given the active preference
  strength (or ``None`` when no preference references this criterion), return a
  :class:`Verdict`.
* ``to_signal(verdict)``, project a *soft* verdict into the existing
  :class:`~tuneshift.matching.penalties.SignalPenalty` consumed by
  :class:`~tuneshift.matching.engine.Distance`, or ``None``.
* ``hard_cap``, how a *hard* verdict caps the recommendation.

**Parity-critical contract (AC-C1 / AC-C5 winner-parity):** a criterion that is
NOT referenced by an active preference returns :attr:`Verdict.NO_VERDICT`, and
``to_signal`` returns ``None`` for it, it contributes NOTHING to the
``Distance`` (not a zero-weight signal, literally no signal object). Only
criteria referenced by an active preference (at any scope) may emit a signal.
Adding a new criterion is therefore registration + config, never bespoke
scoring code, and cannot perturb today's scores until a preference opts in.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import Enum, auto
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import yaml

from tuneshift.matching.penalties import SignalPenalty

_WHITELIST_PATH = Path(__file__).with_name("token_whitelist.yaml")


def _fold(token: object) -> str:
    """Lowercase and strip to alphanumerics (``Dolby Atmos`` -> ``dolbyatmos``)."""

    return "".join(ch for ch in str(token).lower() if ch.isalnum())


class Strength(str, Enum):
    """The four preference strengths a user may attach to any criterion.

    ``require``/``forbid`` are *hard* (phase-1 candidate elimination);
    ``prefer``/``avoid`` are *soft* (phase-2 weighted score adjustment).
    """

    REQUIRE = "require"
    PREFER = "prefer"
    AVOID = "avoid"
    FORBID = "forbid"

    @property
    def is_hard(self) -> bool:
        return self in (Strength.REQUIRE, Strength.FORBID)

    @property
    def is_soft(self) -> bool:
        return self in (Strength.PREFER, Strength.AVOID)


class Verdict(Enum):
    """The outcome of comparing a candidate against the source for a criterion."""

    NO_VERDICT = auto()  # not referenced by an active preference, or unextractable
    NEUTRAL = auto()  # referenced, but the candidate neither helped nor hurt
    SOFT_BONUS = auto()  # soft preference satisfied -> reward
    SOFT_PENALTY = auto()  # soft preference violated -> penalize
    HARD_PASS = auto()  # hard filter satisfied (require met / forbid absent)
    HARD_REJECT = auto()  # hard filter failed -> eliminate the candidate

    @property
    def is_hard(self) -> bool:
        return self in (Verdict.HARD_PASS, Verdict.HARD_REJECT)

    @property
    def is_soft(self) -> bool:
        return self in (Verdict.SOFT_BONUS, Verdict.SOFT_PENALTY)


class HardCapPolicy(str, Enum):
    """How a criterion's HARD verdict caps the recommendation for a candidate.

    Mirrors the existing recommendation caps in
    :class:`~tuneshift.matching.engine.Distance`: a hard rejection forces
    ``REJECT``; a soft-substitute cap limits to ``SUGGEST``; ``NONE`` applies no
    cap (the criterion only ever contributes soft signals).
    """

    REJECT = "reject"
    SUGGEST = "suggest"
    NONE = "none"


@dataclass(frozen=True)
class CriterionValue:
    """A typed value extracted from a track's metadata for one criterion.

    ``raw`` is the underlying value (str, int, list, ...). ``tokens`` is an
    optional normalized token set for token-based criteria (audio-mode,
    recording-class, edition ...). ``structured`` marks the value as coming from
    a structured metadata field rather than a parsed title token, this drives
    the AC-C3 confidence gate (only structured or whitelisted-token values may
    drive a *hard* filter).
    """

    raw: Any
    tokens: frozenset[str] = field(default_factory=frozenset)
    structured: bool = False


@runtime_checkable
class Criterion(Protocol):
    """A registered typed matching/preference criterion (AC-C1)."""

    name: str
    hard_cap: HardCapPolicy

    def extract(self, meta: object) -> CriterionValue | None: ...

    def compare(
        self,
        source: CriterionValue,
        candidate: CriterionValue,
        strength: Strength | None,
    ) -> Verdict: ...

    def to_signal(self, verdict: Verdict) -> SignalPenalty | None: ...


def resolve_strength_verdict(strength: Strength | None, *, satisfied: bool) -> Verdict:
    """Map an active preference strength + candidate satisfaction to a verdict.

    ``satisfied`` means "the candidate has the property the preference is about"
    (e.g. it carries the atmos token for a ``spatial=atmos`` preference). This is
    the single routing table every criterion shares, so a new criterion needs
    only supply extraction + a satisfaction test, never bespoke verdict logic.
    """

    if strength is None:
        return Verdict.NO_VERDICT
    if strength is Strength.REQUIRE:
        return Verdict.HARD_PASS if satisfied else Verdict.HARD_REJECT
    if strength is Strength.FORBID:
        return Verdict.HARD_REJECT if satisfied else Verdict.HARD_PASS
    if strength is Strength.PREFER:
        return Verdict.SOFT_BONUS if satisfied else Verdict.SOFT_PENALTY
    # AVOID
    return Verdict.SOFT_PENALTY if satisfied else Verdict.SOFT_BONUS


def _soft_signal(name: str, weight: int, verdict: Verdict) -> SignalPenalty | None:
    """Project a *soft* verdict onto a SignalPenalty (None for hard/no-verdict)."""

    if verdict is Verdict.SOFT_PENALTY:
        return SignalPenalty(name, -weight, 1.0, weight)
    if verdict is Verdict.SOFT_BONUS:
        return SignalPenalty(name, weight, 0.0, weight)
    return None


class TokenWhitelist:
    """Committed, axis-grouped set of tokens trusted to drive hard filters.

    Membership and axis lookups fold surface variants onto canonical tokens via
    the alias table, so ``"Dolby Atmos"``, ``"dolby_atmos"`` and ``"atmos"`` all
    resolve to the same whitelisted token on the ``spatial`` axis.
    """

    def __init__(self, axes: dict[str, list[str]], aliases: dict[str, str]) -> None:
        self._axis_of: dict[str, str] = {}
        for axis, tokens in axes.items():
            for tok in tokens or ():
                self._axis_of[_fold(tok)] = axis
        self._aliases: dict[str, str] = {
            _fold(k): _fold(v) for k, v in (aliases or {}).items()
        }

    def canonical(self, token: object) -> str:
        folded = _fold(token)
        return self._aliases.get(folded, folded)

    def axis(self, token: object) -> str | None:
        return self._axis_of.get(self.canonical(token))

    def __contains__(self, token: object) -> bool:
        return self.canonical(token) in self._axis_of


@lru_cache(maxsize=1)
def load_token_whitelist() -> TokenWhitelist:
    """Load (and cache) the committed token whitelist from the packaged YAML."""

    data = yaml.safe_load(_WHITELIST_PATH.read_text(encoding="utf-8")) or {}
    return TokenWhitelist(
        axes=data.get("axes") or {}, aliases=data.get("aliases") or {}
    )


def _is_confident(
    *, value: CriterionValue, target: str, whitelist: TokenWhitelist
) -> bool:
    """Whether ``value`` is a confident basis for a hard filter on ``target``.

    Confident iff the value came from a structured field, or ``target`` is a
    committed whitelist token AND the value carries *exactly one* token on that
    axis (unambiguous evidence). Zero same-axis tokens means the value offers no
    evidence at all (e.g. a plain title "Wouldn't It Be Nice" says nothing about
    mix) and must never drive a hard elimination; two or more conflicting
    same-axis tokens are ambiguous (e.g. a title reading "Mono & Stereo Mix").
    Either way the hard verdict is demoted to soft.
    """

    if value.structured:
        return True
    if target not in whitelist:
        return False
    axis = whitelist.axis(target)
    same_axis = {t for t in value.tokens if whitelist.axis(t) == axis}
    return len(same_axis) == 1


def apply_confidence_gate(
    verdict: Verdict,
    *,
    value: CriterionValue,
    target: str,
    whitelist: TokenWhitelist,
) -> Verdict:
    """Demote a HARD verdict to its soft equivalent unless the value is confident.

    Soft/no verdicts pass through unchanged. A confident hard verdict stands; an
    unconfident one is demoted (``HARD_REJECT`` -> ``SOFT_PENALTY``,
    ``HARD_PASS`` -> ``SOFT_BONUS``) so an ambiguous token can nudge the score
    but never eliminate a candidate.
    """

    if not verdict.is_hard:
        return verdict
    if _is_confident(value=value, target=target, whitelist=whitelist):
        return verdict
    return (
        Verdict.SOFT_PENALTY if verdict is Verdict.HARD_REJECT else Verdict.SOFT_BONUS
    )


def _title_ngrams(title: object) -> list[str]:
    """Folded 1- and 2-word n-grams from a free-text title, for token matching."""

    words = [_fold(w) for w in re.split(r"\W+", str(title))]
    words = [w for w in words if w]
    grams = list(words)
    grams += [words[i] + words[i + 1] for i in range(len(words) - 1)]
    return grams


@dataclass
class TokenCriterion:
    """A reusable token-membership criterion (audio-mode, edition, class, ...).

    Extraction reads a token list off ``field_name`` on the metadata object; the
    candidate is *satisfied* when it carries ``target`` (case-insensitive,
    non-alphanumerics folded so ``DOLBY_ATMOS`` matches ``dolby_atmos``). Verdict
    routing is delegated to :func:`resolve_strength_verdict`; soft verdicts
    project to a :class:`SignalPenalty` with the criterion's ``weight`` while
    hard verdicts carry no soft signal (they cap via ``hard_cap``).

    ``structured`` marks values as coming from a structured field so they may
    drive a hard filter under the AC-C3 confidence gate.
    """

    name: str
    field_name: str
    target: str
    weight: int = 10
    hard_cap: HardCapPolicy = HardCapPolicy.NONE
    structured: bool = True

    def _norm(self, token: object) -> str:
        return _fold(token)

    def extract(self, meta: object) -> CriterionValue | None:
        raw = getattr(meta, self.field_name, None)
        if not raw:
            return None
        if isinstance(raw, (list, tuple, set, frozenset)):
            tokens = frozenset(self._norm(t) for t in raw if t)
        else:
            tokens = frozenset({self._norm(raw)})
        if not tokens:
            return None
        return CriterionValue(raw=raw, tokens=tokens, structured=self.structured)

    def compare(
        self,
        source: CriterionValue,  # noqa: ARG002 - required by Criterion.compare interface
        candidate: CriterionValue,
        strength: Strength | None,
    ) -> Verdict:
        satisfied = self._norm(self.target) in candidate.tokens
        return resolve_strength_verdict(strength, satisfied=satisfied)

    def to_signal(self, verdict: Verdict) -> SignalPenalty | None:
        return _soft_signal(self.name, self.weight, verdict)


@dataclass
class TitleTokenCriterion:
    """A criterion whose tokens are PARSED from a free-text title (AC-C3).

    Because title tokens are unstructured, every hard verdict is run through the
    committed-whitelist confidence gate before it may eliminate a candidate. An
    off-whitelist token (e.g. "Deluxe") or an internally ambiguous title (e.g.
    "Mono & Stereo Mix") demotes the hard verdict to a soft signal.
    """

    name: str
    target: str
    whitelist: TokenWhitelist
    field_name: str = "title"
    weight: int = 10
    hard_cap: HardCapPolicy = HardCapPolicy.NONE

    def extract_from_title(self, title: object) -> CriterionValue:
        tokens = frozenset(
            self.whitelist.canonical(gram)
            for gram in _title_ngrams(title)
            if gram in self.whitelist
        )
        return CriterionValue(raw=title, tokens=tokens, structured=False)

    def extract(self, meta: object) -> CriterionValue | None:
        raw = getattr(meta, self.field_name, None)
        if not raw:
            return None
        return self.extract_from_title(raw)

    def compare(
        self,
        source: CriterionValue,  # noqa: ARG002 - required by Criterion.compare interface
        candidate: CriterionValue,
        strength: Strength | None,
    ) -> Verdict:
        satisfied = self.whitelist.canonical(self.target) in candidate.tokens
        verdict = resolve_strength_verdict(strength, satisfied=satisfied)
        return apply_confidence_gate(
            verdict,
            value=candidate,
            target=self.target,
            whitelist=self.whitelist,
        )

    def to_signal(self, verdict: Verdict) -> SignalPenalty | None:
        return _soft_signal(self.name, self.weight, verdict)


@dataclass
class EditAxisCriterion:
    """The single/radio-edit vs album-version axis (M7).

    Two properties distinguish this axis from a plain token criterion:

    * **Dual source.** Edit markers (``radio_edit`` / ``single_version`` /
      ``extended`` / ``album_version``) are read from BOTH the free-text title
      and the structured Tidal ``version`` field (``tidal_version``), because a
      radio edit is as often marked in the version field as in the title.
    * **Unmarked default.** ``album_version`` is the *absence* of a competing
      edit marker: a release that carries no radio/single/extended marker IS the
      album version. So ``prefer album_version`` selects the plain album track
      (which carries no "album version" text) and down-ranks the radio edit,
      and ``require album_version`` hard-eliminates the radio edit.

    Title-derived markers pass through the committed-whitelist confidence gate
    (AC-C3) before they may drive a hard filter; a marker asserted by the
    structured version field is confident on its own.
    """

    whitelist: TokenWhitelist
    target: str = "album_version"
    name: str = "edit"
    version_field: str = "tidal_version"
    title_field: str = "title"
    weight: int = 10
    hard_cap: HardCapPolicy = HardCapPolicy.NONE

    #: Edit markers (canonical whitelist form) that mean "NOT the album version".
    _COMPETING = frozenset({"radioedit", "singleversion", "extended"})
    #: Canonical form of the unmarked-default target.
    _ALBUM_VERSION = "albumversion"

    def _axis_tokens(self, text: object) -> frozenset[str]:
        """Canonical edit-axis tokens found in a free-text string."""
        return frozenset(
            self.whitelist.canonical(gram)
            for gram in _title_ngrams(text)
            if gram in self.whitelist and self.whitelist.axis(gram) == "edit"
        )

    def extract(self, meta: object) -> CriterionValue | None:
        title = getattr(meta, self.title_field, None)
        version = getattr(meta, self.version_field, None)
        title_tokens = self._axis_tokens(title) if title else frozenset()
        version_tokens = frozenset()
        structured = False
        if version:
            canon = self.whitelist.canonical(version)
            if canon in self.whitelist and self.whitelist.axis(canon) == "edit":
                version_tokens = frozenset({canon})
                structured = True
            else:
                # A multi-word version string ("Radio Edit") -> scan its n-grams.
                scanned = self._axis_tokens(version)
                if scanned:
                    version_tokens = scanned
                    structured = True
        tokens = title_tokens | version_tokens
        # A release with no edit marker at all is still meaningful evidence: it
        # is the album version. Represent that with an empty token set (never
        # None) so ``album_version`` satisfaction can key on the ABSENCE of a
        # competing marker rather than being treated as unextractable.
        return CriterionValue(
            raw=(title, version), tokens=tokens, structured=structured
        )

    def compare(
        self,
        source: CriterionValue,  # noqa: ARG002 - required by Criterion.compare interface
        candidate: CriterionValue,
        strength: Strength | None,
    ) -> Verdict:
        target = self.whitelist.canonical(self.target)
        if target == self._ALBUM_VERSION:
            satisfied = self._ALBUM_VERSION in candidate.tokens or not (
                candidate.tokens & self._COMPETING
            )
        else:
            satisfied = target in candidate.tokens
        verdict = resolve_strength_verdict(strength, satisfied=satisfied)
        return apply_confidence_gate(
            verdict, value=candidate, target=target, whitelist=self.whitelist
        )

    def to_signal(self, verdict: Verdict) -> SignalPenalty | None:
        return _soft_signal(self.name, self.weight, verdict)


@dataclass
class DateCriterion:
    """A date/year criterion over a candidate's recording/release/remaster date (M3).

    ``date_field`` names the metadata attribute to read. It may hold an integer
    year (``remaster_year``) or an ISO date string (``release_date`` /
    ``recording_date``), from which the four-digit year is parsed. ``target`` is
    either a four-digit year (exact-year match) or the literal ``"original"``,
    which is satisfied only when the field is ABSENT, i.e. the un-remastered /
    un-dated original form.

    Dates are structured metadata, so a verdict is confident (a ``require`` may
    hard-filter). An absent field yields no value -> NO_VERDICT -> no signal, per
    the parity rule (section 5.1): a criterion with nothing to read never perturbs the
    score.
    """

    name: str
    date_field: str
    target: str
    weight: int = 10
    hard_cap: HardCapPolicy = HardCapPolicy.NONE

    _ORIGINAL = "original"

    @staticmethod
    def _year_of(raw: object) -> int | None:
        if raw is None:
            return None
        if isinstance(raw, int):
            return raw
        match = re.search(r"\d{4}", str(raw))
        return int(match.group()) if match else None

    def extract(self, meta: object) -> CriterionValue | None:
        raw = getattr(meta, self.date_field, None)
        if raw is None:
            # ``original`` keys on ABSENCE, but an absent field is still "no
            # evidence to compare years" - represent absence with an explicit
            # marker value so the comparator can distinguish it from "unknown".
            if self.target.strip().lower() == self._ORIGINAL:
                return CriterionValue(
                    raw=None, tokens=frozenset({self._ORIGINAL}), structured=True
                )
            return None
        year = self._year_of(raw)
        if year is None:
            return None
        return CriterionValue(raw=year, tokens=frozenset({str(year)}), structured=True)

    def compare(
        self,
        source: CriterionValue,  # noqa: ARG002 - required by Criterion.compare interface
        candidate: CriterionValue,
        strength: Strength | None,
    ) -> Verdict:
        target = self.target.strip().lower()
        if target == self._ORIGINAL:
            satisfied = self._ORIGINAL in candidate.tokens
        else:
            satisfied = target in candidate.tokens
        # Dates are structured -> confident; a hard verdict stays hard.
        return resolve_strength_verdict(strength, satisfied=satisfied)

    def to_signal(self, verdict: Verdict) -> SignalPenalty | None:
        return _soft_signal(self.name, self.weight, verdict)


@dataclass
class DurationCriterion:
    """A running-length tolerance criterion (M4, AC-M4).

    Unlike the global tiered duration band (``penalties.duration_signal``) which
    is calibrated against the candidate cluster and cannot be tightened per
    playlist, this criterion compares the CANDIDATE's length against the SOURCE's
    within a configurable tolerance. ``target`` is the tolerance:

    - ``"5%"``, relative: within 5% of the source duration.
    - ``"3s"`` / ``"3"``, absolute: within 3 seconds of the source duration.

    A ``require`` tolerance hard-filters candidates outside the band (the "reject
    an extended mix a lenient global band would accept" case); a ``prefer`` nudges
    the score. Duration is structured numeric metadata, so the verdict is
    confident and a hard verdict stays hard. Either side missing a duration ->
    NO_VERDICT -> no signal (parity rule section 5.1), a criterion with nothing to
    measure never perturbs the outcome.
    """

    name: str
    target: str
    weight: int = 10
    hard_cap: HardCapPolicy = HardCapPolicy.NONE

    @staticmethod
    def _seconds_of(meta: object) -> float | None:
        raw = getattr(meta, "duration_seconds", None)
        if raw is None:
            raw = getattr(meta, "duration", None)
        if raw is None:
            return None
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return None
        return value if value > 0 else None

    @staticmethod
    def is_valid_target(target: str) -> bool:
        """True when ``target`` is a parseable, non-negative tolerance spec.

        Accepts ``"5%"`` (relative), ``"3s"`` / ``"3"`` (absolute seconds). Used
        by the CLI to reject a malformed tolerance at set-time (fail fast) and
        mirrors the parse in :meth:`_tolerance_seconds` (the runtime safety net).
        """
        return DurationCriterion._parse_tolerance(target) is not None

    @staticmethod
    def _parse_tolerance(target: str) -> tuple[float, bool] | None:
        """Parse ``target`` into ``(value, is_relative)`` or ``None`` if malformed."""
        spec = target.strip().lower()
        relative = spec.endswith("%")
        if relative:
            spec = spec[:-1]
        elif spec.endswith("s"):
            spec = spec[:-1]
        try:
            value = float(spec)
        except ValueError:
            return None
        if not math.isfinite(value) or value < 0:
            return None
        return value, relative

    def _tolerance_seconds(self, source_seconds: float) -> float | None:
        parsed = self._parse_tolerance(self.target)
        if parsed is None:
            return None
        value, relative = parsed
        return abs(source_seconds) * value / 100.0 if relative else value

    def extract(self, meta: object) -> CriterionValue | None:
        seconds = self._seconds_of(meta)
        if seconds is None:
            return None
        return CriterionValue(raw=seconds, tokens=frozenset(), structured=True)

    def compare(
        self,
        source: CriterionValue,
        candidate: CriterionValue,
        strength: Strength | None,
    ) -> Verdict:
        if not isinstance(source.raw, (int, float)) or not isinstance(
            candidate.raw, (int, float)
        ):
            return Verdict.NO_VERDICT
        tolerance = self._tolerance_seconds(float(source.raw))
        if tolerance is None:
            # A malformed tolerance target (a typo past validation, or a legacy
            # row) yields no measurable band -> no signal, rather than crashing
            # selection (parity rule section 5.1).
            return Verdict.NO_VERDICT
        satisfied = abs(float(candidate.raw) - float(source.raw)) <= tolerance
        # Numeric/structured -> confident; a hard verdict stays hard.
        return resolve_strength_verdict(strength, satisfied=satisfied)

    def to_signal(self, verdict: Verdict) -> SignalPenalty | None:
        return _soft_signal(self.name, self.weight, verdict)


@dataclass
class ArtistRoleCriterion:
    """A role-aware artist-set criterion (M5, AC-M5).

    Distinguishes MAIN artists from FEATURED ones so a ``feat. X`` variant of the
    same recording still matches on its main artist, while a candidate on which
    the source artist is merely *featured* (a different main artist) is rejected.
    ``target`` selects the role compared; only ``"main"`` is meaningful today,
    the main-artist set of the source must be a subset of the candidate's main
    set. Featured artists are carried but never gate the match.

    The credit string is structured metadata, so the verdict is confident and a
    ``require`` may hard-filter. Either side missing an artist credit -> NO_VERDICT
    -> no signal. KNOWN COVERAGE GAP (spec section 11): precise roles ultimately
    want the MusicBrainz artist-credit ``joinphrase``/role fields, which are
    inconsistent; this reads the platform credit's feat/ft markers, so role
    distinctions fire only where the credit carries them, a low fire-rate is
    expected, not a bug.
    """

    name: str
    target: str = "main"
    weight: int = 10
    hard_cap: HardCapPolicy = HardCapPolicy.NONE

    def extract(self, meta: object) -> CriterionValue | None:
        raw = getattr(meta, "artist", None)
        if not raw:
            return None
        from tuneshift.matching.normalize import split_artist_roles

        main, featured = split_artist_roles(str(raw))
        if not main:
            return None
        return CriterionValue(
            raw=(frozenset(main), frozenset(featured)),
            tokens=frozenset(main),
            structured=True,
        )

    def compare(
        self,
        source: CriterionValue,
        candidate: CriterionValue,
        strength: Strength | None,
    ) -> Verdict:
        if not isinstance(source.raw, tuple) or not isinstance(candidate.raw, tuple):
            return Verdict.NO_VERDICT
        source_main = source.raw[0]
        cand_main = candidate.raw[0]
        if not source_main or not cand_main:
            return Verdict.NO_VERDICT
        satisfied = source_main.issubset(cand_main)
        # Structured credit -> confident; a hard verdict stays hard.
        return resolve_strength_verdict(strength, satisfied=satisfied)

    def to_signal(self, verdict: Verdict) -> SignalPenalty | None:
        return _soft_signal(self.name, self.weight, verdict)


@dataclass
class ComposerCriterion:
    """A composer identity-match criterion (M6, AC-M6).

    Differentiates same-title, different-WORK recordings by comparing the
    SOURCE's composer set against the CANDIDATE's: satisfied when every source
    composer is also credited on the candidate (source is a subset of
    candidate). A multi-name composer credit (``"Lennon, McCartney"``) is split
    like an artist credit so
    order and packaging do not matter. ``target`` is a mode selector (``"match"``),
    the comparison is always source-vs-candidate, not against a fixed name.

    Composer is structured metadata (from the MB WORK relations) so the verdict
    is confident and a ``require`` may hard-filter. Either side missing a composer
    -> NO_VERDICT -> no signal (parity rule section 5.1). KNOWN COVERAGE GAP
    (spec section 11): MB WORK-relation composer data is inconsistent and platform
    search results
    rarely expose it, so this criterion fires only where both sides carry a
    composer, a low fire-rate is expected, not a bug.
    """

    name: str
    target: str = "match"
    weight: int = 10
    hard_cap: HardCapPolicy = HardCapPolicy.NONE

    def extract(self, meta: object) -> CriterionValue | None:
        raw = getattr(meta, "composer", None)
        if not raw:
            return None
        from tuneshift.matching.normalize import split_artists

        names = split_artists(str(raw))
        if not names:
            return None
        return CriterionValue(
            raw=frozenset(names), tokens=frozenset(names), structured=True
        )

    def compare(
        self,
        source: CriterionValue,
        candidate: CriterionValue,
        strength: Strength | None,
    ) -> Verdict:
        if not isinstance(source.raw, frozenset) or not isinstance(
            candidate.raw, frozenset
        ):
            return Verdict.NO_VERDICT
        if not source.raw or not candidate.raw:
            return Verdict.NO_VERDICT
        satisfied = source.raw.issubset(candidate.raw)
        # Structured -> confident; a hard verdict stays hard.
        return resolve_strength_verdict(strength, satisfied=satisfied)

    def to_signal(self, verdict: Verdict) -> SignalPenalty | None:
        return _soft_signal(self.name, self.weight, verdict)


@dataclass
class WorkCriterion:
    """A MusicBrainz WORK-entity criterion (M2, AC-M2).

    Distinguishes the original recording of a composition from covers and
    deliberate re-recordings by the MB *work* identity, not by fuzzy strings.
    Two aspects are compared:

    * **Work identity**, the MB ``mb_work_id`` ties a recording to its
      composition. A same-titled candidate with a DIFFERENT work-id is a
      different song, not a cover; ``target="original"`` requires the candidate
      to share the source's work-id.
    * **Re-recording marker**, a canonical marker parsed from the RAW title
      (``"taylors version"`` / ``"re-recorded"``) via
      :func:`normalize.extract_rerecording_marker`, captured before the edition
      strip removes the parenthetical. ``target="original"`` is satisfied only
      when the candidate carries NO re-recording marker; a specific target such
      as ``"taylors version"`` is satisfied only when the candidate carries that
      marker, so a playlist can prefer the re-recording over the original.

    Work identity is structured, so a verdict is confident and a ``require`` may
    hard-filter. When neither side exposes a work-id nor a marker there is
    nothing to compare -> NO_VERDICT -> no signal (parity rule section 5.1). KNOWN
    COVERAGE GAP (spec section 11): MB work-ids are populated on identity-resolved
    candidates; raw platform search results seldom carry them, so this fires
    where the candidate has been hydrated with MB data, a low fire-rate on
    un-hydrated candidates is expected, not a bug.
    """

    name: str
    target: str = "original"
    weight: int = 15
    hard_cap: HardCapPolicy = HardCapPolicy.NONE

    _ORIGINAL = "original"

    def extract(self, meta: object) -> CriterionValue | None:
        from tuneshift.matching.normalize import extract_rerecording_marker

        work_id = getattr(meta, "mb_work_id", None)
        title = getattr(meta, "title", None)
        marker = extract_rerecording_marker(str(title) if title else None)
        if work_id is None and marker is None:
            return None
        tokens = frozenset({marker}) if marker else frozenset()
        return CriterionValue(raw=(work_id, marker), tokens=tokens, structured=True)

    def compare(
        self,
        source: CriterionValue,
        candidate: CriterionValue,
        strength: Strength | None,
    ) -> Verdict:
        if not isinstance(source.raw, tuple) or not isinstance(candidate.raw, tuple):
            return Verdict.NO_VERDICT
        src_work, _src_marker = source.raw
        cand_work, cand_marker = candidate.raw
        target = self.target.strip().lower()
        if target == self._ORIGINAL:
            # The original recording of the SOURCE's composition: same work-id
            # and no re-recording marker. Without both work-ids we cannot
            # confirm the composition, so emit no signal rather than guess.
            if src_work is None or cand_work is None:
                return Verdict.NO_VERDICT
            satisfied = src_work == cand_work and cand_marker is None
        else:
            # A specific re-recording (e.g. "taylors version"): the candidate
            # must carry that marker, and - when both work-ids are known - be the
            # same composition (a re-recording of a DIFFERENT work is rejected).
            if src_work is not None and cand_work is not None and src_work != cand_work:
                satisfied = False
            else:
                satisfied = cand_marker is not None and target == cand_marker
        # Work identity is structured -> confident; a hard verdict stays hard.
        return resolve_strength_verdict(strength, satisfied=satisfied)

    def to_signal(self, verdict: Verdict) -> SignalPenalty | None:
        return _soft_signal(self.name, self.weight, verdict)


class CriterionRegistry:
    """An ordered, name-unique collection of registered criteria.

    Order is registration order, which fixes a stable default precedence; the
    per-playlist precedence override is applied by the selection engine (AC-C4),
    not here.
    """

    def __init__(self) -> None:
        self._by_name: dict[str, Criterion] = {}

    def register(self, criterion: Criterion) -> None:
        name = criterion.name
        if name in self._by_name:
            raise ValueError(f"criterion {name!r} is already registered")
        self._by_name[name] = criterion

    def get(self, name: str) -> Criterion:
        return self._by_name[name]

    def all(self) -> tuple[Criterion, ...]:
        return tuple(self._by_name.values())

    def names(self) -> tuple[str, ...]:
        return tuple(self._by_name.keys())

    def __contains__(self, name: object) -> bool:
        return name in self._by_name

    def __iter__(self) -> Iterator[Criterion]:
        return iter(self._by_name.values())

    def __len__(self) -> int:
        return len(self._by_name)
