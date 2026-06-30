"""Public resolve() function — orchestrates the full MPI pipeline."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd

from analytics_toolbox.entity_resolution._block import build_blocks
from analytics_toolbox.entity_resolution._cluster import build_clusters
from analytics_toolbox.entity_resolution._match import score_pairs
from analytics_toolbox.entity_resolution._preprocess import normalize_fields

if TYPE_CHECKING:
    from analytics_toolbox._config import AnalyticsToolboxConfig


def resolve(
    systems: dict[str, pd.DataFrame],
    *,
    config: AnalyticsToolboxConfig,
) -> pd.DataFrame:
    """Link records across source systems into entity clusters.

    Args:
        systems: Mapping of {system_id_col_name: DataFrame}. Each DataFrame
            must contain the column named by its key as the record identifier.
            Must have ≥ 2 entries.
        config: Root analytics-toolbox config. Must have
            ``config.entity_resolution`` populated with ``match_threshold`` set.

    Returns:
        DataFrame with one row per entity cluster: one column per system_id
        key (None if not in cluster) plus ``avg_similarity`` and
        ``min_similarity``. Sorted by descending avg_similarity. Records with
        no match above the threshold do NOT appear in the output.

    Raises:
        ValueError: If fewer than 2 systems, config.entity_resolution is None,
            or any DataFrame is empty.
        RuntimeError: If a block exceeds config.entity_resolution.max_block_pairs.
    """
    _validate(systems, config)
    er_cfg = config.entity_resolution

    # Preprocess each system DataFrame
    preprocessed = {
        sys_name: normalize_fields(df, er_cfg)
        for sys_name, df in systems.items()
    }

    # Block candidates
    blocks = build_blocks(preprocessed, er_cfg)

    # Score pairwise candidates
    pairs = score_pairs(blocks, preprocessed, er_cfg)

    # Cluster via connected components
    return build_clusters(pairs, systems)


def _validate(systems: dict[str, pd.DataFrame], config: AnalyticsToolboxConfig) -> None:
    if len(systems) < 2:
        raise ValueError(
            f"resolve() requires at least 2 systems; got {len(systems)}. "
            "Pass a dict with 2 or more {system_id_col: DataFrame} entries."
        )

    if config.entity_resolution is None:
        raise ValueError(
            "config.entity_resolution is None. Add an 'entity_resolution:' section "
            "to your config.yaml with at least 'match_threshold' set."
        )

    for sys_name, df in systems.items():
        if df.empty:
            raise ValueError(
                f"System '{sys_name}' has an empty DataFrame. "
                "All systems must have at least one record."
            )
