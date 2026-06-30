"""Command-line interface for analytics-toolbox geospatial operations.

Entry point: ``analytics-toolbox`` (registered in pyproject.toml).

Subcommands:
    ingest-nad      Download and ingest NAD state CSV files into DuckDB.
    ingest-tiger    Download and ingest TIGER/Line shapefiles into DuckDB.
    ingest-acs      Fetch ACS Census data into the DuckDB ``raw`` schema.

Progress output goes to stderr so stdout stays clean for piping.

This module hosts the toolbox-wide ``analytics-toolbox`` command; subcommand
handlers import their module lazily, so adding one (e.g. ingest-acs) does not
couple geospatial to that module at import time. It could be promoted to a
top-level ``analytics_toolbox.cli`` later without changing this contract.
"""

from __future__ import annotations

import argparse
import sys


def _run_ingest_nad(config_path: str, force_refresh: bool) -> None:
    from analytics_toolbox._config import load_config
    from analytics_toolbox.geospatial.nad_preprocess_ingest import ingest_nad

    geo_config = load_config(config_path).geospatial
    if force_refresh:
        geo_config.nad.force_refresh = True
    ingest_nad(geo_config)


def _run_ingest_tiger(config_path: str, force_refresh: bool) -> None:
    from analytics_toolbox._config import load_config
    from analytics_toolbox.geospatial.address_geocoder import ingest_tiger

    geo_config = load_config(config_path).geospatial
    if force_refresh:
        geo_config.tiger.force_refresh = True
    ingest_tiger(geo_config)


def _run_ingest_acs(config_path: str, write_manifest: bool) -> None:
    from analytics_toolbox._config import load_config
    from analytics_toolbox.acs import ingest_acs

    config = load_config(config_path)
    if config.acs is None:
        raise ValueError(f"config {config_path!r} has no 'acs:' section")
    manifest = ingest_acs(config.acs, config.storage, write_manifest=write_manifest)
    print(
        f"ingest-acs: loaded {len(manifest.tables)} table(s) into the '{manifest.schema}' schema",
        file=sys.stderr,
    )
    for t in manifest.tables:
        years = f"{t.year_min}-{t.year_max}" if t.year_min is not None else "no years"
        print(f"  {manifest.schema}.{t.table}  ({t.row_count:,} rows, {years})", file=sys.stderr)


def main(argv: list[str] | None = None) -> None:
    """Entry point for the ``analytics-toolbox`` command."""
    parser = argparse.ArgumentParser(
        prog="analytics-toolbox",
        description="analytics-toolbox geospatial reference-data management",
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")
    sub.required = True

    # ingest-nad
    nad_parser = sub.add_parser(
        "ingest-nad",
        help="Download and ingest NAD state CSV files into DuckDB",
    )
    nad_parser.add_argument("--config", required=True, metavar="PATH", help="Path to config YAML")
    nad_parser.add_argument(
        "--force-refresh",
        action="store_true",
        default=False,
        help="Re-download and re-ingest even if data already exists",
    )

    # ingest-tiger
    tiger_parser = sub.add_parser(
        "ingest-tiger",
        help="Download and ingest TIGER/Line shapefiles into DuckDB",
    )
    tiger_parser.add_argument("--config", required=True, metavar="PATH", help="Path to config YAML")
    tiger_parser.add_argument(
        "--force-refresh",
        action="store_true",
        default=False,
        help="Re-download and re-ingest even if data already exists",
    )

    # ingest-acs
    acs_parser = sub.add_parser(
        "ingest-acs",
        help="Fetch ACS Census data into the DuckDB raw schema",
    )
    acs_parser.add_argument("--config", required=True, metavar="PATH", help="Path to config YAML")
    acs_parser.add_argument(
        "--no-manifest",
        action="store_true",
        default=False,
        help="Skip writing acs.manifest.json into the data directory",
    )

    args = parser.parse_args(argv)

    try:
        if args.command == "ingest-nad":
            _run_ingest_nad(args.config, args.force_refresh)
        elif args.command == "ingest-tiger":
            _run_ingest_tiger(args.config, args.force_refresh)
        elif args.command == "ingest-acs":
            _run_ingest_acs(args.config, not args.no_manifest)
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
