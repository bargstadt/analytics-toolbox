"""Synthetic Medicaid fixture for feature_engineering tests and demos.

Fully synthetic — no PHI, no real codes. Deterministic from a seed.
The canonical as-of date for boundary claims is 2024-06-30.
"""

from __future__ import annotations

import random

import duckdb
import pandas as pd

# Canonical as-of date that boundary claims are defined relative to.
_AS_OF = pd.Timestamp("2024-06-30")

# Named roles for deliberate edge-case members.
# Values are assigned during make_medicaid_fixture() and exposed here.
FIXTURE_MEMBERS: dict[str, int] = {}

# Reserved member IDs for edge cases (low values; real members start at 1001).
_ID_ZERO_CLAIMS = 1
_ID_ON_AS_OF = 2
_ID_LOWER_BOUNDARY = 3
_ID_HIGH_VOLUME = 4
_ID_MULTI_SNAPSHOT = 5

_DRUG_CLASSES = ["analgesic", "antibiotic", "antidiabetic", "cardiovascular", "psychiatric"]
_PLACES = ["inpatient", "outpatient", "ed", "office", "home"]
_PROVIDER_TYPES = ["physician", "nurse_practitioner", "specialist", "hospital"]
_CLAIM_TYPES = ["fee_for_service", "managed_care"]
_ELIGIBILITY_CATS = ["aged", "blind_disabled", "child", "adult", "pregnant"]
_SEXES = ["M", "F"]
_COUNTIES = ["01001", "01003", "06037", "17031", "48201"]


def make_medicaid_fixture(
    con: duckdb.DuckDBPyConnection,
    n_members: int = 100,
    seed: int = 42,
    start: str = "2023-01-01",
    end: str = "2025-12-31",
) -> None:
    """Create members, rx_claims, and med_claims tables in con.

    Edge cases seeded at fixed member IDs (1–5):
    - member 1 (zero_claims): no rx or med claims at all
    - member 2 (on_as_of_boundary): has a claim dated exactly on 2024-06-30
    - member 3 (lower_boundary): claims at as_of-30d (included) and as_of-31d (excluded)
    - member 4 (high_volume): ≥500 rx claims
    - member 5 (multi_snapshot): claims spanning many months for multi-as-of tests
    """
    FIXTURE_MEMBERS.clear()
    FIXTURE_MEMBERS.update({
        "zero_claims": _ID_ZERO_CLAIMS,
        "on_as_of_boundary": _ID_ON_AS_OF,
        "lower_boundary": _ID_LOWER_BOUNDARY,
        "high_volume": _ID_HIGH_VOLUME,
        "multi_snapshot": _ID_MULTI_SNAPSHOT,
    })

    rng = random.Random(seed)
    start_dt = pd.Timestamp(start)
    end_dt = pd.Timestamp(end)

    # --- members ---
    edge_ids = list(range(1, 6))
    bulk_ids = list(range(1001, 1001 + max(n_members - 5, 0)))
    all_ids = edge_ids + bulk_ids

    members_rows = []
    for mid in all_ids:
        dob = _rand_date(rng, pd.Timestamp("1940-01-01"), pd.Timestamp("2005-12-31"))
        members_rows.append({
            "member_id": mid,
            "dob": dob,
            "sex": rng.choice(_SEXES),
            "eligibility_category": rng.choice(_ELIGIBILITY_CATS),
            "county_fips": rng.choice(_COUNTIES),
            "enroll_start": start_dt.date(),
            "enroll_end": end_dt.date(),
        })

    members_df = pd.DataFrame(members_rows)
    con.register("_tmp_members", members_df)
    con.execute("CREATE OR REPLACE TABLE members AS SELECT * FROM _tmp_members")

    # --- rx_claims ---
    rx_rows = []
    claim_id = 1

    # Edge case 2: claim ON the as-of date
    rx_rows.append(_rx_row(claim_id := claim_id + 1, _ID_ON_AS_OF, _AS_OF, rng))

    # Edge case 3: claim exactly at as_of-30d (included) and as_of-31d (excluded)
    rx_rows.append(_rx_row(claim_id := claim_id + 1, _ID_LOWER_BOUNDARY,
                           _AS_OF - pd.Timedelta(days=30), rng))
    rx_rows.append(_rx_row(claim_id := claim_id + 1, _ID_LOWER_BOUNDARY,
                           _AS_OF - pd.Timedelta(days=31), rng))

    # Edge case 4: high-volume — 500 claims spread across the date range
    for _ in range(500):
        rx_rows.append(_rx_row(claim_id := claim_id + 1, _ID_HIGH_VOLUME,
                               _rand_date(rng, start_dt, _AS_OF - pd.Timedelta(days=1)), rng))

    # Edge case 5: multi-snapshot — one claim per month for 18 months before as-of
    for months_back in range(1, 19):
        dt = _AS_OF - pd.DateOffset(months=months_back)
        rx_rows.append(_rx_row(claim_id := claim_id + 1, _ID_MULTI_SNAPSHOT, dt, rng))

    # Bulk members (1001+): random claims
    for mid in bulk_ids:
        n_claims = rng.randint(0, 20)
        for _ in range(n_claims):
            dt = _rand_date(rng, start_dt, end_dt)
            rx_rows.append(_rx_row(claim_id := claim_id + 1, mid, dt, rng))

    rx_df = pd.DataFrame(rx_rows)
    con.register("_tmp_rx", rx_df)
    con.execute("CREATE OR REPLACE TABLE rx_claims AS SELECT * FROM _tmp_rx")

    # --- med_claims ---
    med_rows = []
    med_claim_id = 1

    # Edge case 2: on-as-of claim in med too
    med_rows.append(_med_row(med_claim_id := med_claim_id + 1, _ID_ON_AS_OF, _AS_OF, rng))

    # Edge case 5: multi-snapshot med claims
    for months_back in range(1, 19):
        dt = _AS_OF - pd.DateOffset(months=months_back)
        med_rows.append(_med_row(med_claim_id := med_claim_id + 1, _ID_MULTI_SNAPSHOT, dt, rng))

    # Bulk members
    for mid in bulk_ids:
        n_claims = rng.randint(0, 10)
        for _ in range(n_claims):
            dt = _rand_date(rng, start_dt, end_dt)
            med_rows.append(_med_row(med_claim_id := med_claim_id + 1, mid, dt, rng))

    med_df = pd.DataFrame(med_rows)
    con.register("_tmp_med", med_df)
    con.execute("CREATE OR REPLACE TABLE med_claims AS SELECT * FROM _tmp_med")


def make_spine(
    con: duckdb.DuckDBPyConnection,
    as_of_dates: list[pd.Timestamp],
    member_ids: list[int] | None = None,
) -> pd.DataFrame:
    """Build a member × as-of-date spine from the fixture members table.

    Returns one row per (member_id, as_of_date) combination. Passing
    member_ids restricts the spine to those members only.
    """
    if member_ids is None:
        ids = (
            con.execute("SELECT member_id FROM members ORDER BY member_id")
            .df()["member_id"]
            .tolist()
        )
    else:
        ids = list(member_ids)

    rows = [
        {"member_id": mid, "as_of_date": pd.Timestamp(dt)}
        for dt in as_of_dates
        for mid in ids
    ]
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _rand_date(rng: random.Random, lo: pd.Timestamp, hi: pd.Timestamp) -> pd.Timestamp:
    delta = (hi - lo).days
    return lo + pd.Timedelta(days=rng.randint(0, max(delta, 0)))


def _rx_row(claim_id: int, member_id: int, claim_date, rng: random.Random) -> dict:
    return {
        "claim_id": claim_id,
        "member_id": member_id,
        "claim_date": pd.Timestamp(claim_date).date(),
        "ndc_code": f"NDC{rng.randint(10000, 99999)}",
        "drug_class": rng.choice(_DRUG_CLASSES),
        "paid_amount": round(rng.uniform(5.0, 500.0), 2),
        "days_supply": rng.randint(1, 90),
        "quantity": rng.randint(1, 180),
    }


def _med_row(claim_id: int, member_id: int, claim_date, rng: random.Random) -> dict:
    return {
        "claim_id": claim_id,
        "member_id": member_id,
        "claim_date": pd.Timestamp(claim_date).date(),
        "place_of_service": rng.choice(_PLACES),
        "provider_type": rng.choice(_PROVIDER_TYPES),
        "dx_primary": f"Z{rng.randint(100, 999)}",
        "paid_amount": round(rng.uniform(50.0, 5000.0), 2),
        "claim_type": rng.choice(_CLAIM_TYPES),
    }
