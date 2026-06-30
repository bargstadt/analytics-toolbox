"""Tests for the public resolve() function."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from analytics_toolbox._config import AnalyticsToolboxConfig
from analytics_toolbox.entity_resolution import resolve
from analytics_toolbox.entity_resolution._config import EntityResolutionConfig
from analytics_toolbox.geospatial._config import StorageConfig


def minimal_config(match_threshold: float = 0.80) -> AnalyticsToolboxConfig:
    """Build a minimal AnalyticsToolboxConfig with only entity_resolution set."""
    storage = StorageConfig(data_dir=Path("/tmp"), connection="/tmp/test.duckdb")
    er = EntityResolutionConfig(match_threshold=match_threshold)
    return AnalyticsToolboxConfig(storage=storage, geospatial=None, entity_resolution=er)


def smith_systems() -> dict[str, pd.DataFrame]:
    return {
        "system_a_id": pd.DataFrame(
            {
                "system_a_id": ["456"],
                "First_Name": ["JOHN"],
                "Last_Name": ["SMITH"],
                "DOB": ["2025-01-06"],
                "Address": ["151 NW Something St"],
                "City": ["Des Moines"],
                "Postal_Code": ["50131"],
                "State": ["IA"],
            }
        ),
        "system_b_id": pd.DataFrame(
            {
                "system_b_id": ["879"],
                "First_Name": ["JOHNY"],
                "Last_Name": ["SMITH"],
                "DOB": ["2025-01-06"],
                "Address": ["151 NW Something St"],
                "City": ["Des Moines"],
                "Postal_Code": ["50131"],
                "State": ["IA"],
            }
        ),
    }


class TestValidation:
    def test_fewer_than_two_systems_raises(self):
        config = minimal_config()
        with pytest.raises(ValueError, match="at least 2"):
            resolve(
                {"only_one": pd.DataFrame({"only_one": ["x"], "DOB": ["2000-01-01"]})},
                config=config,
            )

    def test_empty_systems_dict_raises(self):
        config = minimal_config()
        with pytest.raises(ValueError, match="at least 2"):
            resolve({}, config=config)

    def test_missing_entity_resolution_config_raises(self):
        storage = StorageConfig(data_dir=Path("/tmp"), connection="/tmp/test.duckdb")
        config = AnalyticsToolboxConfig(storage=storage, geospatial=None, entity_resolution=None)
        with pytest.raises(ValueError, match="entity_resolution"):
            resolve(smith_systems(), config=config)

    def test_empty_dataframe_raises(self):
        config = minimal_config()
        systems = {
            "sys_a_id": pd.DataFrame(columns=["sys_a_id", "DOB"]),
            "sys_b_id": pd.DataFrame({"sys_b_id": ["b1"], "DOB": ["2000-01-01"]}),
        }
        with pytest.raises(ValueError, match="empty"):
            resolve(systems, config=config)


class TestBasicOutput:
    def test_returns_dataframe(self):
        config = minimal_config()
        result = resolve(smith_systems(), config=config)
        assert isinstance(result, pd.DataFrame)

    def test_output_has_system_id_columns(self):
        config = minimal_config()
        result = resolve(smith_systems(), config=config)
        assert "system_a_id" in result.columns
        assert "system_b_id" in result.columns

    def test_output_has_similarity_columns(self):
        config = minimal_config()
        result = resolve(smith_systems(), config=config)
        assert "avg_similarity" in result.columns
        assert "min_similarity" in result.columns

    def test_john_johny_smith_cluster_together(self):
        config = minimal_config(match_threshold=0.70)
        result = resolve(smith_systems(), config=config)
        assert len(result) >= 1
        # Find the row where system_a_id = "456"
        a_row = result[result["system_a_id"] == "456"]
        assert not a_row.empty
        assert a_row.iloc[0]["system_b_id"] == "879"

    def test_min_leq_avg(self):
        config = minimal_config(match_threshold=0.70)
        result = resolve(smith_systems(), config=config)
        for _, row in result.iterrows():
            assert row["min_similarity"] <= row["avg_similarity"] + 1e-9


class TestReExport:
    def test_resolve_importable_from_package(self):
        from analytics_toolbox.entity_resolution import resolve as r
        assert callable(r)
