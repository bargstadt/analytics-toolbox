# analytics_toolbox.geospatial

Offline US address-to-block-group geocoding pipeline. Normalizes, fuzzy-matches against the National Address Database, and assigns Census block group FIPS codes — all locally, no input data ever leaves the machine.

> **Architecture & design rationale:** this README is the runbook. For the pipeline internals, per-component design, and the rules of engagement, see [`ARCHITECTURE.md`](ARCHITECTURE.md).

## Privacy guarantee

No input address data is ever transmitted externally. The only network calls are one-time downloads of public reference data from `.gov` domains (NAD from `data.transportation.gov`, TIGER/ZCTA from `census.gov`). This holds even in HIPAA-constrained environments.

## Quick start

### 1. Install

```bash
pip install privacy-analytics-toolbox
```

### 2. Create a config file

```yaml
# config.yaml
# storage is shared at the root; geospatial settings nest under `geospatial:`.
storage:
  data_dir: ~/.local/share/analytics_toolbox/
  connection: ~/.local/share/analytics_toolbox/analytics_toolbox.duckdb
  # MotherDuck (no code change required — config-only):
  # connection: md:analytics_toolbox

geospatial:
  nad:
    states: [OR, WA]       # Two-letter state codes to ingest
    force_refresh: false
  tiger:
    vintage: 2024
    force_refresh: false
  matching:
    confidence_threshold: 90   # RapidFuzz WRatio score; 0–100
```

### 3. Ingest reference data (one-time)

**Disk space**: plan for ~25 GB free during NAD ingest (8.5 GB ZIP + ~14 GB extracted text file). Both are kept in `data_dir` so re-ingesting additional states later doesn't re-download. TIGER block groups are ~600 MB. The DuckDB database storing the indexed result is much smaller.

**Download time**: the NAD national ZIP (~8.5 GB) can take 15–30 min on a fast connection; it downloads once for all states. TIGER is a few minutes.

```bash
analytics-toolbox ingest-nad --config config.yaml
analytics-toolbox ingest-tiger --config config.yaml
```

Re-run with `--force-refresh` to replace existing data:

```bash
analytics-toolbox ingest-nad --config config.yaml --force-refresh
analytics-toolbox ingest-tiger --config config.yaml --force-refresh
```

### 4. Geocode addresses

```python
import pandas as pd
from analytics_toolbox.geospatial import geocode_address_table

addresses = pd.DataFrame([
    {"Street_Address": "123 SW Main St", "City": "Portland", "State": "OR", "Postal_Code": "97201"},
    {"Street_Address": "PO Box 999",     "City": "Portland", "State": "OR", "Postal_Code": "97201"},
])

result = geocode_address_table(addresses, "config.yaml")
print(result[["Street_Address", "block_group_fips", "match_method", "location_imputed"]])
```

## Output schema

`geocode_address_table` returns the original DataFrame with these columns appended:

| Column | Source | Description |
|---|---|---|
| `normalized_address_line_1` | normalizer | USPS-standardized street line, or `None` if parsing failed |
| `normalized_postal_code` | normalizer | Zero-padded ZIP/ZIP+4, or `None` if parsing failed |
| `is_standard_address` | normalizer | `False` for PO boxes, military addresses, or unparseable inputs |
| `address_flag` | normalizer | `"standard"`, `"military"`, or the scourgify exception name |
| `nad_id` | matcher | NAD `Join_ID` of the matched record, or `None` |
| `match_score` | matcher | RapidFuzz WRatio score (0–100), or `None` |
| `match_rank` | matcher | 1-based rank; always 1 when `top_n=1` (default) |
| `match_method` | matcher | `"nad_match"`, `"nad_match_county_block"`, `"postal_centroid"`, or `"non_standard"` |
| `matched_latitude` | matcher | Point lat from NAD or ZCTA centroid |
| `matched_longitude` | matcher | Point lon from NAD or ZCTA centroid |
| `matched_state_fips` | matcher | 2-digit state FIPS (from NAD county_fips), or `None` |
| `matched_county_fips` | matcher | County FIPS string from NAD, or `None` |
| `standardized_city` | matcher | Authoritative NAD city name for the ZIP, corrected from any input typo. `None` if no NAD city data for the postal code. Populated for all rows including non-standard and centroid fallbacks. |
| `standardized_state` | matcher | NAD state code (2-letter) for the ZIP, or `None` |
| `standardized_county` | matcher | NAD county name for the ZIP (corresponding to `standardized_city`), or `None` |
| `block_group_fips` | geocoder | 12-digit Census block group FIPS, or `None` if outside all polygons |
| `census_tract_fips` | geocoder | First 11 chars of `block_group_fips` |
| `tiger_vintage` | geocoder | TIGER release year used (from config) |
| `location_imputed` | geocoder | `True` when lat/lon is a ZCTA centroid rather than a street point |

## Match method reference

| `match_method` | Meaning | `location_imputed` |
|---|---|---|
| `nad_match` | Street-level match, score ≥ threshold, blocked by postal code | `False` |
| `nad_match_county_block` | Street-level match, score ≥ threshold, blocked by county (no ZIP available) | `False` |
| `postal_centroid` | Best score below threshold; used ZCTA centroid | `True` |
| `non_standard` | PO box / military / unparseable; used ZCTA centroid | `True` |
| `nad_match_sub_threshold` | `top_n > 1` only: candidate below threshold included for review | `False` |

## Using the pipeline steps individually

Each step is independently importable for partial pipeline use or debugging:

```python
from analytics_toolbox.geospatial.address_normalizer import normalize_addresses
from analytics_toolbox.geospatial.address_matcher import match_addresses
from analytics_toolbox.geospatial.address_geocoder import geocode_addresses
from analytics_toolbox._config import load_config

config = load_config("config.yaml").geospatial

# Step 1: normalize only
normalized = normalize_addresses(df)

# Step 2: match (requires ingest-nad to have run)
matched = match_addresses(normalized, config)

# Step 3: geocode (requires ingest-tiger to have run)
geocoded = geocode_addresses(matched, config)
```

### Top-N matching for human adjudication

Set `top_n > 1` to return multiple NAD candidates per input row. Each candidate gets a `match_rank` column (1 = best). Filter to `match_rank == 1` to recover the standard one-row-per-input shape:

```python
matched = match_addresses(normalized, config, top_n=3)
top_only = matched[matched["match_rank"] == 1]
```

Candidates below the confidence threshold in a top-N result set are labeled `"nad_match_sub_threshold"` so they're distinguishable from confident matches.

## Config reference

```yaml
storage:                         # shared at the root, not under geospatial
  data_dir: /path/to/dir/        # required; temp files go here during ingest
  connection: /path/to/db.duckdb # required; DuckDB file path or "md:analytics_toolbox"

geospatial:
  nad:
    states: [OR, WA, CA]          # required; list of two-letter state codes
    force_refresh: false           # optional; default false
    url: "..."                     # optional; override the NAD download URL
  tiger:
    vintage: 2024                  # optional; default 2024
    force_refresh: false           # optional; default false
  matching:
    confidence_threshold: 90       # optional; default 90 (RapidFuzz WRatio, 0–100)
```

## Troubleshooting

**`ValueError: Block group table 'tiger_block_groups_2024' not found`**
Run `analytics-toolbox ingest-tiger --config <path>` first.

**`ValueError: Missing required config key: nad.states`**
Ensure the config YAML has `nad: states: [OR, WA]` as a list, not a scalar string.

**NAD download fails / URL changed**
The NAD is distributed as a single national ZIP from `datahub.transportation.gov` (dataset `fc2s-wawr`). If the blob asset ID changes on a new release, check `https://www.transportation.gov/gis/national-address-database` for the current download link and override via `nad.url` in your config.

**Low match rates for a state**
Delete `nad_national.zip` and `nad_national.txt` from `data_dir`, then run `ingest-nad --config <path> --force-refresh` to re-download the latest NAD release. Match quality correlates with how current the NAD data is.

**All matches fall back to `postal_centroid`**
Lower `matching.confidence_threshold` in your config (try 80). If still poor, verify that `ingest-nad` ingested the correct state(s) and that the address format closely matches the NAD (`"123 NW MAIN ST"` style, not `"123 northwest main street"`).

## Development

```bash
# Install the toolbox + dev dependencies
poetry install --with dev

# Run tests
pytest tests/geospatial/ -v

# Lint
ruff check src/ tests/
```
