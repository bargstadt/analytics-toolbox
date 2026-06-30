# entity_resolution

Config-driven Master Patient Index (MPI) — link records for the same person across N source systems.

`resolve()` blocks candidate pairs by a shared key (default: date of birth), scores each pair with weighted RapidFuzz fuzzy matching across all common fields, clusters matches with NetworkX connected components, and returns a wide table with one row per entity and one column per system. All processing is in-memory and HIPAA-safe — the output contains only system IDs, never PHI.

> **Architecture & design:** this README is the runbook. For the pipeline, blocking, scoring, and clustering internals, see [`ARCHITECTURE.md`](ARCHITECTURE.md); for the original design spec, [`SPEC.md`](SPEC.md).

## Install

```bash
pip install privacy-analytics-toolbox
```

Address normalization uses `geospatial`'s scourgify-based normalizer, which ships in the single install (no NAD download required).

## Quick start

```python
from analytics_toolbox.entity_resolution import resolve
from analytics_toolbox._config import load_config

config = load_config("config.yaml")   # entity_resolution.match_threshold is required

# Each key is the ID column name for that system; each DataFrame must contain that column.
systems = {
    "medicaid_id": medicaid_df,
    "hospital_id": hospital_df,
    "pbm_id": pbm_df,
}

clusters = resolve(systems, config=config)
```

## API

```python
resolve(
    systems,        # dict[str, pd.DataFrame] — {id_column_name: DataFrame}, ≥ 2 entries
    *,
    config,         # AnalyticsToolboxConfig with a populated entity_resolution section
) -> pd.DataFrame
```

### `systems` format

A dict mapping the name of each system's ID column to that system's DataFrame:

```python
systems = {
    "system_a_id": system_a_df,   # system_a_df must contain a "system_a_id" column
    "system_b_id": system_b_df,
}
```

Each DataFrame should carry the fields referenced in `field_weights`. Missing fields are silently excluded from scoring for that pair — their weight drops out of the denominator — so heterogeneous schemas across systems are fine.

### Output

One row per entity cluster:

| system_a_id | system_b_id | system_c_id | avg_similarity | min_similarity |
|---|---|---|---|---|
| 456 | 879 | 1941 | 0.971 | 0.951 |

- One column per `systems` key — the matched record ID, or `None` if that system has no record in the cluster.
- `avg_similarity` — mean edge weight in the cluster's match graph.
- `min_similarity` — minimum edge weight (the weakest link holding the cluster together).

Records that match nothing above the threshold do **not** appear in the output. Output is sorted by descending `avg_similarity` and is deterministic across runs.

## Config schema

```yaml
entity_resolution:
  match_threshold: 0.80                              # REQUIRED — no default
  block_column: DOB                                  # primary blocking key
  secondary_block_columns: [Last_Name, Postal_Code]  # fallback when block_column is null
  top_n_matches: 1                                   # top matches kept per record per pair
  max_block_pairs: 500000                            # fan-out guard
  address_col: Address                               # input column fed to the normalizer
  field_weights:
    DOB: 1.0
    Last_Name: 0.35
    First_Name: 0.2
    Middle_Name: 0.05
    SSN: 0.2
    Phone: 0.05
    Address: 0.05
    City: 0.03
    County: 0.02
    Postal_Code: 0.05
```

`match_threshold` has no default — the right value is too domain-specific to guess. `load_config()` raises `ValueError` if it is absent while the `entity_resolution:` section is present.

## HIPAA / privacy

- No PHI in logs or error messages — only counts, column names, and scores.
- No disk writes; RapidFuzz and NetworkX are purely in-memory.
- The output DataFrame contains only system IDs and similarity scores, so it is safe to export, inspect, or log.
- Input `systems` DataFrames may contain PHI; the caller owns their governance (access control, audit, transmission). This library never persists or transmits them.

## Dependencies

This module's runtime dependencies ship in the single `privacy-analytics-toolbox` install (there are no per-module extras):

- `rapidfuzz>=3.9` — fuzzy scoring; shared with `geospatial`.
- `networkx>=3.0` — connected-component clustering (NumFocus, MIT, ~3 MB).

`pandas` is part of the shared core backbone.

## Limitations

- **Deterministic scoring only.** v1 uses weighted RapidFuzz, not a probabilistic Fellegi-Sunter model or ML-learned thresholds.
- **One record per system per cluster.** The wide format keeps the strongest-linked record when transitive merging pulls two records from the same system into one cluster; the surplus is dropped and logged as a count. Frequent collisions usually signal a too-low threshold or genuine within-system duplicates.
- **Batch, not incremental.** Re-linking adds no records to an existing cluster graph — each call resolves the supplied systems from scratch.
- **`top_n_matches` is directional** (applied per A-record within each system pair); see `score_pairs` for the rationale.
