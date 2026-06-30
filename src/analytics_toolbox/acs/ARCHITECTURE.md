# acs — Architecture

How the Census ACS ingest is built. This is the **as-built** architecture; [`README.md`](README.md) is the runbook (install, config, usage). There is no SPEC — `acs` was ported from the standalone `acs-census-ingest` repo rather than designed here; the port dropped `dlt` in favour of a hand ingest (`httpx` → DataFrame → `utils.save_table`).

## Why it exists

The toolbox geocodes addresses to Census block-group FIPS. ACS keys its demographic estimates by the **same FIPS**, so ingesting ACS into a local `raw` schema lets demographics join straight onto geocoded records. `acs` is deliberately ingest-only: it lands public aggregates in `raw` and leaves transformation to downstream tooling.

## Ingest flow

`ingest_acs(acs_config, storage)` (in `ingest.py`):

```
resolve_api_key(api_key)              # arg → CENSUS_API_KEY env → .env
ensure data_dir + DuckDB parent exist
open on_disk_con(storage.connection)  # refuses md: cloud strings
CREATE SCHEMA IF NOT EXISTS raw
for (code, geography) in acs_config.variable_geographies():
    years = resolve_valid_years(code, …)     # skip variable if none
    df    = fetch_variable_dataframe(code, geography, states, years, …)  # skip if empty
    save_table(df, f"raw.{raw_table_name(code, geography)}",
               certify_no_phi=True, if_exists="replace")
manifest = build_manifest(acs_config, con, schema="raw")
manifest.write(data_dir / "acs.manifest.json")   # unless write_manifest=False
```

**Every run is a full replace** — each `raw` table is rebuilt to a complete, self-consistent snapshot, so re-running is idempotent rather than appending duplicates.

The `client` parameter is a seam: when omitted, an `httpx.Client` is created and closed internally; when supplied, the caller owns its lifecycle (used by tests).

## Census API layer (`_census_api.py`)

- **Endpoint selection** is driven by the variable code prefix: `B`/`C` → detailed tables, `S` → subject tables, `DP` → data profile. The caller never picks an endpoint.
- **Geography** params are built by `_geography.py` (`build_geo_params`, `state_to_fips`) for `block_group` / `tract` / `county`.
- **Key safety** is a deliberate design point. httpx embeds the request URL — including the `?key=<secret>` query param — in its exception text and in `resp.url`. The module never logs `resp.url`; it logs the **bare URL** instead, so the API key cannot leak into logs or error messages. See `_get()` and the surrounding fetch code.

## Variable year resolution (`_variable_history.py`)

ACS variables are not valid for every vintage. `resolve_valid_years(code, …)` resolves each variable's valid year range from the Census **label metadata**, caching the metadata under `storage.data_dir / "acs_metadata_cache"` to avoid repeat lookups. A variable with no valid years is logged and skipped rather than erroring the whole run.

## Storage contract

- Data always lands in the **`raw`** schema (`RAW_SCHEMA = "raw"`) — the handoff contract for downstream consumers.
- One table per variable+geography, named by `raw_table_name(code, geography)`.
- Writes go through the toolbox's sanctioned, audited path: `utils.save_table(..., certify_no_phi=True, if_exists="replace")`. ACS is public aggregate data, so `certify_no_phi=True` is **honestly assertable** here — the rare case where it is. Each write therefore also emits the standard data-free audit line.
- `on_disk_con` refuses MotherDuck (`md:`) connection strings: cloud egress is not wired up in this module.

## Manifest (`_manifest.py`)

`build_manifest()` inspects the loaded `raw` tables and returns a `RunManifest` (a list of `TableManifest`, each with `ColumnInfo`). Unless `write_manifest=False`, it is serialized to `acs.manifest.json` in the data dir — a human- and machine-readable readout of exactly what a run loaded.

## Module structure

```
acs/
    __init__.py          # public exports (ingest_acs, RAW_SCHEMA, AcsConfig, manifests, …)
    ingest.py            # ingest_acs() — the orchestrator
    _census_api.py       # endpoint selection, fetch, key-safe HTTP, raw_table_name()
    _geography.py        # geography → Census geo params; state_to_fips
    _variable_history.py # resolve_valid_years() from label metadata (cached)
    _settings.py         # resolve_api_key() via pydantic-settings (env/.env)
    _config.py           # AcsConfig / ReportConfig / VariableConfig (pydantic)
    _manifest.py         # RunManifest / TableManifest / ColumnInfo + build_manifest()
    _errors.py           # module-specific errors
```

## Key decisions

- **Hand ingest, not `dlt`.** The port dropped `dlt` for a direct `httpx` → DataFrame → `save_table` path — fewer dependencies, and it reuses the toolbox's existing audited write gate instead of a second ingestion framework.
- **Secrets via `pydantic-settings`, never YAML.** `CENSUS_API_KEY` is read from the environment / `.env` by `_settings.resolve_api_key`, keeping the secret out of the committed-config surface.
- **Full replace, not incremental.** Idempotent snapshots are simpler to reason about than append/merge for a periodically-refreshed public dataset.
- **Public-data certification.** Because ACS is public aggregate data, this is the one ingest in the toolbox that can legitimately pass `certify_no_phi=True` to the on-disk write gate.
