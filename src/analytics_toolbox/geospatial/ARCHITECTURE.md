# geospatial — Architecture

How the offline address-to-block-group geocoder is built, and the reasoning behind the load-bearing decisions. This is the **as-built** architecture; [`README.md`](README.md) is the runbook (install, usage, config, troubleshooting). `geospatial` is the flagship module and is built to a higher bar than the rest.

## Pipeline overview

Four stages, each independently importable, chained by `geocode_address_table()`:

```
normalize_addresses()      # USPS Pub 28 standardization + flag non-standard
       │                   #   address_normalizer.py
       ▼
match_addresses()          # Block by ZIP / county → fuzzy-match against NAD in DuckDB
       │                   #   address_matcher.py
       ▼
geocode_addresses()        # Point-in-polygon → block group FIPS via TIGER
       │                   #   address_geocoder.py
       ▼
geocode_address_table()    # Convenience wrapper for the full chain (pipeline.py)
```

Reference data (NAD, TIGER) is downloaded once into DuckDB via two setup commands (`ingest-nad`, `ingest-tiger`) and reused across all runs. Geocoding itself makes **no network calls** — it runs entirely against the local DuckDB and shapefiles.

## Rules of engagement

These apply to every component of the module and are non-negotiable:

1. **No input data leaves the machine, ever.** The only network calls anywhere in this module are one-time/periodic downloads of public reference data from `.gov` domains (NAD from `data.transportation.gov`; TIGER/ZCTA and block-group shapefiles from `census.gov`). The address data you feed in is never transmitted anywhere. This must hold even in a HIPAA-constrained environment.
2. **Dataframe operations: SQL first, pandas second.** Prefer doing transforms in DuckDB SQL over pandas where reasonable; pandas is the fallback, not the default. There's loose room for a future Spark backend, but that complexity isn't warranted now.
3. **Don't silently expand scope.** If a simpler structure than what's planned becomes apparent, raise it before implementing rather than just building it.

## Components

### `address_normalizer.py`

Wraps `usaddress-scourgify`'s `normalize_address_record` to standardize street-address formatting per USPS Pub 28.

Flags non-standard addresses two ways:
- `is_military` — a direct rule check on raw city/state (APO/FPO/DPO city; AA/AE/AP state), since scourgify does not flag these itself.
- Everything else (PO boxes, incomplete records, bad ZIPs, ambiguous parses) surfaces naturally as a scourgify `AddressNormalizationError` subclass, captured in `address_flag`.

Any row where `is_standard_address` is False should skip street-level matching downstream and use the postal-code centroid instead.

> **Never import `scourgify.get_geocoder_normalized_addr`** — it calls Google's geocoder API and would violate Rule 1. See [Key design decisions](#the-scourgify-geocoder-stub-load-bearing) for the related `geocoder` stub, which is load-bearing.

### `nad_preprocess_ingest.py`

Downloads the National Address Database from `data.transportation.gov` as a single national ZIP (~8.45 GB, ~97 million records, NAD Release 22 — no GDAL dependency). The ZIP is downloaded once to `storage.data_dir` and reused for all state ingests; **state filtering happens locally via DuckDB.**

- Column names changed in Release 22 (e.g. `OID_` → `nad_id`, `Zip_Code`, component street columns `Add_Number` / `St_PreDir` / `St_Name` / `St_PosTyp` / `St_PosDir`, `Post_City`); the street line is reconstructed from components.
- Stores `nad_city` (authoritative postal delivery city name from NAD `Post_City`, all-caps) alongside the street and ZIP fields — this is the source for city/county/state standardization in the matcher.
- After normalization, `_impute_missing_postal_codes` fills `normalized_postal_code` for NAD records that have lat/lon but no postal code, via a point-in-polygon join against the TIGER ZCTA polygons already in DuckDB. This requires `ingest_tiger` to have run first; if the ZCTA table is absent or predates the `geom_wkt` column, the step is silently skipped (logs a warning). **Recommended ingest order: `ingest_tiger` before `ingest_nad`.**
- Databases ingested before `nad_city` existed are migrated automatically (`ALTER TABLE ADD COLUMN`) on the next `ingest_nad` run; existing rows without city data produce `None` for the standardized columns until those states are re-ingested with `force_refresh=True`.

### `address_matcher.py`

Fuzzy-matches a normalized input address table against the ingested NAD.

**Two-tier blocking:**
1. **Primary** — block by `normalized_postal_code`.
2. **County fallback** — when postal code is absent or yields no NAD candidates, block by `(state, county)` using the input's `County` column (ILIKE match against NAD `county_fips`). The fallback is **per-row, not per-group**: addresses without a postal code are grouped under a `None` key and may span different counties, so each row resolves its own county candidates independently. County-block matches are tagged `match_method: "nad_match_county_block"` so consumers can apply lower confidence.

**Performance:** candidates and ZCTA centroids are cached per unique postal-code / county key, so DuckDB queries are O(unique keys), not O(rows). Scoring uses `rapidfuzz.process.cdist` to batch-score all addresses in a ZIP group against the candidate set in one vectorized call; original row order is restored via stable argsort before building the output DataFrame.

**City/county/state standardization:** for every output row (including non-standard and centroid fallbacks), queries distinct `(nad_city, county_fips, state)` from NAD for the postal code and fuzzy-matches the input city against them — correcting typos like `"Marshalltow"` → `"MARSHALLTOWN"`. Emits `standardized_city`, `standardized_state`, `standardized_county`; all three are `None` when NAD has no city data for the postal code or when county fallback was used.

Matches scoring below the confidence threshold fall back to the ZCTA centroid.

### `address_geocoder.py`

Downloads TIGER ZCTA and block-group shapefiles from `census.gov` and assigns block group FIPS.

- Block-group files are per-state (no national rollup as of TIGER 2024) — downloads only the states in `config.nad.states`.
- Point-in-polygon assignment uses **geopandas/shapely** (see [decision](#point-in-polygon-via-geopandasshapely-not-duckdb-spatial)).
- The ZCTA table stores polygon `geom_wkt` (for NAD postal-code imputation) plus centroid lat/lon (for fallback geocoding).
- Chooses between the matcher's lat/long and the ZCTA centroid based on the normalizer's `is_standard_address` flag and the matcher's confidence threshold.
- Output preserves every original input record untouched, plus the standardized address elements, lat/long, block group FIPS, postal-code-impute indicator, and the city/county/state standardization columns from the matcher.

## Key design decisions

### The scourgify `geocoder` stub (load-bearing)

scourgify's `normalize.py` does a bare, unconditional `import geocoder` at module load time, so `import scourgify` itself requires the `geocoder` package to be installed and importable — regardless of whether the geocoding function is ever called. `geocoder` pulls in `future` and `ratelim` (both effectively abandoned) and has a known invalid-escape-sequence issue ([DenisCarriere/geocoder#409](https://github.com/DenisCarriere/geocoder/issues/409)) — currently a warning, but the kind of thing that tends to become a hard error in a future Python version. An upstream fix exists ([GreenBuildingRegistry/usaddress-scourgify#35](https://github.com/GreenBuildingRegistry/usaddress-scourgify/pull/35)) but is unmerged on a low-activity repo, so we don't depend on that fork.

Instead, `geospatial/_scourgify_compat.py` stubs `geocoder` into `sys.modules` **before** any submodule imports scourgify (triggered from `geospatial/__init__.py`, which Python always runs first). Verified with the real `geocoder` package fully uninstalled that this does not change `normalize_address_record`'s behavior.

**Do not remove this import as a "cleanup" — it is load-bearing.** It is also a privacy control: it structurally prevents scourgify from reaching an external geocoding API.

### Point-in-polygon via geopandas/shapely, not DuckDB spatial

Block-group assignment uses geopandas/shapely rather than DuckDB's spatial extension — chosen as the more battle-tested option for production point-in-polygon work. (This is the one place the module deliberately steps outside the SQL-first default.)

### State filtering in DuckDB, not pandas

The ~97M-row NAD is filtered to the configured states in DuckDB; pandas never holds the national table. Consistent with Rule 2.

## Data & storage

Everything persists under `storage.data_dir` / `storage.connection` (a local DuckDB file, or MotherDuck via a config-only change):

| Artifact | Source | Notes |
|---|---|---|
| `nad_national.zip` / `.txt` | public NAD | downloaded once, reused for all state ingests |
| NAD state tables | derived from the ZIP | normalized + postal-code-imputed; the match target |
| TIGER ZCTA table | public TIGER | polygon `geom_wkt` + centroid lat/lon |
| TIGER block-group tables | public TIGER | per-state; the point-in-polygon target |

Input address data is **not** written to any of these — it is processed in memory and returned in the output DataFrame. The reference data above is entirely public.
