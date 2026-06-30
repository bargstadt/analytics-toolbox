"""Top-level config loader for analytics-toolbox.

Reads a single YAML file that controls all modules. Each module extracts
its own slice; the storage section is shared.

Example config file::

    storage:
      connection: ~/.local/share/analytics_toolbox/analytics_toolbox.duckdb
      data_dir: ~/.local/share/analytics_toolbox/

    geospatial:
      nad:
        states: [IA]
        force_refresh: false
      tiger:
        vintage: 2024
        force_refresh: false
      matching:
        confidence_threshold: 90

      # Override the NAD download URL (optional):
      # nad:
      #   url: https://datahub.transportation.gov/api/views/fc2s-wawr/files/<blob-id>?filename=TXT.zip

      # MotherDuck (config-only change):
      # storage:
      #   connection: md:analytics_toolbox
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ValidationError

from analytics_toolbox._storage import StorageConfig
from analytics_toolbox.acs._config import AcsConfig
from analytics_toolbox.entity_resolution._config import (
    _DEFAULT_FIELD_WEIGHTS,
    EntityResolutionConfig,
)
from analytics_toolbox.geospatial._config import (
    _DEFAULT_NAD_URL,
    GeospatialConfig,
    MatcherConfig,
    NadConfig,
    TigerConfig,
    _require,
)


class AnalyticsToolboxConfig(BaseModel):
    """Root configuration for the full analytics-toolbox pipeline.

    ``geospatial`` is None when the YAML has no ``geospatial:`` section —
    callers that need it (geocode_address_table, ingest_nad, ingest_tiger)
    will raise ValueError at the point of use rather than at config load time.

    ``entity_resolution`` is None when the YAML has no ``entity_resolution:``
    section — callers that need it (resolve()) will raise ValueError at the
    point of use.

    ``acs`` is None when the YAML has no ``acs:`` section — callers that need it
    (ingest_acs()) will raise ValueError at the point of use.
    """

    storage: StorageConfig
    geospatial: GeospatialConfig | None = None
    entity_resolution: EntityResolutionConfig | None = None
    acs: AcsConfig | None = None


def load_config(path: str | Path) -> AnalyticsToolboxConfig:
    """Load analytics-toolbox config from a YAML file.

    Args:
        path: Path to the config YAML file.

    Returns:
        ``AnalyticsToolboxConfig`` with all paths expanded and defaults applied.

    Raises:
        FileNotFoundError: If the config file does not exist.
        ValueError: If a required key is missing.
    """
    raw = yaml.safe_load(Path(path).read_text()) or {}

    _require(raw, "storage", "storage")
    storage_raw = raw["storage"] or {}
    _require(storage_raw, "connection", "storage.connection")
    _require(storage_raw, "data_dir", "storage.data_dir")

    # StorageConfig validators expand ~ and pass MotherDuck md: strings through.
    storage = StorageConfig(
        data_dir=storage_raw["data_dir"],
        connection=storage_raw["connection"],
    )

    geospatial: GeospatialConfig | None = None
    if "geospatial" in raw:
        geo_raw = raw["geospatial"] or {}
        nad_raw = geo_raw.get("nad") or {}
        _require(nad_raw, "states", "geospatial.nad.states")

        states = nad_raw["states"]
        if not isinstance(states, list):
            states = [str(states)]

        tiger_raw = geo_raw.get("tiger") or {}
        matching_raw = geo_raw.get("matching") or {}

        geospatial = GeospatialConfig(
            nad=NadConfig(
                states=states,
                force_refresh=nad_raw.get("force_refresh", False),
                url=nad_raw.get("url", _DEFAULT_NAD_URL),
            ),
            tiger=TigerConfig(
                vintage=tiger_raw.get("vintage", 2024),
                force_refresh=tiger_raw.get("force_refresh", False),
            ),
            matching=MatcherConfig(
                confidence_threshold=matching_raw.get("confidence_threshold", 90),
            ),
            storage=storage,
        )

    entity_resolution: EntityResolutionConfig | None = None
    if "entity_resolution" in raw:
        er_raw = raw["entity_resolution"] or {}
        _require(er_raw, "match_threshold", "entity_resolution.match_threshold")

        weights_raw = er_raw.get("field_weights")
        field_weights = dict(weights_raw) if weights_raw else dict(_DEFAULT_FIELD_WEIGHTS)

        secondary = er_raw.get("secondary_block_columns", ["Last_Name", "Postal_Code"])
        if not isinstance(secondary, list):
            secondary = [str(secondary)]

        entity_resolution = EntityResolutionConfig(
            match_threshold=er_raw["match_threshold"],
            block_column=er_raw.get("block_column", "DOB"),
            secondary_block_columns=secondary,
            top_n_matches=er_raw.get("top_n_matches", 1),
            max_block_pairs=er_raw.get("max_block_pairs", 500_000),
            address_col=er_raw.get("address_col", "Address"),
            field_weights=field_weights,
        )

    acs: AcsConfig | None = None
    if "acs" in raw:
        # AcsConfig's own validators carry the field-located error messages;
        # translate to ValueError to match the loader's contract.
        try:
            acs = AcsConfig.model_validate(raw["acs"] or {})
        except ValidationError as exc:
            raise ValueError(f"Invalid acs config: {exc}") from exc

    return AnalyticsToolboxConfig(
        storage=storage,
        geospatial=geospatial,
        entity_resolution=entity_resolution,
        acs=acs,
    )
