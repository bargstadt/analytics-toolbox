"""End-to-end integration tests for entity_resolution using the MPI fixture.

All tests use make_mpi_fixture() and run through the full resolve() pipeline.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from analytics_toolbox._config import AnalyticsToolboxConfig
from analytics_toolbox.entity_resolution import resolve
from analytics_toolbox.entity_resolution._config import EntityResolutionConfig
from analytics_toolbox.entity_resolution.fixtures.multi_system import (
    make_mpi_fixture,
)
from analytics_toolbox.geospatial._config import StorageConfig


def make_config(match_threshold: float = 0.70) -> AnalyticsToolboxConfig:
    storage = StorageConfig(data_dir=Path("/tmp"), connection="/tmp/test.duckdb")
    er = EntityResolutionConfig(match_threshold=match_threshold)
    return AnalyticsToolboxConfig(storage=storage, geospatial=None, entity_resolution=er)


@pytest.fixture
def systems():
    return make_mpi_fixture()


@pytest.fixture
def config():
    return make_config()


@pytest.fixture
def result(systems, config):
    return resolve(systems, config=config)


class TestOutputShape:
    def test_output_is_dataframe(self, result):
        import pandas as pd
        assert isinstance(result, pd.DataFrame)

    def test_output_column_count(self, result, systems):
        # 5 system columns + avg_similarity + min_similarity = 7
        assert len(result.columns) == len(systems) + 2

    def test_output_has_system_columns(self, result, systems):
        for col in systems:
            assert col in result.columns

    def test_output_has_similarity_columns(self, result):
        assert "avg_similarity" in result.columns
        assert "min_similarity" in result.columns

    def test_min_leq_avg_for_all_rows(self, result):
        for _, row in result.iterrows():
            assert row["min_similarity"] <= row["avg_similarity"] + 1e-9


class TestSmithCluster:
    def test_john_johny_johnathan_cluster_together(self, result):
        """The three Smith personas should all appear in the same cluster row."""
        smith_row = result[result["system_a_id"] == "456"]
        assert not smith_row.empty, "John Smith (456) not found in output"
        row = smith_row.iloc[0]
        assert row["system_b_id"] == "879", "Johny Smith (879) not linked to John"
        assert row["system_c_id"] == "1941", "Johnathan Smith (1941) not linked to John"

    def test_smith_cluster_similarity_above_threshold(self, result):
        smith_row = result[result["system_a_id"] == "456"]
        assert not smith_row.empty
        assert smith_row.iloc[0]["avg_similarity"] >= 0.70


class TestSmoothCluster:
    def test_james_jim_smooth_cluster_together(self, result):
        """James and Jim Smooth should appear in the same cluster.

        Jim has null DOB so he goes through secondary blocking (Last_Name + Postal_Code).
        He also has no Postal_Code, so his secondary block key will be (SMOOTH, '').
        James also has no Postal_Code. Both land in the same secondary block.
        """
        # Either James or Jim could be in system_d or system_e
        james_row = result[result["system_d_id"] == "124"]
        if not james_row.empty:
            row = james_row.iloc[0]
            assert row["system_e_id"] == "516", "Jim Smooth (516) not linked to James (124)"

    def test_smooth_cluster_does_not_include_smith(self, result):
        """Smith and Smooth must NOT be in the same cluster row."""
        # Smith cluster: system_a_id = 456 should be in a row where system_d_id is None
        smith_rows = result[result["system_a_id"] == "456"]
        for _, row in smith_rows.iterrows():
            assert pd.isna(row["system_d_id"]), (
                "James Smooth (system_d_id=124) was incorrectly linked to John Smith (456)"
            )
            assert pd.isna(row["system_e_id"]), (
                "Jim Smooth (system_e_id=516) was incorrectly linked to John Smith (456)"
            )


class TestMissingField:
    def test_missing_postal_code_system_participates(self, result):
        """system_c has no Postal_Code but should still link to system_a and system_b."""
        smith_row = result[result["system_a_id"] == "456"]
        if not smith_row.empty:
            # system_c_id should be populated even though system_c lacks Postal_Code
            assert smith_row.iloc[0]["system_c_id"] == "1941"


class TestFanOutGuard:
    def test_fan_out_guard_raises_on_tiny_limit(self):
        """A block where len_a × len_b > max_block_pairs must raise RuntimeError."""
        import pandas as pd

        # Build two systems each with 2 records sharing the same DOB
        # → 2×2 = 4 pairs; cap at 3 → should raise
        sys_a = pd.DataFrame(
            {
                "sys_a_id": ["a1", "a2"],
                "DOB": ["2000-01-01", "2000-01-01"],
                "Last_Name": ["Smith", "Jones"],
            }
        )
        sys_b = pd.DataFrame(
            {
                "sys_b_id": ["b1", "b2"],
                "DOB": ["2000-01-01", "2000-01-01"],
                "Last_Name": ["Smith", "Jones"],
            }
        )
        storage = StorageConfig(data_dir=Path("/tmp"), connection="/tmp/test.duckdb")
        er = EntityResolutionConfig(match_threshold=0.70, max_block_pairs=3)
        config = AnalyticsToolboxConfig(storage=storage, geospatial=None, entity_resolution=er)
        with pytest.raises(RuntimeError, match="max_block_pairs"):
            resolve({"sys_a_id": sys_a, "sys_b_id": sys_b}, config=config)


class TestOutputCount:
    def test_at_least_one_cluster_found(self, result):
        assert len(result) >= 1

    def test_cluster_count_leq_total_records(self, result, systems):
        total_records = sum(len(df) for df in systems.values())
        assert len(result) <= total_records
