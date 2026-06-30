"""Phase 1 SQL profiling: schema extraction, numeric stats, categorical distributions."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from analytics_toolbox.synth_kit._types import CategoricalProfile, ColumnProfile

logger = logging.getLogger(__name__)

_PERCENTILE_KEYS = ("p01", "p05", "p10", "p25", "p50", "p75", "p90", "p95", "p99")
_PERCENTILE_VALS = (0.01, 0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95, 0.99)

_STDDEV_FUNCS: dict[str, str] = {
    "mssql": "STDEV",
}


def _to_float(v: Any) -> float | None:
    return float(v) if v is not None else None


def _stddev_fn(dialect: str) -> str:
    return _STDDEV_FUNCS.get(dialect, "STDDEV")


def query_schema(
    engine: Engine,
    query: str,
) -> tuple[list[tuple[str, str]], int]:
    """Extract column metadata and row count from a SQL query.

    Args:
        engine: SQLAlchemy engine.
        query: Caller-supplied SQL query.

    Returns:
        Tuple of ([(column_name, sql_type), ...], row_count).
    """
    wrapped = f"SELECT * FROM ({query}) _synth_source"
    with engine.connect() as conn:
        result = conn.execute(text(wrapped))
        cursor_desc = result.cursor.description  # list of 7-tuples per DBAPI2
        cols: list[tuple[str, str]] = [
            (desc[0], str(desc[1].__name__ if hasattr(desc[1], "__name__") else desc[1]))
            for desc in cursor_desc
        ]
        result.close()  # intentional: only cursor.description is read; no source rows enter Python

        row = conn.execute(
            text(f"SELECT COUNT(*) FROM ({query}) _synth_source")
        ).fetchone()
        n_rows: int = int(row[0])  # type: ignore[index]

    return cols, n_rows


def profile_numeric(
    engine: Engine,
    query: str,
    column: str,
) -> ColumnProfile:
    """Profile a numeric column: counts, min/max/mean/stddev, 11-point CDF.

    Args:
        engine: SQLAlchemy engine.
        query: Caller-supplied SQL query (used as a CTE source).
        column: Column name to profile (must be numeric).

    Returns:
        ColumnProfile with aggregate statistics.
    """
    dialect = engine.dialect.name
    stddev = _stddev_fn(dialect)

    col = f'"{column}"'
    pct_exprs = ", ".join(
        f"PERCENTILE_CONT({v}) WITHIN GROUP (ORDER BY {col}) AS {k}"
        for k, v in zip(_PERCENTILE_KEYS, _PERCENTILE_VALS, strict=False)
    )

    sql = f"""
        SELECT
            COUNT(*)                           AS n_total,
            COUNT({col})                       AS n_non_null,
            MIN(CAST({col} AS DOUBLE))         AS val_min,
            MAX(CAST({col} AS DOUBLE))         AS val_max,
            AVG(CAST({col} AS DOUBLE))         AS val_mean,
            {stddev}(CAST({col} AS DOUBLE))    AS val_stddev,
            {pct_exprs}
        FROM ({query}) _synth_source
    """

    with engine.connect() as conn:
        row = conn.execute(text(sql)).mappings().fetchone()

    n_total = int(row["n_total"])
    n_non_null = int(row["n_non_null"])
    null_rate = 1.0 - (n_non_null / n_total) if n_total > 0 else 0.0

    percentiles: dict[str, float] = {}
    for key in _PERCENTILE_KEYS:
        v = row[key]
        percentiles[key] = float(v) if v is not None else float("nan")

    return ColumnProfile(
        name=column,
        sql_type="",
        n_total=n_total,
        n_non_null=n_non_null,
        null_rate=null_rate,
        val_min=_to_float(row["val_min"]),
        val_max=_to_float(row["val_max"]),
        val_mean=_to_float(row["val_mean"]),
        val_stddev=_to_float(row["val_stddev"]),
        percentiles=percentiles,
    )


def profile_categorical(
    engine: Engine,
    query: str,
    column: str,
) -> CategoricalProfile:
    """Profile a categorical column: value counts (capped at 500) and null rate.

    Args:
        engine: SQLAlchemy engine.
        query: Caller-supplied SQL query (used as a CTE source).
        column: Column name to profile.

    Returns:
        CategoricalProfile with value → count mapping.
    """
    col = f'"{column}"'
    count_sql = f"""
        SELECT COUNT(*) AS n_total, COUNT({col}) AS n_non_null
        FROM ({query}) _synth_source
    """
    dist_sql = f"""
        SELECT {col} AS val, COUNT(*) AS cnt
        FROM ({query}) _synth_source
        WHERE {col} IS NOT NULL
        GROUP BY {col}
        ORDER BY cnt DESC
        LIMIT 500
    """

    with engine.connect() as conn:
        count_row = conn.execute(text(count_sql)).mappings().fetchone()
        dist_rows = conn.execute(text(dist_sql)).fetchall()

    n_total = int(count_row["n_total"])
    n_non_null = int(count_row["n_non_null"])
    null_rate = 1.0 - (n_non_null / n_total) if n_total > 0 else 0.0

    value_counts: dict[str, int] = {str(r[0]): int(r[1]) for r in dist_rows}

    return CategoricalProfile(
        name=column,
        sql_type="",
        n_total=n_total,
        n_non_null=n_non_null,
        null_rate=null_rate,
        value_counts=value_counts,
    )
