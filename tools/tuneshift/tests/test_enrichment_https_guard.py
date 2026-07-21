"""Scheme guards for enrichment HTTP clients (S310 defense).

Genius and Last.fm are https-only APIs. ``_ensure_https_url`` refuses any URL
whose scheme is not https before it reaches ``urllib.request``, so a hostile
search-result URL (Genius returns URLs in its API responses) cannot redirect
tuneshift to ``file:``, ``http:``, or a custom scheme.
"""

import pytest

from tuneshift.enrichment import genius, lastfm


@pytest.mark.parametrize("module", [genius, lastfm])
class TestEnsureHttpsUrl:
    def test_https_accepted(self, module) -> None:
        assert module._ensure_https_url("https://api.example.com/x") == (
            "https://api.example.com/x"
        )

    def test_http_rejected(self, module) -> None:
        with pytest.raises(ValueError, match="non-https"):
            module._ensure_https_url("http://api.example.com/x")

    def test_file_scheme_rejected(self, module) -> None:
        with pytest.raises(ValueError, match="non-https"):
            module._ensure_https_url("file:///etc/passwd")

    def test_empty_scheme_rejected(self, module) -> None:
        with pytest.raises(ValueError, match="non-https"):
            module._ensure_https_url("api.example.com/x")
