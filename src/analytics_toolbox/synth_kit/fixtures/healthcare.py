"""Sample healthcare dataset for synth_kit demos and tests.

Produces a realistic claims table with PHI (names, SSNs, addresses, dates)
intended as the *input* to synthesize() — not as synthetic output itself.
"""

from __future__ import annotations

import random
from datetime import date, timedelta

import pandas as pd

_STATE_ZIPS: dict[str, list[tuple[str, str]]] = {
    "IA": [
        ("Des Moines", "50309"), ("Des Moines", "50312"), ("Des Moines", "50316"),
        ("Iowa City", "52240"), ("Cedar Rapids", "52401"), ("Davenport", "52801"),
        ("Sioux City", "51101"), ("Waterloo", "50701"),
    ],
    "MO": [
        ("Kansas City", "64101"), ("Kansas City", "64105"), ("St. Louis", "63101"),
        ("St. Louis", "63103"), ("Springfield", "65801"), ("Columbia", "65201"),
    ],
    "IL": [
        ("Chicago", "60601"), ("Chicago", "60614"), ("Chicago", "60640"),
        ("Springfield", "62701"), ("Rockford", "61101"), ("Aurora", "60505"),
    ],
    "CA": [
        ("Los Angeles", "90001"), ("Los Angeles", "90210"), ("San Francisco", "94102"),
        ("San Diego", "92101"), ("Sacramento", "95814"), ("Oakland", "94607"),
    ],
}

_FIRST_NAMES = [
    "John", "Jane", "Robert", "Mary", "Michael", "Patricia",
    "James", "Jennifer", "William", "Linda", "David", "Barbara",
    "Richard", "Susan", "Joseph", "Jessica",
]
_LAST_NAMES = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia",
    "Miller", "Davis", "Rodriguez", "Martinez", "Hernandez",
    "Lopez", "Gonzalez", "Wilson", "Anderson",
]
_STREET_NAMES = [
    "Main St", "Oak Ave", "Elm St", "Pine Rd", "Washington Ave",
    "Jefferson St", "Market St", "Park Ave", "Spring St", "Union St",
]
_DIAGNOSES = ["E11.9", "I10", "M79.3", "J45.50", "Z00.00", "F32.9", "K21.9"]


def make_healthcare_source(
    n_patients: int = 50,
    states: list[str] | None = None,
    claims_per_patient: tuple[int, int] = (1, 3),
    as_of: date | None = None,
    date_range_days: int = 180,
    random_seed: int | None = None,
) -> pd.DataFrame:
    """Generate a realistic healthcare claims table for use as synth_kit input.

    Produces one row per claim (1–3 per patient by default) with PHI columns
    (name, SSN, DOB, address) that synthesize() will detect and replace.
    City and ZIP are always consistent with each other.

    Args:
        n_patients: Number of unique patients (default 50).
        states: State codes to sample from. Default: ['IA', 'MO', 'IL'].
            Supported: 'IA', 'MO', 'IL', 'CA'.
        claims_per_patient: (min, max) claims per patient, inclusive (default (1, 3)).
        as_of: Latest possible claim date. Defaults to today.
        date_range_days: Claim dates span this many days before as_of (default 180).
        random_seed: Seed for reproducibility. None = non-deterministic.

    Returns:
        DataFrame with columns: patient_mrn, patient_name, date_of_birth,
        street_address, city, state, postal_code, ssn, claim_date,
        claim_amount, diagnosis. Row count is n_patients × random draws
        in claims_per_patient range.

    Raises:
        ValueError: If states contains only unsupported codes.
    """
    if states is None:
        states = ["IA", "MO", "IL"]

    available = {s: _STATE_ZIPS[s] for s in states if s in _STATE_ZIPS}
    if not available:
        raise ValueError(
            f"No supported states in {states}. Supported: {sorted(_STATE_ZIPS)}"
        )

    rng = random.Random(random_seed)
    end_date = as_of or date.today()
    start_date = end_date - timedelta(days=date_range_days)
    min_claims, max_claims = claims_per_patient

    rows = []
    for i in range(n_patients):
        state = rng.choice(states)
        city, postal_code = rng.choice(available[state])
        mrn = f"MRN{1000000 + i}"
        name = f"{rng.choice(_FIRST_NAMES)} {rng.choice(_LAST_NAMES)}"
        dob = date(
            1940 + rng.randint(0, 60),
            rng.randint(1, 12),
            rng.randint(1, 28),
        )
        street = f"{rng.randint(100, 9999)} {rng.choice(_STREET_NAMES)}"
        ssn = f"{rng.randint(100, 999)}-{rng.randint(10, 99)}-{rng.randint(1000, 9999)}"

        for _ in range(rng.randint(min_claims, max_claims)):
            claim_date = start_date + timedelta(days=rng.randint(0, date_range_days))
            rows.append({
                "patient_mrn":    mrn,
                "patient_name":   name,
                "date_of_birth":  dob.isoformat(),
                "street_address": street,
                "city":           city,
                "state":          state,
                "postal_code":    postal_code,
                "ssn":            ssn,
                "claim_date":     claim_date.isoformat(),
                "claim_amount":   round(rng.uniform(500.0, 5000.0), 2),
                "diagnosis":      rng.choice(_DIAGNOSES),
            })

    return pd.DataFrame(rows)
