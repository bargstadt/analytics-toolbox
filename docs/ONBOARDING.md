# Onboarding & Code Conventions

A single document with two jobs:

1. **[Part 1 — Orientation](#part-1--orientation)** — what this toolbox is, how the pieces relate, and how to get it running on your machine. Read top to bottom on day one.
2. **[Part 2 — How we write code here](#part-2--how-we-write-code-here)** — the conventions and the *why* behind them, shown with real code from the modules rather than abstract rules. Read it before your first change; refer back to it during review.

> This is the human-facing companion to [`CLAUDE.md`](../CLAUDE.md). `CLAUDE.md` is written to an AI agent and is exhaustive about edge cases and load-bearing details; this doc is written to a person and optimizes for getting oriented fast. When the two disagree, `CLAUDE.md` is authoritative.

---

## Part 1 — Orientation

### What this toolbox is

`analytics-toolbox` is a single Python package (`analytics_toolbox`) that collects utilities that recur across analytics projects: geospatial geocoding, entity resolution, synthetic data, feature engineering, Census ingest, and DuckDB write helpers. It exists so a new project doesn't start from a blank file.

Three ideas explain almost every decision in the repo:

1. **Privacy by construction.** The design bar is HIPAA-grade strictness: input data must never leave the machine. The *only* outbound network calls anywhere are to public `.gov` sources (address/boundary reference data, the Census API). This isn't a feature bolted on — it's enforced structurally (see the `geocoder` stub in Part 2).
2. **Build when needed, not before.** Modules get real implementations when an actual need calls for them — never speculatively. The module map below is the current reality, not a roadmap.
3. **Small composable functions over class hierarchies.** Lean dependencies, SQL-first transforms, and functions you can import one at a time.

### The module map

| Module | What it does | Primary entry point |
|---|---|---|
| `geospatial` | Offline US address → Census block-group FIPS: normalize → fuzzy-match against the National Address Database → point-in-polygon to a block group. The flagship; built to a higher bar than the rest. | `geocode_address_table()` |
| `entity_resolution` | Config-driven Master Patient Index: block → weighted-fuzzy score → cluster across N systems. In-memory; output holds only system IDs. | `resolve()` |
| `synth_kit` | SQL-first synthetic data: profile via server-side aggregates, replace PHI via Faker. Raw rows never enter Python. | `synthesize()` |
| `feature_engineering` | Leakage-safe windowed aggregates over DuckDB: entity-date spine, point-in-time correctness, fan-out guardrails. | `compute_features()` / `join_features()` |
| `acs` | U.S. Census ACS 5-year ingest into a local DuckDB `raw` schema, keyed by FIPS. | `ingest_acs()` |
| `utils` | The toolbox's *only* sanctioned data-write API. Destination is explicit; disk writes are PHI-certification-gated. | `save_table()` / `save_csv()` / `in_memory_con()` / `on_disk_con()` |

Shared, package-root building blocks the modules lean on:

- `analytics_toolbox/_config.py` — the single `load_config(path)` entry point and the root `AnalyticsToolboxConfig`.
- `analytics_toolbox/_storage.py` — `StorageConfig` (connection + data_dir), shared by every module.
- `analytics_toolbox/_fips.py` — FIPS helpers shared by `geospatial` and `acs`.

### How the modules relate (the FIPS thread)

The modules aren't islands — they're designed to chain. The connective tissue is the **Census block-group FIPS code**:

```
synth_kit          geospatial                         acs
─────────          ──────────                         ───
synthesize()  →    geocode_address_table()       ←→   ingest_acs()
PHI-safe           address → block_group_fips         demographics keyed by the
test data          (12-digit FIPS)                    same block_group_fips
                          │                                   │
                          └──────────── join on FIPS ─────────┘
                                          │
                          feature_engineering.compute_features()
                          leakage-safe windowed features for ML
```

`geospatial` geocodes to the *same* FIPS that `acs` keys demographics by, so ACS data joins straight onto geocoded records. `notebooks/end_to_end.ipynb` runs this whole chain — it's the single best artifact for seeing how the toolbox fits together, and the right thing to run once your environment works.

### Repo layout

```
src/analytics_toolbox/
    _config.py _storage.py _fips.py utils.py   # package-root shared code
    geospatial/  entity_resolution/  synth_kit/  feature_engineering/  acs/
        __init__.py        # the public surface — what callers import
        <public>.py        # public entry points (e.g. resolve.py, engine.py, ingest.py)
        _<internal>.py      # underscore-prefixed = internal, not part of the API
        README.md           # runbook — how to use the module
        ARCHITECTURE.md     # how it's built + design rationale
        SPEC.md             # frozen design spec (only where one exists)
        fixtures/ examples/ # synthetic data + reference usage patterns
tests/<module>/             # mirrors src/ one-to-one
notebooks/                  # one demo per module + end_to_end.ipynb
docs/                       # this file + DATA_HANDLING.md (compliance statement)
config.example.yaml         # copy to config.yaml (gitignored) to run anything
```

The naming convention is load-bearing: **a leading underscore means "internal."** If it's not exported from a module's `__init__.py`, don't import it from outside that module.

### Get it running

```bash
# 1. Install the package + dev tooling (pytest, ruff, pip-audit)
poetry install --with dev

# 2. Create your local config (config.yaml is gitignored — it holds local paths)
cp config.example.yaml config.yaml

# 3. Confirm a green baseline
poetry run pytest          # ~561 tests, all should pass
poetry run ruff check .

# 4. (geospatial only) one-time reference-data download — see geospatial/README.md
#    Plan for ~25 GB free and a 15–30 min NAD download.
poetry run analytics-toolbox ingest-tiger --config config.yaml
poetry run analytics-toolbox ingest-nad   --config config.yaml
```

`synth_kit`, `entity_resolution`, and `feature_engineering` need no downloads — they run against in-memory DuckDB and their bundled fixtures, so they're the fastest modules to poke at first. `acs` needs a free Census API key in `CENSUS_API_KEY` (env var or `.env`, never in YAML).

### A suggested first week

Nothing formal — just a path that follows the difficulty gradient instead of the alphabet:

1. **Read this doc + `CLAUDE.md`**, then skim each module's `README.md`.
2. **Run the easy modules' notebooks** (`synth_kit_demo`, `entity_resolution_demo`, `feature_engineering_demo`) — no setup, fast feedback.
3. **Do the geospatial reference-data ingest** and run `geospatial_demo`.
4. **Run `end_to_end.ipynb`** — the "it all clicks" moment.
5. **Make a small real change** with a test, and read Part 2 before you open the PR.

---

## Part 2 — How we write code here

The conventions, each with a real example from the codebase and the reason it's a convention. This is the part to internalize — the rules are cheap to state but the *why* is what keeps the toolbox coherent.

### Small composable functions over class hierarchies

Reach for a class only when state genuinely needs to persist across calls. Most of the toolbox is plain functions that take data in and return data out. The public API of an entire module is often a single function (`resolve()`, `synthesize()`, `geocode_address_table()`); everything else is internal helpers wired together in the orchestrator.

The shape to copy — a thin public orchestrator over `_`-prefixed steps:

```python
# entity_resolution/resolve.py (public) calls, in order:
#   _preprocess.normalize_fields → _block.build_blocks →
#   _match.score_pairs → _cluster.build_clusters
```

Each step is independently importable and independently testable. Structured data uses a small dataclass or a Pydantic model (`Agg`, `Guardrails`, `ColumnProfile`) — never a class hierarchy.

### Type hints and Google-style docstrings on the public surface

Every public function is fully type-hinted and carries a Google-style docstring (`Args:` / `Returns:` / `Raises:`). Match the surrounding style:

```python
def load_config(path: str | Path) -> AnalyticsToolboxConfig:
    """Load analytics-toolbox config from a YAML file.

    Args:
        path: Path to the config YAML file.

    Returns:
        ``AnalyticsToolboxConfig`` with all paths expanded and defaults applied.

    Raises:
        FileNotFoundError: If the config file does not exist.
        ValueError: If a required key is missing.
    """
```

### SQL-first, pandas second

Prefer doing transforms in DuckDB SQL over pandas where it's reasonable; pandas is the fallback, not the default. This shows up everywhere:

- `feature_engineering` builds **one** `FILTER`-per-window SQL statement and does all windows in a single DuckDB scan, rather than looping windows in pandas.
- `synth_kit` does *all* profiling as server-side SQL aggregates — raw rows are never pulled into Python at all.
- `geospatial` filters the ~97M-row NAD by state in DuckDB, not pandas.

When you're about to write a `groupby`/`merge` in pandas, ask whether DuckDB should do it instead.

### Configuration is Pydantic, loaded once, from one place

`load_config(path)` in `analytics_toolbox/_config.py` is the **single** loader for every module. There is no per-module loader (a stale doc once implied `geospatial._config.load_config` exists — it does not). The YAML has `storage:` shared at the root and each module's settings nested under its own key:

```yaml
storage:                       # shared by every module
  connection: ~/.local/share/analytics_toolbox/analytics_toolbox.duckdb
  data_dir: ~/.local/share/analytics_toolbox/
geospatial:                    # module settings nest under the module name
  nad: { states: [IA] }
  tiger: { vintage: 2024 }
  matching: { confidence_threshold: 90 }
```

A module's section is `None` when its YAML key is absent, and callers that need it raise a `ValueError` *at the point of use* — not at load time — so an unrelated module's config can't block you. **Secrets never live in YAML**: `acs` reads `CENSUS_API_KEY` from the environment via `pydantic-settings`.

### Privacy is enforced structurally, not by convention

The strongest privacy guarantees in the toolbox are the ones a caller *can't* accidentally violate:

- **`geospatial/_scourgify_compat.py`** stubs a fake `geocoder` module into `sys.modules` before `usaddress-scourgify` imports, so scourgify physically cannot reach Google's geocoding API. An external call that would leak address data is *structurally prevented*. (This is load-bearing — don't "clean it up.")
- **`synth_kit`** has two strictly separated phases with no code path that loads raw rows into Python — the guarantee holds even if the caller makes a mistake.
- **`acs/_census_api.py`** is careful never to log `resp.url`, because httpx embeds the `?key=<secret>` API key in it — it logs the bare URL instead.

When you add anything that touches input data, prefer making the unsafe thing impossible over documenting that it's unsafe.

### Persisting data is a deliberate, gated act

Everything stays in memory by default. `utils` is the *only* code that writes data out, and it makes that a conscious decision:

```python
mem = in_memory_con()
save_table(phi_df, "patients", con=mem)                    # free — nothing persists

disk = on_disk_con("warehouse.duckdb")
save_table(df, "model_ready", con=disk, certify_no_phi=True)   # disk write REQUIRES certification
```

Disk writes require `certify_no_phi=True`; a MotherDuck/cloud target additionally requires `allow_cloud_egress=True` and warns loudly. Every persistent write logs a **data-free** audit line (destination + row/col counts only, never cell values). If you need a new write path, route it through here — don't add a bare `df.to_csv` or `con.execute("CREATE TABLE ...")` elsewhere.

### Fail loud, and never put sensitive values in logs or errors

Validate inputs *before* doing work, and raise with enough context to fix the problem — but never with the data itself. `feature_engineering._validate` rejects bad inputs (missing columns, null grain keys, non-unique spine, fan-out over budget) before running any aggregation. The pattern to copy from `entity_resolution`: when a block is too large, the error reports the block **sizes**, never the block **key value** (which could be a DOB). Logs and errors carry counts, column names, and scores — never cell values.

### Lean dependencies — flag any new one before it lands

The toolbox is one install with no per-module extras, but the bar for *adding* a dependency is still high. Before anything new lands in `pyproject.toml`, it's worth a sentence each on install weight, maintenance risk, and version maturity. CI runs `pip-audit` over the full dependency surface on every push, so a fragile or vulnerable dependency fails the build. (The heaviest accepted dependency is the GIS stack — geopandas/shapely/pyproj — used by `geospatial`.)

### Tests mirror `src/` one-to-one

`tests/<module>/` mirrors `src/analytics_toolbox/<module>/`. Tests use in-memory DuckDB and bundled fixtures — **no network calls, ever.** For randomized output (synthesis, fuzzy scoring), assert on *properties* (shape, dtype, null rate, "no source value leaked into a PHI column"), not exact values. Run the full suite plus lint before opening a PR:

```bash
poetry run pytest
poetry run ruff check .
```

### Don't silently expand scope

If a simpler structure than what's planned becomes apparent while you're building — or you spot a doc that contradicts the code — raise it before implementing, rather than quietly building the bigger thing or fixing it in a way nobody agreed to. This is a solo-maintained library; surprises in the diff cost more than they save.
