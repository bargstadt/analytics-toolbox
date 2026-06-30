"""Tests for _cluster.build_clusters()."""

from __future__ import annotations

import pandas as pd
import pytest

from analytics_toolbox.entity_resolution._cluster import build_clusters


def make_systems(*names: str) -> dict[str, pd.DataFrame]:
    """Minimal systems dict (DataFrames are empty — only structure matters for cluster tests)."""
    return {name: pd.DataFrame({name: []}) for name in names}


class TestTwoNodeCluster:
    def test_two_matching_records_form_one_cluster(self):
        pairs = [("sys_a_id", "a1", "sys_b_id", "b1", 0.95)]
        systems = make_systems("sys_a_id", "sys_b_id")
        result = build_clusters(pairs, systems)
        assert len(result) == 1
        assert result.iloc[0]["sys_a_id"] == "a1"
        assert result.iloc[0]["sys_b_id"] == "b1"

    def test_avg_and_min_similarity_for_single_edge(self):
        pairs = [("sys_a_id", "a1", "sys_b_id", "b1", 0.92)]
        systems = make_systems("sys_a_id", "sys_b_id")
        result = build_clusters(pairs, systems)
        assert result.iloc[0]["avg_similarity"] == pytest.approx(0.92)
        assert result.iloc[0]["min_similarity"] == pytest.approx(0.92)

    def test_output_columns_include_all_systems_and_similarity(self):
        pairs = [("sys_a_id", "a1", "sys_b_id", "b1", 0.90)]
        systems = make_systems("sys_a_id", "sys_b_id", "sys_c_id")
        result = build_clusters(pairs, systems)
        assert "sys_a_id" in result.columns
        assert "sys_b_id" in result.columns
        assert "sys_c_id" in result.columns
        assert "avg_similarity" in result.columns
        assert "min_similarity" in result.columns

    def test_system_absent_from_cluster_is_none(self):
        pairs = [("sys_a_id", "a1", "sys_b_id", "b1", 0.90)]
        systems = make_systems("sys_a_id", "sys_b_id", "sys_c_id")
        result = build_clusters(pairs, systems)
        assert pd.isna(result.iloc[0]["sys_c_id"])


class TestTransitiveCluster:
    def test_transitive_link_creates_one_cluster(self):
        """A matches B, B matches C → A-B-C all in one cluster even if A-C pair absent."""
        pairs = [
            ("sys_a_id", "a1", "sys_b_id", "b1", 0.95),
            ("sys_b_id", "b1", "sys_c_id", "c1", 0.88),
        ]
        systems = make_systems("sys_a_id", "sys_b_id", "sys_c_id")
        result = build_clusters(pairs, systems)
        assert len(result) == 1
        row = result.iloc[0]
        assert row["sys_a_id"] == "a1"
        assert row["sys_b_id"] == "b1"
        assert row["sys_c_id"] == "c1"

    def test_avg_and_min_for_multi_edge_cluster(self):
        pairs = [
            ("sys_a_id", "a1", "sys_b_id", "b1", 0.95),
            ("sys_b_id", "b1", "sys_c_id", "c1", 0.85),
        ]
        systems = make_systems("sys_a_id", "sys_b_id", "sys_c_id")
        result = build_clusters(pairs, systems)
        row = result.iloc[0]
        assert row["avg_similarity"] == pytest.approx((0.95 + 0.85) / 2)
        assert row["min_similarity"] == pytest.approx(0.85)
        assert row["min_similarity"] <= row["avg_similarity"]


class TestSeparateClusters:
    def test_disjoint_pairs_produce_separate_clusters(self):
        pairs = [
            ("sys_a_id", "a1", "sys_b_id", "b1", 0.95),
            ("sys_a_id", "a2", "sys_b_id", "b2", 0.90),
        ]
        systems = make_systems("sys_a_id", "sys_b_id")
        result = build_clusters(pairs, systems)
        assert len(result) == 2

    def test_two_clusters_have_correct_ids(self):
        pairs = [
            ("sys_a_id", "a1", "sys_b_id", "b1", 0.95),
            ("sys_a_id", "a2", "sys_b_id", "b2", 0.90),
        ]
        systems = make_systems("sys_a_id", "sys_b_id")
        result = build_clusters(pairs, systems)
        a_ids = set(result["sys_a_id"].tolist())
        b_ids = set(result["sys_b_id"].tolist())
        assert a_ids == {"a1", "a2"}
        assert b_ids == {"b1", "b2"}


class TestSameSystemCollision:
    """Two records from the SAME system landing in one cluster (transitive merge)."""

    def _collision_pairs(self):
        # a1→b1 (0.80) and a2→b1 (0.95): both sys_a records pulled into one cluster via b1
        return [
            ("sys_a", "a1", "sys_b", "b1", 0.80),
            ("sys_a", "a2", "sys_b", "b1", 0.95),
        ]

    def _systems(self):
        return {
            "sys_a": pd.DataFrame({"sys_a": ["a1", "a2"]}),
            "sys_b": pd.DataFrame({"sys_b": ["b1"]}),
        }

    def test_collision_winner_is_deterministic(self):
        """Which record survives the collision must not depend on set/hash ordering."""
        results = {
            build_clusters(self._collision_pairs(), self._systems()).iloc[0]["sys_a"]
            for _ in range(5)
        }
        assert len(results) == 1, f"non-deterministic collision winner: {results}"

    def test_collision_keeps_highest_scoring_record(self):
        """a2 has the stronger link (0.95 > 0.80) → a2 should win the single sys_a slot."""
        result = build_clusters(self._collision_pairs(), self._systems())
        assert result.iloc[0]["sys_a"] == "a2"

    def test_collision_emits_warning(self, caplog):
        import logging

        with caplog.at_level(logging.WARNING):
            build_clusters(self._collision_pairs(), self._systems())
        assert any("collision" in r.message.lower() or "multiple" in r.message.lower()
                   for r in caplog.records), "expected a warning about the dropped record"

    def test_collision_warning_contains_no_phi(self, caplog):
        """The collision warning must report counts only — no record IDs."""
        import logging

        with caplog.at_level(logging.WARNING):
            build_clusters(self._collision_pairs(), self._systems())
        joined = " ".join(r.message for r in caplog.records)
        assert "a1" not in joined and "a2" not in joined


class TestIsolatedRecord:
    def test_no_pairs_yields_empty_result(self):
        pairs = []
        systems = make_systems("sys_a_id", "sys_b_id")
        result = build_clusters(pairs, systems)
        assert len(result) == 0

    def test_isolated_record_not_in_output(self):
        """A record that matched no other record does not appear as a singleton cluster."""
        pairs = [("sys_a_id", "a1", "sys_b_id", "b1", 0.95)]
        systems = make_systems("sys_a_id", "sys_b_id")
        result = build_clusters(pairs, systems)
        # Only a1 and b1 appear — isolated records (none here) don't get singleton rows
        assert len(result) == 1
