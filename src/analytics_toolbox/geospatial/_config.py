"""Typed config models for the geospatial pipeline configuration.

These are consumed by ``analytics_toolbox._config.load_config()``, which
parses the unified YAML and populates them. Internal geospatial code imports
``GeospatialConfig`` directly from here; callers should load config via the
top-level ``analytics_toolbox._config.load_config()``.

These are pydantic ``BaseModel`` subclasses: field validators handle ``~``
expansion, MotherDuck passthrough, and range checks at construction time, so an
invalid value fails fast with a clear message rather than deep inside a module.
"""

from __future__ import annotations

from pydantic import BaseModel, field_validator

# StorageConfig is shared across modules and now lives at the package root.
# Re-exported here for backward compatibility with existing imports.
from analytics_toolbox._storage import StorageConfig

__all__ = [
    "StorageConfig",
    "NadConfig",
    "TigerConfig",
    "MatcherConfig",
    "GeospatialConfig",
]

# NAD national download URL. The NAD is distributed by the US DOT as a single
# national ZIP (~8.45 GB) via datahub.transportation.gov. As of NAD Release 22
# there is no per-state filtered download endpoint; the full national file must
# be downloaded and filtered locally. The blob asset ID is stable across NAD
# releases on this dataset (fc2s-wawr), but if it changes, update this constant
# or override via geospatial.nad.url in the config file.
_DEFAULT_NAD_URL = (
    "https://datahub.transportation.gov/api/views/fc2s-wawr"
    "/files/3a27e4dd-7fe4-42c8-bd58-1e81a8de6ab9?filename=TXT.zip"
)


class NadConfig(BaseModel):
    """Configuration for the NAD download and ingest step."""

    states: list[str]
    force_refresh: bool = False
    url: str = _DEFAULT_NAD_URL


class TigerConfig(BaseModel):
    """Configuration for the TIGER/Line shapefile download and ingest step."""

    vintage: int = 2024
    force_refresh: bool = False


class MatcherConfig(BaseModel):
    """Configuration for the address matching step."""

    confidence_threshold: int = 90

    @field_validator("confidence_threshold")
    @classmethod
    def _check_threshold(cls, v: int) -> int:
        if not 0 <= v <= 100:
            raise ValueError(f"confidence_threshold must be between 0 and 100, got {v}")
        return v


class GeospatialConfig(BaseModel):
    """Configuration for the geospatial pipeline."""

    nad: NadConfig
    tiger: TigerConfig
    matching: MatcherConfig
    storage: StorageConfig


def _require(mapping: dict, key: str, dotted_path: str) -> None:
    if key not in mapping:
        raise ValueError(f"Missing required config key: {dotted_path}")
