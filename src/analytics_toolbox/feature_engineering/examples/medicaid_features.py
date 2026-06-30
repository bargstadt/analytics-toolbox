"""Example feature functions built on compute_features using the Medicaid fixture.

These are thin wrappers that demonstrate the engine's patterns — grain expansion
via group_cols and the CASE-expr workaround for conditional aggregates. No
domain logic belongs here beyond what is passed through to the engine.
"""

from __future__ import annotations

import duckdb
import pandas as pd

from analytics_toolbox.feature_engineering._types import Agg
from analytics_toolbox.feature_engineering.engine import compute_features


def rx_spend_features(
    spine: pd.DataFrame,
    con: duckdb.DuckDBPyConnection,
    windows: tuple[int, ...] = (30, 90, 365),
) -> pd.DataFrame:
    """Pharmacy spend and utilisation features from rx_claims.

    Namespace: rx. Base table: rx_claims (must exist in con).
    Output grain: (member_id, as_of_date) — one row per spine row.
    """
    return compute_features(
        spine,
        "rx_claims",
        entity_keys=["member_id"],
        as_of_col="as_of_date",
        base_date_col="claim_date",
        namespace="rx",
        aggregations=[
            Agg("paid_sum", "SUM(paid_amount)"),
            Agg("claims_cnt", "COUNT(*)"),
            Agg("ndc_ndist", "COUNT(DISTINCT ndc_code)"),
            Agg("dsupply_sum", "SUM(days_supply)"),
        ],
        windows=list(windows),
        con=con,
    )


def rx_spend_features_by_drug_class(
    spine: pd.DataFrame,
    con: duckdb.DuckDBPyConnection,
    windows: tuple[int, ...] = (30, 90, 365),
) -> pd.DataFrame:
    """Pharmacy spend by drug class — demonstrates grain expansion via group_cols.

    Spine must carry a drug_class column. Output grain: (member_id, drug_class, as_of_date).
    One row per (member × drug_class × as_of_date) combination in the spine.
    """
    return compute_features(
        spine,
        "rx_claims",
        entity_keys=["member_id"],
        as_of_col="as_of_date",
        base_date_col="claim_date",
        namespace="rx",
        aggregations=[
            Agg("paid_sum", "SUM(paid_amount)"),
            Agg("claims_cnt", "COUNT(*)"),
            Agg("ndc_ndist", "COUNT(DISTINCT ndc_code)"),
            Agg("dsupply_sum", "SUM(days_supply)"),
        ],
        windows=list(windows),
        group_cols=["drug_class"],
        con=con,
    )


def med_utilization_features(
    spine: pd.DataFrame,
    con: duckdb.DuckDBPyConnection,
    windows: tuple[int, ...] = (30, 90, 365),
) -> pd.DataFrame:
    """Medical utilisation features from med_claims.

    Namespace: med. Base table: med_claims (must exist in con).
    Output grain: (member_id, as_of_date) — one row per spine row.

    ED and inpatient counts use SUM(CASE …) rather than COUNT(*) FILTER so
    the engine's single window-FILTER wraps cleanly without nesting.
    """
    return compute_features(
        spine,
        "med_claims",
        entity_keys=["member_id"],
        as_of_col="as_of_date",
        base_date_col="claim_date",
        namespace="med",
        aggregations=[
            Agg("claims_cnt", "COUNT(*)"),
            Agg("paid_sum", "SUM(paid_amount)"),
            Agg("ed_visits_cnt", "SUM(CASE WHEN place_of_service = 'ed' THEN 1 ELSE 0 END)"),
            Agg("inpatient_cnt", "SUM(CASE WHEN place_of_service = 'inpatient' THEN 1 ELSE 0 END)"),
            Agg("provider_ndist", "COUNT(DISTINCT provider_type)"),
        ],
        windows=list(windows),
        con=con,
    )
