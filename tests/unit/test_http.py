"""Unit tests for wikiguard.common.http.

All tests run without a Spark cluster or network access.  No real HTTP
requests are made.
"""
import requests

from wikiguard.common.http import build_headers, session_with_retries

_TEST_EMAIL: str = "test@example.com"
_UA_PREFIX: str = "WikiGuard/0.1"


# ---------------------------------------------------------------------------
# build_headers
# ---------------------------------------------------------------------------

class TestBuildHeaders:
    def test_returns_both_required_headers(self):
        headers = build_headers(_TEST_EMAIL)
        assert "User-Agent" in headers
        assert "Api-User-Agent" in headers

    def test_user_agent_contains_email(self):
        headers = build_headers(_TEST_EMAIL)
        assert _TEST_EMAIL in headers["User-Agent"]

    def test_api_user_agent_contains_email(self):
        headers = build_headers(_TEST_EMAIL)
        assert _TEST_EMAIL in headers["Api-User-Agent"]

    def test_user_agent_starts_with_wikiguard(self):
        headers = build_headers(_TEST_EMAIL)
        assert headers["User-Agent"].startswith(_UA_PREFIX)

    def test_api_user_agent_starts_with_wikiguard(self):
        headers = build_headers(_TEST_EMAIL)
        assert headers["Api-User-Agent"].startswith(_UA_PREFIX)

    def test_both_headers_are_identical(self):
        """User-Agent and Api-User-Agent must carry the same value."""
        headers = build_headers(_TEST_EMAIL)
        assert headers["User-Agent"] == headers["Api-User-Agent"]

    def test_format_matches_wikimedia_policy(self):
        """Expected format: WikiGuard/0.1 (capstone project; email)"""
        headers = build_headers(_TEST_EMAIL)
        ua = headers["User-Agent"]
        assert "(" in ua
        assert "capstone project" in ua
        assert _TEST_EMAIL in ua

    def test_different_emails_produce_different_headers(self):
        h1 = build_headers("alice@example.com")
        h2 = build_headers("bob@example.com")
        assert h1["User-Agent"] != h2["User-Agent"]


# ---------------------------------------------------------------------------
# session_with_retries
# ---------------------------------------------------------------------------

class TestSessionWithRetries:
    def test_returns_requests_session(self):
        session = session_with_retries(_TEST_EMAIL)
        assert isinstance(session, requests.Session)

    def test_session_user_agent_contains_email(self):
        session = session_with_retries(_TEST_EMAIL)
        assert _TEST_EMAIL in session.headers["User-Agent"]

    def test_session_api_user_agent_contains_email(self):
        session = session_with_retries(_TEST_EMAIL)
        assert _TEST_EMAIL in session.headers["Api-User-Agent"]

    def test_session_user_agent_starts_with_wikiguard(self):
        session = session_with_retries(_TEST_EMAIL)
        assert session.headers["User-Agent"].startswith(_UA_PREFIX)

    def test_session_has_https_adapter(self):
        """Verify an HTTPS adapter with retry logic is mounted."""
        from requests.adapters import HTTPAdapter
        session = session_with_retries(_TEST_EMAIL)
        adapter = session.get_adapter(url="https://api.wikimedia.org/")
        assert isinstance(adapter, HTTPAdapter)
        assert adapter.max_retries is not None

    def test_custom_total_retries(self):
        session = session_with_retries(_TEST_EMAIL, total=3)
        adapter = session.get_adapter(url="https://api.wikimedia.org/")
        assert adapter.max_retries.total == 3
