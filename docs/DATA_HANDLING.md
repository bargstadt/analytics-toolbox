# Data Handling & Privacy Statement

**Audience:** compliance, privacy, and security reviewers (HIPAA Security/Privacy Officer, government data-governance reviewer, BAA counterparty).
**Purpose:** a single, plain-language statement of how `analytics-toolbox` handles data — what enters, what (if anything) leaves, what is written to disk, and what reaches logs — so it can be assessed without reading source code.

> **This is not a compliance certification.** It describes the software's design properties. HIPAA (or any other regulatory) compliance is a property of *your* organization and deployment, not of any library. Validate every claim below against your own controls and the source code before processing protected data.

> **How to use this template:** every `[VERIFY: …]` marker is a claim the maintainer must confirm against the current code and then replace with the verified statement (and ideally a `file.py:function` citation). Do not hand this document to a reviewer with `[VERIFY]` markers still in it.

> **Status — parked, fed by the self-audit.** This outward-facing statement is intentionally incomplete. Its `[VERIFY]` markers are the same questions in [`CODE_REVIEW_PLAN.md`](CODE_REVIEW_PLAN.md); resolve them there during the line-by-line review and copy the confirmed answers back here when an external reviewer actually needs this document.

---

## 1. One-paragraph summary

`analytics-toolbox` is a Python library designed to run **entirely inside the environment that holds the data** — a laptop, a secured workstation, or an on-prem server. It does not transmit input data anywhere. The only outbound network calls are one-time downloads of **public government reference data** (address and census-boundary files), performed during an explicit setup step, never while processing your data. [VERIFY: confirm no module makes any other outbound call.] Processing is in-memory by default; the library's **only** deliberate way to write data out (`utils.save_table` / `save_csv`) requires the caller to certify the data is PHI-free before it touches disk, and a cloud (MotherDuck) target requires a separate, explicit egress acknowledgement (see §5).

---

## 2. Summary table

| Question | geospatial | synth_kit | entity_resolution | feature_engineering | acs |
|---|---|---|---|---|---|
| Does input data leave the environment? | [VERIFY: No] | [VERIFY: No] | [VERIFY: No] | [VERIFY: No] | No input data — ingests public aggregates only |
| Outbound network calls? | Yes — public .gov reference downloads, setup only | [VERIFY: No] | [VERIFY: No] | [VERIFY: No] | Yes — Census API (`api.census.gov`), public aggregate data |
| Writes to disk? | Yes — public reference data + DuckDB file | [VERIFY: No] | [VERIFY: No] | [VERIFY: in-memory DuckDB? confirm] | Yes — public ACS estimates into the DuckDB `raw` schema |
| Can input data (PHI/PII) be written to disk? | **[VERIFY — highest-priority question]** | [VERIFY] | [VERIFY] | [VERIFY] | N/A — no input data |
| Can PHI/PII reach logs or errors? | [VERIFY] | [VERIFY] | No (documented; counts/scores only) | [VERIFY] | No input PHI; the `CENSUS_API_KEY` is deliberately kept out of logs/errors |

---

## 3. System data flow

Describe, in plain language, the path data takes. Suggested structure:

1. **Where data enters** — the caller passes a pandas DataFrame / SQL query in their own process. [VERIFY: list each public entry point: `geocode_address_table`, `synthesize`, `resolve`, `compute_features`.]
2. **Where it is processed** — in-memory in the caller's Python process and/or a local DuckDB instance on the same machine.
3. **Where results go** — returned to the caller as a DataFrame. [VERIFY: confirm the library itself does not write results anywhere the caller didn't ask for.]
4. **What persists after a run** — [VERIFY: per module, what remains on disk: reference caches, DuckDB tables, temp files.]

> Consider adding a simple boxes-and-arrows diagram here. Reviewers respond well to one.

---

## 4. Network egress (the exhaustive list)

The only outbound network activity in the entire library:

| Module | Destination host | Trigger | What is sent | What is received |
|---|---|---|---|---|
| geospatial | `data.transportation.gov` / `datahub.transportation.gov` [VERIFY exact host] | `ingest_nad` setup command, run manually | Nothing but the HTTP GET request | National Address Database archive |
| geospatial | `census.gov` (`www2.census.gov`) [VERIFY exact host] | `ingest_tiger` setup command, run manually | Nothing but the HTTP GET request | TIGER/ZCTA + block-group shapefiles |
| acs | `api.census.gov` | `ingest_acs` command, run manually | The requested ACS variables/geographies + the `CENSUS_API_KEY` query param — **no input data** | Public ACS 5-year aggregate estimates |

Key points for a reviewer:
- These calls download/request **public** data; **no input data is ever included** in the request. [VERIFY]
- The geospatial calls happen only during the explicit `ingest_*` setup steps — **never during geocoding/matching/inference.** [VERIFY]
- The `acs` call carries the `CENSUS_API_KEY` secret as a query parameter. The module deliberately logs the bare request URL (not httpx's `resp.url`, which embeds the key) so the key cannot leak into logs or error text. See `acs/_census_api.py`.
- The remaining modules (synth_kit, entity_resolution, feature_engineering) make no network call. [VERIFY]
- **Notable design choice worth highlighting:** the address normalizer deliberately stubs out the `geocoder` library so that `usaddress-scourgify` cannot reach an external geocoding API — an external call that would otherwise transmit address data is structurally prevented. See `geospatial/_scourgify_compat.py`. [VERIFY wording against code.]

If the environment is air-gapped, the reference data can be transferred by approved media and the library run with no network access at all. [VERIFY: document the offline-install path.]

---

## 5. Disk persistence

For each module, state exactly what is written to disk, where, and whether it can contain PHI/PII.

- **geospatial** — writes public reference data and a DuckDB database under `storage.data_dir` (configurable; default `~/.local/share/analytics_toolbox/`). The NAD archive (~8.45 GB) and TIGER shapefiles are public. **[VERIFY — the single most important question for a reviewer: is the caller's input address table ever written into the DuckDB file or any temp file, or is it processed purely in memory and only the returned DataFrame contains it? Trace this in `address_geocoder` / `address_matcher` and state the answer unambiguously.]**
- **synth_kit** — [VERIFY: confirm "no disk writes; profiling is server-side SQL aggregates, synthesis is in-memory." State whether any temp tables are created.]
- **entity_resolution** — [VERIFY: confirm fully in-memory, no disk writes.]
- **feature_engineering** — [VERIFY: runs in DuckDB; confirm whether it uses an in-memory connection or writes a database file, and whether input data persists after the call.]
- **acs** — writes **public** ACS aggregate estimates into the DuckDB `raw` schema (one table per variable+geography) at `storage.connection`, plus an `acs.manifest.json` readout. No input data is involved, so nothing PHI/PII reaches disk via this module.
- **utils (intentional writes)** — `utils.save_table` / `save_csv` are the library's *only* deliberate disk-write API. They are certify-gated: persisting to disk requires the caller to pass `certify_no_phi=True`, and every persistent write logs a data-free audit line (destination + row/column counts only, never cell values). In-memory writes (`in_memory_con()`) persist nothing. A MotherDuck/cloud target additionally requires `allow_cloud_egress=True` and is the only path that can send data off the machine — call it out explicitly in your egress review (§4) if you permit it.

If any module writes input-derived data to disk, document: the path, whether it is encrypted at rest (the OS/filesystem's responsibility, not the library's), and how it should be cleaned up.

---

## 6. Logging and error messages

State the guarantee uniformly across modules: **no PHI/PII values appear in log messages or exception text** — only counts, column names, scores, and non-sensitive identifiers.

- **entity_resolution** — guaranteed and documented: logs and errors carry only counts, column names, and similarity scores; the fan-out error reports block *sizes*, never the block key value. [Citable.]
- **geospatial / synth_kit / feature_engineering** — [VERIFY: review every `log.*`, `warnings.warn`, and `raise` for interpolated input values, and state the guarantee or list exceptions.]

---

## 7. Dependencies & supply chain

- Dependencies are pinned and resolved via a committed lockfile (`poetry.lock`).
- **Every change is scanned for known vulnerabilities**: CI runs `pip-audit --strict` over the full dependency surface (runtime + dev) on every push and pull request, and fails the build on any known CVE in a shipped dependency.
- The test suite (561 tests) and a repo-wide lint check also gate every change.
- [VERIFY: note the maturity/maintenance status of fragile upstreams — e.g. `usaddress-scourgify` — and the compatibility shims the library carries for them.]

---

## 8. The deploying organization's responsibilities

The library never transmits or (per §5) persists input data outside what is documented, but the following remain the **caller's / organization's** responsibility:

- Access control on the machine and the data files passed in.
- Encryption at rest and in transit at the OS/storage/network layer.
- Audit logging of who runs what against which data.
- Retention and secure deletion of inputs, outputs, and any cached DuckDB files.
- Executing a Business Associate Agreement (BAA) where applicable.
- Validating this software against the organization's own security controls.

---

## 9. How the claims are enforced

- Automated tests assert privacy-relevant behavior (e.g. entity_resolution output contains only IDs; no-PHI-in-logs checks). [VERIFY: list the specific tests so a reviewer can see the guarantee is tested, not just asserted.]
- CI runs tests, lint, and the dependency audit on every change.
- [VERIFY: link to the test files and the CI workflow.]

---

*Maintainer: Matthew Bargstadt. Last reviewed: [DATE]. Software version: [VERSION].*
