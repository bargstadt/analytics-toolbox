# Code Review & Self-Audit Plan

A structured, few-week pass for the author to (re)learn the codebase line by line **and** pin down exactly where risk lives. This repo was built fast and module-by-module; this is the deliberate slow read that makes sure nothing load-bearing is held only in your head.

## How to use this

- Work it **module by module** on the schedule below. Each module has three parts: *read in this order* → *comprehension* (know what it does) → *audit checks* (know where it could break). Check the boxes as you go — they're committed, so this doubles as a record of what was reviewed and when.
- **Surface and fix issues as you find them.** Don't record confirmed-unfixed vulnerabilities in committed files — this repo is becoming public and git history is permanent. Check a box only once the issue behind it is resolved.
- This converges with [`DATA_HANDLING.md`](DATA_HANDLING.md): the audit checks below are the same questions its `[VERIFY]` markers ask. As you confirm each, you can resolve the matching marker there — the self-audit *produces* the compliance evidence.

## The risk lens (read this first)

This is a privacy-first library handling PHI, so "vulnerability" mostly means a privacy or integrity failure, not a classic web exploit. Read every module against these axes:

1. **Data egress** — does any input data leave the machine? The only allowed outbound calls are public `.gov` downloads (geospatial) and the Census API (acs). Anything else is a finding.
2. **Input data to disk** — is any caller-supplied (potentially-PHI) DataFrame ever written to disk or a temp file, rather than processed in memory and returned? (This is DATA_HANDLING's single highest-priority question.)
3. **PHI / secrets in logs & errors** — does any `log.*`, `warnings.warn`, or `raise` interpolate a cell value, a block key, or an API key? Only counts, column names, and scores are allowed.
4. **SQL injection** — synth_kit and feature_engineering build SQL from inputs. Verify user-controlled values are parameterized or structurally contained (CTE-wrapped, validated identifiers), never naively string-formatted.
5. **Secret handling** — `CENSUS_API_KEY` must come from env/`.env`, never YAML, and must never reach logs (httpx embeds it in the request URL).
6. **Supply chain** — pinned via `poetry.lock`; `pip-audit` in CI. Watch the known-fragile `usaddress-scourgify` → `geocoder` line.
7. **The write gate** — `utils.save_table`/`save_csv` is the *only* sanctioned disk write. Verify the `certify_no_phi` / `allow_cloud_egress` gates are genuinely un-bypassable and every persistent write logs a data-free audit line.

## Schedule (≈4 weeks)

| Week | Focus | Why this order |
|---|---|---|
| **0 (½ day)** | Tooling baseline + setup | Establish a green floor before reading. |
| **1** | Shared core + the write gate | The contract every module depends on — highest leverage. |
| **2** | The SQL builders: `synth_kit`, `feature_engineering` | The real injection surface; both generate SQL. |
| **3** | `geospatial` | Largest module; egress, supply chain, and the disk question. |
| **4** | `entity_resolution`, `acs`, integration sweep | PHI-in-logs and secret handling; then the cross-module seams. |

---

## Week 0 — Tooling baseline

- [ ] Run `poetry run pytest` — full suite green (561 tests). Note anything skipped/xfailed.
- [ ] Run `poetry run ruff check .` — clean.
- [ ] Run `poetry run pip-audit` — note any advisories on the dependency surface.
- [ ] Skim `CLAUDE.md` and each module's `ARCHITECTURE.md` so the line-by-line read has a map.

## Week 1 — Shared core + the write gate

Files, in order: `utils.py` → `_storage.py` → `_config.py` → `_fips.py`.

**Comprehension**
- [ ] `utils.py`: trace `in_memory_con`, `on_disk_con`, `save_table`, `save_csv` — what each gate checks and in what order.
- [ ] `_config.py`: how `load_config` builds `AnalyticsToolboxConfig`; how each module's section becomes `None` when absent and where the point-of-use `ValueError` fires.
- [ ] `_storage.py`: how `StorageConfig` expands `~`, and how it handles `md:` MotherDuck strings.
- [ ] `_fips.py`: what FIPS helpers exist and who consumes them (geospatial + acs).

**Audit checks** (axes 7, 5, 2)
- [ ] `save_table` on an **on-disk** con without `certify_no_phi=True` raises — and there's no code path that skips the check (default value, truthiness of non-`True` values).
- [ ] A **MotherDuck/cloud** target additionally requires `allow_cloud_egress=True` and warns; `on_disk_con` refuses `md:` strings outright.
- [ ] The audit log line on every persistent write contains **only** destination + row/col counts — no cell values.
- [ ] `in_memory_con` writes persist nothing.
- [ ] No secret or path-with-credentials is logged by the config/storage layer.

## Week 2 — The SQL builders

### synth_kit
Files: `_public.py` → `_profile.py` → `_detect.py` → `_synthesize.py` → `_phi.py` → `_types.py`.

**Comprehension**
- [ ] Trace the Phase 1 / Phase 2 boundary in `_public.py` — confirm there is **no code path** that fetches raw rows into Python.
- [ ] `_detect.py`: the PHI pattern registry, first-match-wins, override/suppress merge.
- [ ] `_phi.py`: each PHI type → replacement; the NAD soft-import path.

**Audit checks** (axes 4, 1, 2, 3)
- [ ] The caller's `query` is only ever wrapped in a CTE (`SELECT … FROM (<query>) _q`) — never string-concatenated into a larger statement.
- [ ] Any user-controlled value in a profiling query is parameterized via SQLAlchemy.
- [ ] PHI columns are **never** queried for distinct values (categorical profiling excludes them).
- [ ] No network calls; no disk writes; the logged PHI map carries column names only.
- [ ] A column matching a PHI pattern but `suppress_phi`'d warns loudly (no silent skips → false negatives).

### feature_engineering
Files: `engine.py` → `_validate.py` → `_sql.py` → `compose.py` → `_types.py`.

**Comprehension**
- [ ] `engine.py`: validation → fan-out guard → SQL build → execute → left-join back to spine.
- [ ] `_sql.py`: how `build_feature_sql` assembles the single-scan `FILTER`-per-window statement.

**Audit checks** (axes 4, 2)
- [ ] Identifiers that get interpolated into SQL — `namespace`, `Agg.name`, window values, `group_cols`, `entity_keys` — are validated in `_validate.py` before they reach `_sql.py` (no raw user string lands in SQL unchecked).
- [ ] The point-in-time bound is **exclusive** (`base_date < as_of`) everywhere it's generated — re-derive it from the SQL, don't trust the comment.
- [ ] The fan-out estimate runs before the join; `raise`/`warn` behaves per `Guardrails`.
- [ ] Computation stays in DuckDB on the caller's machine; nothing is transmitted.

## Week 3 — geospatial

Files: `__init__.py` + `_scourgify_compat.py` → `address_normalizer.py` → `nad_preprocess_ingest.py` → `address_matcher.py` → `address_geocoder.py` → `pipeline.py` → `cli.py`.

**Comprehension**
- [ ] How `_scourgify_compat.py` stubs `geocoder` into `sys.modules`, and why `__init__.py` ordering makes it load-bearing.
- [ ] The two-tier blocking (postal → per-row county fallback) in `address_matcher.py`.
- [ ] How `pipeline.geocode_address_table` chains the four stages.

**Audit checks** (axes 1, 2, 6, 3)
- [ ] Enumerate **every** network call in the module — confirm all are GETs to `data.transportation.gov` / `census.gov`, triggered only by `ingest_*`, and that no input data is in any request.
- [ ] **The disk question:** trace the input address table through matcher + geocoder — is it ever written into the DuckDB file or a temp file, or only held in memory and returned? State the answer unambiguously (feeds DATA_HANDLING §5).
- [ ] Confirm `scourgify.get_geocoder_normalized_addr` is never imported anywhere.
- [ ] Review `log.*`/`raise` in each file for interpolated address values.
- [ ] Sanity-check the pinned `usaddress-scourgify` / `geocoder` versions against the `pip-audit` output from Week 0.

## Week 4 — entity_resolution, acs, integration

### entity_resolution
Files: `resolve.py` → `_preprocess.py` → `_block.py` → `_match.py` → `_cluster.py`.

**Comprehension**
- [ ] The pipeline and the weighted-score normalization (missing columns drop from numerator and denominator).

**Audit checks** (axes 3, 2)
- [ ] The fan-out `RuntimeError` reports block **sizes only** — never the block key value (e.g. a DOB).
- [ ] Every `log.*` carries counts/column-names/scores only; grep the module for f-strings interpolating row data.
- [ ] The output DataFrame contains only system IDs + scores; no disk writes anywhere.

### acs
Files: `ingest.py` → `_settings.py` → `_census_api.py` → `_geography.py` → `_variable_history.py` → `_config.py` → `_manifest.py`.

**Comprehension**
- [ ] The ingest loop: resolve key → per variable×geography resolve years → fetch → `save_table(replace)` → manifest.

**Audit checks** (axes 5, 1, 7)
- [ ] `CENSUS_API_KEY` is read only via `_settings.resolve_api_key` (env/`.env`), never from YAML.
- [ ] No code logs `resp.url` (which carries `?key=`); confirm `_census_api.py` logs the bare URL on every error path.
- [ ] `on_disk_con` refusal of `md:` holds for acs; the `certify_no_phi=True` is honest (data is public ACS aggregates only).

### Integration sweep
- [ ] Re-read `notebooks/end_to_end.ipynb` against the modules — confirm imports/usage match the current APIs (watch for the old `load_config` patterns).
- [ ] Repo-wide grep for the danger patterns: bare `.to_csv(` / `.to_parquet(` / `CREATE TABLE` outside `utils`; `print(`/`log` lines interpolating DataFrame contents; any `http`/`requests`/`httpx` call outside geospatial+acs.
- [ ] Resolve the corresponding `[VERIFY]` markers in `DATA_HANDLING.md` from what you confirmed.

---

## Done =

When every box is checked, you have: (1) a current mental model of every line, (2) a tracked record of the review, and (3) the evidence to finish `DATA_HANDLING.md` for any external reviewer.
