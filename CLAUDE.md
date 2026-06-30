# CLAUDE.md

Context and conventions for working in **analytics-toolbox** — an open-source, reusable analytics utility library, built module by module.

## What this repo is

A single, Poetry-managed Python package (`analytics_toolbox`) collecting common analytics utilities: geospatial analytics, entity resolution, synthetic data generation, drive-time routing, DuckDB helpers, and feature engineering. It's MIT-licensed and published to PyPI, so a project doesn't have to start from a blank file.

Build philosophy: small composable functions over class hierarchies, lean dependencies, and nothing pre-built speculatively — modules get real implementations when an actual need shows up, not before.

## Module map

| Module | Status | Purpose |
|---|---|---|
| `geospatial` | **Complete** | Offline US address-to-block-group geocoding pipeline: normalize → fuzzy-match against NAD → assign Census block group FIPS. The most polished module — built to a higher bar than the rest. |
| `entity_resolution` | **Complete** | Config-driven Master Patient Index (MPI): blocks by DOB (secondary fallback for null DOBs), scores pairs with weighted RapidFuzz, clusters across N systems via NetworkX connected components. In-memory, HIPAA-safe — output holds only system IDs. |
| `synth_kit` | **Complete** | SQL-first, HIPAA-safe synthetic data generator: profiles via server-side SQL aggregates, replaces PHI via Faker — raw rows never enter Python memory. Realistic tabular data for testing, demos, and privacy-safe sharing. |
| `feature_engineering` | **Complete** | Leakage-safe windowed aggregate features over DuckDB: entity-date spine, FILTER-per-window SQL in one scan, point-in-time (as-of-exclusive) correctness, fan-out guardrails. |
| `utils` | **Built (partial)** | Cross-cutting utilities. Sanctioned data-write helpers: `in_memory_con`/`on_disk_con` make the destination explicit; `save_table` writes into the given DuckDB connection and gates on persistence (in-memory → free; on-disk → requires `certify_no_phi=True`; MotherDuck/cloud → additionally requires `allow_cloud_egress=True` and warns). `save_csv` is always gated. All persistent writes log a data-free audit line (destination + row/col counts only). `get_data` and others still TBD — add when 2+ modules actually need them. |
| `acs` | **Complete** | U.S. Census ACS 5-year ingest into the DuckDB `raw` schema (one table per variable+geography), keyed by block group / tract / county FIPS — the same FIPS `geospatial` geocodes to, so demographics join straight onto geocoded records. Public-data-only network calls (the Census API); ported from the standalone `acs-census-ingest` repo, dropping `dlt` for hand ingest (httpx → DataFrame → `utils.save_table`, certified PHI-free since ACS is public aggregate data). `pydantic-settings` reads `CENSUS_API_KEY` from env/.env (never YAML). Resolves each variable's valid year range from label metadata; writes an `acs.manifest.json` readout. |

All modules ship in a single install (no extras). `geospatial` and `entity_resolution` could still be split into standalone PyPI packages later if there's a reason, but they are not separately installable today.

Drive-time routing (OSRM) lives in a separate repo — it's a server deployment, not a pip package.

## Packaging & dependencies

- One package, one repo, one version number — not independently versioned sub-packages. Coordinating releases across many PyPI projects is overhead this project doesn't need.
- Dependency manager: **Poetry**, using the PEP 621 `[project]` table for metadata and dependencies, and `[tool.poetry.group.dev]` for dev-only tooling (pytest, ruff).
- **No per-module extras — a single dependency set.** `pip install privacy-analytics-toolbox` installs the whole toolbox. The modules share a common backbone (pandas/numpy, duckdb, pydantic) and the remaining deps are individually light and reliably-wheeled, so splitting them behind extras bought little and cost a lot: per-module extras forced lazy `__init__`s and import-isolation shims, and pushed the shared `StorageConfig` to live inside a feature module. Extras were dropped in favor of one install; the heaviest group is the GIS stack (geopandas/shapely/pyproj), accepted as a default. Shared config (`StorageConfig`) lives at the package root in `analytics_toolbox/_storage.py`.
- The lean-deps discipline still holds: flag any **new** third-party dependency before it lands (install weight, maintenance risk, version maturity). What changed is only the per-module split, not the bar for adding deps.

## The geospatial module — rules of engagement

These apply to every component of `geospatial` (address normalization, NAD ingestion, matching, geocoding to block group). **Full per-component architecture and design rationale lives in [`src/analytics_toolbox/geospatial/ARCHITECTURE.md`](src/analytics_toolbox/geospatial/ARCHITECTURE.md) — read it before changing pipeline internals.**

1. **No input data leaves the machine, ever.** The only network calls anywhere in this module are one-time/periodic downloads of public reference data from .gov domains (NAD from data.transportation.gov, TIGER/ZCTA and block group shapefiles from census.gov). The address data you feed in is never transmitted anywhere. This must hold even in a HIPAA-constrained environment.
2. **Dataframe operations: SQL first, pandas second.** Prefer doing transforms in DuckDB SQL over pandas where reasonable; pandas is the fallback, not the default. Plan loosely for a future Spark option but don't add the complexity now.
3. **Don't silently expand scope.** If a simpler structure than what's planned becomes apparent, raise it before implementing rather than just building it.

**Load-bearing — do not "clean up":** `geospatial/_scourgify_compat.py` stubs the `geocoder` package into `sys.modules` before scourgify imports (triggered from `geospatial/__init__.py`). Removing it breaks `import scourgify` and would re-expose an external geocoding call. **Never import `scourgify.get_geocoder_normalized_addr`** — it calls Google's geocoder API and violates Rule 1. ARCHITECTURE.md explains why.

Pipeline: `address_normalizer.py` → `nad_preprocess_ingest.py` → `address_matcher.py` → `address_geocoder.py`, chained by `pipeline.py`'s `geocode_address_table()`. See ARCHITECTURE.md for each component.

## Code conventions

- Small, composable functions over class hierarchies. Reach for a class only when state genuinely needs to persist across calls.
- Type hints on all public functions.
- Google-style docstrings.
- Formatting/linting: `ruff` (config lives in `pyproject.toml`).
- Tests: `pytest`, mirroring `src/analytics_toolbox/<module>/` under `tests/<module>/`.

## Module documentation convention

Each module carries up to three docs, each with one job and a single source of truth — don't duplicate the same fact across them:

- **`README.md`** — the **runbook**: install, usage, API, config, output schema, troubleshooting. Living doc. Every module has one (including `acs`). No "Architecture" section — link to ARCHITECTURE.md instead.
- **`ARCHITECTURE.md`** — **how it's built**: pipeline, components, data flow, diagrams, and the *why* behind key decisions. Living doc. This is the human-facing home for the deep architecture detail; CLAUDE.md keeps only the always-loaded guardrails and points here.
- **`SPEC.md`** — the **design intent**, frozen at build and dated (banner at top). A historical record, **not** maintained or backfilled. Only modules that were specced before building have one (`entity_resolution`, `feature_engineering`, `synth_kit`); `geospatial` and `acs` have none and won't get one retroactively.

`utils` is a single file, not a package — it stays documented by its docstring, no per-module docs.

## Versioning & release (planned, not yet wired up)

Single version number for the whole package. GitHub Actions triggered by version tags will handle PyPI publishing once a module is ready for public release — the workflow itself isn't written yet.

## Cross-module configuration

**Built**: `analytics_toolbox/_config.py` owns `AnalyticsToolboxConfig` and the single `load_config(path)` entry point used by all modules. `geospatial/_config.py` holds the typed dataclasses (`GeospatialConfig`, `NadConfig`, etc.) but no loader — the top-level loader populates them.

YAML structure — `storage` is shared at root; module settings are nested under their name:

```yaml
storage:
  connection: ~/.local/share/analytics_toolbox/analytics_toolbox.duckdb
  data_dir: ~/.local/share/analytics_toolbox/

geospatial:
  nad:
    states: [IA]
  tiger:
    vintage: 2024
  matching:
    confidence_threshold: 90
```

`config.yaml` is gitignored. `config.example.yaml` at the repo root is the template. The `end_to_end.ipynb` notebook uses `CONFIG_PATH = "config.yaml"` as a single controller — `synthesize()` passes it via `geospatial_config=`, `geocode_address_table()` uses it directly. Feature engineering settings are inline `Guardrails(...)` for now; add a `feature_engineering:` section here when they need to be file-configurable.

## Working agreement

- Build one module at a time, to completion, before starting the next.
- Don't introduce a new third-party dependency without flagging it first — install weight, maintenance risk, and version maturity are each worth a sentence of discussion before something lands in `pyproject.toml`.
