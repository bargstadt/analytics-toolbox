# acs

Ingest U.S. Census ACS 5-year estimates into a local DuckDB `raw` schema, keyed by FIPS.

`ingest_acs()` fetches the ACS variables you describe in config from the Census Bureau API and lands them in the `raw` schema — **one table per variable+geography**, keyed by block group / tract / county FIPS. That's the same FIPS the `geospatial` geocoder assigns, so ACS demographics join straight onto geocoded address records.

> **Architecture & design:** this README is the runbook. For the ingest flow, the Census API layer, and the storage contract, see [`ARCHITECTURE.md`](ARCHITECTURE.md). (No SPEC — `acs` was ported from the standalone `acs-census-ingest` repo rather than specced here.)

## Install

```bash
pip install privacy-analytics-toolbox
```

## Prerequisites: a Census API key

The Census API key is a **secret** and is **not** read from `config.yaml`. Set it via the `CENSUS_API_KEY` environment variable or a `.env` file ([free signup](https://api.census.gov/data/key_signup.html)):

```bash
export CENSUS_API_KEY=your_key_here   # or put CENSUS_API_KEY=... in a .env file
```

## Quick start

```python
from analytics_toolbox.acs import ingest_acs
from analytics_toolbox._config import load_config

config = load_config("config.yaml")          # needs an acs: section (see below)

manifest = ingest_acs(config.acs, config.storage)   # key read from env/.env
print(manifest.tables)                         # what was loaded
```

`ingest_acs()` also accepts `api_key=` (overrides the env var), `write_manifest=False` (skip the JSON readout), and `client=` (supply your own `httpx.Client`). Every run is a **full replace**: each `raw` table is rebuilt to a complete, self-consistent snapshot.

## Config schema

```yaml
acs:
  states: [IA]                      # USPS abbreviations to pull estimates for
  reports:
    - name: poverty_estimate        # a label grouping one or more variables
      variables:
        - code: B01001_001E         # Total population (detailed table)
          geographies: [block_group, tract]
        - code: S1701_C03_001E      # Pct below poverty level (subject table)
          geographies: [tract, county]
```

- **`states`** — list of USPS state abbreviations.
- **`reports`** — each report groups one or more ACS variable codes; `name` is just a label.
- **`code`** — an ACS variable code. The prefix selects the Census endpoint automatically: `B`/`C` → detailed tables, `S` → subject tables, `DP` → data profile.
- **`geographies`** — any of `block_group`, `tract`, `county`. All three are keyed by FIPS, so they join onto geocoded records.

## Output

- **Tables** — `raw.<table>`, one per variable+geography, in the DuckDB file at `storage.connection`. Each table is keyed by the geography's FIPS plus the estimate value.
- **Manifest** — unless `write_manifest=False`, an `acs.manifest.json` readout is written into `storage.data_dir` describing the loaded tables (also returned as a `RunManifest`).

The module only ingests into `raw` — transformation is left to whatever you prefer downstream (SQL, pandas, R).

## Privacy

ACS 5-year estimates are **public aggregate data**, so this module sends no input data and the on-disk write is honestly certified PHI-free. The only network call is to the Census API (`api.census.gov`); the `CENSUS_API_KEY` is kept out of logs and error messages. See [`ARCHITECTURE.md`](ARCHITECTURE.md) and the repo's [`docs/DATA_HANDLING.md`](../../../docs/DATA_HANDLING.md).
