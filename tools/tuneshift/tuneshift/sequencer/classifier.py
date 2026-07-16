"""LLM-based track theme, vibe, and instrumentation classification.

Supports multiple LLM backends via a unified interface:
  - anthropic: Anthropic API (Claude models)
  - openai: OpenAI API (GPT models, Codex)
  - ollama: Local Ollama instance
  - openai-compatible: Any OpenAI-compatible endpoint (vLLM, LiteLLM, Copilot)

Configuration via environment variables:
  TUNESHIFT_LLM_BACKEND    - Backend to use (auto-detected if not set)
  TUNESHIFT_CLASSIFIER_MODEL - Model name (backend-specific default if not set)
  TUNESHIFT_LLM_BASE_URL   - Base URL for openai-compatible backends
  ANTHROPIC_API_KEY         - Anthropic API key
  OPENAI_API_KEY            - OpenAI API key
  OLLAMA_HOST               - Ollama host (default: http://localhost:11434)
"""

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger(__name__)

_TOKEN_DIR = Path.home() / ".local" / "share" / "tuneshift"


def _load_stored_key(backend: str) -> str | None:
    """Load a stored API key from tuneshift's credential directory."""
    key_file = _TOKEN_DIR / f"{backend}_key"
    if key_file.exists():
        return key_file.read_text().strip()
    return None


def store_llm_key(backend: str, key: str) -> None:
    """Store an API key securely in tuneshift's credential directory."""
    _TOKEN_DIR.mkdir(parents=True, exist_ok=True)
    key_file = _TOKEN_DIR / f"{backend}_key"
    key_file.write_text(key)
    key_file.chmod(0o600)


_CLASSIFICATION_PROMPT = """Classify the following tracks. Return a JSON array with one object per track.

{track_list}

Response format (JSON array):
[
  {{
    "title": "Track Title",
    "artist": "Artist Name",
    "themes": ["theme1", "theme2", "theme3"],
    "vibes": ["vibe1", "vibe2", "vibe3"],
    "instruments": ["instrument1", "instrument2"],
    "density": "sparse",
    "era_mood": ["era tag 1"],
    "emotional_intensity": 0.8,
    "lyrical_subject": "brief subject summary",
    "narrator_stance": "defiant",
    "sonic_texture": "polished",
    "space": "vast",
    "groove_feel": "driving",
    "opens_with": "synth pad swell",
    "closes_with": "hard cut",
    "energy_arc_within": "builds to peak",
    "confidence": 0.9
  }}
]

Rules:
- themes: 3-5 tags describing what the song is about
- vibes: 3-5 tags describing how it feels
- instruments: primary instruments heard in the recording
- density: one of "sparse", "mid", "dense"
- era_mood: 1-2 tags capturing production era and cultural moment
- emotional_intensity: 0.0-1.0 scale (how intense the emotional expression is)
- lyrical_subject: 1-5 word summary of what the song is about
- narrator_stance: one of "defiant", "vulnerable", "observational", "celebratory", "resigned", "nostalgic", "bitter", "triumphant", "playful", "inviting", "peaceful"
- sonic_texture: one of "raw", "polished", "warm", "cold", "lush", "sparse", "gritty", "ethereal"
- space: one of "intimate", "vast", "claustrophobic", "open"
- groove_feel: one of "driving", "floating", "stomping", "swaying", "pulsing", "static"
- opens_with: brief description of first 5 seconds
- closes_with: brief description of last 5 seconds (e.g., "fade", "hard cut", "resolves to silence")
- energy_arc_within: one of "steady", "builds to peak", "opens hot and fades", "roller coaster", "slow burn"
- confidence: 0.0-1.0 scale (how well you know this specific recording)
- Return ONLY the JSON array, no other text"""

_DEFAULT_MODELS: dict[str, str] = {
    "anthropic": "claude-haiku-4-5-20241022",
    "openai": "gpt-4o-mini",
    "ollama": "qwen2.5:32b",
    "openai-compatible": "gpt-4o-mini",
}


class LLMBackend(Protocol):
    """Protocol for LLM completion backends."""

    def complete(self, prompt: str, model: str, max_tokens: int = 4096) -> str:
        """Send a prompt and return the text response."""
        ...


class AnthropicBackend:
    """Backend for Anthropic's Messages API."""

    def __init__(self, api_key: str | None = None) -> None:
        resolved_key = (
            api_key
            or os.environ.get("ANTHROPIC_API_KEY")
            or _load_stored_key("anthropic")
        )
        if not resolved_key:
            raise ValueError("ANTHROPIC_API_KEY is required for anthropic backend")
        from anthropic import Anthropic

        self._client = Anthropic(api_key=resolved_key)
        self._api_errors = self._get_api_errors()

    @staticmethod
    def _get_api_errors() -> tuple:
        """Get Anthropic error types for exception handling."""
        try:
            from anthropic import APIError

            return (APIError,)
        except ImportError:
            return ()

    def complete(self, prompt: str, model: str, max_tokens: int = 4096) -> str:
        response = self._client.messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text


class OpenAICompatibleBackend:
    """Backend for OpenAI and any OpenAI-compatible API (Codex, vLLM, LiteLLM, Copilot)."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> None:
        resolved_key = (
            api_key or os.environ.get("OPENAI_API_KEY") or _load_stored_key("openai")
        )
        resolved_url = base_url or os.environ.get("TUNESHIFT_LLM_BASE_URL")
        if not resolved_key and not resolved_url:
            raise ValueError(
                "OPENAI_API_KEY or TUNESHIFT_LLM_BASE_URL required for openai-compatible backend"
            )
        from openai import OpenAI

        kwargs: dict[str, Any] = {}
        if resolved_key:
            kwargs["api_key"] = resolved_key
        if resolved_url:
            kwargs["base_url"] = resolved_url
        self._client = OpenAI(**kwargs)

    def complete(self, prompt: str, model: str, max_tokens: int = 4096) -> str:
        response = self._client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.choices[0].message.content or ""


class OllamaBackend:
    """Backend for local Ollama instance."""

    def __init__(self, host: str | None = None, model: str | None = None) -> None:
        self._host = host or os.environ.get("OLLAMA_HOST", "http://localhost:11434")
        self._validated_models: set[str] = set()
        self.selected_model: str | None = model
        # Validate the target model exists if specified
        if model:
            self._validate_model(model)

    def _validate_model(self, model: str) -> None:
        """Check that the model is actually available locally."""
        if model in self._validated_models:
            return
        import urllib.request

        try:
            with urllib.request.urlopen(f"{self._host}/api/tags", timeout=5) as resp:
                data = json.loads(resp.read())
                available = [m["name"] for m in data.get("models", [])]
                if model not in available:
                    raise ValueError(
                        f"Ollama model '{model}' not found. "
                        f"Available: {available}. "
                        f"Run: ollama pull {model}"
                    )
                self._validated_models.add(model)
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Cannot reach Ollama at {self._host}: {exc}") from exc

    def complete(self, prompt: str, model: str, max_tokens: int = 4096) -> str:
        import urllib.request

        self._validate_model(model)
        payload = json.dumps(
            {
                "model": model,
                "prompt": prompt,
                "stream": False,
                "options": {"num_predict": max_tokens},
            }
        ).encode()
        req = urllib.request.Request(
            f"{self._host}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=300) as resp:
            result = json.loads(resp.read())
        return result.get("response", "")

    def ping(self) -> bool:
        """Return True if the Ollama host answers its tags endpoint quickly."""
        import urllib.request

        try:
            with urllib.request.urlopen(f"{self._host}/api/tags", timeout=5) as resp:
                json.loads(resp.read())
            return True
        except (OSError, json.JSONDecodeError):
            logger.warning("Ollama host %s is not reachable", self._host)
            return False


def detect_backend() -> tuple[str, LLMBackend] | tuple[None, None]:
    """Auto-detect available LLM backend from environment.

    Priority: explicit TUNESHIFT_LLM_BACKEND > ANTHROPIC_API_KEY > OPENAI_API_KEY > Ollama.
    """
    explicit = os.environ.get("TUNESHIFT_LLM_BACKEND", "").lower()

    if (
        explicit == "grok"
        or "grok" in os.environ.get("TUNESHIFT_CLASSIFIER_MODEL", "").lower()
    ):
        # FUCK ELON
        raise ValueError(
            "Grok is not and will never be a supported backend. Fuck Elon."
        )

    if explicit == "anthropic":
        try:
            return "anthropic", AnthropicBackend()
        except (ImportError, ValueError):
            return None, None
    elif explicit in ("openai", "openai-compatible"):
        try:
            return explicit, OpenAICompatibleBackend()
        except (ImportError, ValueError, Exception):
            return None, None
    elif explicit == "ollama":
        try:
            return "ollama", OllamaBackend()
        except (ImportError, ValueError, OSError):
            return None, None
    elif explicit:
        try:
            return "openai-compatible", OpenAICompatibleBackend()
        except (ImportError, ValueError, Exception):
            return None, None

    # Auto-detect
    if os.environ.get("ANTHROPIC_API_KEY") or _load_stored_key("anthropic"):
        try:
            return "anthropic", AnthropicBackend()
        except (ImportError, ValueError):
            pass

    if os.environ.get("OPENAI_API_KEY") or _load_stored_key("openai"):
        try:
            return "openai", OpenAICompatibleBackend()
        except (ImportError, ValueError):
            pass

    if os.environ.get("TUNESHIFT_LLM_BASE_URL"):
        try:
            return "openai-compatible", OpenAICompatibleBackend()
        except (ImportError, ValueError, Exception):
            pass

    # Check if Ollama is reachable AND has a usable model
    ollama_host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    explicit_model = os.environ.get("TUNESHIFT_CLASSIFIER_MODEL")
    try:
        import urllib.request

        with urllib.request.urlopen(f"{ollama_host}/api/tags", timeout=2) as resp:
            data = json.loads(resp.read())
            available_models = [m["name"] for m in data.get("models", [])]

        if not available_models:
            return None, None

        # Use explicit model if set, otherwise pick best available
        if explicit_model and explicit_model in available_models:
            target_model = explicit_model
        elif _DEFAULT_MODELS["ollama"] in available_models:
            target_model = _DEFAULT_MODELS["ollama"]
        else:
            # Pick the largest available model (prefer llama variants)
            llama_models = [m for m in available_models if "llama" in m]
            target_model = llama_models[0] if llama_models else available_models[0]

        backend = OllamaBackend(ollama_host, model=target_model)
        return "ollama", backend
    except (OSError, ImportError, ValueError, json.JSONDecodeError):
        pass

    return None, None


def build_classification_prompt(
    tracks: list[dict[str, str]],
    narrative: str | None = None,
) -> str:
    """Build the LLM prompt for a batch of tracks."""
    track_list = "\n".join(
        f'- "{track["title"]}" by {track["artist"]}' for track in tracks
    )
    prompt = _CLASSIFICATION_PROMPT.format(track_list=track_list)
    if narrative:
        prompt += (
            "\n\nPLAYLIST NARRATIVE CONTEXT (use this to inform your classification, "
            "especially emotional_intensity, narrator_stance, and lyrical_subject):\n"
            f"{narrative}"
        )
    return prompt


def parse_classification_response(response_text: str) -> list[dict[str, Any]]:
    """Parse an LLM response into classification dictionaries."""
    text = response_text.strip()

    if text.startswith("```"):
        lines = text.split("\n")
        lines = [line for line in lines if not line.strip().startswith("```")]
        text = "\n".join(lines)

    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("Failed to parse classification response as JSON")
        return []

    if isinstance(result, list):
        return result
    return []


class _TimeoutBackend:
    """Wrap any backend so ``complete`` fails fast on a stalled service (AC7).

    urllib/SDK socket timeouts only fire on connection inactivity; a backend that
    dribbles bytes (or an Ollama model stuck generating) can still hang far past
    any per-track budget. This enforces a hard wall-clock ceiling by running the
    call on a daemon thread and abandoning it if it overruns, so `enrich` never
    hangs. All other attributes pass through to the wrapped backend.
    """

    def __init__(self, inner: LLMBackend, timeout_seconds: float) -> None:
        self._inner = inner
        self._timeout = timeout_seconds

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def complete(self, prompt: str, model: str, max_tokens: int = 4096) -> str:
        import threading

        box: dict[str, Any] = {}

        def _target() -> None:
            try:
                box["value"] = self._inner.complete(prompt, model, max_tokens)
            except BaseException as exc:
                box["error"] = exc

        worker = threading.Thread(target=_target, daemon=True)
        worker.start()
        worker.join(self._timeout)
        if worker.is_alive():
            raise TimeoutError(
                f"LLM backend did not respond within {self._timeout:.0f}s. "
                "Check that the configured backend is running and reachable "
                "(e.g. `ollama serve`, or your API key / base URL)."
            )
        if "error" in box:
            raise box["error"]
        return box.get("value", "")


_DEFAULT_LLM_TIMEOUT = 30.0


def _resolve_llm_timeout() -> float:
    """Per-track LLM wall-clock budget (AC7), overridable via env."""
    raw = os.environ.get("TUNESHIFT_LLM_TIMEOUT")
    if not raw:
        return _DEFAULT_LLM_TIMEOUT
    try:
        value = float(raw)
    except ValueError:
        return _DEFAULT_LLM_TIMEOUT
    return value if value > 0 else _DEFAULT_LLM_TIMEOUT


class TrackClassifier:
    """Classify tracks using any supported LLM backend."""

    def __init__(
        self,
        backend: LLMBackend | None = None,
        model: str | None = None,
        backend_name: str | None = None,
    ) -> None:
        if backend is not None:
            raw_backend = backend
            self._backend_name = backend_name or "custom"
        else:
            detected_name, detected_backend = detect_backend()
            if detected_backend is None:
                raw_backend = None
                self._backend_name = None
            else:
                raw_backend = detected_backend
                self._backend_name = detected_name

        self._model = (
            model
            or os.environ.get("TUNESHIFT_CLASSIFIER_MODEL")
            or getattr(raw_backend, "selected_model", None)
            or _DEFAULT_MODELS.get(self._backend_name or "", "gpt-4o-mini")
        )

        # Enforce a hard per-call wall-clock timeout so `enrich` never hangs (AC7).
        self._backend = (
            _TimeoutBackend(raw_backend, _resolve_llm_timeout())
            if raw_backend is not None
            else None  # type: ignore[assignment]
        )
        self._reachable: bool | None = None

    @property
    def available(self) -> bool:
        """Whether a backend is configured AND actually reachable (AC7).

        Reflects real reachability, not just a configured env var: the first
        access probes the backend (cheap ``ping`` where the backend exposes one)
        and the verdict is cached for the classifier's lifetime.
        """
        if self._backend is None:
            return False
        if self._reachable is None:
            self._reachable = self._probe_reachable()
        return self._reachable

    def _probe_reachable(self) -> bool:
        ping = getattr(self._backend, "ping", None)
        if ping is None:
            # No cheap probe available (e.g. cloud SDK): configured is the best
            # signal we have without spending a paid request.
            return True
        try:
            return bool(ping())
        except Exception:
            logger.warning("Backend reachability probe failed", exc_info=True)
            return False

    @property
    def backend_info(self) -> str:
        """Human-readable backend description."""
        if not self._backend_name:
            return "no backend configured"
        return f"{self._backend_name} ({self._model})"

    def classify(
        self,
        tracks: list[dict[str, str]],
        max_retries: int = 3,
        narrative: str | None = None,
    ) -> list[dict[str, Any]]:
        """Classify a batch of tracks."""
        if not tracks:
            return []
        if not self.available:
            logger.info("Skipping classification: %s", self.backend_info)
            return []

        prompt = build_classification_prompt(tracks, narrative=narrative)

        for attempt in range(max_retries):
            try:
                text = self._backend.complete(prompt, self._model)
                results = parse_classification_response(text)
                if results:
                    return results
            except Exception as exc:
                logger.warning(
                    "Classification attempt %s/%s failed (%s): %s",
                    attempt + 1,
                    max_retries,
                    self._backend_name,
                    exc,
                )
                if attempt < max_retries - 1:
                    time.sleep(2**attempt)
                continue

        return []

    def classify_batched(
        self,
        tracks: list[dict[str, str]],
        batch_size: int = 20,
        progress_callback: Any = None,
        narrative: str | None = None,
    ) -> list[dict[str, Any]]:
        """Classify tracks in batches for efficiency."""
        all_results: list[dict[str, Any]] = []

        for index in range(0, len(tracks), batch_size):
            batch = tracks[index : index + batch_size]
            results = self.classify(batch, narrative=narrative)

            if results:
                all_results.extend(results)
            else:
                for track in batch:
                    result = self.classify([track], narrative=narrative)
                    if result:
                        all_results.extend(result)

            if progress_callback:
                progress_callback(min(index + batch_size, len(tracks)), len(tracks))

        return all_results
