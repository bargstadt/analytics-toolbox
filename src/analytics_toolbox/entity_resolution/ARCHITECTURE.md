# entity_resolution — Architecture

How the config-driven Master Patient Index (MPI) is built. This is the **as-built** architecture; [`README.md`](README.md) is the runbook (install, usage, config, output) and [`SPEC.md`](SPEC.md) is the frozen design spec. All processing is in-memory and HIPAA-safe — the output holds only system IDs, never PHI.

## Pipeline

```
systems dict + config
  │
  ▼  _preprocess.normalize_fields()
  │    uppercase + strip string fields, normalize block_column to YYYY-MM-DD,
  │    optionally scourgify-normalize the address column
  ▼  _block.build_blocks()
  │    group by block_column; null → secondary block by secondary_block_columns;
  │    records with all-null keys are dropped (count logged, never the values)
  ▼  _match.score_pairs()
  │    for each system pair × block: weighted RapidFuzz WRatio over common fields;
  │    keep top_n_matches above match_threshold; fan-out guard raises if a block is too large
  ▼  _cluster.build_clusters()
  │    undirected graph (nodes = (system, id), edges = matched pairs weighted by score);
  │    connected components → entity clusters → wide output
  ▼
cluster DataFrame
```

The public `resolve()` orchestrator (in `resolve.py`) validates inputs and runs these four steps in order.

## Module structure

```
entity_resolution/
    __init__.py          # exports resolve()
    resolve.py           # resolve() orchestrator + input validation
    _config.py           # EntityResolutionConfig + default field weights
    _preprocess.py       # field standardization + optional scourgify address normalization
    _block.py            # primary + secondary blocking
    _match.py            # weighted RapidFuzz pairwise scoring + fan-out guard
    _cluster.py          # NetworkX connected-component clustering + wide output
    fixtures/
        multi_system.py  # make_mpi_fixture() — synthetic multi-system demo data
```

## Blocking strategy

Blocking limits the pairwise comparison space so the cross-join stays tractable:

1. **Primary** — group by `config.block_column` (default `DOB`). Within a primary block every record shares the block value.
2. **Secondary fallback** — records whose `block_column` is null fall through to a block keyed by `tuple(config.secondary_block_columns)` (default `[Last_Name, Postal_Code]`).
3. Records where **all** block keys are null are dropped — the count is logged, never the values.

A block key present in only one system produces a one-sided block: pairwise matching yields no pairs and no edges, which is correct.

## Scoring

Each common field is scored 0–100 by `rapidfuzz.fuzz.WRatio` (the same scorer geospatial uses for address matching), normalized to 0–1, then combined:

```
score = Σ(similarity_c × weight_c) / Σ(weight_c)   for c in common columns
```

- In a **primary** block all records share the same `block_column` value, so that field contributes similarity = 1.0 directly (not re-scored).
- In a **secondary** block the block column is null for every record, so it is excluded from the score entirely.
- Missing columns drop out of **both** numerator and denominator, so the threshold means the same thing regardless of which fields a given system provides.

Scoring is batched: `process.cdist` scores all candidate pairs in a block in one vectorized call rather than pair-by-pair.

## Fan-out guard

A placeholder DOB like `1900-01-01` can create a block so large the pairwise cross-join exhausts memory. Before scoring a block, `len(a) × len(b)` is checked against `config.max_block_pairs`; if exceeded, a `RuntimeError` is raised reporting the block **sizes** only — **never the block key value** (which could be a DOB). This is both a safety valve and a privacy control.

## Clustering

Matched pairs become edges in an undirected NetworkX graph: nodes are `(system_name, record_id)`, edge weight is the pair's score. Connected components are entity clusters. For each cluster:

- one column per `systems` key holds the matched record ID (or `None`),
- `avg_similarity` = mean edge weight in the component,
- `min_similarity` = minimum edge weight (the weakest link holding the cluster together).

Isolated records (no match above threshold) do not appear in the output. The result is sorted by descending `avg_similarity` and is deterministic across runs.

## Privacy by design

- No PHI in logs or error messages — only counts, column names, and scores. The fan-out error reports block sizes, never the key value.
- No disk writes; RapidFuzz and NetworkX are purely in-memory.
- The output DataFrame contains only system IDs and similarity scores — safe to export, inspect, or log.
- Input `systems` DataFrames may contain PHI; the caller owns their governance. This module never persists or transmits them.

## Key decisions & limitations

- **Deterministic scoring, not probabilistic.** v1 uses weighted RapidFuzz, not a Fellegi-Sunter model or ML-learned thresholds — chosen for transparency and zero training data.
- **One record per system per cluster.** When transitive merging pulls two records from the same system into one cluster, the strongest-linked record is kept and the surplus dropped (logged as a count). Frequent collisions usually signal a too-low threshold or genuine within-system duplicates.
- **Batch, not incremental.** Each call resolves the supplied systems from scratch; there is no persistent cluster graph to add to.
- **`top_n_matches` is directional** — applied per A-record within each system pair; see `score_pairs` for the rationale.
- **`match_threshold` has no default.** The right value is too domain-specific to guess; `load_config()` raises if it's absent while the `entity_resolution:` section is present.
