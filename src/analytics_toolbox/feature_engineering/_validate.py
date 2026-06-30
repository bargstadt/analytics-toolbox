from __future__ import annotations

import duckdb


def validate_namespace(ns: str) -> None:
    if not ns:
        raise ValueError("namespace must be a non-empty string")
    if "_" in ns:
        raise ValueError(f"namespace must not contain underscores, got {ns!r}")
    if ns != ns.lower():
        raise ValueError(f"namespace must be lowercase, got {ns!r}")


def validate_windows(windows: list) -> None:
    if not windows:
        raise ValueError("windows must contain at least one value")
    seen: set = set()
    for w in windows:
        if isinstance(w, str):
            if w != "all":
                raise ValueError(f"string windows must be 'all', got {w!r}")
        elif isinstance(w, int):
            if w <= 0:
                raise ValueError(f"integer windows must be positive, got {w!r}")
        else:
            raise ValueError(f"windows must be positive integers or 'all', got {w!r}")
        if w in seen:
            raise ValueError(f"duplicate window value: {w!r}")
        seen.add(w)


def validate_columns_present(
    rel_name: str,
    required_cols: list[str],
    which_table: str,
    con: duckdb.DuckDBPyConnection,
) -> None:
    result = con.execute(f'DESCRIBE "{rel_name}"').fetchall()
    existing = {row[0].lower() for row in result}
    for col in required_cols:
        if col.lower() not in existing:
            raise ValueError(
                f"Column {col!r} missing from {which_table} table {rel_name!r}"
            )


def validate_no_nulls_in_grain(
    rel_name: str,
    grain_cols: list[str],
    con: duckdb.DuckDBPyConnection,
) -> None:
    for col in grain_cols:
        count = con.execute(
            f'SELECT COUNT(*) FROM "{rel_name}" WHERE "{col}" IS NULL'
        ).fetchone()[0]
        if count > 0:
            raise ValueError(
                f"Grain column {col!r} contains {count} NULL value(s) in {rel_name!r}"
            )


def validate_spine_uniqueness(
    rel_name: str,
    grain_cols: list[str],
    con: duckdb.DuckDBPyConnection,
) -> None:
    grain_sql = ", ".join(f'"{c}"' for c in grain_cols)
    dup_count = con.execute(
        f"""
        SELECT COUNT(*) FROM (
            SELECT {grain_sql}, COUNT(*) AS n
            FROM "{rel_name}"
            GROUP BY {grain_sql}
            HAVING n > 1
        )
        """
    ).fetchone()[0]
    if dup_count > 0:
        grain_str = ", ".join(grain_cols)
        raise ValueError(
            f"Spine {rel_name!r} has {dup_count} duplicate row(s) on grain ({grain_str})"
        )


def validate_agg_name_no_collision(
    agg_names: list[str],
    grain_cols: list[str],
) -> None:
    grain_set = set(grain_cols)
    for name in agg_names:
        if name in grain_set:
            raise ValueError(
                f"Agg name {name!r} collides with spine grain column — choose a different name"
            )
