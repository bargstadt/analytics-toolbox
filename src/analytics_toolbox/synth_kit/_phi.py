"""PHI replacement: Faker wrappers, sequential IDs, and soft NAD integration."""

from __future__ import annotations

import datetime
import random
from typing import Any

from faker import Faker


class PhiReplacer:
    """Stateful PHI replacement engine for a single synthesize() call.

    Sequential ID counters are scoped to the instance so they reset between
    calls. Faker locale defaults to en_US.

    Args:
        random_seed: Seed for Faker and Python random. None means non-reproducible.
        nad_address_pool: Pre-loaded list of real NAD street addresses to cycle
            through for address PHI replacement. When empty, falls back to Faker.
    """

    def __init__(
        self,
        random_seed: int | None = None,
        nad_address_pool: list[str] | None = None,
    ) -> None:
        self._fake = Faker("en_US")
        self._rng = random.Random(random_seed)
        if random_seed is not None:
            self._fake.seed_instance(random_seed)
        self._ssn_counter = 1
        self._mrn_counter = 1
        self._member_id_counter = 1
        self._account_counter = 1
        self._license_counter = 1
        self._nad_pool: list[str] = list(nad_address_pool or [])
        if self._nad_pool:
            self._rng.shuffle(self._nad_pool)
        self._nad_idx = 0
        self._dispatch = {
            "name":      self._replace_name,
            "dob":       self._replace_dob,
            "date_phi":  self._replace_date_phi,
            "phone":     self._replace_phone,
            "email":     self._replace_email,
            "ssn":       self._replace_ssn,
            "mrn":       self._replace_mrn,
            "member_id": self._replace_member_id,
            "account":   self._replace_account,
            "address":   self._replace_address,
            "ip":        self._replace_ip,
            "url":       self._replace_url,
            "license":   self._replace_license,
            "device_id": self._replace_device_id,
            "vin":       self._replace_vin,
        }

    # ── dispatch ──────────────────────────────────────────────────────────────

    def replace(
        self,
        column: str,
        phi_type: str,
        source_values: list[Any],
    ) -> list[Any]:
        """Replace every value in source_values with a synthetic PHI substitute.

        Args:
            column: Column name (used for name sub-type heuristics).
            phi_type: PHI type string from the detect_phi registry.
            source_values: Raw values (length determines output length).

        Returns:
            List of synthetic values, same length as source_values.
        """
        fn = self._dispatch.get(phi_type, self._replace_generic)
        return [fn(column) for _ in range(len(source_values))]

    # ── name ─────────────────────────────────────────────────────────────────

    def _replace_name(self, column: str) -> str:
        col = column.lower()
        if "first" in col or col in ("fname",):
            return self._fake.first_name()
        if "last" in col or col in ("lname",):
            return self._fake.last_name()
        return self._fake.name()

    # ── dates ─────────────────────────────────────────────────────────────────

    def _replace_dob(self, _column: str) -> datetime.date:
        base = self._fake.date_of_birth(minimum_age=0, maximum_age=100)
        shift = self._rng.randint(-30, 30)
        return base + datetime.timedelta(days=shift)

    def _replace_date_phi(self, _column: str) -> datetime.date:
        # Fixed historical window (1986–2011 as of 2026) — clearly separated from
        # modern healthcare encounter data and eliminates coincidental date collisions.
        return self._fake.date_between(start_date="-40y", end_date="-15y")

    # ── contact ───────────────────────────────────────────────────────────────

    def _replace_phone(self, _column: str) -> str:
        return self._fake.phone_number()

    def _replace_email(self, _column: str) -> str:
        return self._fake.email()

    # ── sequential IDs ────────────────────────────────────────────────────────

    def _replace_ssn(self, _column: str) -> str:
        val = f"{self._ssn_counter:09d}"
        self._ssn_counter += 1
        return val

    def _replace_mrn(self, _column: str) -> str:
        val = f"MRN{self._mrn_counter:08d}"
        self._mrn_counter += 1
        return val

    def _replace_member_id(self, _column: str) -> str:
        val = f"MBR{self._member_id_counter:010d}"
        self._member_id_counter += 1
        return val

    def _replace_account(self, _column: str) -> int:
        val = self._account_counter
        self._account_counter += 1
        return val

    def _replace_license(self, _column: str) -> str:
        val = f"LIC{self._license_counter:07d}"
        self._license_counter += 1
        return val

    # ── address ───────────────────────────────────────────────────────────────

    def _replace_address(self, _column: str) -> str:
        if self._nad_pool:
            # Cycles through the pool; callers needing uniqueness should increase pool size
            addr = self._nad_pool[self._nad_idx % len(self._nad_pool)]
            self._nad_idx += 1
            return addr
        return self._fake.street_address()

    # ── network / identifiers ─────────────────────────────────────────────────

    def _replace_ip(self, _column: str) -> str:
        return self._fake.ipv4_private()

    def _replace_url(self, _column: str) -> str:
        return self._fake.url()

    def _replace_device_id(self, _column: str) -> str:
        return self._fake.uuid4()

    def _replace_vin(self, _column: str) -> str:
        return self._fake.vin()

    def _replace_generic(self, _column: str) -> str:
        return self._fake.word()
