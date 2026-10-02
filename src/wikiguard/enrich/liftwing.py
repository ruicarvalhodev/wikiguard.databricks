"""
WikiGuard Lift Wing API client.

Thin wrapper around the revertrisk-language-agnostic model endpoint.
Calls are made synchronously; threading or rate-limiting are out of scope
at this POC stage.
"""
from __future__ import annotations

import json
import logging

import requests

log = logging.getLogger(__name__)


class InvalidResponse(Exception):
    """Raised when the Lift Wing response body fails validation.

    Attributes
    ----------
    body_text:
        The raw response body, available to the caller for error logging.
    """

    def __init__(self, message: str, body_text: str = "") -> None:
        super().__init__(message)
        self.body_text = body_text


def parse_response(status_code: int, body_text: str) -> dict:
    """
    Parse and validate a Lift Wing API response.

    For HTTP 200, validates that the body is JSON, that
    ``output.probabilities.true`` is a number in ``[0, 1]``, and that
    ``model_version`` is present.  Raises ``InvalidResponse`` if any check
    fails.

    For HTTP 400 or 422, returns a null-score result immediately.
    400 means the wiki is not supported by the model; 422 means there is
    no parent revision to compare against.

    Parameters
    ----------
    status_code:
        HTTP status code of the response.
    body_text:
        Raw response body as a string.

    Returns
    -------
    dict
        Keys: ``revert_risk``, ``prediction``, ``model_version``,
        ``http_status``.

    Raises
    ------
    InvalidResponse
        If the body fails any validation check.
    """
    if status_code in (400, 422):
        return {
            "revert_risk":   None,
            "prediction":    None,
            "model_version": None,
            "http_status":   status_code,
        }

    try:
        data = json.loads(body_text)
    except json.JSONDecodeError as exc:
        raise InvalidResponse(
            f"Response is not valid JSON: {exc}", body_text=body_text
        ) from exc

    output = data.get("output", {})
    probs  = output.get("probabilities", {})

    revert_risk = probs.get("true")
    if revert_risk is None:
        raise InvalidResponse(
            "output.probabilities.true is missing from response",
            body_text=body_text,
        )
    if not isinstance(revert_risk, (int, float)) or not (0 <= revert_risk <= 1):
        raise InvalidResponse(
            f"output.probabilities.true must be a number in [0, 1], got {revert_risk!r}",
            body_text=body_text,
        )

    model_version = data.get("model_version")
    if model_version is None:
        raise InvalidResponse(
            "model_version is missing from response", body_text=body_text
        )

    return {
        "revert_risk":   float(revert_risk),
        "prediction":    output.get("prediction"),
        "model_version": model_version,
        "http_status":   200,
    }


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
        For anything other than 200, 400, or 422 that the session's own
        retries did not resolve.  The caller should log, skip, and retry
        next run.
    """
    resp = session.post(
        url,
        json={"rev_id": rev_id, "lang": lang},
        timeout=(10, 30),
    )

    if resp.status_code in (400, 422):
        return parse_response(resp.status_code, resp.text)

    resp.raise_for_status()  # raises for any other non-2xx

    return parse_response(200, resp.text)  # may raise InvalidResponse
