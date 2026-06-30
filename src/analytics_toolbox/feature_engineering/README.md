# feature_engineering

Leakage-safe windowed aggregate features over DuckDB.

Given an entity-date **spine** and a table of timestamped events, `compute_features()` computes windowed aggregates (counts, sums, distinct counts, conditionals — any SQL aggregate) with strict point-in-time correctness. All windows are produced in a single DuckDB scan via `FILTER`, and the output lands at exactly the spine grain. The windowed engine is the reusable core; project-specific feature sets are thin wrappers on top of it.

> **Architecture & design:** this README is the runbook. For the single-scan SQL, the point-in-time/leakage contract, validation, and module internals, see [`ARCHITECTURE.md`](ARCHITECTURE.md); for the original design spec, [`SPEC.md`](SPEC.md).

## Install

```bash
pip install privacy-analytics-toolbox
```

## Quick start

```python
import duckdb
from analytics_toolbox.feature_engineering import Agg, compute_features, join_features

con = duckdb.connect()
# spine: one row per (member_id, as_of_date) — defines the output grain
# rx_claims: timestamped events with member_id + claim_date

rx = compute_features(
    spine, "rx_claims",
    entity_keys=["member_id"],
    as_of_col="as_of_date",
    base_date_col="claim_date",
    namespace="rx",
    aggregations=[
        Agg("paid_sum", "SUM(paid_amount)"),
        Agg("claims_cnt", "COUNT(*)"),
        Agg("ndc_ndist", "COUNT(DISTINCT ndc_code)"),
    ],
    windows=[30, 90, 365],
    con=con,
)
# Columns: member_id, as_of_date, rx__paid_sum_30d, rx__claims_cnt_30d, ... rx__ndc_ndist_365d
```

## API

### `compute_features`

```python
compute_features(
    spine,                  # pd.DataFrame | table name — defines the output grain
    base,                   # pd.DataFrame | table name — timestamped events to aggregate
    *,
    entity_keys,            # list[str]   — join keys (e.g. ["member_id"])
    as_of_col,              # str         — spine column holding the as-of date
    base_date_col,          # str         — base column holding the event date
    namespace,              # str         — prefix for output feature columns
    aggregations,           # list[Agg]   — the aggregate expressions
    windows,                # list[int | str] — lookback days, or "all" for full history
    group_cols=(),          # list[str]   — extra grain dimensions (e.g. ["drug_class"])
    con=None,               # duckdb connection (created if omitted)
    guardrails=None,        # Guardrails  — fan-out protection
) -> pd.DataFrame
```

Output is **exactly one row per spine row**: every spine column plus one feature column per `(aggregation × window)`, named `{namespace}__{agg_name}_{window}d` (or `_all`). Spine rows with no matching events get null feature values — there is **no imputation**.

### `Agg`

```python
Agg(name, expr)   # e.g. Agg("ed_visits_cnt", "SUM(CASE WHEN place = 'ED' THEN 1 ELSE 0 END)")
```

`expr` is any SQL aggregate the base table supports — `SUM`, `COUNT`, `COUNT(DISTINCT ...)`, `AVG`, percentiles, or a conditional `CASE` aggregate.

### `join_features`

```python
join_features(frames, on)   # inner-join feature sets on the shared spine grain
```

Every frame must carry exactly the `on` grain columns plus its own features. Any non-grain column appearing in two frames is an error — use distinct namespaces to keep them apart. Generate the spine **once** and pass it to every feature function so their outputs join losslessly.

### `Guardrails`

```python
Guardrails(
    max_fanout_rows=50_000_000,   # estimate before running; act if exceeded
    on_fanout_exceed="raise",     # "raise" or "warn"
    require_unique_spine=True,     # fail if the spine grain has duplicate rows
)
```

## Examples and fixtures

```python
from analytics_toolbox.feature_engineering.examples.medicaid_features import (
    med_utilization_features,
    rx_spend_features,
)
from analytics_toolbox.feature_engineering.fixtures.medicaid import (
    make_medicaid_fixture,
    make_spine,
)
```

`examples/medicaid_features.py` shows the intended pattern — small, named wrappers around `compute_features` for pharmacy spend and medical utilization. `fixtures/medicaid.py` generates synthetic claims for testing and demos. See [`notebooks/feature_engineering_demo.ipynb`](../../../notebooks/feature_engineering_demo.ipynb) for a full walkthrough.

## Notes and limits

A summary; see [`ARCHITECTURE.md`](ARCHITECTURE.md) for the full reasoning.

- **The spine defines the grain, fully and explicitly.** The engine never invents grain — for per-category features, put the category in the spine via `group_cols`.
- **Derived / cross-window features** (ratios like 30d ÷ 90d, deltas, recency) are deliberately *not* part of the windowed engine.
- **DuckDB-backed.** Computation runs in DuckDB on the caller's machine; a future Spark backend is possible but not built.
