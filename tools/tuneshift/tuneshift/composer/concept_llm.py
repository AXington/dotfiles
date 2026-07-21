"""Injectable, degradable LLM judge for thematic concept rules.

A thematic rule (e.g. "not about wanting a man") cannot be enforced by matching
artist tags or release years; it needs a model that can read a track's themes
and lyrical subject and decide whether the track complies. This module wraps the
same LLM backends the sequencer classifier uses, exposes a small batched judge,
and degrades cleanly to ``None`` when no backend is reachable so ``review`` can
fall back to an honest "LLM unavailable" message instead of guessing.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field

from tuneshift.sequencer.classifier import (
    _DEFAULT_MODELS,
    _resolve_llm_timeout,
    _TimeoutBackend,
    detect_backend,
)
from tuneshift.sequencer.classifier import (
    LLMBackend as LLMBackend,
)

_VALID_VERDICTS = frozenset({"complies", "violates", "unsure"})
_DEFAULT_JUDGE_BATCH = 8
_DEFAULT_MIN_CONFIDENCE = 0.6

ConceptJudge = Callable[[str, list["TrackCtx"]], dict[int, str]]


@dataclass
class TrackCtx:
    """Minimal per-track context handed to the concept judge."""

    track_id: int
    title: str
    artist: str
    themes: list[str] = field(default_factory=list)
    lyrical_subject: str | None = None


def _build_prompt(rule: str, tracks: list[TrackCtx]) -> str:
    lines = []
    for track in tracks:
        themes = ", ".join(track.themes) if track.themes else "unknown"
        subject = track.lyrical_subject or "unknown"
        lines.append(
            f'- id {track.track_id}: "{track.title}" by {track.artist} '
            f"(themes: {themes}; lyrical subject: {subject})"
        )
    track_block = "\n".join(lines)
    return (
        "You are auditing a music playlist against a thematic rule.\n"
        f'RULE: "{rule}"\n\n'
        "For each track below, decide whether it COMPLIES with the rule, "
        "VIOLATES it, or you are UNSURE. Judge only the rule; do not consider "
        "audio quality or popularity.\n\n"
        f"TRACKS:\n{track_block}\n\n"
        "Return ONLY a JSON object mapping each track id (as a string) to an "
        'object with a "verdict" (one of "complies", "violates", "unsure") and '
        'a "confidence" from 0.0 to 1.0 for that verdict. Use a low confidence '
        "when the lyrics/themes are ambiguous or you are guessing. Example: "
        '{"12": {"verdict": "complies", "confidence": 0.9}, '
        '"13": {"verdict": "violates", "confidence": 0.55}}. No other text.'
    )


def _extract_json_object(text: str) -> dict:
    """Pull the first ``{...}`` JSON object out of a model response.

    Models often wrap JSON in prose or code fences; this finds the outermost
    brace pair and parses it. Any failure raises, and the caller degrades every
    verdict to "unsure".
    """
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("no JSON object found in model response")
    return json.loads(text[start : end + 1])


def _resolve_batch_size() -> int:
    """Per-call track batch for the judge (env-overridable).

    A single prompt covering every track can exceed the backend's wall-clock
    timeout on a local model (a 29-track batch reliably overran 30s in testing,
    degrading every verdict to "unsure"). Chunking keeps each call inside the
    budget while still batching for efficiency.
    """
    import os

    raw = os.environ.get("TUNESHIFT_CONCEPT_BATCH")
    if not raw:
        return _DEFAULT_JUDGE_BATCH
    try:
        value = int(raw)
    except ValueError:
        return _DEFAULT_JUDGE_BATCH
    return value if value > 0 else _DEFAULT_JUDGE_BATCH


def _resolve_min_confidence() -> float:
    """Confidence below which a complies/violates verdict is downgraded to unsure.

    Env-overridable via ``TUNESHIFT_CONCEPT_MIN_CONFIDENCE``. A weak model tends
    to return confidently-wrong thematic verdicts; treating low-confidence
    verdicts as "unsure" keeps those false positives out of removal proposals.
    Set to 0 to disable the downgrade.
    """
    import os

    raw = os.environ.get("TUNESHIFT_CONCEPT_MIN_CONFIDENCE")
    if not raw:
        return _DEFAULT_MIN_CONFIDENCE
    try:
        value = float(raw)
    except ValueError:
        return _DEFAULT_MIN_CONFIDENCE
    return min(max(value, 0.0), 1.0)


def _coerce_verdict(value: object) -> tuple[str, float]:
    """Normalize a model's per-track answer to ``(verdict, confidence)``.

    Accepts both the confidence-aware object form
    ``{"verdict": ..., "confidence": ...}`` and the older bare-string form
    ``"complies"``. A bare string carries no confidence signal, so it is treated
    as fully confident (1.0) to preserve prior behavior. Anything unparseable
    becomes ``("unsure", 1.0)``.
    """
    if isinstance(value, str):
        return value.strip().lower(), 1.0
    if isinstance(value, dict):
        verdict = str(value.get("verdict", "")).strip().lower()
        try:
            confidence = float(value.get("confidence", 1.0))
        except (TypeError, ValueError):
            confidence = 1.0
        # An out-of-range confidence means the model ignored the 0.0-1.0 scale
        # (e.g. returned 1.5, or a percentage like 55). We cannot trust it, so
        # treat it as a low signal -> a positive threshold downgrades the verdict
        # to "unsure" rather than letting a garbage score become a violation.
        if not (0.0 <= confidence <= 1.0):
            confidence = 0.0
        return verdict, confidence
    return "unsure", 1.0


def build_concept_judge(
    backend: LLMBackend,
    model: str,
    batch_size: int | None = None,
    min_confidence: float | None = None,
) -> ConceptJudge:
    """Build a judge callable over an explicit backend (used in tests too).

    The returned ``judge(rule, tracks)`` sends the tracks in timeout-safe chunks
    and returns a ``{track_id: verdict}`` map. Parsing is defensive: any missing,
    unknown, or malformed verdict becomes "unsure", and any backend/parse
    exception degrades that chunk to "unsure" (other chunks are unaffected), so a
    flaky or slow model never blocks a review or sinks the whole batch. A
    complies/violates verdict whose reported confidence is below
    ``min_confidence`` is downgraded to "unsure" so a weak model's
    low-confidence guesses do not become violations.
    """
    chunk = batch_size if batch_size and batch_size > 0 else _resolve_batch_size()
    threshold = (
        min_confidence if min_confidence is not None else _resolve_min_confidence()
    )

    def _judge_chunk(rule: str, tracks: list[TrackCtx]) -> dict[int, str]:
        verdicts: dict[int, str] = {track.track_id: "unsure" for track in tracks}
        try:
            raw = backend.complete(_build_prompt(rule, tracks), model)
            parsed = _extract_json_object(raw)
        except Exception:  # noqa: BLE001
            return verdicts
        for track in tracks:
            value = parsed.get(str(track.track_id))
            if value is None:
                value = parsed.get(track.track_id)
            verdict, confidence = _coerce_verdict(value)
            if verdict not in _VALID_VERDICTS:
                verdict = "unsure"
            if verdict in ("complies", "violates") and confidence < threshold:
                verdict = "unsure"
            verdicts[track.track_id] = verdict
        return verdicts

    def judge(rule: str, tracks: list[TrackCtx]) -> dict[int, str]:
        verdicts: dict[int, str] = {}
        for start in range(0, len(tracks), chunk):
            verdicts.update(_judge_chunk(rule, tracks[start : start + chunk]))
        return verdicts

    return judge


def _select_backend() -> tuple[LLMBackend, str, str] | None:
    """Select and wrap an LLM backend for concept judging, or None.

    Reuses the classifier's :func:`detect_backend` (env-driven priority) and its
    hard per-call timeout wrapper so a stalled model cannot hang ``review``.
    ``TUNESHIFT_CONCEPT_MODEL`` overrides just the model used for concept
    judging (so a heavier model can be used here without changing the classifier
    model). Returns ``(backend, model, backend_name)`` or ``None`` when no
    backend is reachable.
    """
    import os

    name, backend = detect_backend()
    if backend is None or name is None:
        return None
    model = (
        os.environ.get("TUNESHIFT_CONCEPT_MODEL")
        or os.environ.get("TUNESHIFT_CLASSIFIER_MODEL")
        or getattr(backend, "selected_model", None)
        or _DEFAULT_MODELS.get(name, "gpt-4o-mini")
    )
    wrapped = _TimeoutBackend(backend, _resolve_llm_timeout())
    return wrapped, model, name


def make_concept_judge() -> ConceptJudge | None:
    """Return a ready concept judge, or ``None`` if no LLM backend is reachable.

    The returned callable carries a ``model_label`` attribute (e.g.
    ``"ollama (llama3.2:3b)"``) so callers can surface which model produced the
    thematic verdicts; verdict quality is model-dependent.
    """
    selected = _select_backend()
    if selected is None:
        return None
    backend, model, name = selected
    judge = build_concept_judge(backend, model)
    judge.model_label = f"{name} ({model})"  # type: ignore[attr-defined]
    return judge
