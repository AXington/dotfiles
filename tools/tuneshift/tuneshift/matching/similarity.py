"""String-similarity primitives.

A thin, dependency-free wrapper over :func:`difflib.SequenceMatcher` so the
rest of the matching layer never touches ``difflib`` directly and there is a
single place to swap the ratio implementation later (e.g. rapidfuzz) without
disturbing the scorers.

Inputs are expected to be already normalized (see :mod:`matching.normalize`);
``ratio`` performs no normalization of its own so callers stay in control.
"""

from difflib import SequenceMatcher


def ratio(a: str, b: str) -> float:
    """Return the SequenceMatcher similarity ratio of two strings in [0.0, 1.0].

    Two empty strings are treated as a perfect match (1.0) to mirror
    ``SequenceMatcher`` semantics; callers that must treat empty inputs as
    "no signal" should guard before calling.
    """
    return SequenceMatcher(None, a, b).ratio()
