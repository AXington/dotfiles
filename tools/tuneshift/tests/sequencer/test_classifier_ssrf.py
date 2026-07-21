"""SSRF protection for the Ollama backend host (SEC-M1).

OLLAMA_HOST (and any host passed to OllamaBackend) must be validated before any
network call so a malicious or misconfigured value cannot make tuneshift reach a
cloud metadata endpoint (e.g. 169.254.169.254) or another internal service.
"""

import urllib.request

import pytest

from tuneshift.sequencer import classifier
from tuneshift.sequencer.classifier import (
    OllamaBackend,
    _validate_ollama_host,
    detect_backend,
)


class TestValidateOllamaHost:
    def test_loopback_localhost_accepted(self) -> None:
        assert (
            _validate_ollama_host("http://localhost:11434") == "http://localhost:11434"
        )

    def test_loopback_ipv4_accepted(self) -> None:
        assert _validate_ollama_host("http://127.0.0.1:11434")

    def test_loopback_ipv6_accepted(self) -> None:
        assert _validate_ollama_host("http://[::1]:11434")

    def test_metadata_endpoint_rejected(self) -> None:
        with pytest.raises(ValueError, match="loopback|allowlist"):
            _validate_ollama_host("http://169.254.169.254")

    def test_private_lan_host_rejected(self) -> None:
        with pytest.raises(ValueError, match="loopback|allowlist"):
            _validate_ollama_host("http://10.0.0.5:11434")

    def test_public_host_rejected(self) -> None:
        with pytest.raises(ValueError, match="loopback|allowlist"):
            _validate_ollama_host("http://evil.example.com:11434")

    def test_non_http_scheme_rejected(self) -> None:
        with pytest.raises(ValueError, match="scheme|http"):
            _validate_ollama_host("file:///etc/passwd")

    def test_missing_hostname_rejected(self) -> None:
        with pytest.raises(ValueError):
            _validate_ollama_host("http://")

    def test_allowlisted_hostname_accepted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TUNESHIFT_OLLAMA_ALLOWLIST", "ollama.internal")
        assert _validate_ollama_host("http://ollama.internal:11434")

    def test_allowlisted_host_port_accepted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TUNESHIFT_OLLAMA_ALLOWLIST", "ollama.internal:11434")
        assert _validate_ollama_host("http://ollama.internal:11434")

    def test_non_allowlisted_host_still_rejected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TUNESHIFT_OLLAMA_ALLOWLIST", "ollama.internal")
        with pytest.raises(ValueError):
            _validate_ollama_host("http://other.example.com:11434")


class TestOllamaBackendValidatesHost:
    def test_init_rejects_metadata_host_argument(self) -> None:
        with pytest.raises(ValueError, match="loopback|allowlist"):
            OllamaBackend(host="http://169.254.169.254")

    def test_init_rejects_metadata_host_from_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OLLAMA_HOST", "http://169.254.169.254")
        with pytest.raises(ValueError, match="loopback|allowlist"):
            OllamaBackend()

    def test_init_accepts_loopback_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("OLLAMA_HOST", raising=False)
        backend = OllamaBackend()
        assert backend._host == "http://localhost:11434"


class TestDetectBackendValidatesHost:
    def test_detect_backend_never_reaches_metadata_host(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Force the Ollama auto-detect path and prove urlopen is never invoked
        # against the metadata endpoint.
        for var in (
            "TUNESHIFT_LLM_BACKEND",
            "ANTHROPIC_API_KEY",
            "OPENAI_API_KEY",
            "TUNESHIFT_LLM_BASE_URL",
            "TUNESHIFT_CLASSIFIER_MODEL",
        ):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setattr(classifier, "_load_stored_key", lambda backend: None)
        monkeypatch.setenv("OLLAMA_HOST", "http://169.254.169.254")

        called: list[str] = []

        def spy(url: object, *args: object, **kwargs: object) -> object:
            target = getattr(url, "full_url", url)
            called.append(str(target))
            raise OSError("network blocked in test")

        monkeypatch.setattr(urllib.request, "urlopen", spy)

        assert detect_backend() == (None, None)
        assert not any("169.254.169.254" in c for c in called)
