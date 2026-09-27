"""Unit tests for the SSE parser -- no network, no cluster."""
from wikiguard.ingest.connector import parse_sse


class TestParseSse:
    def test_single_event(self):
        lines = ["data: hello world", "id: 42", ""]
        assert list(parse_sse(lines)) == [("42", "hello world")]

    def test_keepalive_ignored(self):
        lines = [": keepalive", "data: actual", "id: 1", ""]
        assert list(parse_sse(lines)) == [("1", "actual")]

    def test_multiline_data(self):
        lines = ["data: line1", "data: line2", "id: 5", ""]
        assert list(parse_sse(lines)) == [("5", "line1\nline2")]

    def test_no_id(self):
        lines = ["data: payload", ""]
        assert list(parse_sse(lines)) == [(None, "payload")]

    def test_no_trailing_blank_not_emitted(self):
        """An event without a trailing blank line is not yielded."""
        lines = ["data: incomplete"]
        assert list(parse_sse(lines)) == []

    def test_leading_space_stripped(self):
        lines = ["data:  two spaces", "id: x", ""]
        # Only one leading space stripped; the second is data
        assert list(parse_sse(lines)) == [("x", " two spaces")]

    def test_multiple_events(self):
        lines = [
            "data: first", "id: 1", "",
            ": comment",
            "data: second", "id: 2", "",
        ]
        assert list(parse_sse(lines)) == [
            ("1", "first"),
            ("2", "second"),
        ]
