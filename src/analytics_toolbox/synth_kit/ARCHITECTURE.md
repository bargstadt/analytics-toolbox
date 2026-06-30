# synth_kit — Architecture

How the SQL-first synthetic data generator is built. This is the **as-built** architecture; [`README.md`](README.md) is the runbook (install, API, the PHI-type and dialect reference tables) and [`SPEC.md`](SPEC.md) is the frozen design spec. The privacy guarantee below is the reason the module exists, and it drives every design choice.

## The core guarantee: two phases, no raw rows crossing

`synthesize()` runs two strictly separated phases. **No raw rows ever cross from Phase 1 to Phase 2** — the only values that leave the database are aggregate statistics and column metadata. This holds even if the caller makes a mistake, because there is no code path that loads raw rows into Python.

```
        ┌─────────────────────── Phase 1: server-side SQL ───────────────────────┐
query → │ schema + COUNT(*) · numeric profile (MIN/MAX/AVG/STDDEV/9×PERCENTILE)   │
        │ categorical profile (GROUP BY … LIMIT 500, PHI cols excluded)           │
        │ PHI detection (column-name patterns — zero data access)                 │
        └────────────────────────────────┬───────────────────────────────────────┘
                                          │  aggregates + metadata only
        ┌─────────────────────────────────▼─────── Phase 2: Python ──────────────┐
        │ numeric: numpy.interp over 11-point CDF, clipped, null rate preserved   │
        │ categorical: numpy.random.choice with source proportions / aliases      │
        │ PHI: Faker / sequential IDs — never sampled from real values            │
        └────────────────────────────────┬───────────────────────────────────────┘
                                          ▼
                          pd.DataFrame (same schema, all PHI replaced)
```

No intermediate data is written to disk; no network calls — it works fully air-gapped.

## Phase 1 — server-side profiling

All queries wrap the caller's query in a CTE (`SELECT … FROM (<query>) _q`) so raw rows are never fetched:

- **Schema + row count** — `information_schema.columns` + `COUNT(*)`.
- **Numeric profiling** (per numeric column) — `MIN`, `MAX`, `AVG`, `STDDEV`, and a 9-point `PERCENTILE_CONT` ladder (p01…p99), plus null count. ANSI SQL, so it runs on DuckDB, SQL Server, and PostgreSQL alike.
- **Categorical profiling** (non-PHI columns only) — `GROUP BY col ORDER BY cnt DESC LIMIT 500`. PHI columns are **never** queried for distinct values.
- **PHI detection** — column names matched against the pattern registry. No data access at all.

## Phase 2 — synthesis from aggregates

No SQL in this phase:

- **Numeric** — sample via `numpy.interp` across the 11-point empirical CDF (p01…p99 + min/max), clipped to `[min, max]`, with the source null rate preserved.
- **Categorical** — sample from the value distribution proportional to source counts; if `categorical_aliases` is supplied, the alias list replaces the source labels while preserving proportions.
- **PHI** — replaced wholesale, never sampled from real values (see below).

## PHI detection & replacement design

- **Detection** is column-name pattern matching against a registry (lowercased names, first match wins). `phi_overrides` are applied after auto-detection and can add or change a column's type; `suppress_phi` removes a column from detection and emits a warning per suppressed column for audit. The detected PHI map is logged before Phase 2 so the caller can verify it. The full pattern→type and type→replacement tables live in [`README.md`](README.md).
- **Replacement** maps each PHI type to a strategy: Faker for free-text types (name, email, phone…), and **format-stable sequential integers** for ID types (`MRN{i:08d}`, `MBR{i:010d}`, SSN as `{i:09d}` — deliberately *not* a valid SSN format, to avoid minting real-looking SSNs). Date PHI is shifted by a per-row random offset, preserving relative gaps within a row.

## SQL dialect dispatch

Profiling SQL is ANSI by default. `_profile.py` carries a dispatch keyed on `engine.dialect.name` for the few places ANSI is insufficient (e.g. SQL Server's `STDEV` vs `STDDEV`). Unsupported dialects raise `UnsupportedDialectError` with a clear message rather than silently producing wrong results.

## NAD address integration (soft)

`geospatial` ships in the same install, so when the result set has a state column and NAD has been ingested for that state, `address`-type PHI is filled with **real NAD street addresses** sampled for the dominant state — more realistic than Faker. It falls back to `faker.street_address()` when NAD is unavailable. This is a soft dependency: synth_kit works without any NAD data.

## Module structure

```
synth_kit/
    __init__.py      # exports synthesize()
    _public.py       # synthesize() — Phase 1 + Phase 2 orchestration
    _detect.py       # PHI pattern registry + detect_phi()
    _profile.py      # Phase 1: schema, numeric profiling, categorical profiling
    _synthesize.py   # Phase 2: numeric interpolation, categorical sampling
    _phi.py          # PHI replacement: Faker, sequential IDs, NAD soft import
    _types.py        # ColumnProfile, CategoricalProfile, PhiMap, SynthConfig
```

## Key decisions & limits

- **Plausible, not faithful.** The 11-point CDF does not capture multimodality, skew, or cross-column correlations — each column is synthesized independently. The target is privacy compliance, not statistical fidelity.
- **Categorical profiling caps at 500 distinct values**; the long tail of very-high-cardinality columns is undersampled.
- **Non-PHI date columns** are treated as categorical (sampled from distinct source values), so very high-cardinality date ranges may not be fully covered.
- **SQL injection surface** is contained: the caller's `query` is only ever wrapped in a CTE, never string-formatted into a larger query, and user-controlled values use parameterized queries via SQLAlchemy.
