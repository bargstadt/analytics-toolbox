"""Blocking strategy for entity resolution.

Groups records across systems by a shared key so that pairwise scoring only
compares candidates that share the same block value, keeping the comparison
space tractable.

Primary block: ``config.block_column`` (default: DOB).
Null fallback: when the primary block key is null, fall back to a composite
    of ``config.secondary_block_columns`` (default: Last_Name + Postal_Code).
Drop: records where ALL block keys are null are excluded from matching.
    Their count is logged; their values are never logged.
"""

from __future__ import annotations

import logging
from collections import defaultdict

import pandas as pd

from analytics_toolbox.entity_resolution._config import EntityResolutionConfig

log = logging.getLogger(__name__)

# Return type: block_key → {system_name → subset DataFrame}
Blocks = dict[tuple, dict[str, pd.DataFrame]]


def build_blocks(
    systems: dict[str, pd.DataFrame],
    config: EntityResolutionConfig,
) -> Blocks:
    """Partition records across all systems into candidate blocks.

    Args:
        systems: Mapping of {system_id_col_name: DataFrame} (already preprocessed).
        config: EntityResolutionConfig controlling block column and fallback.

    Returns:
        Dict keyed by block_key tuple. Each value is a dict mapping
        system name → subset DataFrame containing only the records in that block.
        Blocks that exist in only one system are included — pairwise scoring
        will produce no pairs for them, which is correct.
    """
    blocks: dict[tuple, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    total_dropped = 0

    for system_name, df in systems.items():
        for row_idx, row in df.iterrows():
            key = _block_key(row, config)
            if key is None:
                total_dropped += 1
                continue
            blocks[key][system_name].append(row_idx)

    if total_dropped > 0:
        log.warning(
            "entity_resolution: %d record(s) excluded — all block keys were null",
            total_dropped,
        )

    # Convert index lists back to DataFrames
    result: Blocks = {}
    for block_key, system_indices in blocks.items():
        result[block_key] = {
            sys_name: systems[sys_name].loc[idx_list].reset_index(drop=True)
            for sys_name, idx_list in system_indices.items()
        }

    return result


def _block_key(row: pd.Series, config: EntityResolutionConfig) -> tuple | None:
    """Compute the block key for a single record.

    Returns None if all block keys are null (record should be dropped).
    Returns a 1-tuple for primary blocking, or an N-tuple for secondary blocking.
    """
    primary_val = row.get(config.block_column)
    if _is_present(primary_val):
        return (str(primary_val),)

    # Primary key is null — try secondary block columns
    secondary_vals = []
    for col in config.secondary_block_columns:
        val = row.get(col)
        secondary_vals.append(str(val) if _is_present(val) else None)

    # If all secondary keys are also null, drop the record
    if all(v is None for v in secondary_vals):
        return None

    # Substitute empty string for null secondary keys so the tuple is hashable
    return tuple(v if v is not None else "" for v in secondary_vals)


def _is_present(val) -> bool:
    """Return True if val is a non-null, non-empty (non-whitespace) value."""
    if val is None:
        return False
    if isinstance(val, str) and val.strip() == "":
        return False
    try:
        return not pd.isna(val)
    except (TypeError, ValueError):
        return True
