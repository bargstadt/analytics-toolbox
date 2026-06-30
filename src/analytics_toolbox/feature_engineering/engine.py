from __future__ import annotations

import warnings

import duckdb
import pandas as pd

from analytics_toolbox.feature_engineering._sql import build_feature_sql
from analytics_toolbox.feature_engineering._types import Agg, Guardrails
from analytics_toolbox.feature_engineering._validate import (
    validate_agg_name_no_collision,
    validate_columns_present,
    validate_namespace,
    validate_no_nulls_in_grain,
    validate_spine_uniqueness,
    validate_windows,
)


def compute_features(
    spine,
    base,
    *,
    entity_keys: list[str],
    as_of_col: str,
    base_date_col: str,
    namespace: str,
    aggregations: list[Agg],
    windows: list[int | str],
    group_cols: list[str] = (),
    con: duckdb.DuckDBPyConnection | None = None,
    guardrails: Guardrails | None = None,
) -> pd.DataFrame:
    """Compute windowed aggregate features for every row in spine.

    Returns a DataFrame at exactly the spine grain: all spine columns plus one
    feature column per (aggregation × window). Rows with no matching base events
    have null feature values — no imputation.
    """
    if guardrails is None:
        guardrails = Guardrails()
    own_con = con is None
    if own_con:
        con = duckdb.connect()

    try:
        spine_tbl = _register_input(con, spine, "_fe_spine")
        base_tbl = _register_input(con, base, "_fe_base")

        join_keys = list(entity_keys) + list(group_cols)
        grain_cols = join_keys + [as_of_col]
        agg_names = [a.name for a in aggregations]

        validate_namespace(namespace)
        validate_windows(list(windows))
        validate_agg_name_no_collision(agg_names, grain_cols)
        validate_columns_present(spine_tbl, list(entity_keys) + [as_of_col], "spine", con)
        validate_columns_present(base_tbl, join_keys + [base_date_col], "base", con)
        validate_no_nulls_in_grain(spine_tbl, grain_cols, con)
        if guardrails.require_unique_spine:
            validate_spine_uniqueness(spine_tbl, grain_cols, con)

        int_windows = [w for w in windows if isinstance(w, int)]
        max_window = max(int_windows) if int_windows else None

        if guardrails.max_fanout_rows is not None:
            fanout = _estimate_fanout(
                spine_tbl, base_tbl, join_keys, as_of_col, base_date_col, max_window, con
            )
            if fanout > guardrails.max_fanout_rows:
                msg = (
                    f"Estimated pre-aggregation fan-out ({fanout:,} rows) exceeds guardrail "
                    f"({guardrails.max_fanout_rows:,}). "
                    "Batch the spine by as-of window or increase Guardrails.max_fanout_rows."
                )
                if guardrails.on_fanout_exceed == "raise":
                    raise RuntimeError(msg)
                warnings.warn(msg, RuntimeWarning, stacklevel=2)

        sql = build_feature_sql(
            spine_tbl=spine_tbl,
            base_tbl=base_tbl,
            entity_keys=entity_keys,
            as_of_col=as_of_col,
            base_date_col=base_date_col,
            namespace=namespace,
            aggregations=aggregations,
            windows=list(windows),
            group_cols=list(group_cols),
            max_window=max_window,
        )

        try:
            result = con.execute(sql).df()
        except duckdb.Error as exc:
            agg_detail = ", ".join(f"{a.name}={a.expr!r}" for a in aggregations)
            raise ValueError(
                f"DuckDB error in namespace {namespace!r} — check each Agg.expr. "
                f"Aggregations: [{agg_detail}]. Original error: {exc}"
            ) from exc

        return result

    finally:
        if own_con:
            con.close()


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _register_input(con: duckdb.DuckDBPyConnection, inp, tmp_name: str) -> str:
    """Register input (str, DataFrame, or DuckDB relation) and return table name."""
    if isinstance(inp, str):
        return inp
    if isinstance(inp, pd.DataFrame):
        con.register(tmp_name, inp)
        return tmp_name
    # Try to treat as DuckDB relation
    try:
        inp.create_view(tmp_name, replace=True)
        return tmp_name
    except AttributeError as e:
        raise TypeError(
            f"inp must be a table name (str), pandas DataFrame, or DuckDB relation, "
            f"got {type(inp).__name__}"
        ) from e


def _estimate_fanout(
    spine_tbl: str,
    base_tbl: str,
    join_keys: list[str],
    as_of_col: str,
    base_date_col: str,
    max_window: int | None,
    con: duckdb.DuckDBPyConnection,
) -> int:
    q = lambda c: f'"{c}"'  # noqa: E731
    join_on = " AND ".join(f's.{q(k)} = b.{q(k)}' for k in join_keys)
    upper = f'(SELECT MAX({q(as_of_col)}) FROM {spine_tbl})'
    base_where = f'{q(base_date_col)} < {upper}'
    if max_window is not None:
        lower = f'(SELECT MIN({q(as_of_col)}) FROM {spine_tbl}) - INTERVAL {max_window} DAY'
        base_where += f' AND {q(base_date_col)} >= {lower}'

    sql = f"""
        WITH base_f AS (
            SELECT * FROM {base_tbl} WHERE {base_where}
        )
        SELECT COUNT(*) FROM {spine_tbl} s
        JOIN base_f b ON {join_on}
        WHERE b.{q(base_date_col)} < s.{q(as_of_col)}
    """  # noqa: S608
    return con.execute(sql).fetchone()[0]
