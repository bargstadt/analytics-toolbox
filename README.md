# analytics-toolbox

**Privacy-first analytics utilities** — geospatial, synthetic data, entity resolution, feature engineering, and DuckDB helpers, built module by module.

Every module is designed to run entirely on your own machine: your data never leaves it, and the only network calls anywhere are to public `.gov` sources — one-time downloads of public reference data, and the Census API for public aggregate demographics. It was built because a privacy-first analytics toolbox like this didn't seem to exist — one where the offline-by-default, in-memory design means sensitive data never has to leave the machine. It's strict enough for HIPAA-regulated healthcare data, but nothing here is healthcare-specific. If you work in finance, legal, government, or any on-prem or air-gapped environment — or you just don't want your data leaving your laptop — this toolbox was built the way you'd want it built.

Built and maintained by Matthew Bargstadt.

## Modules

| Module | Status | Purpose |
|---|---|---|
| [`geospatial`](src/analytics_toolbox/geospatial/) | **Complete** | Offline US address-to-block-group geocoding. Normalize → fuzzy-match against NAD (~97M addresses) → assign Census block group FIPS. No network calls at runtime, HIPAA-safe. |
| [`synth_kit`](src/analytics_toolbox/synth_kit/) | **Complete** | SQL-first, HIPAA-safe synthetic data generation. Profiles via server-side SQL aggregates, replaces PHI via Faker — raw rows never enter Python memory. |
| [`entity_resolution`](src/analytics_toolbox/entity_resolution/) | **Complete** | Config-driven Master Patient Index (MPI). Blocks by DOB, scores pairs with weighted RapidFuzz, clusters matches via NetworkX connected components. In-memory and HIPAA-safe — output holds only system IDs. |
| `feature_engineering` | **Complete** | Leakage-safe windowed aggregate features over DuckDB. Entity-date spine, `FILTER`-per-window SQL in a single scan, fan-out guardrails. |
| [`acs`](src/analytics_toolbox/acs/) | **Complete** | U.S. Census ACS 5-year ingest into a local DuckDB `raw` schema (one table per variable+geography), keyed by block group / tract / county FIPS — the same FIPS `geospatial` geocodes to, so demographics join straight onto geocoded records. Public-data-only network calls (the Census API). |
| [`utils`](src/analytics_toolbox/utils.py) | **Built** | Sanctioned data-write helpers. `in_memory_con`/`on_disk_con` make the destination explicit; `save_table` gates persistence on a no-PII/PHI certification (in-memory writes are free; MotherDuck/cloud is locked down hardest), and `save_csv` is always gated. Every persistent write logs a data-free audit line. |

Drive-time routing (OSRM) lives in a separate repo — it's a server deployment, not a pip package.

## Privacy & HIPAA

The toolbox is built to run inside HIPAA-constrained environments — the design goal is that protected data never leaves the machine it is processed on. HIPAA-grade strictness is the bar it is designed to meet, but nothing here is healthcare-specific: the same offline, in-memory guarantees apply to any sensitive data — financial records, legal discovery, PII of any kind, or data that contractually cannot leave a customer's environment. Concretely, per module:

- **geospatial** — fully offline at runtime. The only network calls anywhere are one-time/periodic downloads of public reference data from `.gov` domains (NAD from data.transportation.gov; TIGER/ZCTA and block-group shapefiles from census.gov). Input address data is never transmitted — geocoding runs entirely against locally cached reference data.
- **synth_kit** — two strictly separated phases, no raw rows crossing the boundary. Phase 1 issues only server-side SQL aggregates (schema, counts, percentiles, capped category distributions); Phase 2 synthesizes from those aggregates in Python. Raw PII never enters Python memory, nothing is written to disk, and there are no network calls — it works air-gapped.
- **entity_resolution** — all matching is in-memory. No PHI appears in logs or error messages (only counts, column names, and scores), nothing is written to disk, and the returned cluster table contains only system IDs and similarity scores — safe to export, inspect, or log.
- **feature_engineering** — computation runs locally in DuckDB on the caller's data; nothing is transmitted off the machine.
- **acs** — the only module that makes a runtime network call, and it sends **no input data**: it requests public Census ACS aggregates from the Census Bureau API (`api.census.gov`). The optional `CENSUS_API_KEY` is read from the environment (never YAML) and is deliberately kept out of logs and error messages (httpx embeds it in the request URL; the module logs the bare URL instead).

**Persisting is a deliberate, gated act.** Everything above stays in memory; the `utils` helpers are the *only* code in the toolbox that writes data out, and they make that a conscious decision rather than an accident. You choose the destination explicitly — `in_memory_con()` keeps PHI ephemeral (nothing persists), while writing to disk requires you to certify the data is PHI-free (`certify_no_phi=True`). A MotherDuck/cloud target — the only path that sends data *off the machine* — is locked down hardest: it additionally requires `allow_cloud_egress=True` and emits a loud warning. Every persistent write logs a data-free audit line (destination + row/column counts, never cell values), so disk egress always leaves a trail.

**Shared caveat.** The DataFrames *you* pass in may contain PHI. The library never persists or transmits them, but handling them under your data-governance obligations — access control, audit logging, transmission and retention rules — remains the caller's responsibility.

**Not a compliance certification.** These are design properties that support deployment in regulated environments; HIPAA (or any other regulatory) compliance is a property of your organization and deployment, not of any library. Validate against your own controls before processing protected data.

## Install

> **Release status:** the first public release is in progress. Beta pre-releases are published to PyPI as **`privacy-analytics-toolbox`**; the stable `0.1.0` follows a code self-audit. (The import name is `analytics_toolbox`; the package is renamed only because `analytics-toolbox` was already taken on PyPI.)

One install pulls the whole toolbox — there are no per-module extras:

```bash
pip install --pre privacy-analytics-toolbox   # latest beta
pip install privacy-analytics-toolbox         # stable, once 0.1.0 ships
```

Or install from source:

```bash
git clone https://github.com/bargstadt/analytics-toolbox
cd analytics-toolbox
poetry install
```

## Quick start

### Geospatial — offline address geocoding

```python
from analytics_toolbox.geospatial import geocode_address_table
from analytics_toolbox._config import load_config
from analytics_toolbox.geospatial.nad_preprocess_ingest import ingest_nad
from analytics_toolbox.geospatial.address_geocoder import ingest_tiger

# One-time reference data setup (run once per state, data cached locally)
geo_config = load_config("config.yaml").geospatial   # states: [IA], storage: ~/.local/...
ingest_tiger(geo_config)              # TIGER ZCTA + block group shapefiles
ingest_nad(geo_config)                # NAD ~97M address records

# Geocode addresses → block group FIPS
result = geocode_address_table(addresses_df, "config.yaml")
# Adds: normalized_address, match_method, match_score,
#       matched_latitude, matched_longitude, block_group_fips
```

See [`notebooks/geospatial_demo.ipynb`](notebooks/geospatial_demo.ipynb) for a full walkthrough.

### synth_kit — HIPAA-safe synthetic data

```python
from sqlalchemy import create_engine
from analytics_toolbox.synth_kit import synthesize

# Works with DuckDB, SQL Server, PostgreSQL — swap only the connection string
engine = create_engine("duckdb:///warehouse.duckdb")

synth = synthesize(
    engine,
    "SELECT * FROM patient_registry",
    categorical_aliases={"payer": ["Payer A", "Payer B", "Payer C", "Payer D"]},
    random_seed=42,
)
# Returns a pandas DataFrame — same schema, all PHI replaced, no raw rows fetched
```

See [`notebooks/synth_kit_demo.ipynb`](notebooks/synth_kit_demo.ipynb) for a full walkthrough including PHI detection, categorical aliases, overrides, reproducibility, and multi-dialect support.

### entity_resolution — multi-system patient linking

```python
from analytics_toolbox.entity_resolution import resolve
from analytics_toolbox._config import load_config

config = load_config("config.yaml")   # entity_resolution.match_threshold required

# {id_column_name: DataFrame} — one entry per source system (Medicaid, hospital, PBM, ...)
systems = {"medicaid_id": medicaid_df, "hospital_id": hospital_df, "pbm_id": pbm_df}

clusters = resolve(systems, config=config)
# One row per linked entity, one column per system ID (None if absent),
# plus avg_similarity and min_similarity. Output holds only IDs — no PHI.
```

See [`notebooks/entity_resolution_demo.ipynb`](notebooks/entity_resolution_demo.ipynb) for a full walkthrough including blocking, weighted scoring, graph clustering, and edge cases.

### feature_engineering — leakage-safe windowed features

```python
import duckdb
from analytics_toolbox.feature_engineering import compute_features, join_features

con = duckdb.connect()
# spine = one row per (entity, as_of_date); base table = timestamped events
features = compute_features(
    spine, "rx_claims",
    entity_keys=["member_id"], as_of_col="as_of_date", base_date_col="claim_date",
    namespace="rx", aggregations=[...], windows=[30, 90, 365], con=con,
)
# Windowed aggregates with strict temporal boundaries — no forward leakage.
```

See [`notebooks/feature_engineering_demo.ipynb`](notebooks/feature_engineering_demo.ipynb) for a full walkthrough.

### acs — Census demographics keyed to the same FIPS

```python
from analytics_toolbox.acs import ingest_acs
from analytics_toolbox._config import load_config

config = load_config("config.yaml")   # acs.states + acs.reports required

# Pulls ACS 5-year estimates into the DuckDB `raw` schema, one table per
# variable+geography, keyed by block group / tract / county FIPS.
manifest = ingest_acs(config.acs, config.storage)   # CENSUS_API_KEY read from env/.env
print(manifest.tables)
# Because tables are keyed by the same FIPS the geospatial geocoder assigns,
# ACS demographics join straight onto geocoded address records.
```

The Census API key is a secret — set `CENSUS_API_KEY` via environment variable or a `.env` file ([free signup](https://api.census.gov/data/key_signup.html)), never in `config.yaml`.

### utils — sanctioned, audited data writes

Everything in the toolbox stays in memory by design. The `utils` helpers make the *destination* an explicit choice, and gate persistence on the real risk — data reaching disk:

```python
from analytics_toolbox.utils import in_memory_con, on_disk_con, save_csv, save_table

# PHI is fine in an in-memory DuckDB — nothing persists, so no certification is needed.
mem = in_memory_con()
save_table(phi_df, "patients", con=mem)          # free; logs "(not persisted)"

# Persisting to disk requires certifying the data is PHI-free.
disk = on_disk_con("warehouse.duckdb")
save_table(features_df, "model_ready", con=disk, certify_no_phi=True)
save_csv(features_df, "model_ready.csv", certify_no_phi=True)
# INFO  utils: wrote 1,240 rows x 27 cols -> duckdb table 'model_ready' at warehouse.duckdb [caller certified no PII/PHI]
```

Omitting `certify_no_phi` on a disk write (or passing anything but `True`) raises — certification is a deliberate control, not a default. A MotherDuck/cloud connection sends data *off the machine*, so it is locked down hardest: it additionally requires `allow_cloud_egress=True` and emits a warning (and `on_disk_con` refuses `md:` strings outright).

## Notebooks

| Notebook | What it covers |
|---|---|
| [`geospatial_demo.ipynb`](notebooks/geospatial_demo.ipynb) | End-to-end geocoding: config, NAD + TIGER ingest, normalize → match → block group, edge cases, top-N, 100K-row stress test |
| [`synth_kit_demo.ipynb`](notebooks/synth_kit_demo.ipynb) | End-to-end synthesis: PHI detection, privacy guarantee, numeric/categorical fidelity, aliases, overrides, reproducibility, SQL dialect swap |
| [`entity_resolution_demo.ipynb`](notebooks/entity_resolution_demo.ipynb) | Multi-system MPI: blocking (DOB + secondary), weighted fuzzy scoring, NetworkX clustering, name-variant and null-DOB edge cases, threshold tuning |
| [`feature_engineering_demo.ipynb`](notebooks/feature_engineering_demo.ipynb) | Windowed features: entity-date spine, RX/medical aggregates, custom SQL aggregations, feature joins, fan-out guardrails |
| [`end_to_end.ipynb`](notebooks/end_to_end.ipynb) | All four modules chained: synth_kit → geospatial → entity_resolution → feature_engineering into one ML-ready dataset |

## Development

```bash
poetry install --with dev
poetry run pytest
poetry run ruff check .
```

CI runs the test suite, a repo-wide `ruff check`, and a `pip-audit` security gate over the full dependency surface on every push and pull request.

## License

MIT
