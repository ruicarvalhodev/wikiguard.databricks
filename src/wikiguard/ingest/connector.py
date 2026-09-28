"""
WikiGuard SSE connector.

Consumes ``https://stream.wikimedia.org/v2/stream/recentchange`` and writes
raw events as JSONL files into the UC Volume for Auto Loader to pick up.

Delivery is **at-least-once**.  File publish happens before the checkpoint
is saved, so a crash between the two replays a few seconds of events.  Silver
deduplicates on ``(wiki, rev_id)`` in task 2.2.

Canary events (``meta.domain == "canary"``) are written like any other
event -- raw bronze keeps everything; silver filters them out.
"""
from __future__ import annotations

import logging
import os
import time
import uuid
from dataclasses import dataclass
from typing import Iterator, Optional, Tuple

import requests

from wikiguard.common.http import build_headers
from wikiguard.config import CONFIG, Config

log = logging.getLogger(__name__)


# ------------------------------------------------------------------ #
# SSE parser                                                          #
# ------------------------------------------------------------------ #

def parse_sse(lines) -> Iterator[Tuple[Optional[str], str]]:
    """
    Parse Server-Sent Events from an iterable of text lines.

    Yields ``(event_id, data)`` tuples.  *event_id* is ``None`` when the
    server omits the ``id:`` field.  Lines starting with ``:`` are
    keepalive comments and are silently discarded.

    This is a dependency-free parser that follows the SSE specification:
    blank lines delimit events, a single leading space after the colon is
    stripped, and unknown fields are ignored.
    """
    event_id: Optional[str] = None
    data_parts: list[str] = []

    for raw in lines:
        line = raw.rstrip("\r\n")

        if not line:                       # blank line -> emit event
            if data_parts:
                yield event_id, "\n".join(data_parts)
                event_id = None
                data_parts = []
            continue

        if line.startswith(":"):           # comment / keepalive
            continue

        if ":" in line:
            name, _, value = line.partition(":")
            if value.startswith(" "):      # strip single leading space
                value = value[1:]
        else:
            name, value = line, ""

        if name == "data":
            data_parts.append(value)
        elif name == "id":
            event_id = value
        # event:, retry:, etc. -- ignored


# ------------------------------------------------------------------ #
# Checkpoint helpers                                                   #
# ------------------------------------------------------------------ #

def _load_checkpoint(config: Config) -> Optional[str]:
    """Return the saved Last-Event-ID, or ``None`` if no checkpoint exists."""
    try:
        with open(config.connector_checkpoint_file) as f:
            return f.read().strip() or None
    except FileNotFoundError:
        return None


def _save_checkpoint(config: Config, event_id: str) -> None:
    """Atomically persist *event_id* (write tmp -> rename)."""
    path = config.connector_checkpoint_file
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(event_id)
    os.rename(tmp, path)


# ------------------------------------------------------------------ #
# Batch writer                                                        #
# ------------------------------------------------------------------ #

class _BatchWriter:
    """Accumulate raw event strings and flush to JSONL files."""

    def __init__(self, config: Config, run_id: str) -> None:
        self._events_dir = config.events_path
        self._flush_seconds = config.flush_seconds
        self._flush_max = config.flush_max_events
        self._run_id = run_id
        self._buf: list[str] = []
        self._seq = 0
        self._last_flush = time.time()

    def append(self, data: str) -> None:
        self._buf.append(data)

    @property
    def should_flush(self) -> bool:
        if not self._buf:
            return False
        if len(self._buf) >= self._flush_max:
            return True
        return (time.time() - self._last_flush) >= self._flush_seconds

    def flush(self) -> Optional[str]:
        """Flush the buffer to a JSONL file.  Returns the path, or ``None``."""
        if not self._buf:
            return None

        ts = int(time.time() * 1000)
        name = f"events_{ts}_{self._run_id}_{self._seq:06d}.jsonl"
        tmp_path = os.path.join(self._events_dir, f"_tmp_{name}")
        final_path = os.path.join(self._events_dir, name)

        with open(tmp_path, "w") as f:
            for line in self._buf:
                f.write(line)
                f.write("\n")

        os.rename(tmp_path, final_path)

        count = len(self._buf)
        self._buf.clear()
        self._seq += 1
        self._last_flush = time.time()
        log.info("Flushed %s (%d events)", name, count)
        return final_path


# ------------------------------------------------------------------ #
# RunStats                                                             #
# ------------------------------------------------------------------ #

@dataclass
class RunStats:
    """Counters returned by :func:`run`."""
    events: int = 0
    files: int = 0
    reconnects: int = 0


# ------------------------------------------------------------------ #
# Connector entry point                                                #
# ------------------------------------------------------------------ #

def run(
    config: Config = CONFIG,
    max_seconds: Optional[float] = None,
) -> RunStats:
    """
    Connect to Wikimedia EventStreams and write events to the Volume.

    Parameters
    ----------
    config:
        Runtime configuration (default: module singleton).
    max_seconds:
        Stop after this many seconds.  ``None`` runs forever.

    Returns
    -------
    RunStats
    """
    stats = RunStats()
    run_id = uuid.uuid4().hex[:8]
    writer = _BatchWriter(config, run_id)
    last_id = _load_checkpoint(config)

    if last_id:
        log.info("Resuming from checkpoint: %.80s", last_id)
    else:
        log.info("No checkpoint -- starting from live stream")

    deadline = (time.time() + max_seconds) if max_seconds else None
    hdrs = build_headers(config.contact_email)
    backoff = 1.0

    try:
        while deadline is None or time.time() < deadline:
            try:
                log.info("Connecting to %s", config.stream_url)
                extra = {"Last-Event-ID": last_id} if last_id else {}
                with requests.get(
                    config.stream_url,
                    headers={**hdrs, **extra},
                    stream=True,
                    timeout=(config.connect_timeout, config.read_timeout),
                ) as resp:

                    if resp.status_code == 403:
                        raise SystemExit(
                            "HTTP 403 -- Wikimedia rejected the request.  "
                            "Check CONFIG.contact_email (Wikimedia task T400119)."
                        )
                    resp.raise_for_status()
                    log.info("Connected (HTTP %d)", resp.status_code)
                    backoff = 1.0

                    resp.encoding = "utf-8"
                    for eid, data in parse_sse(
                        resp.iter_lines(decode_unicode=True)
                    ):
                        stats.events += 1
                        writer.append(data)
                        if eid:
                            last_id = eid

                        if writer.should_flush:
                            writer.flush()
                            stats.files += 1
                            if last_id:
                                _save_checkpoint(config, last_id)

                        if deadline and time.time() >= deadline:
                            break

                log.info("Stream ended -- will reconnect")

            except (SystemExit, KeyboardInterrupt):
                raise
            except Exception as exc:
                stats.reconnects += 1
                wait = min(backoff, 60.0)
                log.warning("Error (%s), reconnecting in %.0fs", exc, wait)
                time.sleep(wait)
                backoff = min(backoff * 2, 60.0)

    finally:
        path = writer.flush()
        if path:
            stats.files += 1
            if last_id:
                _save_checkpoint(config, last_id)
            log.info("Final flush on exit")

    log.info(
        "Done: %d events, %d files, %d reconnects",
        stats.events, stats.files, stats.reconnects,
    )
    return stats
