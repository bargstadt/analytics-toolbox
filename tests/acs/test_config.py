"""Tests for AcsConfig validation and the unified loader's acs slice."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from pydantic import ValidationError

from analytics_toolbox._config import load_config
from analytics_toolbox.acs._config import AcsConfig


def _good_dict() -> dict:
    return {
        "states": ["IA"],
        "reports": [
            {
                "name": "poverty",
                "variables": [
                    {"code": "B01001_001E", "geographies": ["block_group", "tract"]},
                    {"code": "S1701_C03_001E", "geographies": ["tract", "county"]},
                ],
            }
        ],
    }


class TestAcsConfig:
    def test_valid(self):
        cfg = AcsConfig.model_validate(_good_dict())
        assert cfg.states == ["IA"]
        assert len(cfg.reports) == 1
        assert cfg.reports[0].variables[0].code == "B01001_001E"

    def test_states_uppercased(self):
        cfg = AcsConfig.model_validate({**_good_dict(), "states": ["ia", "Mo"]})
        assert cfg.states == ["IA", "MO"]

    def test_variable_geographies_dedup_preserves_order(self):
        d = _good_dict()
        # Duplicate the first variable in a second report — should dedup.
        d["reports"].append(
            {"name": "dup", "variables": [{"code": "B01001_001E", "geographies": ["tract"]}]}
        )
        cfg = AcsConfig.model_validate(d)
        combos = cfg.variable_geographies()
        assert combos == [
            ("B01001_001E", "block_group"),
            ("B01001_001E", "tract"),
            ("S1701_C03_001E", "tract"),
            ("S1701_C03_001E", "county"),
        ]

    def test_unknown_state_rejected(self):
        with pytest.raises(ValidationError, match="unrecognised state"):
            AcsConfig.model_validate({**_good_dict(), "states": ["ZZ"]})

    def test_empty_states_rejected(self):
        with pytest.raises(ValidationError, match="at least one state"):
            AcsConfig.model_validate({**_good_dict(), "states": []})

    def test_invalid_variable_code_rejected(self):
        d = _good_dict()
        d["reports"][0]["variables"][0]["code"] = "bad code!"
        with pytest.raises(ValidationError, match="invalid"):
            AcsConfig.model_validate(d)

    def test_unsupported_geography_rejected(self):
        d = _good_dict()
        d["reports"][0]["variables"][0]["geographies"] = ["zip"]
        with pytest.raises(ValidationError, match="unsupported geographies"):
            AcsConfig.model_validate(d)

    def test_empty_geographies_rejected(self):
        d = _good_dict()
        d["reports"][0]["variables"][0]["geographies"] = []
        with pytest.raises(ValidationError, match="at least one geography"):
            AcsConfig.model_validate(d)

    def test_empty_reports_rejected(self):
        with pytest.raises(ValidationError, match="at least one report"):
            AcsConfig.model_validate({**_good_dict(), "reports": []})

    def test_report_without_variables_rejected(self):
        d = {"states": ["IA"], "reports": [{"name": "empty", "variables": []}]}
        with pytest.raises(ValidationError, match="at least one variable"):
            AcsConfig.model_validate(d)


class TestLoaderAcsSlice:
    def _write(self, tmp_path: Path, body: str) -> Path:
        p = tmp_path / "config.yaml"
        storage = (
            "storage:\n" f"  connection: {tmp_path / 'atb.duckdb'}\n" f"  data_dir: {tmp_path}\n"
        )
        p.write_text(storage + body)
        return p

    def test_acs_section_loads(self, tmp_path: Path):
        p = self._write(
            tmp_path,
            textwrap.dedent("""\
                acs:
                  states: [IA]
                  reports:
                    - name: poverty
                      variables:
                        - code: B01001_001E
                          geographies: [block_group, tract]
                """),
        )
        cfg = load_config(p)
        assert cfg.acs is not None
        assert cfg.acs.states == ["IA"]

    def test_no_acs_section_is_none(self, tmp_path: Path):
        p = self._write(tmp_path, "")
        assert load_config(p).acs is None

    def test_invalid_acs_raises_valueerror(self, tmp_path: Path):
        # Loader translates pydantic ValidationError into ValueError.
        p = self._write(
            tmp_path,
            textwrap.dedent("""\
                acs:
                  states: [ZZ]
                  reports:
                    - name: poverty
                      variables:
                        - code: B01001_001E
                          geographies: [tract]
                """),
        )
        with pytest.raises(ValueError, match="Invalid acs config"):
            load_config(p)
