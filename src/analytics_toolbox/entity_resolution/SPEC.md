# SPEC.md — Entity Resolution (MPI)

> **Status:** design spec, frozen at build — kept as the original design record. For the as-built architecture, see [`ARCHITECTURE.md`](ARCHITECTURE.md); for usage, see [`README.md`](README.md).

**Module:** `analytics_toolbox.entity_resolution`
**Purpose:** A config-driven Master Patient Index (MPI) engine that links records for the
same person across N source systems. Blocks candidates by a shared key (default: DOB),
scores pairs via weighted RapidFuzz fuzzy matching, clusters matches via NetworkX connected
components, and returns a wide cluster table with one column per system.

---

## 1. Scope

### In scope (v1)
- Multi-system blocking by a configurable column (DOB default) with null fallback.
- Pairwise fuzzy scoring across all common fields using weighted RapidFuzz WRatio.
- Address normalization via geospatial `normalize_addresses()` (scourgify-based, no NAD required).
- NetworkX connected-component clustering to handle transitive matches.
- Wide-format output: one row per entity cluster, one column per source system's ID.
- Fan-out guard to catch pathologically large DOB blocks early.
- Full HIPAA-safe design: no PHI in logs, no disk writes, output contains only system IDs.

### Explicitly deferred (NOT v1)
- Probabilistic scoring / Fellegi-Sunter model.
- Machine-learning-based match thresholds.
- Incremental / streaming linking (add new records to existing cluster graph).
- Feedback / adjudication loop for review of borderline pairs.
- Integration with external MDM or golden-record systems.

---

## 2. HIPAA / Privacy guarantees

- No PHI in log messages. Only counts, block key hashes (if needed), and scores are logged.
- No disk writes during processing — all operations are in-memory.
- Output DataFrame contains only system IDs (not PHI fields). Safe to export, inspect, or log.
- RapidFuzz and NetworkX are purely in-memory libraries with no file I/O.
- SSN, name, DOB, and address fields are compared in-memory only; their values never appear
  in logs, errors (only counts and column names), or output.
- NOTE: Input `systems` DataFrames may contain PHI. The caller is responsible for handling
  those DataFrames per their data governance obligations (access control, audit logging,
  transmission restrictions). This library never writes them to disk or transmits them.

---

## 3. Public API

```python
from analytics_toolbox.entity_resolution import resolve
from analytics_toolbox._config import load_config

config = load_config("config.yaml")

result: pd.DataFrame = resolve(
    systems,   # dict[str, pd.DataFrame]  — {system_id_col_name: DataFrame}
    config=config,
)
```

### `systems` format

A dict mapping the name of the ID column to the DataFrame for that system:

```python
systems = {
    "system_a_id": system_a_df,   # df must contain "system_a_id" as a column
    "system_b_id": system_b_df,
    "system_c_id": system_c_df,
}
```

Each DataFrame should contain the fields referenced in `field_weights`. Missing fields
are silently excluded from scoring for that pair (their weight is dropped from the
denominator), so heterogeneous schemas across systems are fine.

### Output

One row per entity cluster. Columns:
- One column per system_id key (values are the actual IDs, None if not in cluster)
- `avg_similarity`: mean edge weight in the cluster graph for this component
- `min_similarity`: minimum edge weight (worst-case pair in the component)

```
| system_a_id | system_b_id | system_c_id | avg_similarity | min_similarity |
|-------------|-------------|-------------|----------------|----------------|
| 456         | 879         | 1941        | 0.971          | 0.951          |
| None        | None        | None        | ...            | ...            |
```

Records that match no other record across any system pair do NOT appear in the output.

---

## 4. Config schema

```yaml
entity_resolution:
  block_column: DOB                            # column to group candidates by
  secondary_block_columns: [Last_Name, Postal_Code]  # fallback when block_column is null
  match_threshold: 0.80                        # REQUIRED — minimum weighted score for a candidate pair
  top_n_matches: 1                             # top N matches per record per pairwise comparison
  max_block_pairs: 500000                      # fan-out guard: raise if block_a × block_b exceeds this
  address_col: Address                         # input column name mapped to geospatial normalizer
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

`match_threshold` has no default. `load_config()` raises `ValueError` if it is absent
from the YAML when the `entity_resolution:` section is present.

---

## 5. Pipeline

```
Input: systems dict + AnalyticsToolboxConfig
  │
  ▼
_preprocess.normalize_fields()
  - Uppercase string fields in field_weights keys
  - Strip whitespace from all string columns
  - Normalize block_column dates to YYYY-MM-DD strings
  - If Address column present: run geospatial.normalize_addresses() (scourgify)
    and substitute normalized_address_line_1 as the Address value
  │
  ▼
_block.build_blocks()
  - Group records across systems by block_column value
  - Null block_column → secondary block by tuple(secondary_block_columns)
  - Records where ALL block keys are null → excluded (count logged, no PHI)
  │
  ▼
_match.score_pairs()
  - For each combinations(systems, r=2) × each block:
    - Find common columns = (system_a.cols ∩ system_b.cols ∩ field_weights.keys)
    - Score each column via RapidFuzz process.cdist (WRatio, score_cutoff=0)
    - Block column within block: sim = 1.0 (always exact match within block)
    - Weighted score = Σ(sim_c × w_c) / Σ(w_c for available cols)
    - Fan-out guard: len(a) × len(b) > max_block_pairs → RuntimeError (counts only)
    - For each record in a: keep top_n_matches from b above threshold
  │
  ▼
_cluster.build_clusters()
  - NetworkX undirected graph: nodes = (system_name, record_id), edges = (a, b, weight)
  - nx.connected_components() → entity clusters
  - Per component: avg_similarity = mean(edge weights), min_similarity = min(edge weights)
  - Pivot to wide output DataFrame
  │
  ▼
Output: cluster DataFrame
```

---

## 6. Scoring details

**Column scoring:** Each field is scored 0.0–100.0 by `rapidfuzz.fuzz.WRatio`, then divided
by 100 to normalize to 0.0–1.0. This is the same scorer used in geospatial address matching.

**Block column treatment:** Within a block, all records share the same block_column value,
so block column similarity is always 1.0. This is not re-computed via RapidFuzz — it is
assigned directly.

**Weight normalization for missing columns:**
```
score = Σ(sim_c × weight_c for c in common_cols) / Σ(weight_c for c in common_cols)
```
If `common_cols` is empty (no shared fields), the pair is skipped (score = 0.0, below threshold).

**Secondary block scoring:** When DOB is null and secondary blocking is used, DOB does not
contribute to the score (it's not in `common_cols` for that pair).

---

## 7. Fan-out guard

Blocking on DOB can produce very large blocks if many records share the same DOB (e.g.
`1900-01-01` as a placeholder for unknown birthdate). The pairwise cross-join within a
block is O(len_a × len_b), which can exhaust memory.

Guard: before scoring, estimate `len(block_a) × len(block_b)`. If this exceeds
`config.max_block_pairs`, raise `RuntimeError` reporting the block sizes only — never the
block key value, so no PHI appears in the error message.

---

## 8. Dependencies

```toml
"entity-resolution" = [
    "rapidfuzz>=3.9",
    "networkx>=3.0",
]
```

`pandas>=2.2` is a core dep (already in `[project.dependencies]`).
`rapidfuzz` is also a `geospatial` dep — no duplication risk, pip deduplicates.
`networkx` is new; flagged per working agreement. NumFocus project, MIT license, ~3 MB.

Address normalization uses `geospatial.normalize_addresses`, which ships in the single
install (no per-module extras). All fields, including the normalized address, participate
in scoring.

---

## 9. Test plan

Tests are in `tests/entity_resolution/`. Fixture: `make_mpi_fixture()` from
`src/analytics_toolbox/entity_resolution/fixtures/multi_system.py`.

- **Name variants cluster together:** John / Johny / Johnathan Smith → one cluster.
- **Clear non-match stays separate:** Smith cluster and Smooth cluster never merge.
- **Missing field:** system without Postal_Code participates; Postal_Code excluded from its scores.
- **Null DOB fallback:** records without DOB blocked by Last_Name + Postal_Code, matched correctly.
- **Transitive link:** A matches B, B matches C → A-B-C all in one cluster even if A-C score is below threshold.
- **Fan-out guard:** injected block with `len_a × len_b > max_block_pairs` raises RuntimeError.
- **Output shape:** row count = cluster count; column count = len(systems) + 2.
- **avg_similarity ≥ min_similarity:** always true.
- **Isolated record:** record with no match above threshold does not appear in output.
