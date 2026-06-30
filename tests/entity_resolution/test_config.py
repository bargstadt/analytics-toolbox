"""Tests for EntityResolutionConfig and load_config() entity_resolution section."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from analytics_toolbox.entity_resolution._config import (
    _DEFAULT_FIELD_WEIGHTS,
    EntityResolutionConfig,
)


class TestEntityResolutionConfig:
    def test_match_threshold_stored(self):
        c = EntityResolutionConfig(match_threshold=0.80)
        assert c.match_threshold == 0.80

    def test_defaults(self):
        c = EntityResolutionConfig(match_threshold=0.75)
        assert c.block_column == "DOB"
        assert c.secondary_block_columns == ["Last_Name", "Postal_Code"]
        assert c.top_n_matches == 1
        assert c.max_block_pairs == 500_000
        assert c.address_col == "Address"
        assert c.field_weights == _DEFAULT_FIELD_WEIGHTS

    def test_field_weights_default_is_independent_copy(self):
        c1 = EntityResolutionConfig(match_threshold=0.8)
        c2 = EntityResolutionConfig(match_threshold=0.8)
        c1.field_weights["NEW"] = 99.0
        assert "NEW" not in c2.field_weights

    def test_custom_field_weights(self):
        weights = {"DOB": 1.0, "Last_Name": 0.5}
        c = EntityResolutionConfig(match_threshold=0.8, field_weights=weights)
        assert c.field_weights == weights

    def test_custom_secondary_block_columns(self):
        c = EntityResolutionConfig(
            match_threshold=0.8, secondary_block_columns=["Last_Name", "City"]
        )
        assert c.secondary_block_columns == ["Last_Name", "City"]


class TestLoadConfig:
    def test_load_config_with_entity_resolution_section(self, tmp_path: Path):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(
            textwrap.dedent("""\
                storage:
                  connection: /tmp/test.duckdb
                  data_dir: /tmp/

                entity_resolution:
                  match_threshold: 0.85
                  block_column: DOB
            """)
        )
        from analytics_toolbox._config import load_config

        config = load_config(cfg_file)
        assert config.entity_resolution is not None
        assert config.entity_resolution.match_threshold == 0.85
        assert config.entity_resolution.block_column == "DOB"
        # Unspecified keys take defaults
        assert config.entity_resolution.top_n_matches == 1

    def test_load_config_without_entity_resolution_section(self, tmp_path: Path):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(
            textwrap.dedent("""\
                storage:
                  connection: /tmp/test.duckdb
                  data_dir: /tmp/
            """)
        )
        from analytics_toolbox._config import load_config

        config = load_config(cfg_file)
        assert config.entity_resolution is None

    def test_load_config_missing_match_threshold_raises(self, tmp_path: Path):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(
            textwrap.dedent("""\
                storage:
                  connection: /tmp/test.duckdb
                  data_dir: /tmp/

                entity_resolution:
                  block_column: DOB
            """)
        )
        from analytics_toolbox._config import load_config

        with pytest.raises(ValueError, match="match_threshold"):
            load_config(cfg_file)

    def test_load_config_custom_field_weights(self, tmp_path: Path):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(
            textwrap.dedent("""\
                storage:
                  connection: /tmp/test.duckdb
                  data_dir: /tmp/

                entity_resolution:
                  match_threshold: 0.80
                  field_weights:
                    DOB: 1.0
                    Last_Name: 0.50
            """)
        )
        from analytics_toolbox._config import load_config

        config = load_config(cfg_file)
        assert config.entity_resolution.field_weights == {"DOB": 1.0, "Last_Name": 0.50}

    def test_load_config_secondary_block_columns(self, tmp_path: Path):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(
            textwrap.dedent("""\
                storage:
                  connection: /tmp/test.duckdb
                  data_dir: /tmp/

                entity_resolution:
                  match_threshold: 0.80
                  secondary_block_columns: [Last_Name, City]
            """)
        )
        from analytics_toolbox._config import load_config

        config = load_config(cfg_file)
        assert config.entity_resolution.secondary_block_columns == ["Last_Name", "City"]

    def test_geospatial_still_loads_when_entity_resolution_present(self, tmp_path: Path):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(
            textwrap.dedent("""\
                storage:
                  connection: /tmp/test.duckdb
                  data_dir: /tmp/

                geospatial:
                  nad:
                    states: [IA]
                  tiger:
                    vintage: 2024
                  matching:
                    confidence_threshold: 90

                entity_resolution:
                  match_threshold: 0.80
            """)
        )
        from analytics_toolbox._config import load_config

        config = load_config(cfg_file)
        assert config.geospatial is not None
        assert config.entity_resolution is not None
