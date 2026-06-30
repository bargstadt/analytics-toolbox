"""Typed config models for the ACS ingest pipeline.

Consumed by ``analytics_toolbox._config.load_config()``, which parses the
unified YAML and populates the ``acs:`` slice. Internal acs code imports
``AcsConfig`` directly from here.

Validation lives in the pydantic models: a malformed variable code, an unknown
geography, or an unrecognised state fails fast at load with a field-located
message rather than deep inside a Census API call.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, field_validator

from analytics_toolbox.acs._geography import STATE_FIPS

SUPPORTED_GEOGRAPHIES: frozenset[str] = frozenset({"block_group", "tract", "county"})

# ACS variable codes are letters, digits, and underscores only (e.g. B01001_001E).
# Enforcing this keeps codes safe to interpolate into table/column identifiers and
# turns a malformed code into a clear config error instead of a cryptic failure.
_VARIABLE_CODE_RE = re.compile(r"^[A-Za-z0-9_]+$")


class VariableConfig(BaseModel):
    """A single ACS variable with the geography levels to pull it at."""

    code: str
    geographies: list[str]

    @field_validator("code")
    @classmethod
    def _check_code(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("variable 'code' must not be empty")
        if not _VARIABLE_CODE_RE.match(v):
            raise ValueError(
                f"variable code '{v}' is invalid; expected only letters, digits, "
                "and underscores (e.g. B01001_001E)"
            )
        return v

    @field_validator("geographies")
    @classmethod
    def _check_geographies(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("variable must specify at least one geography")
        unknown = set(v) - SUPPORTED_GEOGRAPHIES
        if unknown:
            raise ValueError(
                f"unsupported geographies {sorted(unknown)}; "
                f"supported: {sorted(SUPPORTED_GEOGRAPHIES)}"
            )
        return v


class ReportConfig(BaseModel):
    """A named report grouping one or more variables."""

    name: str
    variables: list[VariableConfig]

    @field_validator("name")
    @classmethod
    def _check_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("report 'name' must not be empty")
        return v

    @field_validator("variables")
    @classmethod
    def _check_variables(cls, v: list[VariableConfig]) -> list[VariableConfig]:
        if not v:
            raise ValueError("report must define at least one variable")
        return v


class AcsConfig(BaseModel):
    """Top-level ACS ingest configuration (the ``acs:`` YAML slice)."""

    states: list[str]
    reports: list[ReportConfig]

    @field_validator("states")
    @classmethod
    def _check_states(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("acs.states must define at least one state")
        normalized = [s.upper() for s in v]
        unknown = [s for s in normalized if s not in STATE_FIPS]
        if unknown:
            raise ValueError(f"unrecognised state abbreviations: {unknown}")
        return normalized

    @field_validator("reports")
    @classmethod
    def _check_reports(cls, v: list[ReportConfig]) -> list[ReportConfig]:
        if not v:
            raise ValueError("acs.reports must define at least one report")
        return v

    def variable_geographies(self) -> list[tuple[str, str]]:
        """Return unique ``(variable_code, geography)`` combos in first-seen order.

        Deduplicates across reports so the same variable+geography is fetched once.
        """
        combos: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for report in self.reports:
            for var in report.variables:
                for geo in var.geographies:
                    combo = (var.code, geo)
                    if combo not in seen:
                        seen.add(combo)
                        combos.append(combo)
        return combos
