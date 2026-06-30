from __future__ import annotations

from analytics_toolbox.feature_engineering._types import Agg


def col_name(namespace: str, agg_name: str, window: int | str) -> str:
    """Compute the output column name for a (namespace, agg_name, window) triple."""
    suffix = f"{window}d" if isinstance(window, int) else "all"
    return f"{namespace}__{agg_name}_{suffix}"


def window_predicate(base_date_col: str, as_of_col: str, window: int | str) -> str | None:
    if window == "all":
        return None
    return f'"{base_date_col}" >= "{as_of_col}" - INTERVAL {window} DAY'


def filter_expr(agg_expr: str, predicate: str | None, col_alias: str) -> str:
    if predicate is None:
        return f'{agg_expr} AS "{col_alias}"'
    return f'{agg_expr} FILTER (WHERE {predicate}) AS "{col_alias}"'


def build_feature_sql(
    spine_tbl: str,
    base_tbl: str,
    entity_keys: list[str],
    as_of_col: str,
    base_date_col: str,
    namespace: str,
    aggregations: list[Agg],
    windows: list[int | str],
    group_cols: list[str],
    max_window: int | None,
) -> str:
    join_keys = list(entity_keys) + list(group_cols)
    grain_cols = join_keys + [as_of_col]

    # Quoted helpers
    q = lambda c: f'"{c}"'  # noqa: E731
    grain_q = [q(c) for c in grain_cols]

    # --- base_f: pre-filter restricts scan to the relevant time range ---
    upper = f'(SELECT MAX({q(as_of_col)}) FROM {spine_tbl})'
    base_f_where = f'{q(base_date_col)} < {upper}'
    if max_window is not None:
        lower = f'(SELECT MIN({q(as_of_col)}) FROM {spine_tbl}) - INTERVAL {max_window} DAY'
        base_f_where += f'\n  AND {q(base_date_col)} >= {lower}'

    # --- joined: spine × base inner join (upper bound exclusive; GROUP BY restores) ---
    spine_grain_select = ", ".join(f's.{q(c)}' for c in grain_cols)
    join_on = " AND ".join(f's.{q(k)} = b.{q(k)}' for k in join_keys)

    # --- agg: one FILTER expression per (agg × window) ---
    agg_exprs: list[str] = []
    for w in windows:
        pred = window_predicate(base_date_col, as_of_col, w)
        for agg in aggregations:
            alias = col_name(namespace, agg.name, w)
            agg_exprs.append("    " + filter_expr(agg.expr, pred, alias))

    agg_select = ",\n".join(agg_exprs)
    grain_select = ", ".join(grain_q)
    exclude_cols = ", ".join(grain_q)

    # --- final LEFT JOIN restores zero-event spine rows with null features ---
    final_on = " AND ".join(f's.{q(c)} = a.{q(c)}' for c in grain_cols)

    return f"""\
WITH base_f AS (
    SELECT *
    FROM {base_tbl}
    WHERE {base_f_where}
),
joined AS (
    SELECT {spine_grain_select}, b.*
    FROM {spine_tbl} s
    JOIN base_f b ON {join_on}
    WHERE b.{q(base_date_col)} < s.{q(as_of_col)}
),
agg AS (
    SELECT
        {grain_select},
{agg_select}
    FROM joined
    GROUP BY {grain_select}
)
SELECT s.*, a.* EXCLUDE ({exclude_cols})
FROM {spine_tbl} s
LEFT JOIN agg a ON {final_on}"""
