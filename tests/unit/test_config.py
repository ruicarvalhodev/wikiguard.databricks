"""Unit tests for wikiguard.config.

All tests run without a Spark cluster or network access.
"""
import pytest

from wikiguard.config import Config, CONFIG, _PLACEHOLDER_EMAIL

_TEST_EMAIL: str = "test@example.com"


# ---------------------------------------------------------------------------
# Default field values
# ---------------------------------------------------------------------------

class TestConfigDefaults:
    def test_catalog_default(self):
        cfg = Config(contact_email=_TEST_EMAIL)
        assert cfg.catalog == "bootcamp_students"

    def test_project_default(self):
        cfg = Config(contact_email=_TEST_EMAIL)
        assert cfg.project == "wikiguard"

    def test_bronze_schema_default(self):
        cfg = Config(contact_email=_TEST_EMAIL)
        assert cfg.bronze_schema == "wikiguard_bronze"

    def test_silver_schema_default(self):
        cfg = Config(contact_email=_TEST_EMAIL)
        assert cfg.silver_schema == "wikiguard_silver"

    def test_gold_schema_default(self):
        cfg = Config(contact_email=_TEST_EMAIL)
        assert cfg.gold_schema == "wikiguard_gold"

    def test_landing_volume_default(self):
        cfg = Config(contact_email=_TEST_EMAIL)
        assert cfg.landing_volume == "landing"

    def test_wikis_default(self):
        cfg = Config(contact_email=_TEST_EMAIL)
        assert cfg.wikis == ["enwiki", "dewiki", "frwiki"]


# ---------------------------------------------------------------------------
# Derived properties
# ---------------------------------------------------------------------------

class TestConfigDerivedProperties:
    def test_bronze_table(self):
        cfg = Config(contact_email=_TEST_EMAIL)
        assert cfg.bronze_table == "bootcamp_students.wikiguard_bronze.raw_recentchange"

    def test_volume_path(self):
        cfg = Config(contact_email=_TEST_EMAIL)
        assert cfg.volume_path == "/Volumes/bootcamp_students/wikiguard_bronze/landing"

    def test_events_path(self):
        cfg = Config(contact_email=_TEST_EMAIL)
        assert cfg.events_path == "/Volumes/bootcamp_students/wikiguard_bronze/landing/events"

    def test_checkpoint_path(self):
        cfg = Config(contact_email=_TEST_EMAIL)
        assert cfg.checkpoint_path == "/Volumes/bootcamp_students/wikiguard_bronze/landing/checkpoints"

    def test_schema_path(self):
        cfg = Config(contact_email=_TEST_EMAIL)
        assert cfg.schema_path == "/Volumes/bootcamp_students/wikiguard_bronze/landing/schema"

    def test_derived_paths_reflect_catalog_override(self):
        cfg = Config(catalog="other_catalog", contact_email=_TEST_EMAIL)
        assert cfg.bronze_table == "other_catalog.wikiguard_bronze.raw_recentchange"
        assert cfg.volume_path == "/Volumes/other_catalog/wikiguard_bronze/landing"

    def test_derived_schemas_reflect_project_override(self):
        cfg = Config(project="myproject", contact_email=_TEST_EMAIL)
        assert cfg.bronze_schema == "myproject_bronze"
        assert cfg.silver_schema == "myproject_silver"
        assert cfg.gold_schema == "myproject_gold"
        assert cfg.bronze_table == "bootcamp_students.myproject_bronze.raw_recentchange"


# ---------------------------------------------------------------------------
# Field overrides
# ---------------------------------------------------------------------------

class TestConfigOverride:
    def test_catalog_override(self):
        cfg = Config(catalog="test_catalog", contact_email=_TEST_EMAIL)
        assert cfg.catalog == "test_catalog"

    def test_project_override(self):
        cfg = Config(project="otherwiki", contact_email=_TEST_EMAIL)
        assert cfg.project == "otherwiki"
        assert cfg.bronze_schema == "otherwiki_bronze"

    def test_override_does_not_affect_global_config(self):
        """Overriding fields in a local instance must not mutate CONFIG."""
        cfg = Config(catalog="override", contact_email=_TEST_EMAIL)
        assert cfg.catalog == "override"
        assert CONFIG.catalog == "bootcamp_students"  # global unchanged

    def test_frozen_rejects_mutation(self):
        cfg = Config(contact_email=_TEST_EMAIL)
        with pytest.raises((AttributeError, TypeError)):
            cfg.catalog = "mutated"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

class TestConfigValidation:
    def test_placeholder_email_raises(self):
        with pytest.raises(ValueError):
            Config(contact_email=_PLACEHOLDER_EMAIL)

    def test_no_args_raises(self):
        """Config() with no arguments uses the placeholder and must fail."""
        with pytest.raises(ValueError):
            Config()

    def test_error_message_mentions_placeholder(self):
        with pytest.raises(ValueError) as exc_info:
            Config(contact_email=_PLACEHOLDER_EMAIL)
        message = str(exc_info.value).lower()
        assert "contact_email" in message or "placeholder" in message


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

class TestConfigSingleton:
    def test_config_singleton_is_valid(self):
        """The module-level CONFIG must be importable and not the placeholder."""
        assert CONFIG.contact_email != _PLACEHOLDER_EMAIL
        assert CONFIG.catalog == "bootcamp_students"
        assert CONFIG.project == "wikiguard"
