"""
Shared pytest fixtures for the WikiGuard test suite.

All tests in this suite run without a Spark cluster or network access.
Fixtures defined here are available to every test module automatically.
"""
import pytest

from wikiguard.config import Config

_TEST_EMAIL: str = "test@example.com"


@pytest.fixture()
def cfg() -> Config:
    """A valid Config instance with all defaults and a test-safe email."""
    return Config(contact_email=_TEST_EMAIL)
