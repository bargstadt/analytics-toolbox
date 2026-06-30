# synth_kit

SQL-first HIPAA-compliant synthetic data generation.

Given a SQL connection and a query, `synthesize()` produces a fully synthetic version of the result set — same schema, statistically plausible values, all PHI replaced — without any raw PII ever entering Python memory.

> **Architecture & design:** this README is the runbook. For the two-phase design, the profiling/synthesis internals, and PHI-detection mechanics, see [`ARCHITECTURE.md`](ARCHITECTURE.md); for the original design spec, [`SPEC.md`](SPEC.md).

## Install

```bash
pip install privacy-analytics-toolbox
```

## Quick start

```python
from sqlalchemy import create_engine
from analytics_toolbox.synth_kit import synthesize

engine = create_engine("duckdb:///warehouse.duckdb")   # or SQL Server, PostgreSQL

synth = synthesize(
    engine,
    "SELECT * FROM patient_registry WHERE admit_year = 2024",
    random_seed=42,
)
# synth is a pandas DataFrame — same columns as source, all PHI replaced
```

## API

```python
synthesize(
    engine,             # sqlalchemy.Engine
    query,              # str — any SQL your database supports
    *,
    n_rows=None,               # int | None — rows to generate (default: match source)
    categorical_aliases=None,  # dict[str, list] — replace category values by column
    phi_overrides=None,        # dict[str, str] — force PHI type by column name
    suppress_phi=None,         # list[str] — exclude columns from PHI detection
    random_seed=None,          # int | None — reproducible output
    geospatial_config=None,    # GeospatialConfig | str | Path | None — enables NAD address sampling
) -> pd.DataFrame
```

### Parameters

| Parameter | Purpose |
|---|---|
| `engine` | SQLAlchemy engine. Dialect-agnostic — DuckDB, SQL Server, PostgreSQL, others. |
| `query` | The SQL to synthesize. Can be any SELECT — JOINs, CTEs, WHERE filters, derived columns. |
| `n_rows` | Row count override. Omit to match the source query's row count. |
| `categorical_aliases` | `{"payer": ["Payer A", "Payer B", ...]}` — replaces source category labels while preserving proportions. |
| `phi_overrides` | `{"ref_num": "mrn"}` — force-classifies columns auto-detection would miss. Merged on top of auto-detection. |
| `suppress_phi` | Exclude columns from PHI detection. Emits a `WARNING` per suppressed column for audit. |
| `random_seed` | Pass the same seed for identical output on repeat runs. |
| `geospatial_config` | `GeospatialConfig` object or path to a `config.yaml` file. When provided and the result set has a state column (`state`, `state_code`, etc.), address PHI is replaced with real NAD street addresses sampled for the dominant state. Falls back to `faker.street_address()` if NAD is not ingested for that state. |

## Privacy architecture

Two strictly separated phases, with **no raw rows ever crossing the boundary**: Phase 1 profiles the data using server-side SQL aggregates only; Phase 2 synthesizes in Python from those aggregates. Raw PII never enters Python memory, nothing is written to disk, and there are no network calls — it works fully air-gapped. See [`ARCHITECTURE.md`](ARCHITECTURE.md) for the phase-by-phase design.

## PHI auto-detection

Column names are matched against a 15-type pattern registry (case-insensitive, first match wins):

| PHI Type | Example column names | Replacement |
|---|---|---|
| `name` | `patient_name`, `first_name`, `last_name`, `provider_name` | `faker.name()` |
| `dob` | `date_of_birth`, `dob`, `birthdate` | `faker.date_of_birth()` ±30 days |
| `date_phi` | `admit_date`, `discharge_date`, `visit_date` | Random date shift ±365 days |
| `phone` | `phone`, `phone_number`, `mobile_num`, `fax` | `faker.phone_number()` |
| `email` | `email`, `email_address` | `faker.email()` |
| `ssn` | `ssn`, `social_security_number` | Sequential `{i:09d}` (not a real SSN format) |
| `mrn` | `mrn`, `medical_record_number`, `patient_id` | `MRN{i:08d}` |
| `member_id` | `member_id`, `beneficiary_number`, `subscriber_num` | `MBR{i:010d}` |
| `account` | `account_number`, `acct_id`, `acct_no` | Sequential integer |
| `address` | `street_address`, `addr_line1`, `address_line_2` | NAD sample (if available) or `faker.street_address()` |
| `ip` | `ip_address`, `ipv4`, `ipv6` | `faker.ipv4_private()` |
| `url` | `url`, `website`, `homepage` | `faker.url()` |
| `license` | `license_number`, `npi` | `LIC{i:07d}` |
| `device_id` | `device_id`, `equipment_serial` | `faker.uuid4()` |
| `vin` | `vin`, `vehicle_id` | `faker.vin()` |

**Not detected** (standard analytic dimensions, not HIPAA identifiers): ZIP codes, state codes, county names, year-only dates, age (integer), race/ethnicity, ICD codes, CPT codes.

## SQL dialect support

Profiling queries use ANSI SQL throughout. Supported dialects:

| Dialect | Connection string | Notes |
|---|---|---|
| **DuckDB** | `duckdb:///path/to/file.duckdb` | Full support. Also `duckdb:///md:db` for MotherDuck. |
| **SQL Server** | `mssql+pyodbc://server/db?driver=ODBC+Driver+17+for+SQL+Server` | `STDEV` substituted for `STDDEV` automatically. |
| **PostgreSQL** | `postgresql+psycopg2://user:pw@host/db` | Full support. |

## Address synthesis and NAD integration

`geospatial` ships in the same install, so when the NAD database has been ingested, `address`-type PHI columns are filled with real addresses sampled from NAD filtered to the state in your data. Falls back to `faker.street_address()` when NAD is unavailable for that state.

## Limitations

- **Date columns that are not PHI** are treated as categorical (sampled from distinct source values). If your non-PHI date column has very high cardinality, the synthetic values may not cover the full range.
- **Categorical profiling caps at 500 distinct values.** Columns with more than 500 distinct values will have the long tail undersampled. For very high cardinality non-PHI strings, consider casting to a category or providing `categorical_aliases`.
- **Numeric synthesis is plausible, not faithful.** The 11-point CDF does not capture multimodal distributions, skewness, or correlations between columns. For fidelity requirements, the synthesis parameters are insufficient — this tool targets privacy compliance, not statistical testing.
- **Column correlations are not preserved.** Each column is synthesized independently.
