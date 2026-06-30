"""Pairwise fuzzy scoring for entity resolution.

For each (system_a, system_b) combination and each candidate block, finds the
common scoreable columns, batch-scores them via RapidFuzz cdist, computes
a weighted similarity score per candidate pair, and returns pairs that exceed
the match threshold.

Scoring formula:
    score = Σ(sim_c × weight_c for c in common_cols) / Σ(weight_c for c in common_cols)

The block column within a block always has sim=1.0 (all records in the same block
share the same block-column value by construction — no need to re-score it).
"""

from __future__ import annotations

from itertools import combinations

import numpy as np
import pandas as pd
from rapidfuzz import fuzz, process

from analytics_toolbox.entity_resolution._config import EntityResolutionConfig

# A match pair: (system_a_name, id_a, system_b_name, id_b, score)
MatchPair = tuple[str, str, str, str, float]


def score_pairs(
    blocks: dict[tuple, dict[str, pd.DataFrame]],
    systems: dict[str, pd.DataFrame],
    config: EntityResolutionConfig,
) -> list[MatchPair]:
    """Score all candidate pairs across all blocks and system combinations.

    Args:
        blocks: Output of _block.build_blocks() — {block_key: {sys_name: subset_df}}.
        systems: Original systems dict (needed to know system id column names).
        config: EntityResolutionConfig with weights, threshold, and top_n_matches.

    Returns:
        List of (sys_a_name, id_a, sys_b_name, id_b, score) tuples for pairs
        that exceed ``config.match_threshold``.

    Note:
        ``top_n_matches`` is applied *directionally*: for each unordered system
        pair (A, B), each A-record keeps its top-N B-matches, but B-records are
        not independently capped against A. This is intentional — clustering
        operates on an undirected graph, so a B-record that is the best match for
        several A-records still gets linked. The cap bounds fan-out per query
        record, not per node.

    Raises:
        RuntimeError: If any block's pairwise size exceeds ``config.max_block_pairs``.
    """
    pairs: list[MatchPair] = []
    system_names = list(systems.keys())

    for block_key, block_systems in blocks.items():
        for sys_a_name, sys_b_name in combinations(system_names, 2):
            df_a = block_systems.get(sys_a_name)
            df_b = block_systems.get(sys_b_name)
            if df_a is None or df_b is None or df_a.empty or df_b.empty:
                continue

            n_pairs = len(df_a) * len(df_b)
            if n_pairs > config.max_block_pairs:
                # PHI-safe: report sizes only, never the block key value (a DOB or
                # Last_Name+Postal_Code). See SPEC §"errors (only counts and column names)".
                raise RuntimeError(
                    f"entity_resolution: a candidate block would produce "
                    f"{n_pairs} candidate pairs ({len(df_a)} × {len(df_b)}), "
                    f"exceeding max_block_pairs={config.max_block_pairs}. "
                    "Reduce the block size, increase max_block_pairs, or add "
                    "a secondary blocking column."
                )

            block_pairs = _score_block_pair(
                df_a, sys_a_name, df_b, sys_b_name, block_key, config,
                is_primary_block=(len(block_key) == 1),
            )
            pairs.extend(block_pairs)

    return pairs


def _score_block_pair(
    df_a: pd.DataFrame,
    sys_a_name: str,
    df_b: pd.DataFrame,
    sys_b_name: str,
    block_key: tuple,
    config: EntityResolutionConfig,
    *,
    is_primary_block: bool = True,
) -> list[MatchPair]:
    """Score all pairs from two system subsets within one block.

    ``is_primary_block`` indicates whether block_key came from the primary block
    column. In secondary blocks, the block column (DOB) is null for all records —
    it provides no discrimination and must NOT be credited as sim=1.0.
    """
    # Determine common scoreable columns (intersection of both DFs and field_weights)
    common_cols = list(
        (set(df_a.columns) & set(df_b.columns)) & set(config.field_weights.keys())
    )
    if not common_cols:
        return []

    block_col = config.block_column
    # In a secondary block, the block_col is null for all records — exclude from scoring
    credit_block_col = is_primary_block and block_col in common_cols
    fuzzy_cols = [c for c in common_cols if c != block_col]

    # Weight denominator: include block_col only if we're crediting it
    scored_cols = ([block_col] if credit_block_col else []) + fuzzy_cols
    total_weight_common = sum(config.field_weights[c] for c in scored_cols)
    if total_weight_common == 0:
        return []

    # Build the (len_a × len_b) weighted-score matrix in one vectorized pass.
    # cdist already batch-scores each column; we accumulate the weighted sum with
    # numpy rather than walking every cell in Python.
    weighted = np.zeros((len(df_a), len(df_b)), dtype=float)
    if credit_block_col:
        # All records in a primary block share the block-column value → sim=1.0.
        weighted += config.field_weights[block_col]
    for col in fuzzy_cols:
        queries = _to_str_list(df_a[col])
        choices = _to_str_list(df_b[col])
        matrix = process.cdist(queries, choices, scorer=fuzz.WRatio, score_cutoff=0)
        weighted += (np.asarray(matrix, dtype=float) / 100.0) * config.field_weights[col]
    score_matrix = weighted / total_weight_common

    ids_a = df_a[sys_a_name].astype(str).tolist()  # dict key is also the ID column name
    ids_b = df_b[sys_b_name].astype(str).tolist()
    threshold = config.match_threshold
    top_n = config.top_n_matches

    pairs: list[MatchPair] = []
    for i, row_scores in enumerate(score_matrix):
        above = np.nonzero(row_scores >= threshold)[0]
        if above.size == 0:
            continue
        # Top-N b-candidates by score; stable sort preserves original b order on ties.
        ranked = above[np.argsort(-row_scores[above], kind="stable")]
        for j in ranked[:top_n]:
            pairs.append((sys_a_name, ids_a[i], sys_b_name, ids_b[j], float(row_scores[j])))

    return pairs


def _to_str_list(series: pd.Series) -> list[str]:
    """Convert a Series to a list of strings, replacing nulls with empty string."""
    return [str(v) if v is not None and not pd.isna(v) else "" for v in series]
