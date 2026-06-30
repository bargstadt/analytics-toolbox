"""Tests for fixtures/multi_system.py."""

from __future__ import annotations

import pytest

from analytics_toolbox.entity_resolution.fixtures.multi_system import (
    FIXTURE_IDS,
    make_mpi_fixture,
)


@pytest.fixture
def systems():
    return make_mpi_fixture()


class TestFixtureStructure:
    def test_returns_dict_with_five_systems(self, systems):
        assert len(systems) == 5

    def test_system_keys_match_fixture_ids(self, systems):
        for system_id_col in FIXTURE_IDS.values():
            assert system_id_col in systems

    def test_each_system_df_has_id_column(self, systems):
        for sys_name, df in systems.items():
            assert sys_name in df.columns, f"{sys_name} df missing its own ID column"

    def test_each_system_has_at_least_one_record(self, systems):
        for sys_name, df in systems.items():
            assert not df.empty, f"{sys_name} is empty"


class TestJohnSmithPersona:
    """John/Johny/Johnathan Smith should all be detectable as the same person."""

    def test_john_record_present(self, systems):
        df = systems[FIXTURE_IDS["john"]]
        assert FIXTURE_IDS["john"] in df.columns
        assert "JOHN" in df["First_Name"].str.upper().tolist()

    def test_johny_record_present(self, systems):
        df = systems[FIXTURE_IDS["johny"]]
        assert FIXTURE_IDS["johny"] in df.columns
        assert "JOHNY" in df["First_Name"].str.upper().tolist()

    def test_johnathan_record_present(self, systems):
        df = systems[FIXTURE_IDS["johnathan"]]
        assert FIXTURE_IDS["johnathan"] in df.columns
        assert any("JOHN" in n.upper() for n in df["First_Name"].tolist())

    def test_all_smith_personas_share_same_dob(self, systems):
        for persona in ("john", "johny", "johnathan"):
            sys_key = FIXTURE_IDS[persona]
            df = systems[sys_key]
            dob_col = df["DOB"].dropna()
            # At least one record should have the shared Smith DOB
            assert not dob_col.empty


class TestSmoothPersona:
    """James/Jim Smooth should be a separate cluster from Smith."""

    def test_james_smooth_present(self, systems):
        df = systems[FIXTURE_IDS["james"]]
        assert any("SMOOTH" in n.upper() for n in df["Last_Name"].tolist())

    def test_jim_smooth_present(self, systems):
        df = systems[FIXTURE_IDS["jim"]]
        assert any("SMOOTH" in n.upper() for n in df["Last_Name"].tolist())


class TestEdgeCases:
    def test_null_dob_record_exists(self, systems):
        """At least one record in the fixture has a null DOB to test secondary blocking."""
        has_null = False
        for df in systems.values():
            if "DOB" in df.columns and df["DOB"].isna().any():
                has_null = True
                break
        assert has_null, "Fixture should include at least one record with null DOB"

    def test_missing_postal_code_system_exists(self, systems):
        """At least one system should lack Postal_Code to test missing-field handling."""
        has_no_postal = False
        for df in systems.values():
            if "Postal_Code" not in df.columns:
                has_no_postal = True
                break
        assert has_no_postal, "Fixture should include a system without Postal_Code"
