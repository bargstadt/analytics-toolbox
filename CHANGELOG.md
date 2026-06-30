# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/), and the project aims to follow
[Semantic Versioning](https://semver.org/).

> Distributed on PyPI as **`privacy-analytics-toolbox`** (imports as `analytics_toolbox`).

## [Unreleased]

Working toward the first stable release (`0.1.0`). Beta pre-releases (`0.1.0bN`) are
published to PyPI during an in-progress code self-audit; `pip install` skips them
unless you pass `--pre`.

## [0.1.0] — first public release (in progress)

Initial public release: a single, privacy-first analytics package designed to run
entirely on the machine that holds the data.

### Added
- **geospatial** — offline US address → Census block-group FIPS geocoding
  (normalize → NAD fuzzy-match → point-in-polygon). The only network calls are
  one-time public `.gov` reference-data downloads; input data never leaves the machine.
- **entity_resolution** — config-driven Master Patient Index: blocking, weighted
  RapidFuzz scoring, and NetworkX clustering across N systems. In-memory; the output
  holds only system IDs, never PHI.
- **synth_kit** — SQL-first synthetic data: server-side aggregate profiling plus Faker
  PHI replacement, so raw rows never enter Python memory.
- **feature_engineering** — leakage-safe windowed aggregates over DuckDB with
  point-in-time (as-of-exclusive) correctness and fan-out guardrails.
- **acs** — U.S. Census ACS 5-year ingest into a DuckDB `raw` schema, keyed by the
  same block-group / tract / county FIPS the geocoder assigns.
- **utils** — the sanctioned, audited DuckDB write path (`save_table` / `save_csv`)
  with PHI-certification and cloud-egress gates and data-free audit logging.
- Single-install packaging (no per-module extras), a Pydantic configuration layer,
  the MIT license, and a full documentation set (per-module README + ARCHITECTURE,
  plus an onboarding guide).

[Unreleased]: https://github.com/bargstadt/analytics-toolbox/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/bargstadt/analytics-toolbox/releases/tag/v0.1.0
