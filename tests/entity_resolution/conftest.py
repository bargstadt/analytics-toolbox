"""Shared fixtures for entity_resolution tests."""

from __future__ import annotations

import pandas as pd
import pytest

from analytics_toolbox.entity_resolution._config import EntityResolutionConfig


@pytest.fixture
def er_config() -> EntityResolutionConfig:
    """Minimal EntityResolutionConfig for unit tests."""
    return EntityResolutionConfig(match_threshold=0.80)


@pytest.fixture
def smith_systems() -> dict[str, pd.DataFrame]:
    """Three-system fixture: John/Johny/Johnathan Smith — should cluster together."""
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
    system_c = pd.DataFrame(
        {
            "system_c_id": ["1941"],
            "First_Name": ["Johnathan"],
            "Last_Name": ["Smith"],
            "Address": ["151 NW Something St"],
            "DOB": ["2025-01-06"],
            "City": ["Des Moines"],
            "State": ["IA"],
        }
    )
    return {"system_a_id": system_a, "system_b_id": system_b, "system_c_id": system_c}
