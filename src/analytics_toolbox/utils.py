"""Cross-cutting utilities.

Currently: the toolbox's *sanctioned* ways to put a DataFrame somewhere, with the
privacy guarantee tied to where the data actually lands.

The caller chooses the destination explicitly by which connection they pass:

- ``in_memory_con()`` — an ephemeral DuckDB. Nothing persists, so it is safe to
  load PII/PHI here for in-process work.
- ``on_disk_con(path)`` — a local DuckDB file. Persisting requires certifying the
  data is PHI-free. (Rejects MotherDuck ``md:`` strings — cloud is not disk.)

``save_table`` writes a DataFrame into whichever connection it is given and gates
on the *real* risk: in-memory writes are free, on-disk writes require
``certify_no_phi=True``, and a MotherDuck/cloud write (data leaving the machine)
additionally requires ``allow_cloud_egress=True`` and warns loudly. ``save_csv``
is always a disk write, so it is always gated. Every write logs a data-free audit
line (destination + row/column counts only — never cell values).

``save_csv`` needs only pandas (a core dependency). The DuckDB helpers import
duckdb lazily, so importing this module for the CSV path never requires the
``utils`` extra.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    import duckdb

logger = logging.getLogger(__name__)

# Schema-qualified or bare SQL identifier: ``table`` or ``schema.table``.
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?$")

_IF_EXISTS_MODES = ("error", "replace", "append")


def in_memory_con() -> duckdb.DuckDBPyConnection:
    """Return a fresh in-memory DuckDB connection.

    Nothing written through this connection is persisted to disk, so it is safe to
    load PII/PHI here for in-process work (e.g. running SQL or feature engineering
    on protected data). The data lives only as long as the returned connection is
    held; close it (or let it go out of scope) and the data is gone.

    Returns:
        An open in-memory ``duckdb.DuckDBPyConnection``. The caller owns its
        lifecycle.
    """
    import duckdb

    return duckdb.connect()


def on_disk_con(connection: str | Path) -> duckdb.DuckDBPyConnection:
    """Open a local on-disk DuckDB database for persistence.

    This is the sanctioned way to obtain a connection that ``save_table`` will
    persist to. Writing PII/PHI through it is gated by ``save_table``.

    Args:
        connection: Path to a DuckDB file (``~`` is expanded). ``config.storage.connection``
            is a valid value. A MotherDuck/cloud string (``md:``) is rejected —
            cloud is not local disk.

    Returns:
        An open file-backed ``duckdb.DuckDBPyConnection``. The caller owns its
        lifecycle (remember to ``close()`` it).

    Raises:
        ValueError: If ``connection`` is a MotherDuck (``md:``) string.
    """
    import duckdb

    conn = str(connection)
    if conn.startswith("md:"):
        raise ValueError(
            "on_disk_con() is for local disk only; refusing a MotherDuck 'md:' "
            "connection (that is cloud egress — data leaving the machine). To "
            "persist to the cloud, build the connection yourself and pass "
            "allow_cloud_egress=True to save_table()."
        )
    return duckdb.connect(str(Path(conn).expanduser()))


def save_table(
    df: pd.DataFrame,
    table: str,
    *,
    con: duckdb.DuckDBPyConnection,
    certify_no_phi: bool = False,
    allow_cloud_egress: bool = False,
    if_exists: str = "error",
) -> None:
    """Write a DataFrame into the given DuckDB connection, gated by where it lands.

    The destination is whatever ``con`` points at — use ``in_memory_con()`` for
    ephemeral/PHI-safe work or ``on_disk_con(path)`` to persist. Certification is
    required only when the write actually leaves memory:

    - **in-memory** — written freely (no certification); nothing is persisted.
    - **on-disk** — requires ``certify_no_phi=True``.
    - **MotherDuck/cloud** — data leaves the machine: requires ``certify_no_phi=True``
      *and* ``allow_cloud_egress=True``, and logs a warning.

    Args:
        df: The DataFrame to write.
        table: Destination table name — a bare or ``schema.table`` SQL identifier
            (validated to avoid injection, since table names cannot be parameterized).
        con: An open DuckDB connection (from ``in_memory_con`` / ``on_disk_con``, or
            your own). Left open by this function — the caller owns its lifecycle.
        certify_no_phi: Must be ``True`` to persist (on-disk or cloud). By passing
            ``True`` you certify the DataFrame contains no PII/PHI. Ignored for
            in-memory writes.
        allow_cloud_egress: Must be ``True`` to write to a MotherDuck/cloud database.
            A deliberate, separate acknowledgement that data will leave the machine.
        if_exists: ``"error"`` (default) fails if the table exists; ``"replace"``
            overwrites; ``"append"`` inserts (creating the table if absent).

    Raises:
        ValueError: For an invalid identifier or ``if_exists``; if persisting
            without ``certify_no_phi=True``; or if writing to cloud without
            ``allow_cloud_egress=True``.
    """
    _validate_identifier(table)
    if if_exists not in _IF_EXISTS_MODES:
        raise ValueError(f"if_exists must be one of {_IF_EXISTS_MODES}, got {if_exists!r}")

    kind, path = _con_target(con)

    if kind == "memory":
        _write_duckdb(con, df, table, if_exists)
        logger.info(
            "utils: loaded %d rows x %d cols into in-memory duckdb table %r (not persisted)",
            len(df),
            df.shape[1],
            table,
        )
        return

    if kind == "cloud":
        _require_certification(certify_no_phi)
        if allow_cloud_egress is not True:
            raise ValueError(
                "Refusing to write to a MotherDuck/cloud database: this sends data "
                "off the machine. Pass allow_cloud_egress=True to override — only do "
                "so if cloud egress is permitted for this data."
            )
        logger.warning(
            "utils: writing %d rows x %d cols to CLOUD (MotherDuck) table %r — "
            "DATA IS LEAVING THE MACHINE [caller certified no PII/PHI; cloud egress allowed]",
            len(df),
            df.shape[1],
            table,
        )
        _write_duckdb(con, df, table, if_exists)
        return

    # kind == "disk"
    _require_certification(certify_no_phi)
    _write_duckdb(con, df, table, if_exists)
    logger.info(
        "utils: wrote %d rows x %d cols -> duckdb table %r at %s [caller certified no PII/PHI]",
        len(df),
        df.shape[1],
        table,
        path or "<disk>",
    )


def save_csv(
    df: pd.DataFrame,
    path: str | Path,
    *,
    certify_no_phi: bool = False,
    index: bool = False,
    overwrite: bool = False,
) -> None:
    """Write a DataFrame to a CSV file — only after certifying it is PHI-free.

    A CSV is always a disk artifact (there is no ephemeral equivalent), so this is
    always gated. For PHI data, keep it in an ``in_memory_con()`` instead of writing
    a CSV.

    Args:
        df: The DataFrame to write.
        path: Destination file path.
        certify_no_phi: Must be ``True`` to write. By passing ``True`` you certify
            the DataFrame contains no PII/PHI. Anything else raises ``ValueError``.
        index: Whether to write the DataFrame index (default ``False``).
        overwrite: If ``False`` (default), refuse to clobber an existing file.

    Raises:
        ValueError: If ``certify_no_phi`` is not ``True``.
        FileExistsError: If the path exists and ``overwrite`` is ``False``.
    """
    _require_certification(certify_no_phi)
    path = Path(path)
    if path.exists() and not overwrite:
        raise FileExistsError(f"{path} already exists; pass overwrite=True to replace it")

    df.to_csv(path, index=index)

    logger.info(
        "utils: wrote %d rows x %d cols -> csv %r [caller certified no PII/PHI]",
        len(df),
        df.shape[1],
        str(path),
    )


def _require_certification(certify_no_phi: bool) -> None:
    """Block a persistent write unless the caller explicitly certified PHI-free data."""
    if certify_no_phi is not True:
        raise ValueError(
            "certify_no_phi must be set to True to persist data. By passing "
            "certify_no_phi=True you certify the DataFrame contains no PII/PHI — "
            "this is a deliberate, audited control, not a default. (PHI may be loaded "
            "into an in_memory_con() instead, where nothing is persisted.)"
        )


def _validate_identifier(table: str) -> None:
    if not _IDENTIFIER.match(table):
        raise ValueError(
            f"{table!r} is not a valid table identifier "
            "(letters, digits, underscores; optional schema. prefix)"
        )


def _con_target(con: duckdb.DuckDBPyConnection) -> tuple[str, str | None]:
    """Classify a connection's current database as 'memory', 'disk', or 'cloud'.

    Returns (kind, path). ``path`` is the on-disk file path for 'disk', else None.
    """
    name = con.execute("SELECT current_database()").fetchone()[0]
    row = con.execute(
        "SELECT path, type FROM duckdb_databases() WHERE database_name = ?", [name]
    ).fetchone()
    path = row[0] if row else None
    dbtype = (row[1] if row else None) or ""

    if dbtype.lower() in ("motherduck", "md") or (
        isinstance(path, str) and path.startswith("md:")
    ):
        return "cloud", path
    if not path or path == ":memory:":
        return "memory", None
    return "disk", path


def _write_duckdb(
    con: duckdb.DuckDBPyConnection,
    df: pd.DataFrame,
    table: str,
    if_exists: str,
) -> None:
    exists = _table_exists(con, table)
    if exists and if_exists == "error":
        raise ValueError(
            f"table {table!r} already exists; pass if_exists='replace' or 'append'"
        )

    con.register("_utils_staging", df)
    try:
        if if_exists == "append" and exists:
            con.execute(f"INSERT INTO {table} SELECT * FROM _utils_staging")
        elif if_exists == "replace":
            con.execute(f"CREATE OR REPLACE TABLE {table} AS SELECT * FROM _utils_staging")
        else:
            con.execute(f"CREATE TABLE {table} AS SELECT * FROM _utils_staging")
    finally:
        con.unregister("_utils_staging")


def _table_exists(con: duckdb.DuckDBPyConnection, table: str) -> bool:
    parts = table.split(".")
    name = parts[-1]
    query = "SELECT 1 FROM information_schema.tables WHERE table_name = ?"
    params: list[str] = [name]
    if len(parts) == 2:
        query += " AND table_schema = ?"
        params.append(parts[0])
    return con.execute(query, params).fetchone() is not None
