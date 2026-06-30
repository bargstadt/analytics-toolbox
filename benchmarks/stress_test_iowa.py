"""Stress-test the geospatial pipeline against 1 million Iowa addresses.

Samples directly from the ingested NAD Iowa rows in DuckDB, applies random
deviations to simulate messy real-world input, then times each pipeline stage.

Usage
-----
    python benchmarks/stress_test_iowa.py --config /tmp/demo_config.yaml
    python benchmarks/stress_test_iowa.py --config /tmp/demo_config.yaml --n 100000

The matcher fetches NAD candidates once per unique postal code and reuses them
across all rows with that ZIP, reducing DuckDB queries from O(n_rows) to
O(n_unique_postal_codes). The throughput numbers here show where the remaining
cost lives after that optimization.
"""

from __future__ import annotations

import argparse
import random
import time

import duckdb
import pandas as pd

from analytics_toolbox.geospatial._config import load_config
from analytics_toolbox.geospatial.address_geocoder import geocode_addresses
from analytics_toolbox.geospatial.address_matcher import match_addresses
from analytics_toolbox.geospatial.address_normalizer import normalize_addresses

# ---------------------------------------------------------------------------
# Street abbreviation expansion maps (NAD stores abbreviated; we expand some
# back to simulate how users typically type addresses)
# ---------------------------------------------------------------------------
_STREET_TYPE_EXPANSIONS = {
    "AVE": "Avenue", "ST": "Street", "BLVD": "Boulevard", "DR": "Drive",
    "RD": "Road", "LN": "Lane", "CT": "Court", "PL": "Place",
    "TER": "Terrace", "CIR": "Circle", "WAY": "Way", "PKWY": "Parkway",
    "HWY": "Highway", "FWY": "Freeway", "EXPY": "Expressway",
    "TRLR": "Trailer", "TRL": "Trail",
}

_DIRECTIONAL_EXPANSIONS = {
    "N": "North", "S": "South", "E": "East", "W": "West",
    "NE": "Northeast", "NW": "Northwest", "SE": "Southeast", "SW": "Southwest",
}

_DIRECTIONALS = set(_DIRECTIONAL_EXPANSIONS.keys())


def _apply_deviation(address_line: str, rng: random.Random) -> str:
    """Apply zero or more random deviations to a single address line."""
    if not address_line:
        return address_line

    parts = address_line.split()
    if not parts:
        return address_line

    # --- expand trailing street type ---
    if rng.random() < 0.25 and len(parts) >= 2:
        last = parts[-1].upper()
        # Don't expand if last token is a directional (e.g. "123 MAIN ST N")
        if last in _STREET_TYPE_EXPANSIONS and last not in _DIRECTIONALS:
            parts[-1] = _STREET_TYPE_EXPANSIONS[last]

    # --- expand leading directional ---
    if rng.random() < 0.20 and len(parts) >= 2:
        # parts[0] is house number; parts[1] may be directional
        if len(parts) > 2 and parts[1].upper() in _DIRECTIONAL_EXPANSIONS:
            parts[1] = _DIRECTIONAL_EXPANSIONS[parts[1].upper()]

    # --- drop leading directional entirely ---
    if rng.random() < 0.15 and len(parts) > 3:
        if parts[1].upper() in _DIRECTIONALS:
            parts = [parts[0]] + parts[2:]

    # --- street name character transposition typo ---
    if rng.random() < 0.20 and len(parts) >= 3:
        # pick the first non-numeric, non-directional word in the middle
        for i in range(2, len(parts) - 1):
            word = parts[i]
            upper = word.upper()
            if (
                len(word) >= 4
                and upper not in _DIRECTIONALS
                and upper not in _STREET_TYPE_EXPANSIONS
            ):
                j = rng.randint(1, len(word) - 2)
                parts[i] = word[:j] + word[j + 1] + word[j] + word[j + 2:]
                break

    # --- house number digit transposition ---
    if rng.random() < 0.10:
        num = parts[0]
        if len(num) >= 3 and num.isdigit():
            j = rng.randint(0, len(num) - 2)
            parts[0] = num[:j] + num[j + 1] + num[j] + num[j + 2:]

    return " ".join(parts)


def generate_addresses(n: int, conn: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Sample n Iowa NAD addresses and apply random deviations."""
    print(f"Sampling {n:,} addresses from nad_addresses (state = 'IA')...")
    t0 = time.perf_counter()

    # Filter first in a subquery, then sample — DuckDB applies USING SAMPLE at
    # the table-scan level before WHERE, so sampling on the outer query is required
    # to get exactly n rows from the qualifying population.
    rows = conn.execute(
        f"""
        SELECT nad_address_line_1, postal_code
        FROM (
            SELECT nad_address_line_1, postal_code
            FROM nad_addresses
            WHERE state = 'IA'
              AND is_standard_address = true
              AND nad_address_line_1 IS NOT NULL
              AND nad_address_line_1 != ''
        ) qualified
        USING SAMPLE RESERVOIR({n})
        """
    ).df()

    if len(rows) < n:
        print(
            f"  Note: only {len(rows):,} qualifying IA rows available "
            f"(requested {n:,}); using all."
        )

    print(f"  Sampled {len(rows):,} rows in {time.perf_counter() - t0:.1f}s")

    print("Applying random deviations...")
    t0 = time.perf_counter()
    rng = random.Random(42)
    rows["Street_Address"] = rows["nad_address_line_1"].apply(
        lambda a: _apply_deviation(str(a), rng)
    )
    rows["City"] = "DES MOINES"  # placeholder — normalizer doesn't need accurate city
    rows["State"] = "IA"
    rows["Postal_Code"] = rows["postal_code"]
    rows = rows.drop(columns=["nad_address_line_1", "postal_code"])
    rows = rows.reset_index(drop=True)
    print(f"  Deviations applied in {time.perf_counter() - t0:.1f}s")

    return rows


def run_normalize(df: pd.DataFrame) -> tuple[pd.DataFrame, float]:
    print(f"\n[1/3] normalize_addresses  ({len(df):,} rows)")
    t0 = time.perf_counter()
    result = normalize_addresses(df)
    elapsed = time.perf_counter() - t0
    rate = len(df) / elapsed if elapsed > 0 else 0
    print(f"      done in {elapsed:.1f}s  ({rate:,.0f} rows/sec)")
    return result, elapsed


def run_match(df: pd.DataFrame, config) -> tuple[pd.DataFrame, float]:
    print(f"\n[2/3] match_addresses  ({len(df):,} rows)")
    print("      Candidates fetched once per unique postal code, reused across rows.")
    t0 = time.perf_counter()
    result = match_addresses(df, config)
    elapsed = time.perf_counter() - t0
    rate = len(df) / elapsed if elapsed > 0 else 0
    print(f"      done in {elapsed:.1f}s  ({rate:,.0f} rows/sec)")
    return result, elapsed


def run_geocode(df: pd.DataFrame, config) -> tuple[pd.DataFrame, float]:
    print(f"\n[3/3] geocode_addresses  ({len(df):,} rows)")
    t0 = time.perf_counter()
    result = geocode_addresses(df, config)
    elapsed = time.perf_counter() - t0
    rate = len(df) / elapsed if elapsed > 0 else 0
    print(f"      done in {elapsed:.1f}s  ({rate:,.0f} rows/sec)")
    return result, elapsed


def print_summary(normalized: pd.DataFrame, matched: pd.DataFrame, geocoded: pd.DataFrame,
                  t_normalize: float, t_match: float, t_geocode: float) -> None:
    total = t_normalize + t_match + t_geocode
    n = len(geocoded)

    print("\n" + "=" * 60)
    print("STRESS TEST SUMMARY")
    print("=" * 60)
    print(f"{'Total rows:':<30} {n:>12,}")
    print(f"{'normalize_addresses:':<30} {t_normalize:>10.1f}s  ({n/t_normalize:,.0f} rows/sec)")
    print(f"{'match_addresses:':<30} {t_match/60:>9.1f}m  ({n/t_match:,.0f} rows/sec)")
    print(f"{'geocode_addresses:':<30} {t_geocode:>10.1f}s  ({n/t_geocode:,.0f} rows/sec)")
    print(f"{'Total pipeline:':<30} {total/60:>9.1f}m  ({n/total:,.0f} rows/sec)")

    print("\nNormalization flags:")
    flag_counts = normalized["address_flag"].value_counts()
    for flag, count in flag_counts.items():
        print(f"  {flag:<35} {count:>10,}  ({count/n*100:.1f}%)")

    print("\nMatch method breakdown:")
    method_counts = matched["match_method"].value_counts()
    for method, count in method_counts.items():
        print(f"  {method:<35} {count:>10,}  ({count/n*100:.1f}%)")

    nad_matched = matched[matched["match_method"] == "nad_match"]
    if not nad_matched.empty:
        scores = nad_matched["match_score"].dropna()
        print(f"\nMatch score distribution (nad_match rows, n={len(scores):,}):")
        print(f"  median  {scores.median():.1f}")
        print(f"  p25     {scores.quantile(0.25):.1f}")
        print(f"  p10     {scores.quantile(0.10):.1f}")
        print(f"  min     {scores.min():.1f}")

    imputed = geocoded["location_imputed"].sum()
    print(
        f"\nLocation imputed (postal centroid, not street point): "
        f"{imputed:,} ({imputed/n*100:.1f}%)"
    )

    print("\nBottleneck analysis:")
    stages = [
        ("normalize_addresses", t_normalize),
        ("match_addresses", t_match),
        ("geocode_addresses", t_geocode),
    ]
    slowest = max(stages, key=lambda x: x[1])
    print(f"  Slowest stage: {slowest[0]} ({slowest[1]/total*100:.0f}% of total time)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Geospatial pipeline stress test — Iowa")
    parser.add_argument("--config", required=True, help="Path to geospatial config YAML")
    parser.add_argument("--n", type=int, default=1_000_000, help="Number of addresses (default 1M)")
    args = parser.parse_args()

    config = load_config(args.config)
    conn = duckdb.connect(config.storage.connection, read_only=True)

    print(f"Stress test: {args.n:,} Iowa addresses")
    print(f"DuckDB: {config.storage.connection}")

    try:
        addresses = generate_addresses(args.n, conn)
    finally:
        conn.close()

    normalized, t_normalize = run_normalize(addresses)
    matched, t_match = run_match(normalized, config)
    geocoded, t_geocode = run_geocode(matched, config)

    print_summary(normalized, matched, geocoded, t_normalize, t_match, t_geocode)


if __name__ == "__main__":
    main()
