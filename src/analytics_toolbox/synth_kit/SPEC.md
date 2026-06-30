# synth_kit Spec

> **Status:** design spec, frozen at build (specced 2026-06-17) — kept as the original design record. For the as-built architecture, see [`ARCHITECTURE.md`](ARCHITECTURE.md); for usage, see [`README.md`](README.md).

**Package:** `privacy-analytics-toolbox` (imports as `analytics_toolbox.synth_kit`)
**Install:** `pip install privacy-analytics-toolbox` (single install — no per-module extras)
**Scope:** SQL-first synthetic data generation for HIPAA/GDPR-constrained environments.

---

## 1. Objective

Given a SQL query and a database connection, produce a fully synthetic version of the result set — same schema, statistically plausible values, all PHI replaced — without any raw PII ever entering Python memory.

**Primary users:** Analytics practitioners (data scientists, engineers) working in sensitive data environments (healthcare, financial, government) who need to share or test against data that cannot leave the secure environment in its original form. The tool must be deployable in air-gapped environments with no internet access.

**Core guarantee:** All profiling happens server-side via SQL aggregates. Raw rows are never pulled into Python. The only values that cross the boundary are aggregate statistics (counts, percentiles, distinct value distributions) and column metadata. This guarantee holds even if the caller makes a mistake — the API is designed so there is no code path that loads raw PII.

---

## 2. Privacy and Security Boundaries

These are non-negotiable. They take precedence over ergonomics.

**Always:**
- All statistical profiling runs as SQL aggregates on the server. Raw rows are never fetched into Python.
- No network calls at runtime. The package must work in a fully air-gapped environment.
- No intermediate data is written to disk. Everything is in-memory Python structures derived from aggregates.
- PHI columns are never sampled from real values — they are replaced wholesale by Faker or NAD data.
- Log the detected PHI map before synthesis begins so the caller can verify it.
- Dependencies are pinned in `poetry.lock`. No runtime `pip install` or dynamic imports.

**Ask first (flag before implementing):**
- Any new runtime dependency.
- Any change to the PHI detection patterns that could cause false negatives (missed PHI).
- Any feature that would require loading raw data into Python (e.g. "exact match" categorical synthesis).

**Never:**
- Fetch raw rows from the source query into Python memory.
- Write PII/PHI to disk, even temporarily.
- Make network calls.
- Use `pickle` or deserialize untrusted data.
- Silently skip PHI detection for a column — warn loudly if a column matches a PHI pattern but the user suppressed it.

---

## 3. Public API

One primary function. Everything else is internal.

```python
from analytics_toolbox.synth_kit import synthesize

result: pd.DataFrame = synthesize(
    engine,             # sqlalchemy.Engine — connection to the source database
    query,              # str — SQL query whose result set to synthesize
    *,
    n_rows=None,        # int | None — number of synthetic rows (default: same count as source)
    categorical_aliases=None,   # dict[str, list[str]] | None — {col: [replacement values]}
    phi_overrides=None,         # dict[str, str] | None — {col: phi_type}, adds to auto-detection
    suppress_phi=None,          # list[str] | None — columns to exclude from PHI detection
    random_seed=None,           # int | None — for reproducible output
) -> pd.DataFrame
```

### Parameters

| Parameter | Type | Purpose |
|---|---|---|
| `engine` | `sqlalchemy.Engine` | Source database. Dialect-agnostic — SQL Server, DuckDB, PostgreSQL, etc. |
| `query` | `str` | The SQL query to synthesize. Caller is responsible for query safety. |
| `n_rows` | `int \| None` | Rows to generate. `None` = match source row count. |
| `categorical_aliases` | `dict[str, list[str]] \| None` | User-supplied replacement values for categorical columns. Sampling is proportional to source value distribution. |
| `phi_overrides` | `dict[str, str] \| None` | Force-classify columns as a specific PHI type. Merges with auto-detected PHI. Keys are column names, values are PHI type strings (e.g. `"name"`, `"dob"`, `"ssn"`). |
| `suppress_phi` | `list[str] \| None` | Exclude named columns from PHI auto-detection. Emits a warning for each suppressed column. |
| `random_seed` | `int \| None` | Seed for numpy/Faker RNGs. Enables reproducible output. |

### Return value

`pd.DataFrame` with the same column names and dtypes as the source query result. Row count equals `n_rows` if specified, otherwise matches source. All PHI columns replaced. All numeric columns drawn from synthetic distributions. All categorical columns sampled from value distributions (or `categorical_aliases` if provided).

---

## 4. Processing Pipeline

Two strict phases. No data crosses from Phase 1 to Phase 2.

### Phase 1 — Server-side profiling (SQL only, no raw data in Python)

All queries run against a CTE or subquery wrapping the caller's query. No raw rows are fetched.

**Step 1.1 — Schema + row count**
```sql
SELECT column_name, data_type FROM information_schema.columns WHERE ...
SELECT COUNT(*) FROM (<query>) _q
```
Produces: column names, SQL types, total row count.

**Step 1.2 — Numeric profiling** (one query per numeric column, or batched)
```sql
SELECT
    COUNT(*)                                         AS n_total,
    COUNT(col)                                       AS n_non_null,
    MIN(col)                                         AS val_min,
    MAX(col)                                         AS val_max,
    AVG(CAST(col AS FLOAT))                          AS val_mean,
    STDDEV(CAST(col AS FLOAT))                       AS val_stddev,
    PERCENTILE_CONT(0.01) WITHIN GROUP (ORDER BY col) AS p01,
    PERCENTILE_CONT(0.05) WITHIN GROUP (ORDER BY col) AS p05,
    PERCENTILE_CONT(0.10) WITHIN GROUP (ORDER BY col) AS p10,
    PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY col) AS p25,
    PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY col) AS p50,
    PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY col) AS p75,
    PERCENTILE_CONT(0.90) WITHIN GROUP (ORDER BY col) AS p90,
    PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY col) AS p95,
    PERCENTILE_CONT(0.99) WITHIN GROUP (ORDER BY col) AS p99
FROM (<query>) _q
```
`PERCENTILE_CONT ... WITHIN GROUP` is ANSI SQL and supported by SQL Server, DuckDB, and PostgreSQL.

**Step 1.3 — Categorical profiling** (non-PHI columns only)
```sql
SELECT col, COUNT(*) AS cnt
FROM (<query>) _q
GROUP BY col
ORDER BY cnt DESC
LIMIT 500
```
Produces: value → count map. PHI columns are excluded — they are never queried for distinct values.

**Step 1.4 — PHI detection**
Column names are matched against the PHI pattern registry (see Section 6). No data access. Produces: `{column_name: phi_type}` map. Logged before Phase 2 begins.

---

### Phase 2 — Synthesis (Python only, from aggregate stats)

No SQL calls in this phase.

**Numeric columns:** Sample `n_rows` values using numpy percentile interpolation across the 11-point empirical CDF (p01 through p99 + min/max). Null rate preserved (sample `null_pct * n_rows` NaN values). Clipped to [min, max].

**Categorical columns (non-PHI):** Sample from the value distribution (proportional to source counts). If `categorical_aliases` is provided for this column, the alias list replaces the source values while preserving their relative proportions.

**PHI columns:** Replace with Faker-generated values appropriate to the PHI type. See Section 6 for the type → Faker method mapping. Date PHI is shifted by a random per-row offset (preserving relative ordering within a row where needed). ID-type PHI (MRN, SSN, account numbers) is replaced with sequential integers padded to the same format.

**Address PHI:** When the NAD database is available (`geospatial` ships in the same install), sample real addresses from NAD filtered to the state found in the result set's state column (if any). Falls back to `faker.address()` when NAD is unavailable.

---

## 5. Module Structure

```
src/analytics_toolbox/synth_kit/
    __init__.py          # exports synthesize()
    _profile.py          # Phase 1: SQL profiling queries (dialect-aware via SQLAlchemy)
    _detect.py           # PHI column auto-detection (pattern registry + override merge)
    _synthesize.py       # Phase 2: synthesis engine (numeric, categorical, PHI)
    _phi.py              # PHI replacement: Faker wrappers + NAD address integration
    _types.py            # ColumnProfile, CategoricalProfile, SynthConfig dataclasses
```

No subpackages. No class hierarchies — composable functions, dataclasses for structured data.

---

## 6. PHI Detection and Replacement

### Auto-detection pattern registry

Patterns match against lowercased column names. First match wins. User `phi_overrides` are applied after and can add or change type for any column.

| PHI Type | Column name patterns (regex, case-insensitive) |
|---|---|
| `name` | `(first\|last\|full\|patient\|member\|provider)_?name`, `^name$`, `^fname$`, `^lname$` |
| `dob` | `(date_?of_?birth\|dob\|birth_?date\|birthdate)` |
| `date_phi` | `(admit\|discharge\|death\|procedure\|service\|visit)_?date`, `date_?of_?(death\|service\|admission\|discharge)` |
| `phone` | `(phone\|telephone\|fax\|mobile\|cell)(_?number\|_?num\|_?no)?` |
| `email` | `(email\|e_?mail)(_?address)?` |
| `ssn` | `(ssn\|social_?security(_?number)?)` |
| `mrn` | `(mrn\|medical_?record(_?number)?\|patient_?id)` |
| `member_id` | `(member\|beneficiary\|subscriber\|enrollee)_?(id\|number\|num)` |
| `account` | `(account\|acct)_?(number\|num\|no\|id)` |
| `address` | `(street\|address\|addr)(_?line)?(_?1\|_?2)?`, `^address$` |
| `ip` | `(ip_?address\|ip_?addr\|ipv4\|ipv6)` |
| `url` | `(url\|website\|web_?site\|homepage)` |
| `license` | `(license\|licence)_?(number\|num\|no)`, `npi` |
| `device_id` | `(device\|equipment)_?(id\|serial\|identifier)` |
| `vin` | `^vin$`, `vehicle_?(id\|identification)` |

**Not detected** (not PHI at the column level in typical tabular data): ZIP codes, state codes, county names, year-only dates, age (when stored as integer), race/ethnicity, diagnosis codes (ICD), procedure codes (CPT). These are standard analytic dimensions, not the 18 HIPAA identifiers.

### PHI type → replacement mapping

| PHI Type | Replacement method |
|---|---|
| `name` | `faker.name()` (or `faker.first_name()` / `faker.last_name()` based on column name) |
| `dob` | `faker.date_of_birth(minimum_age=0, maximum_age=100)` — shifted by random ±0–30 days per row |
| `date_phi` | Source date shifted by a per-row random offset (±0–365 days); relative gaps between dates in the same row are preserved |
| `phone` | `faker.phone_number()` |
| `email` | `faker.email()` |
| `ssn` | Sequential integer formatted as `{i:09d}` (not a real SSN format — avoids accidentally valid SSNs) |
| `mrn` | Sequential integer formatted as `MRN{i:08d}` |
| `member_id` | Sequential integer formatted as `MBR{i:010d}` |
| `account` | Sequential integer |
| `address` | NAD sample for detected state (if available), else `faker.street_address()` |
| `ip` | `faker.ipv4_private()` |
| `url` | `faker.url()` |
| `license` | Sequential integer formatted as `LIC{i:07d}` |
| `device_id` | `faker.uuid4()` |
| `vin` | `faker.vin()` |

Sequential integers are stable within a single `synthesize()` call and reset between calls (unless `random_seed` is set, in which case they start from a seeded offset).

---

## 7. SQL Dialect Compatibility

The profiling queries in Section 4 use ANSI SQL (`PERCENTILE_CONT ... WITHIN GROUP`, `STDDEV`, `COUNT`). All are supported by:

| Dialect | Driver | Notes |
|---|---|---|
| **DuckDB** | `duckdb` (native SQLAlchemy dialect) | Full support. DuckDB also has `SKEWNESS()` and `KURTOSIS()` if added later. |
| **SQL Server** | `pyodbc` + `mssql+pyodbc` | `PERCENTILE_CONT` supported (SQL Server 2012+). `STDDEV` → `STDEV`. Handled via SQLAlchemy dialect detection. |
| **PostgreSQL** | `psycopg2` or `psycopg3` | Full support. |
| **Others** | Any SQLAlchemy-compatible dialect | Profiling queries may need dialect-specific variants. Unsupported dialects raise `UnsupportedDialectError` with a clear message rather than silently producing wrong results. |

Dialect is detected from `engine.dialect.name`. A `_profile.py` dispatch table maps dialect names to query variants where ANSI SQL is insufficient.

---

## 8. Dependencies

synth_kit's runtime dependencies ship in the single `privacy-analytics-toolbox` install — there are no per-module extras. The ones this module relies on:

```toml
"sqlalchemy>=2.0",
"pandas>=2.2",
"numpy>=1.26",
"scipy>=1.12",
"faker>=24.0",
```

`scipy` is used for percentile interpolation (`scipy.interpolate.interp1d` or `numpy.interp`). `faker` provides PHI replacement values.

**Geospatial integration:** `geospatial` ships in the same install, so when the NAD database is available (path from `GeospatialConfig`), `_phi.py` uses NAD for address synthesis; it falls back to Faker when NAD is not ingested.

No new packages may be added without flagging first.

---

## 9. Project Structure (within analytics-toolbox)

```
src/analytics_toolbox/synth_kit/
    __init__.py
    _profile.py
    _detect.py
    _synthesize.py
    _phi.py
    _types.py

tests/synth_kit/
    conftest.py                  # in-memory DuckDB engine + sample schemas
    test_detect.py               # PHI pattern matching, overrides, suppress
    test_profile.py              # profiling queries against known DuckDB fixtures
    test_synthesize.py           # synthesis output shape, dtype, null rates
    test_phi.py                  # PHI replacement produces no real values
    test_integration.py          # end-to-end: synthesize() on multi-column fixture
```

---

## 10. Testing Strategy

**No network calls ever.** All tests use in-memory DuckDB engines.

**Pattern:** Each test constructs a known input schema → calls `synthesize()` → asserts on output properties. Never assert on exact values (randomness), always assert on shape, dtype, null rates, and that PHI columns contain no values from the source.

### Unit tests

- **`test_detect.py`**: Column name pattern matching covers all 15 PHI types. Overrides add types. `suppress_phi` removes detection and emits warning. Edge cases: column named `"id"` (not PHI), `"patient_name"` (PHI), `"provider_name"` (PHI).

- **`test_profile.py`**: Profiling queries return expected aggregate shapes. Numeric profile for a known distribution (e.g. 1000 uniform integers) returns correct percentiles. Categorical profile returns correct value counts. Null rate is correct when NULLs are present.

- **`test_synthesize.py`**: Output has same column names and dtypes as source schema. Row count matches `n_rows` when specified, matches source count when not. Null rate in numeric columns is within tolerance of source null rate. Values are within [min, max] of source column.

- **`test_phi.py`**: PHI columns in output contain zero values from the source column. Sequential ID types are non-overlapping across rows. Date PHI values are valid dates. Email PHI values are valid email format.

### Integration tests

- **`test_integration.py`**: End-to-end `synthesize()` call on a 10-column fixture with mixed types (int, float, varchar, date) and 3 PHI columns. Verifies: output shape, no source values in PHI columns, all non-null numeric outputs within source range, `n_rows` override works, `random_seed` produces identical output on repeat calls.

### What not to test

- Faker internals — trust the upstream library.
- SQLAlchemy dialect internals — trust SQLAlchemy.
- Statistical fidelity — "plausible, not faithful" is the spec; don't test distribution moments.

---

## 11. Code Conventions

Follow the same conventions as the rest of `analytics_toolbox`:
- Small composable functions, no class hierarchies (dataclasses for structured data only)
- Type hints on all public and internal functions
- Google-style docstrings on public functions
- `ruff` for formatting and linting
- SQL strings that include user-controlled values use parameterized queries via SQLAlchemy; the source `query` parameter is wrapped in a CTE (`SELECT ... FROM (<query>) _synth_source`) — never string-formatted into a larger query (SQL injection surface)
