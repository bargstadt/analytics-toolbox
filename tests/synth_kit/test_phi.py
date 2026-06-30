"""Tests for _phi.py — PHI replacement via Faker and sequential IDs."""

from __future__ import annotations

import re

import pytest

from analytics_toolbox.synth_kit._phi import PhiReplacer


@pytest.fixture
def replacer() -> PhiReplacer:
    return PhiReplacer(random_seed=42)


# ── general: no source values in output ──────────────────────────────────────

def test_name_replacement_not_from_source(replacer: PhiReplacer) -> None:
    source = ["Patient 1", "Patient 2", "Patient 3"]
    result = replacer.replace("patient_name", "name", source)
    assert len(result) == 3
    # Faker names won't be "Patient N"
    assert not any(v in source for v in result)


def test_email_replacement_valid_format(replacer: PhiReplacer) -> None:
    source = ["alice@example.com", "bob@example.com"]
    result = replacer.replace("email_address", "email", source)
    email_re = re.compile(r"[^@]+@[^@]+\.[^@]+")
    assert all(email_re.match(str(v)) for v in result)


def test_phone_replacement_returns_strings(replacer: PhiReplacer) -> None:
    source = ["555-1234", "555-5678"]
    result = replacer.replace("phone", "phone", source)
    assert len(result) == 2
    assert all(isinstance(v, str) for v in result)


def test_dob_replacement_valid_dates(replacer: PhiReplacer) -> None:
    import datetime
    source = ["1980-01-01", "1990-06-15"]
    result = replacer.replace("dob", "dob", source)
    for v in result:
        assert isinstance(v, datetime.date)


def test_date_phi_replacement_valid_dates(replacer: PhiReplacer) -> None:
    import datetime
    source = ["2023-01-01", "2023-03-15"]
    result = replacer.replace("admit_date", "date_phi", source)
    for v in result:
        assert isinstance(v, datetime.date)


# ── sequential ID types ───────────────────────────────────────────────────────

def test_ssn_sequential_non_overlapping(replacer: PhiReplacer) -> None:
    source = ["123-45-6789", "987-65-4321", "000-00-0001"]
    result = replacer.replace("ssn", "ssn", source)
    # all distinct
    assert len(set(result)) == 3
    # formatted as 9 digits
    assert all(re.match(r"^\d{9}$", str(v)) for v in result)


def test_mrn_format(replacer: PhiReplacer) -> None:
    source = ["MRN001", "MRN002"]
    result = replacer.replace("mrn", "mrn", source)
    assert all(re.match(r"^MRN\d{8}$", str(v)) for v in result)


def test_member_id_format(replacer: PhiReplacer) -> None:
    source = ["M001", "M002"]
    result = replacer.replace("member_id", "member_id", source)
    assert all(re.match(r"^MBR\d{10}$", str(v)) for v in result)


def test_account_format(replacer: PhiReplacer) -> None:
    source = ["ACC001", "ACC002", "ACC003"]
    result = replacer.replace("account_id", "account", source)
    assert len(set(result)) == 3
    assert all(isinstance(v, int) for v in result)


def test_license_format(replacer: PhiReplacer) -> None:
    source = ["LIC123", "LIC456"]
    result = replacer.replace("license_number", "license", source)
    assert all(re.match(r"^LIC\d{7}$", str(v)) for v in result)


# ── sequential IDs reset between PhiReplacer instances ───────────────────────

def test_sequential_ids_reset_between_instances() -> None:
    r1 = PhiReplacer(random_seed=0)
    r2 = PhiReplacer(random_seed=0)
    result1 = r1.replace("ssn", "ssn", ["a", "b"])
    result2 = r2.replace("ssn", "ssn", ["a", "b"])
    assert result1 == result2


# ── Faker-based PHI types ─────────────────────────────────────────────────────

def test_ip_replacement_format(replacer: PhiReplacer) -> None:
    source = ["1.2.3.4", "5.6.7.8"]
    result = replacer.replace("ip_address", "ip", source)
    ip_re = re.compile(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$")
    assert all(ip_re.match(str(v)) for v in result)


def test_url_replacement_format(replacer: PhiReplacer) -> None:
    source = ["https://example.com", "http://foo.org"]
    result = replacer.replace("website", "url", source)
    assert all(str(v).startswith("http") for v in result)


def test_device_id_is_uuid(replacer: PhiReplacer) -> None:
    source = ["dev-001", "dev-002"]
    result = replacer.replace("device_id", "device_id", source)
    uuid_re = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
    assert all(uuid_re.match(str(v).lower()) for v in result)


def test_vin_replacement_length(replacer: PhiReplacer) -> None:
    source = ["1HGBH41JXMN109186", "2C4RC1BG2ER123456"]
    result = replacer.replace("vin", "vin", source)
    assert all(len(str(v)) == 17 for v in result)


def test_address_faker_fallback(replacer: PhiReplacer) -> None:
    """When NAD is unavailable, address falls back to faker.street_address()."""
    source = ["123 Main St", "456 Oak Ave"]
    result = replacer.replace("address", "address", source)
    assert len(result) == 2
    assert all(isinstance(v, str) and len(v) > 0 for v in result)


# ── output length always matches input ───────────────────────────────────────

@pytest.mark.parametrize("phi_type", [
    "name", "dob", "date_phi", "phone", "email", "ssn",
    "mrn", "member_id", "account", "address", "ip", "url",
    "license", "device_id", "vin",
])
def test_output_length_matches_input(phi_type: str, replacer: PhiReplacer) -> None:
    source = ["val1", "val2", "val3", "val4", "val5"]
    result = replacer.replace("col", phi_type, source)
    assert len(result) == 5
