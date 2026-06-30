"""Run manifest: a machine-readable readout of what an ingest loaded.

Every ``ingest_acs`` run introspects the freshly loaded tables and produces a
:class:`RunManifest` describing each ``raw`` table — row counts, year ranges,
suppression counts, and column types. The manifest is the handoff contract for
analysts: it lets downstream tooling discover what was loaded without opening the
database blind. It is written to ``acs.manifest.json`` in the configured data
directory.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from analytics_toolbox.acs._census_api import raw_table_name
from analytics_toolbox.acs._config import AcsConfig

if TYPE_CHECKING:
    import duckdb

try:
    from importlib.metadata import PackageNotFoundError, version

    _LIBRARY_VERSION = version("analytics-toolbox")
except (ImportError, PackageNotFoundError):  # pragma: no cover - editable/uninstalled
    _LIBRARY_VERSION = "0+unknown"


@dataclass(frozen=True)
class ColumnInfo:
    """A column name and its DuckDB data type."""

    name: str
    type: str


@dataclass(frozen=True)
class TableManifest:
    """Readout for a single ``raw`` table loaded by a run."""

    table: str
    variable_code: str
    geography_level: str
    row_count: int
    year_min: int | None
    year_max: int | None
    suppressed_count: int
    columns: list[ColumnInfo]


@dataclass(frozen=True)
class RunManifest:
    """Top-level readout describing everything a run loaded into DuckDB."""

    generated_at: str
    library_version: str
    schema: str
    states: list[str]
    reports: list[str]
    tables: list[TableManifest]

    def to_dict(self) -> dict:
        """Return a JSON-serialisable dict of the manifest."""
        return asdict(self)

    def write(self, path: str | Path) -> Path:
        """Write the manifest as pretty-printed JSON. Returns the path written."""
        path = Path(path)
        path.write_text(json.dumps(self.to_dict(), indent=2) + "\n")
        return path


def _scalar(con: duckdb.DuckDBPyConnection, sql: str, params: list | None = None):
    row = con.execute(sql, params or []).fetchone()
    return row[0] if row else None


def _table_exists(con: duckdb.DuckDBPyConnection, schema: str, table: str) -> bool:
    count = _scalar(
        con,
        "SELECT count(*) FROM information_schema.tables "
        "WHERE table_schema = ? AND table_name = ?",
        [schema, table],
    )
    return bool(count)


def _table_manifest(
    con: duckdb.DuckDBPyConnection,
    schema: str,
    table: str,
    variable_code: str,
    geography_level: str,
) -> TableManifest:
    # schema/table are derived from validated config (raw_table_name), safe to inline.
    qualified = f'{schema}."{table}"'

    row_count = _scalar(con, f"SELECT count(*) FROM {qualified}")
    year_min, year_max = con.execute(f"SELECT min(year), max(year) FROM {qualified}").fetchone()

    columns = [
        ColumnInfo(name=name, type=dtype)
        for name, dtype in con.execute(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = ? AND table_name = ? ORDER BY ordinal_position",
            [schema, table],
        ).fetchall()
    ]

    # Suppression flag column follows the raw_table_name value contract:
    # the lowercased variable code plus '_is_suppressed'.
    suppression_col = f"{variable_code.lower()}_is_suppressed"
    suppressed_count = 0
    if any(c.name == suppression_col for c in columns):
        suppressed_count = _scalar(
            con, f'SELECT count(*) FROM {qualified} WHERE "{suppression_col}"'
        )

    return TableManifest(
        table=table,
        variable_code=variable_code,
        geography_level=geography_level,
        row_count=int(row_count),
        year_min=int(year_min) if year_min is not None else None,
        year_max=int(year_max) if year_max is not None else None,
        suppressed_count=int(suppressed_count),
        columns=columns,
    )


def build_manifest(
    config: AcsConfig,
    con: duckdb.DuckDBPyConnection,
    *,
    schema: str = "raw",
) -> RunManifest:
    """Build a :class:`RunManifest` by introspecting the loaded DuckDB schema.

    Args:
        config: The config that drove the run (used to enumerate expected tables).
        con: An open DuckDB connection holding the loaded ``raw`` tables.
        schema: The schema the tables live in.

    Tables expected from the config but absent (e.g. a variable with no valid
    years, which the source skips) are simply omitted.
    """
    tables: list[TableManifest] = []
    for code, geo in config.variable_geographies():
        table = raw_table_name(code, geo)
        if _table_exists(con, schema, table):
            tables.append(_table_manifest(con, schema, table, code, geo))

    return RunManifest(
        generated_at=datetime.now(UTC).isoformat(timespec="seconds"),
        library_version=_LIBRARY_VERSION,
        schema=schema,
        states=list(config.states),
        reports=[r.name for r in config.reports],
        tables=tables,
    )
