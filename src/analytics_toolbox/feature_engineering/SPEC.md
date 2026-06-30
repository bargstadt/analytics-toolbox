# SPEC.md — Windowed Feature Engine

> **Status:** design spec, frozen at build (specced 2026-06-19) — kept as the original design record. For the as-built architecture, see [`ARCHITECTURE.md`](ARCHITECTURE.md); for usage, see [`README.md`](README.md).

**Module:** `analytics_toolbox.feature_engineering`
**Purpose:** A standardized, leakage-safe engine for point-in-time feature engineering over transactional data, computed server-side in DuckDB. The engine is the reusable core; project-specific pipelines are thin wrappers built on top of it.

---

## 1. Scope

### In scope (v1)
- A single core engine that computes windowed aggregate features off an entity-date **spine**.
- Multi-window fan-out in one call via DuckDB's `FILTER` clause.
- Strict point-in-time correctness (as-of date **exclusive**).
- Output at exactly the spine grain (left-joined back; nulls where no data — **no imputation**).
- Fail-loud validation of inputs, expressions, grain, and naming.
- A composition helper that inner-joins feature-set outputs on the shared spine grain.
- A synthetic Medicaid claims fixture + generator for tests and demos.
- 2 example feature functions built on the engine.

### Explicitly deferred (NOT v1)
- **Derived / cross-window layer** — ratios (30d ÷ 90d), deltas, share-of-total, recency (`as_of − MAX(date)`). These are arithmetic over already-computed columns and will be a separate, trivially leakage-safe layer. Do not entangle with the windowed engine.
- dbt-macro handoff surface for caller-owned materialization.
- Any imputation, scaling, or encoding (all post-processing, owned by the caller).
- Plugin registries, config DSLs, multi-backend abstraction. Build to demand, not to elegance.

---

## 2. Core concepts & contracts

**Spine** — the table the caller passes. It *fully defines the output grain*. Columns:
- One or more `entity_keys` (e.g. `member_id`) — required.
- Zero or more `group_cols` — optional additional grain dimensions (e.g. `drug_class`). These are part of the grain **and** are match keys on the base (so they must exist on both spine and base). Putting a category in the spine is how you get category-level / long-format features.
- One `as_of_col` — the date features are computed *as of*. Required.

**The spine defines the grain, fully and explicitly.** The engine never invents grain. If you want per-category features, build a spine that already contains the category. Output is always **exactly one row per spine row**.

**Base** — the transactional source being aggregated (table name, relation, or registered df). Must contain the join keys (`entity_keys` + `group_cols`) and a `base_date_col`.

**Point-in-time semantics (leakage contract):**
- Upper bound is **exclusive**: only `base_date < as_of`. An event dated *on* the as-of date never contributes.
- Lower bound is **inclusive**: `base_date >= as_of − N days`.
- Window = `[as_of − N days, as_of)`, evaluated **per spine row** against that row's own `as_of`.
- `window = "all"` means unbounded history before as-of: `base_date < as_of`, no lower bound.

This per-row evaluation is what lets one call serve a training spine with many as-of snapshots and a single-date scoring spine identically. No special cases.

**Composition contract:** every feature function returns exactly the spine rows. Generate the spine **once** and pass the same spine to every feature function; their outputs then inner-join losslessly on the spine grain.

---

## 3. Engine API

```python
from dataclasses import dataclass, field
import duckdb

@dataclass(frozen=True)
class Agg:
    name: str          # base feature name, e.g. "paid_sum" -> contributes rx__paid_sum_30d
    expr: str          # a SINGLE SQL aggregate call over base columns, e.g. "SUM(paid_amount)"

@dataclass(frozen=True)
class Guardrails:
    max_fanout_rows: int | None = 50_000_000   # estimated pre-aggregation rows
    on_fanout_exceed: str = "raise"             # "raise" | "warn"
    require_unique_spine: bool = True

def compute_features(
    spine,                                  # df | duckdb relation | table name
    base,                                   # df | duckdb relation | table name
    *,
    entity_keys: list[str],
    as_of_col: str,
    base_date_col: str,
    namespace: str,                         # lowercase, no underscores; collision guard + provenance
    aggregations: list[Agg],
    windows: list[int | str],               # e.g. [7, 30, 90, 365, "all"]
    group_cols: list[str] = (),             # extra grain dims; must exist on spine AND base
    con: duckdb.DuckDBPyConnection = None,
    guardrails: Guardrails = Guardrails(),
):
    """Returns a result at exactly the spine grain: spine columns + one feature
    column per (aggregation x window). Nulls where no events in window. No imputation."""
```

**A feature function is a thin wrapper** (this is the per-project / per-source unit):

```python
def rx_spend_features(spine, con, windows=(30, 90, 365)):
    return compute_features(
        spine, base="rx_claims",
        entity_keys=["member_id"],
        as_of_col="as_of_date",
        base_date_col="claim_date",
        namespace="rx",
        aggregations=[
            Agg("paid_sum",   "SUM(paid_amount)"),
            Agg("claims_cnt", "COUNT(*)"),
            Agg("ndc_ndist",  "COUNT(DISTINCT ndc_code)"),
            Agg("dsupply_sum","SUM(days_supply)"),
        ],
        windows=list(windows),
        con=con,
    )
```

**v1 expression constraint:** each `Agg.expr` must be a **single top-level SQL aggregate call** (`SUM`, `COUNT`, `COUNT(DISTINCT …)`, `MIN`, `MAX`, `AVG`, etc.). The engine wraps it as `{expr} FILTER (WHERE <window_pred>) AS {namespace}__{name}_{window}`. Compound expressions (e.g. `as_of − MAX(date)`, ratios) are **not** valid here — they belong to the deferred derived layer. Document this; surface violations as clear errors (see §6) rather than building a SQL parser to enforce it.

---

## 4. Generated SQL pattern

For one feature set, windows `[30, 90]`, namespace `rx`:

```sql
WITH base_f AS (                        -- §7 base pre-filter: never scan more than needed
  SELECT * FROM rx_claims
  WHERE claim_date >= (SELECT MIN(as_of_date) FROM spine) - INTERVAL 365 DAY  -- max window
    AND claim_date <  (SELECT MAX(as_of_date) FROM spine)
),
joined AS (                             -- inner join: drops zero-event entities (restored below)
  SELECT s.member_id, s.as_of_date, b.claim_date, b.paid_amount
  FROM spine s
  JOIN base_f b USING (member_id)       -- entity_keys + group_cols
  WHERE b.claim_date < s.as_of_date     -- upper bound EXCLUSIVE
),
agg AS (
  SELECT
    member_id, as_of_date,
    SUM(paid_amount) FILTER (WHERE claim_date >= as_of_date - INTERVAL 30 DAY) AS "rx__paid_sum_30d",
    SUM(paid_amount) FILTER (WHERE claim_date >= as_of_date - INTERVAL 90 DAY) AS "rx__paid_sum_90d"
  FROM joined
  GROUP BY member_id, as_of_date        -- full spine grain
)
SELECT s.*, a.* EXCLUDE (member_id, as_of_date)   -- left join back -> exactly spine rows
FROM spine s
LEFT JOIN agg a USING (member_id, as_of_date);     -- nulls where no events
```

The inner join in `joined` is only for aggregation efficiency; the final `LEFT JOIN` from the spine restores zero-event rows with null features. That is the mechanism delivering "exactly spine rows, nulls if no data."

---

## 5. Output naming convention

```
{namespace}__{base_name}_{window}
```

- `namespace`: lowercase, no underscores, mandatory per feature set (e.g. `rx`, `med`). Collision guard + provenance.
- `base_name`: author-supplied per `Agg` (e.g. `paid_sum`, `ndc_ndist`).
- `window`: `{N}d` for day windows, `all` for unbounded. Always last, always present.
- Separator is a **double** underscore after the namespace; single underscores within the body. Split on `__` to recover namespace; the remainder parses as `{base_name}_{window}`.

Examples: `rx__paid_sum_90d`, `med__ed_visits_cnt_30d`, `rx__ndc_ndist_365d`, `med__claims_cnt_all`.

Spine grain columns (`entity_keys`, `group_cols`, `as_of_col`) pass through unprefixed and are reserved — a `base_name` may not collide with them.

---

## 6. Fail loud, fail early

The engine validates before executing and raises clear, specific errors:

1. **Spine grain uniqueness** — if `require_unique_spine`, assert no duplicate rows on `(entity_keys + group_cols + as_of_col)`; error reports the duplicate count.
2. **Required columns present** — `entity_keys`, `as_of_col` on spine; join keys + `base_date_col` on base. Error names the missing column and which table.
3. **No nulls in grain columns** of the spine.
4. **Type normalization** — cast `as_of_col` and `base_date_col` to a consistent date/timestamp type at the boundary; error on incompatible types. Never let an implicit cast decide the leakage boundary.
5. **Window validation** — positive ints or `"all"`; no duplicates.
6. **Namespace validation** — lowercase, non-empty, no underscores.
7. **Aggregate expression failure** — wrap the DuckDB binder error and report *which namespace and which `Agg.name`/`expr`* failed. The raw binder error alone is unusable; the wrapper is the difference between a 30-second and a 30-minute debug.
8. **Naming collisions at assembly** (§8) — report the colliding column and the two namespaces it came from.

---

## 7. Fan-out guardrails

The range join duplicates each base event across every spine snapshot whose window contains it, *before* the GROUP BY collapses it. Two guards:

- **Base pre-filter (always on):** restrict base to `[min(as_of) − max_window, max(as_of))` before joining. Not optional — it's just correct and cheap.
- **Fan-out check (configurable):** estimate pre-aggregation row count (e.g. `COUNT(*)` on the join, or an estimate from spine × avg events-in-window). If it exceeds `Guardrails.max_fanout_rows`, `raise` (default) or `warn`. This is the early-warning system that keeps a large HHS table from silently exhausting memory.

Honest scale note for the spec reader: this design is excellent up to roughly tens of millions of base rows on a single machine. Past that, batch the spine by as-of window or reconsider — do not reach back for Spark mid-project.

---

## 8. Composition

```python
def join_features(frames: list, on: list[str]):
    """Inner-join feature-set outputs on the shared spine grain `on`.
    Asserts the ONLY columns shared across frames are the grain columns;
    any other shared name is a collision error naming the column and frames."""
```

Because every feature function returns exactly the spine rows, inner join == left join here (no row loss). The collision assertion is what protects the compose-everything story as the matrix grows wide.

---

## 9. Synthetic Medicaid fixture

**Fully synthetic. No PHI. No real codes mapped to real people.** Generated deterministically from a seed. Embedded DuckDB keeps it fully local — HIPAA-safe by construction, which is also why this is safe to use at HHS and to open source.

Tables:

- `members(member_id, dob, sex, eligibility_category, county_fips, enroll_start, enroll_end)`
- `rx_claims(claim_id, member_id, claim_date, ndc_code, drug_class, paid_amount, days_supply, quantity)`
- `med_claims(claim_id, member_id, claim_date, place_of_service, provider_type, dx_primary, paid_amount, claim_type)`
  - `place_of_service` ∈ {`inpatient`, `outpatient`, `ed`, `office`, `home`}

**Generator** `make_medicaid_fixture(con, n_members=1000, seed=42, start="2023-01-01", end="2025-12-31")` seeds deliberate edge cases for the test suite:
- A member with **zero claims** (null-fill test).
- A claim dated **exactly on** a known as-of date (exclusive-boundary test — must NOT appear).
- A claim **one day inside** a window edge (boundary test).
- A **high-volume** member (skew/fan-out test).
- Members appearing in **multiple monthly snapshots** (multi-as-of fan-out test).

Also provide `make_spine(con, as_of_dates, member_ids=None, group_cols=None)` to build training (many as-of) and scoring (single as-of) spines from the fixture.

---

## 10. Example feature functions (v1)

1. **`rx_spend_features`** — pharmacy spend/util: `paid_sum`, `claims_cnt`, `ndc_ndist`, `dsupply_sum` over `[30, 90, 365]`. Namespace `rx`. Add a variant with `group_cols=["drug_class"]` to demonstrate grain expansion / long format (spine must carry `drug_class`).
2. **`med_utilization_features`** — medical util: `claims_cnt`, `paid_sum`, `ed_visits_cnt` (`COUNT(*) FILTER` is applied by the window; the ED restriction lives in the expr as `COUNT(*) FILTER (WHERE place_of_service = 'ed')` → note this nests a filter; if that complicates the window-FILTER wrapping, model ED as a separate base view instead), `inpatient_cnt`, `provider_ndist` over `[30, 90, 365]`. Namespace `med`.

> Implementation note for #2: a `FILTER` inside the `expr` plus the engine's window `FILTER` is two filters on one aggregate. DuckDB allows only one `FILTER` per aggregate call. Resolve by folding the category predicate into the expr via `CASE` (`SUM(CASE WHEN place_of_service='ed' THEN 1 ELSE 0 END)`) so the engine's single window `FILTER` still wraps cleanly. Bake this pattern into the example.

---

## 11. Test plan

Build with Claude Code against the synthetic fixture. Golden-value tests with hand-computed expectations.

- **Leakage:** event on the as-of date never contributes (exclusive upper bound).
- **Lower boundary:** event exactly at `as_of − N` is included; at `as_of − N − 1` is excluded.
- **Null-fill:** zero-event entity returns its spine row with all-null features (no zeros).
- **Multi-window:** nested windows are monotonic where the aggregate implies it (e.g. `cnt_30d <= cnt_90d`).
- **Multi-as-of fan-out:** one base event correctly contributes to every snapshot whose window contains it.
- **Grain integrity:** output row count == spine row count, exactly; grain is unique.
- **Composition:** `join_features` over two feature sets preserves grain and raises on an injected name collision.
- **Guardrails:** fan-out cap raises/warns as configured; spine-uniqueness and missing-column errors fire with the right message.

---

## 12. Dependencies & packaging

- Runtime: `duckdb` for computation; `pandas` for DataFrame I/O. Both ship in the shared `privacy-analytics-toolbox` core backbone (single install, no per-module extras).
- `pytest` for tests.
- Lives in the `analytics-toolbox` Poetry mono-repo as the `feature_engineering` module: `pip install privacy-analytics-toolbox`.
- SQL-first throughout; the Python layer only generates and executes SQL and validates inputs.

---

## 13. Durable conventions (candidates for repo CLAUDE.md later)
- SQL-first; Python generates SQL, never row-wise pandas compute.
- As-of exclusive, lower bound inclusive — the leakage contract is non-negotiable.
- No imputation anywhere in the engine.
- Fail loud, fail early; every error names the offending input.
- Build to demand: no abstraction earns its place until three real feature functions need it.
