"""Synthetic multi-system MPI fixture for tests and demos.

Replicates Matthew's original test case (John/Johny/Johnathan Smith across
three systems; James/Jim Smooth across two systems) with deliberate edge cases.

No PHI — all names, addresses, and identifiers are fully synthetic.
All values are deterministic (no randomness).
"""

from __future__ import annotations

import pandas as pd

# System ID column names → also the keys in the SYSTEMS dict returned by make_mpi_fixture().
# FIXTURE_IDS maps a persona name to the system ID column that identifies it.
FIXTURE_IDS: dict[str, str] = {
    "john": "system_a_id",        # John Smith — exact baseline
    "johny": "system_b_id",       # Johny Smith — name variant of John
    "johnathan": "system_c_id",   # Johnathan Smith — longer name variant; also missing Postal_Code
    "james": "system_d_id",       # James Smooth — separate person, same DOB as Smith cluster
    "jim": "system_e_id",         # Jim Smooth — name variant of James; null DOB → secondary block
}


def make_mpi_fixture() -> dict[str, pd.DataFrame]:
    """Return a five-system MPI fixture dict {system_id_col: DataFrame}.

    Edge cases included:
    - John / Johny / Johnathan Smith → should cluster together (name variants).
    - James / Jim Smooth → separate cluster (different last name, address).
    - system_c (Johnathan) has no Postal_Code → tests missing-field weight normalization.
    - system_e (Jim Smooth) has null DOB → tests secondary blocking fallback.
    - Smith and Smooth clusters share the same DOB → tests that they do NOT merge.
    """
    # ---- Smith cluster -------------------------------------------------------
    # system_a: John Smith — canonical record
    system_a = pd.DataFrame(
        {
            "system_a_id": ["456"],
            "First_Name": ["John"],
            "Last_Name": ["Smith"],
            "Address": ["151 NW Something St"],
            "DOB": ["2025-01-06"],
            "City": ["Des Moines"],
            "Postal_Code": ["50131"],
            "State": ["IA"],
        }
    )

    # system_b: Johny Smith — first-name variant, otherwise identical
    system_b = pd.DataFrame(
        {
            "system_b_id": ["879"],
            "First_Name": ["Johny"],
            "Last_Name": ["Smith"],
            "Address": ["151 NW Something St"],
            "DOB": ["2025-01-06"],
            "City": ["Des Moines"],
            "Postal_Code": ["50131"],
            "State": ["IA"],
        }
    )

    # system_c: Johnathan Smith — longer first-name variant; intentionally no Postal_Code
    system_c = pd.DataFrame(
        {
            "system_c_id": ["1941"],
            "First_Name": ["Johnathan"],
            "Last_Name": ["Smith"],
            "Address": ["151 NW Something St"],
            "DOB": ["2025-01-06"],
            "City": ["Des Moines"],
            "State": ["IA"],
            # NO Postal_Code — edge case: missing field in one system
        }
    )

    # ---- Smooth cluster ------------------------------------------------------
    # system_d: James Smooth — different person from Smith cluster.
    # DOB is null so James falls into secondary blocking by Last_Name,
    # keeping him in a completely separate block from the Smith records.
    system_d = pd.DataFrame(
        {
            "system_d_id": ["124"],
            "First_Name": ["James"],
            "Last_Name": ["Smooth"],
            "Address": ["161 Something St"],
            "DOB": [None],
            "City": ["Des Moines"],
            "State": ["IA"],
        }
    )

    # system_e: Jim Smooth — first-name variant of James; null DOB → secondary block fallback
    system_e = pd.DataFrame(
        {
            "system_e_id": ["516"],
            "First_Name": ["Jim"],
            "Last_Name": ["Smooth"],
            "Address": ["1616 NW Something St"],
            "DOB": [None],           # null DOB → secondary block by Last_Name + Postal_Code
            "City": ["Des Moines"],
            "State": ["IA"],
        }
    )

    return {
        "system_a_id": system_a,
        "system_b_id": system_b,
        "system_c_id": system_c,
        "system_d_id": system_d,
        "system_e_id": system_e,
    }
