"""
WikiGuard HTTP utilities.

Wikimedia blocks generic User-Agents at the edge (task T400119).  A request
without an identifiable User-Agent receives HTTP 403 before it reaches the
application layer.  This module provides the shared header-builder and a
pre-configured session used by the three downstream services:
  - Lift Wing ML API
  - MediaWiki compare endpoint
  - MediaWiki recentchanges feed

This module contains no business logic -- only the HTTP transport layer.
"""
from __future__ import annotations

import random
from typing import List, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

_WIKIGUARD_VERSION: str = "0.1"
_PROJECT_DESCRIPTION: str = "capstone project"


def build_headers(contact_email: str) -> dict:
    """
    Return the HTTP headers required by Wikimedia's User-Agent policy.

    Both ``User-Agent`` and ``Api-User-Agent`` are set to the same formatted
    string so the header is recognised regardless of which field the receiving
    service inspects.

    Parameters
    ----------
    contact_email:
        A valid operator e-mail address included in the header so Wikimedia
        can reach the operator if the bot misbehaves.  This is a policy
        requirement, not optional.

    Returns
    -------
    dict
        ``{"User-Agent": ..., "Api-User-Agent": ...}``

    Examples
    --------
    >>> build_headers("me@example.com")
    {'User-Agent': 'WikiGuard/0.1 (capstone project; me@example.com)', ...}
    """
    ua = f"WikiGuard/{_WIKIGUARD_VERSION} ({_PROJECT_DESCRIPTION}; {contact_email})"
    return {"User-Agent": ua, "Api-User-Agent": ua}


class _JitterRetry(Retry):
    """
    ``urllib3.util.retry.Retry`` subclass with full jitter.

    Instead of sleeping for the full computed back-off interval, sleep for a
    random duration in ``[0, computed_backoff]``.  This spreads retries across
    time when many clients hit the same error simultaneously ("thundering herd"
    mitigation).
    """

    def get_backoff_time(self) -> float:  # type: ignore[override]
        base = super().get_backoff_time()
        return random.uniform(0.0, base) if base > 0.0 else 0.0


def session_with_retries(
    contact_email: str,
    total: int = 5,
    backoff_factor: float = 1.0,
    status_forcelist: Optional[List[int]] = None,
    pool_connections: int = 4,
    pool_maxsize: int = 10,
) -> requests.Session:
    """
    Return a ``requests.Session`` configured with:

    - Wikimedia-compliant ``User-Agent`` / ``Api-User-Agent`` headers
      (see :func:`build_headers`).
    - Exponential back-off with full jitter on 429 and 5xx responses.
    - No retry on 4xx errors other than 429 (client errors are not transient).

    Parameters
    ----------
    contact_email:
        Forwarded to :func:`build_headers`.
    total:
        Maximum number of retry attempts.
    backoff_factor:
        Base multiplier for the exponential back-off interval (seconds).
        Effective sleep before attempt *n* is approximately
        ``backoff_factor * 2 ** (n - 1)`` seconds (before jitter).
    status_forcelist:
        HTTP status codes that trigger a retry.  Defaults to
        ``[429, 500, 502, 503, 504]``.
    pool_connections:
        Number of connection pools to cache.
    pool_maxsize:
        Maximum number of connections to keep in each pool.

    Returns
    -------
    requests.Session
    """
    if status_forcelist is None:
        status_forcelist = [429, 500, 502, 503, 504]

    retry = _JitterRetry(
        total=total,
        backoff_factor=backoff_factor,
        status_forcelist=status_forcelist,
        allowed_methods=["GET", "POST"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(
        max_retries=retry,
        pool_connections=pool_connections,
        pool_maxsize=pool_maxsize,
    )
    session = requests.Session()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update(build_headers(contact_email))
    return session
