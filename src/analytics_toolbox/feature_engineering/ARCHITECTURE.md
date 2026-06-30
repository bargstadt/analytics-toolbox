# feature_engineering — Architecture

How the leakage-safe windowed feature engine is built. This is the **as-built** architecture; [`README.md`](README.md) is the runbook (install, API, usage) and [`SPEC.md`](SPEC.md) is the frozen design spec. The windowed engine is the reusable core; project-specific feature sets are thin wrappers on top of it.

## The core idea: one scan, many windows

Given an entity-date **spine** and a table of timestamped events, `compute_features()` computes windowed aggregates (counts, sums, distinct counts, conditionals — any SQL aggregate) at exactly the spine grain. Every window is produced in a **single DuckDB scan** using a `FILTER` clause per window, rather than one pass per window:

```sql
-- conceptually, per (aggregation × window):
SUM(paid_amount) FILTER (WHERE base_date >= as_of - INTERVAL '30 days'
                           AND base_date <  as_of)   AS rx__paid_sum_30d
```

The Python layer's whole job is to **generate and execute this SQL and validate inputs** — it does not move rows through pandas. This is the SQL-first discipline applied to feature engineering.

## Spine / base / grain contract

- **Spine** — one row per `(entity, as_of_date)`. It *defines the output grain*, fully and explicitly. The engine never invents grain; for per-category features, the category goes into the spine via `group_cols`.
- **Base** — timestamped events to aggregate (e.g. claims), joined to the spine on `entity_keys`.
- **Output** — exactly one row per spine row: every spine column plus one feature column per `(aggregation × window)`, named `{namespace}__{agg_name}_{window}d` (or `_all`). The features are left-joined back to the spine, so the grain is preserved exactly and spine rows with no matching events stay **null — there is no imputation.**

## Point-in-time / leakage contract

The whole point of the engine is that features can never see the future:

- **Exclusive upper bound** — only `base_date < as_of` contributes. An event dated *on* the as-of date never counts.
- **Window** = `[as_of − N days, as_of)`, evaluated **per spine row** against that row's own as-of date.
- **`window = "all"`** means unbounded history before as-of (`base_date < as_of`, no lower bound).

Because the window is evaluated per row, one call serves a multi-snapshot training spine and a single-date scoring spine identically — no special cases.

## Validation (fail-loud)

`compute_features` rejects bad inputs *before* running any aggregation (in `_validate.py`):

- namespace and window values are well-formed,
- aggregation names don't collide with grain columns,
- referenced columns exist on spine and base,
- no nulls in grain columns,
- the spine grain is unique (when `require_unique_spine`),
- estimated pre-aggregation fan-out is within `Guardrails.max_fanout_rows`.

The fan-out guard estimates the pre-aggregation row count and acts (`raise` or `warn`) before a pathological join can exhaust memory.

## Module structure

```
feature_engineering/
    __init__.py    # exports compute_features, join_features, Agg, Guardrails
    engine.py      # compute_features() — validation, fan-out guard, orchestration
    _sql.py        # build_feature_sql() — the single-scan FILTER-per-window SQL
    compose.py     # join_features()
    _types.py      # Agg, Guardrails
    _validate.py   # fail-loud input/grain/naming checks
    examples/      # reference feature sets (medicaid_features.py)
    fixtures/      # synthetic data for tests and demos (medicaid.py)
```

`examples/medicaid_features.py` shows the intended pattern — small, named wrappers around `compute_features` for pharmacy spend and medical utilization. `join_features()` inner-joins feature sets on the shared spine grain; generate the spine **once** and pass it to every feature function so the outputs join losslessly.

## Key decisions & limits

- **Derived / cross-window features are deliberately out of scope.** Ratios (30d ÷ 90d), deltas, and recency are trivially leakage-safe arithmetic over already-computed columns and belong in a separate layer — keeping them out keeps the windowed engine focused.
- **No imputation.** Missing data stays null; imputation is a modeling decision, not a feature-engine one.
- **DuckDB-backed.** Computation runs in DuckDB on the caller's machine; a future Spark backend is possible but not built. Nothing is transmitted off the machine.
