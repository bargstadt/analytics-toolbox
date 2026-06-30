"""PHI column auto-detection via column name pattern matching."""

from __future__ import annotations

import logging
import re
import warnings

from analytics_toolbox.synth_kit._types import PhiMap

logger = logging.getLogger(__name__)

# (phi_type, compiled_regex) — first full-match against lowercased column name wins
_REGISTRY: list[tuple[str, re.Pattern[str]]] = [
    ("name", re.compile(r"(first|last|full|patient|member|provider)_?name|^name$|^fname$|^lname$")),
    ("dob",       re.compile(r"date_?of_?birth|^dob$|birth_?date|^birthdate$")),
    (
        "date_phi",
        re.compile(
            r"(admit|discharge|death|procedure|service|visit)_?date"
            r"|date_?of_?(death|service|admission|discharge)"
        ),
    ),
    ("phone",     re.compile(r"(phone|telephone|fax|mobile|cell)(_?number|_?num|_?no)?$")),
    ("email",     re.compile(r"(email|e_?mail)(_?address)?$")),
    ("ssn",       re.compile(r"^ssn$|social_?security(_?number)?")),
    ("mrn",       re.compile(r"^mrn$|medical_?record(_?number)?|^patient_?id$")),
    ("member_id", re.compile(r"(member|beneficiary|subscriber|enrollee)_?(id|number|num)$")),
    ("account",   re.compile(r"(account|acct)_?(number|num|no|id)$")),
    ("ip",        re.compile(r"ip_?address|ip_?addr|^ipv4$|^ipv6$")),
    ("address",   re.compile(r"(street|address|addr)(_?line)?(_?1|_?2)?$|^address$")),
    ("url",       re.compile(r"^url$|^website$|web_?site|^homepage$")),
    ("license",   re.compile(r"(license|licence)_?(number|num|no)|^npi$")),
    ("device_id", re.compile(r"(device|equipment)_?(id|serial|identifier)$")),
    ("vin",       re.compile(r"^vin$|vehicle_?(id|identification)")),
]


def _match_phi_type(col: str) -> str | None:
    """Return the first matching PHI type for a lowercased column name, or None."""
    lower = col.lower()
    for phi_type, pattern in _REGISTRY:
        if pattern.search(lower):
            return phi_type
    return None


def detect_phi(
    columns: list[str],
    phi_overrides: dict[str, str] | None = None,
    suppress_phi: list[str] | None = None,
) -> PhiMap:
    """Detect PHI columns from column names.

    Args:
        columns: Column names from the query result schema.
        phi_overrides: Force-classify columns (merged after auto-detection).
        suppress_phi: Column names to exclude from PHI detection; emits a
            WARNING for each column that would otherwise have been detected.

    Returns:
        Mapping of column name → PHI type string.
    """
    phi: PhiMap = {}

    suppress_set: set[str] = set(suppress_phi or [])

    for col in columns:
        phi_type = _match_phi_type(col)
        if phi_type is not None:
            if col in suppress_set:
                warnings.warn(
                    f"PHI suppressed for column {col!r} (detected type: {phi_type}). "
                    "Ensure this column does not contain protected health information.",
                    stacklevel=2,
                )
            else:
                phi[col] = phi_type

    for col, phi_type in (phi_overrides or {}).items():
        phi[col] = phi_type

    return phi
