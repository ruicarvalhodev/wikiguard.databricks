"""
WikiGuard runtime configuration.

A single frozen dataclass drives all configuration.  There are no YAML files,
environment variables, or widgets -- configuration is plain Python code.

Usage
-----
    from wikiguard.config import CONFIG          # normal access
    from wikiguard.config import Config          # override in tests

    # Override a single field without mutating the global instance:
    test_cfg = Config(catalog="test_catalog", contact_email="user@example.com")

Note
----
contact_email has no valid default.  Constructing Config() with no arguments
raises ValueError with a clear message.  This is intentional -- Wikimedia's
bot policy (task T400119) requires an identifiable operator address in the
User-Agent header.  Anonymous requests are blocked at the edge.

Environment
-----------
The catalog ``bootcamp_students`` is shared with other students and is
provisioned by the training environment -- the project does NOT create it.
Schema names carry the ``wikiguard_`` prefix to avoid collisions with other
projects in the same shared catalog.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List

_PLACEHOLDER_EMAIL: str = "your-email@example.com"


@dataclass(frozen=True)
class Config:
    # ------------------------------------------------------------------ #
    # Unity Catalog topology                                               #
    # ------------------------------------------------------------------ #
    catalog: str = "bootcamp_students"
    project: str = "wikiguard"

    # ------------------------------------------------------------------ #
    # Volume & landing paths                                               #
    # ------------------------------------------------------------------ #
    landing_volume: str = "landing"

    # ------------------------------------------------------------------ #
    # Wikimedia / API                                                      #
    # ------------------------------------------------------------------ #
    contact_email: str = _PLACEHOLDER_EMAIL
    wikis: List[str] = field(default_factory=lambda: ["enwiki", "dewiki", "frwiki"])
    secret_scope: str = "wikiguard"
    liftwing_url: str = (
        "https://api.wikimedia.org/service/lw/inference/v1/models"
        "/revertrisk-language-agnostic:predict"
    )

    # ------------------------------------------------------------------ #
    # SSE connector                                                        #
    # ------------------------------------------------------------------ #
    stream_url: str = "https://stream.wikimedia.org/v2/stream/recentchange"
    flush_seconds: float = 10.0
    flush_max_events: int = 2000
    connect_timeout: float = 10.0
    read_timeout: float = 60.0

    # ------------------------------------------------------------------ #
    # Validation                                                           #
    # ------------------------------------------------------------------ #
    def __post_init__(self) -> None:
        if self.contact_email == _PLACEHOLDER_EMAIL:
            raise ValueError(
                "contact_email is still the placeholder value "
                f"({_PLACEHOLDER_EMAIL!r}).  "
                "Provide a real operator address before making requests to Wikimedia. "
                "They use contact_email to reach you if the bot misbehaves "
                "(Wikimedia policy, task T400119)."
            )

    # ------------------------------------------------------------------ #
    # Derived schema names                                                 #
    # ------------------------------------------------------------------ #
    @property
    def bronze_schema(self) -> str:
        """Bronze schema name: ``{project}_bronze`` (e.g. ``wikiguard_bronze``)."""
        return f"{self.project}_bronze"

    @property
    def silver_schema(self) -> str:
        """Silver schema name: ``{project}_silver`` (e.g. ``wikiguard_silver``)."""
        return f"{self.project}_silver"

    @property
    def gold_schema(self) -> str:
        """Gold schema name: ``{project}_gold`` (e.g. ``wikiguard_gold``)."""
        return f"{self.project}_gold"

    # ------------------------------------------------------------------ #
    # Derived paths                                                        #
    # ------------------------------------------------------------------ #
    @property
    def bronze_table(self) -> str:
        """Fully qualified bronze ingest table: ``catalog.bronze_schema.raw_recentchange``."""
        return f"{self.catalog}.{self.bronze_schema}.raw_recentchange"

    @property
    def volume_path(self) -> str:
        """Root UC Volume path."""
        return f"/Volumes/{self.catalog}/{self.bronze_schema}/{self.landing_volume}"

    @property
    def events_path(self) -> str:
        """Volume sub-directory where raw SSE event files land."""
        return f"{self.volume_path}/events"

    @property
    def checkpoint_path(self) -> str:
        """Volume sub-directory for Structured Streaming checkpoints."""
        return f"{self.volume_path}/checkpoints"

    @property
    def schema_path(self) -> str:
        """Volume sub-directory for persisted Spark schema files."""
        return f"{self.volume_path}/schema"

    @property
    def connector_checkpoint_file(self) -> str:
        """Path to the SSE connector's Last-Event-ID checkpoint."""
        return f"{self.checkpoint_path}/connector/last_event_id"

    @property
    def silver_edits_table(self) -> str:
        """Fully qualified silver edits table: ``catalog.silver_schema.edits``."""
        return f"{self.catalog}.{self.silver_schema}.edits"

    @property
    def silver_quarantine_table(self) -> str:
        """Quarantine table for rows that fail silver validation."""
        return f"{self.catalog}.{self.silver_schema}.edits_quarantine"

    @property
    def silver_candidates_table(self) -> str:
        """Fully qualified silver candidates table."""
        return f"{self.catalog}.{self.silver_schema}.candidates"

    @property
    def silver_edit_risk_table(self) -> str:
        """Fully qualified edit risk scores table."""
        return f"{self.catalog}.{self.silver_schema}.edit_risk"

    @property
    def silver_edit_risk_errors_table(self) -> str:
        """Fully qualified edit risk errors table (failed scoring attempts)."""
        return f"{self.catalog}.{self.silver_schema}.edit_risk_errors"


def load_contact_email() -> str:
    """
    Load the operator contact email.

    On Databricks (``DATABRICKS_RUNTIME_VERSION`` is set): reads the secret
    ``wikiguard/contact_email`` via ``dbutils``.  Raises ``RuntimeError``
    with a helpful message if the secret is missing.

    Locally: reads ``WIKIGUARD_CONTACT_EMAIL`` env var, or falls back to
    ``local-dev@example.com`` (safe for unit tests, which never contact
    Wikimedia).
    """
    # Env-var override wins everywhere — lets tests (and CI) skip the vault.
    if email := os.environ.get("WIKIGUARD_CONTACT_EMAIL"):
        return email

    if os.environ.get("DATABRICKS_RUNTIME_VERSION"):
        try:
            from databricks.sdk.runtime import dbutils  # noqa: PLC0415
            return dbutils.secrets.get("wikiguard", "contact_email")
        except Exception as exc:
            raise RuntimeError(
                "Could not read secret 'wikiguard/contact_email'.  "
                "Run notebooks/03_create_secrets to set it up."
            ) from exc

    return "local-dev@example.com"


# ---------------------------------------------------------------------------
# Module-level singleton -- import this for normal use.
# Tests that need a different catalog/schema construct a new Config() directly.
# ---------------------------------------------------------------------------
CONFIG = Config(contact_email=load_contact_email())
