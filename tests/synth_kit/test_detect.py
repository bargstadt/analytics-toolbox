"""Tests for _detect.py — PHI pattern registry and detect_phi()."""

from __future__ import annotations

import pytest

from analytics_toolbox.synth_kit._detect import detect_phi

# ── auto-detection: one representative column per PHI type ──────────────────

@pytest.mark.parametrize("col, expected", [
    # name
    ("patient_name",     "name"),
    ("first_name",       "name"),
    ("firstname",        "name"),
    ("last_name",        "name"),
    ("full_name",        "name"),
    ("provider_name",    "name"),
    ("member_name",      "name"),
    ("name",             "name"),
    ("fname",            "name"),
    ("lname",            "name"),
    # dob
    ("date_of_birth",    "dob"),
    ("dob",              "dob"),
    ("birthdate",        "dob"),
    ("birth_date",       "dob"),
    # date_phi
    ("admit_date",       "date_phi"),
    ("discharge_date",   "date_phi"),
    ("death_date",       "date_phi"),
    ("procedure_date",   "date_phi"),
    ("service_date",     "date_phi"),
    ("visit_date",       "date_phi"),
    ("date_of_death",    "date_phi"),
    ("date_of_service",  "date_phi"),
    # phone
    ("phone",            "phone"),
    ("phone_number",     "phone"),
    ("telephone",        "phone"),
    ("fax",              "phone"),
    ("mobile_num",       "phone"),
    ("cell_no",          "phone"),
    # email
    ("email",            "email"),
    ("email_address",    "email"),
    ("e_mail",           "email"),
    # ssn
    ("ssn",              "ssn"),
    ("social_security",  "ssn"),
    ("social_security_number", "ssn"),
    # mrn
    ("mrn",              "mrn"),
    ("medical_record_number", "mrn"),
    ("patient_id",       "mrn"),
    # member_id
    ("member_id",        "member_id"),
    ("beneficiary_number", "member_id"),
    ("subscriber_num",   "member_id"),
    ("enrollee_id",      "member_id"),
    # account
    ("account_number",   "account"),
    ("account_id",       "account"),
    ("acct_no",          "account"),
    # address
    ("address",          "address"),
    ("street_address",   "address"),
    ("addr_line1",       "address"),
    ("address_line_2",   "address"),
    # ip
    ("ip_address",       "ip"),
    ("ipv4",             "ip"),
    ("ipv6",             "ip"),
    # url
    ("url",              "url"),
    ("website",          "url"),
    ("homepage",         "url"),
    # license
    ("license_number",   "license"),
    ("licence_no",       "license"),
    ("npi",              "license"),
    # device_id
    ("device_id",        "device_id"),
    ("equipment_serial", "device_id"),
    # vin
    ("vin",              "vin"),
    ("vehicle_id",       "vin"),
])
def test_auto_detect_single_column(col: str, expected: str) -> None:
    result = detect_phi([col])
    assert result.get(col) == expected, f"Expected {col!r} → {expected!r}, got {result.get(col)!r}"


# ── columns that should NOT be detected as PHI ──────────────────────────────

@pytest.mark.parametrize("col", [
    "id",
    "age",
    "zip_code",
    "state",
    "county",
    "salary",
    "score",
    "category",
    "status",
    "join_date",
    "year",
    "icd_code",
    "cpt_code",
    "race",
    "ethnicity",
])
def test_non_phi_columns_not_detected(col: str) -> None:
    result = detect_phi([col])
    assert col not in result, f"Column {col!r} should not be detected as PHI"


# ── phi_overrides ────────────────────────────────────────────────────────────

def test_overrides_add_new_column() -> None:
    result = detect_phi(["score"], phi_overrides={"score": "ssn"})
    assert result["score"] == "ssn"


def test_overrides_change_detected_type() -> None:
    result = detect_phi(["ssn"], phi_overrides={"ssn": "mrn"})
    assert result["ssn"] == "mrn"


def test_overrides_independent_of_auto_detection() -> None:
    result = detect_phi(["patient_name", "age"], phi_overrides={"age": "mrn"})
    assert result["patient_name"] == "name"
    assert result["age"] == "mrn"


# ── suppress_phi ─────────────────────────────────────────────────────────────

def test_suppress_removes_detected_column() -> None:
    with pytest.warns(UserWarning, match="patient_name"):
        result = detect_phi(["patient_name"], suppress_phi=["patient_name"])
    assert "patient_name" not in result


def test_suppress_emits_warning_per_suppressed_column() -> None:
    cols = ["patient_name", "email_address"]
    with pytest.warns(UserWarning) as record:
        detect_phi(cols, suppress_phi=cols)
    # one warning per suppressed PHI column
    assert len(record) >= 2


def test_suppress_non_phi_column_no_warning(recwarn: pytest.WarningsChecker) -> None:
    """Suppressing a non-PHI column emits no warning — nothing was detected."""
    result = detect_phi(["salary"], suppress_phi=["salary"])
    assert "salary" not in result
    assert len(recwarn) == 0


# ── case-insensitivity ────────────────────────────────────────────────────────

def test_detection_is_case_insensitive() -> None:
    result = detect_phi(["PATIENT_NAME", "Email_Address", "SSN"])
    assert result.get("PATIENT_NAME") == "name"
    assert result.get("Email_Address") == "email"
    assert result.get("SSN") == "ssn"


# ── multiple columns, multiple PHI types ──────────────────────────────────────

def test_multi_column_detection() -> None:
    cols = ["age", "patient_name", "dob", "salary", "email_address", "ssn", "status"]
    result = detect_phi(cols)
    assert set(result.keys()) == {"patient_name", "dob", "email_address", "ssn"}
    assert result["patient_name"] == "name"
    assert result["dob"] == "dob"
    assert result["email_address"] == "email"
    assert result["ssn"] == "ssn"
