"""
WikiGuard Lift Wing API client.

Thin wrapper around the revertrisk-language-agnostic model endpoint.
Calls are made synchronously; threading or rate-limiting are out of scope
at this POC stage.
"""
from __future__ import annotations

import logging
from typing import Optional

import requests

log = logging.getLogger(__name__)


def score_revision(
    session: requests.Session,
    url: str,
    rev_id: int,
    lang: str,
) -> dict:
    """
    Score one revision with the Lift Wing revertrisk model.

    Parameters
    ----------
    session:
        Requests session with Wikimedia headers already set
        (``User-Agent`` and ``Api-User-Agent`` are mandatory).
    url:
        Lift Wing model endpoint URL.
    rev_id:
        Revision ID to score.
    lang:
        Language code of the wiki (e.g. ``"en"``, ``"de"``).

    Returns
    -------
    dict
        Keys: ``revert_risk`` (float | None), ``prediction`` (bool | None),
        ``model_version`` (str | None), ``http_status`` (int).

    Raises
    ------
    requests.RequestException
        For anything other than 200 or 422 that the session's own retries
        did not resolve.  The caller should log, skip, and retry next run.
    """
    resp = session.post(
        url,
        json={"rev_id": rev_id, "lang": lang},
        timeout=(10, 30),
    )

    if resp.status_code == 422:
        # The model returns 422 when a revision has no parent to compare
        # against (e.g. page creation with no prior content).  This is
        # final — retrying will return the same result.
        return {
            "revert_risk":   None,
            "prediction":    None,
            "model_version": None,
            "http_status":   422,
        }

    resp.raise_for_status()  # raises for any other non-2xx

    data   = resp.json()
    output = data.get("output", {})
    probs  = output.get("probabilities", {})

    return {
        "revert_risk":   probs.get("true"),
        "prediction":    output.get("prediction"),
        "model_version": data.get("model_version"),
        "http_status":   200,
    }
