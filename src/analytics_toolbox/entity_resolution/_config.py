"""Typed config model for entity_resolution pipeline configuration.

Consumed by ``analytics_toolbox._config.load_config()``, which parses the
unified YAML and populates it. Internal entity_resolution code imports
``EntityResolutionConfig`` directly from here.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

_DEFAULT_FIELD_WEIGHTS: dict[str, float] = {
    "DOB": 1.0,
    "Last_Name": 0.35,
    "First_Name": 0.2,
    "Middle_Name": 0.05,
    "SSN": 0.2,
    "Phone": 0.05,
    "Address": 0.05,
    "City": 0.03,
    "County": 0.02,
    "Postal_Code": 0.05,
}


class EntityResolutionConfig(BaseModel):
    """Configuration for the entity resolution (MPI) pipeline.

    ``match_threshold`` is required and has no default — the right value is
    too domain-specific to guess. ``load_config()`` raises ``ValueError`` if
    it is absent when the ``entity_resolution:`` section is present in YAML.
    """

    match_threshold: float
    block_column: str = "DOB"
    secondary_block_columns: list[str] = Field(default_factory=lambda: ["Last_Name", "Postal_Code"])
    top_n_matches: int = 1
    max_block_pairs: int = 500_000
    address_col: str = "Address"
    field_weights: dict[str, float] = Field(default_factory=lambda: dict(_DEFAULT_FIELD_WEIGHTS))
