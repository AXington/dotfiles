"""Confidence classification.

The single source of truth for turning candidate match scores into a
confidence label. ``classify_scores`` reproduces the historical
``classify_results`` boundaries exactly; the legacy function in ``track.py``
delegates here so there is one implementation, not two.
"""

from __future__ import annotations

# Confidence labels.
HIGH = "high"
AMBIGUOUS = "ambiguous"
NOT_FOUND = "not_found"

# Boundaries (match the historical classify_results). Configurable if needed.
NOT_FOUND_FLOOR = 50  # top < this -> not_found
HIGH_TOP_MIN = 80  # top >= this AND ...
HIGH_SECOND_MAX = 70  # ... second-best < this -> high; else ambiguous
MIN_LEAD = 0  # additionally require (top - second) >= this for high


def classify_scores(
    scores: list[int],
    *,
    quality_scores: list[int] | None = None,
    not_found_floor: int = NOT_FOUND_FLOOR,
    high_top_min: int = HIGH_TOP_MIN,
    high_second_max: int = HIGH_SECOND_MAX,
    min_lead: int = MIN_LEAD,
) -> str:
    """Classify match confidence from candidate scores.

    Returns ``high``, ``ambiguous`` or ``not_found``:

    - ``not_found``: no scores, or the best score is below ``not_found_floor``.
    - ``high``: best >= ``high_top_min``, the runner-up < ``high_second_max``,
      and the top-vs-second gap is at least ``min_lead`` (a clear, unambiguous
      winner).
    - ``ambiguous``: otherwise (a plausible but not decisive match).

    ``min_lead`` defaults to 0, so the default boundaries are byte-identical to
    the historical behaviour. Raising it (e.g. from stricter preferences) demands
    a wider lead before a pick is treated as confident, pushing near-ties into
    ``ambiguous`` for human review instead of a silent guess.

    ``quality_scores`` are the same candidates scored with preference penalties
    excluded (:class:`~tuneshift.matching.track.MatchScores`). When supplied
    they are used for the ``not_found_floor`` test ONLY, because that floor asks
    "was this recording found at all?", a question a listener preference cannot
    answer (BUG-13). The ``high``/``ambiguous`` bands keep using ``scores``,
    since choosing between candidates is exactly what a preference is for.
    ``None`` preserves the historical behaviour.
    """
    if not scores:
        return NOT_FOUND
    floor_scores = quality_scores if quality_scores else scores
    if max(floor_scores) < not_found_floor:
        return NOT_FOUND
    sorted_desc = sorted(scores, reverse=True)
    top = sorted_desc[0]
    second = sorted_desc[1] if len(sorted_desc) > 1 else 0
    if top >= high_top_min and second < high_second_max and (top - second) >= min_lead:
        return HIGH
    return AMBIGUOUS
