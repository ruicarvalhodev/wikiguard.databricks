"""Unit tests for Lift Wing response parsing — no network."""
import json

import pytest

from wikiguard.enrich.liftwing import InvalidResponse, parse_response


def _valid_body(true_prob: float = 0.123, prediction: bool = False, model_version: str = "3") -> str:
    """Build a minimal valid Lift Wing response body."""
    return json.dumps({
        "model_version": model_version,
        "output": {
            "prediction": prediction,
            "probabilities": {"true": true_prob, "false": round(1 - true_prob, 6)},
        },
    })


class TestParseResponse:
    def test_valid_200(self):
        result = parse_response(200, _valid_body())
        assert result["revert_risk"] == pytest.approx(0.123)
        assert result["model_version"] == "3"
        assert result["http_status"] == 200
        assert result["prediction"] is False

    def test_422_returns_nulls(self):
        result = parse_response(422, "")
        assert result["http_status"] == 422
        assert result["revert_risk"] is None
        assert result["model_version"] is None

    def test_not_json_raises(self):
        with pytest.raises(InvalidResponse, match="not valid JSON"):
            parse_response(200, "this is not json")

    def test_probabilities_true_missing_raises(self):
        body = json.dumps({
            "model_version": "3",
            "output": {"prediction": False, "probabilities": {}},
        })
        with pytest.raises(InvalidResponse, match="probabilities.true"):
            parse_response(200, body)

    def test_probability_out_of_range_raises(self):
        with pytest.raises(InvalidResponse, match=r"\[0, 1\]"):
            parse_response(200, _valid_body(true_prob=1.7))

    def test_model_version_missing_raises(self):
        body = json.dumps({
            "output": {
                "prediction": False,
                "probabilities": {"true": 0.1, "false": 0.9},
            }
        })
        with pytest.raises(InvalidResponse, match="model_version"):
            parse_response(200, body)

    def test_invalid_response_stores_body_text(self):
        """body_text attribute is available to the caller for error logging."""
        bad = "definitely not json"
        with pytest.raises(InvalidResponse) as exc_info:
            parse_response(200, bad)
        assert exc_info.value.body_text == bad
