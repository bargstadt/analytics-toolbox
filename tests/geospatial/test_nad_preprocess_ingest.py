"""Tests for nad_preprocess_ingest.

All network calls are mocked by patching _ensure_national_txt to return a
synthetic national text file — no real downloads ever happen in this suite.
The synthetic NAD CSV (Release 22 column names) is defined in conftest.py.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import duckdb
import pytest

from analytics_toolbox._config import load_config
from analytics_toolbox.geospatial._config import GeospatialConfig
from analytics_toolbox.geospatial.nad_preprocess_ingest import ingest_nad


def _open_db(config: GeospatialConfig) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(config.storage.connection)


@pytest.fixture
def config(config_yaml_path: Path) -> GeospatialConfig:
    return load_config(config_yaml_path).geospatial


@pytest.fixture
def synthetic_nad_txt(tmp_path: Path, synthetic_nad_csv: str) -> Path:
    """Write the synthetic NAD CSV to a temp file, simulating the extracted national TXT."""
    txt_path = tmp_path / "nad_national.txt"
    txt_path.write_text(synthetic_nad_csv)
    return txt_path


@patch("analytics_toolbox.geospatial.nad_preprocess_ingest._ensure_national_txt")
def test_ingest_loads_rows(
    mock_ensure: MagicMock, config: GeospatialConfig, synthetic_nad_txt: Path
) -> None:
    mock_ensure.return_value = synthetic_nad_txt

    ingest_nad(config)

    conn = _open_db(config)
    count = conn.execute("SELECT COUNT(*) FROM nad_addresses").fetchone()[0]
    conn.close()
    assert count > 0


@patch("analytics_toolbox.geospatial.nad_preprocess_ingest._ensure_national_txt")
def test_ingest_only_requested_states(
    mock_ensure: MagicMock, config: GeospatialConfig, synthetic_nad_txt: Path
) -> None:
    mock_ensure.return_value = synthetic_nad_txt

    ingest_nad(config)

    conn = _open_db(config)
    states = {r[0] for r in conn.execute("SELECT DISTINCT state FROM nad_addresses").fetchall()}
    conn.close()
    # config requests OR and WA; TX rows in the national file should not be ingested
    assert "TX" not in states
    assert states.issubset({"OR", "WA"})


@patch("analytics_toolbox.geospatial.nad_preprocess_ingest._ensure_national_txt")
def test_ingest_idempotent_skip(
    mock_ensure: MagicMock, config: GeospatialConfig, synthetic_nad_txt: Path
) -> None:
    mock_ensure.return_value = synthetic_nad_txt
    ingest_nad(config)

    conn = _open_db(config)
    count_after_first = conn.execute("SELECT COUNT(*) FROM nad_addresses").fetchone()[0]
    conn.close()

    # Second call — rows already exist, should skip without calling _ensure_national_txt again
    ingest_nad(config)

    conn = _open_db(config)
    count_after_second = conn.execute("SELECT COUNT(*) FROM nad_addresses").fetchone()[0]
    conn.close()

    assert count_after_second == count_after_first
    # _ensure_national_txt called once per state on first ingest (OR + WA = 2),
    # never on second ingest (early return due to existing rows)
    assert mock_ensure.call_count == len(config.nad.states)


@patch("analytics_toolbox.geospatial.nad_preprocess_ingest._ensure_national_txt")
def test_force_refresh_replaces_state_rows(
    mock_ensure: MagicMock, config: GeospatialConfig, synthetic_nad_txt: Path
) -> None:
    from analytics_toolbox.geospatial._config import NadConfig

    mock_ensure.return_value = synthetic_nad_txt

    # First ingest: OR + WA
    ingest_nad(config)

    conn = _open_db(config)
    wa_count_before = conn.execute(
        "SELECT COUNT(*) FROM nad_addresses WHERE state='WA'"
    ).fetchone()[0]
    or_count_before = conn.execute(
        "SELECT COUNT(*) FROM nad_addresses WHERE state='OR'"
    ).fetchone()[0]
    conn.close()
    assert wa_count_before > 0
    assert or_count_before > 0

    # Re-ingest only OR with force_refresh=True — WA rows must survive untouched
    or_only_config = GeospatialConfig(
        nad=NadConfig(states=["OR"], force_refresh=True),
        tiger=config.tiger,
        matching=config.matching,
        storage=config.storage,
    )

    ingest_nad(or_only_config)

    conn = _open_db(config)
    wa_count_after = conn.execute(
        "SELECT COUNT(*) FROM nad_addresses WHERE state='WA'"
    ).fetchone()[0]
    conn.close()
    assert wa_count_after == wa_count_before


@patch("analytics_toolbox.geospatial.nad_preprocess_ingest._ensure_national_txt")
def test_non_standard_addresses_included(
    mock_ensure: MagicMock, config: GeospatialConfig, synthetic_nad_txt: Path
) -> None:
    """PO box and military rows in the source CSV are ingested with is_standard_address=False."""
    mock_ensure.return_value = synthetic_nad_txt
    ingest_nad(config)

    conn = _open_db(config)
    non_standard = conn.execute(
        "SELECT COUNT(*) FROM nad_addresses WHERE is_standard_address = false"
    ).fetchone()[0]
    conn.close()
    assert non_standard >= 1


@patch("analytics_toolbox.geospatial.nad_preprocess_ingest._ensure_national_txt")
def test_csv_with_no_matching_state_rows_skips_gracefully(
    mock_ensure: MagicMock, config: GeospatialConfig, tmp_path: Path
) -> None:
    """If the national file contains no rows for the requested state, ingest warns
    and skips without raising and without inserting any rows."""
    # National file with only TX rows (Release 22 column names); config requests OR and WA
    tx_only_csv = (
        "OID_,Add_Number,St_PreDir,St_Name,St_PosTyp,St_PosDir,Post_City,State,"
        "Zip_Code,County,Latitude,Longitude,UUID\n"
        "1,100,,MAIN,ST,,DALLAS,TX,75201,DALLAS,32.7767,-96.7970,uuid-tx-1\n"
    )
    tx_only_txt = tmp_path / "tx_only.txt"
    tx_only_txt.write_text(tx_only_csv)
    mock_ensure.return_value = tx_only_txt

    ingest_nad(config)  # should not raise

    conn = _open_db(config)
    count = conn.execute("SELECT COUNT(*) FROM nad_addresses").fetchone()[0]
    conn.close()
    assert count == 0


@patch("analytics_toolbox.geospatial.nad_preprocess_ingest._ensure_national_txt")
def test_required_columns_present(
    mock_ensure: MagicMock, config: GeospatialConfig, synthetic_nad_txt: Path
) -> None:
    mock_ensure.return_value = synthetic_nad_txt
    ingest_nad(config)

    conn = _open_db(config)
    cols = {r[0] for r in conn.execute("DESCRIBE nad_addresses").fetchall()}
    conn.close()

    expected = {
        "nad_id", "state", "county_fips", "nad_city", "postal_code",
        "nad_address_line_1", "normalized_address_line_1",
        "normalized_postal_code", "latitude", "longitude",
        "is_standard_address", "address_flag",
    }
    assert expected.issubset(cols)


@patch("analytics_toolbox.geospatial.nad_preprocess_ingest._ensure_national_txt")
def test_nad_id_sourced_from_oid(
    mock_ensure: MagicMock, config: GeospatialConfig, synthetic_nad_txt: Path
) -> None:
    """OID_ is the unique NAD Release 22 record identifier and must be used as nad_id."""
    mock_ensure.return_value = synthetic_nad_txt
    ingest_nad(config)

    conn = _open_db(config)
    nad_ids = {r[0] for r in conn.execute("SELECT DISTINCT nad_id FROM nad_addresses").fetchall()}
    conn.close()
    # Synthetic CSV uses OID_ values "1", "2", etc. — not house numbers like "100", "200"
    assert not any(nid in {"100", "200", "300", "400", "500", "600"} for nid in nad_ids)
    # All ingested nad_ids should be non-empty strings
    assert all(nid for nid in nad_ids)


@patch("analytics_toolbox.geospatial.nad_preprocess_ingest._ensure_national_txt")
def test_directional_street_reconstruction(
    mock_ensure: MagicMock, config: GeospatialConfig, synthetic_nad_txt: Path
) -> None:
    """St_PreDir is included in nad_address_line_1 (e.g. '123 NW BURNSIDE ST')."""
    mock_ensure.return_value = synthetic_nad_txt
    ingest_nad(config)

    conn = _open_db(config)
    # The synthetic CSV has OID_=9 with St_PreDir=NW
    row = conn.execute(
        "SELECT nad_address_line_1 FROM nad_addresses WHERE nad_address_line_1 LIKE '%NW%'"
    ).fetchone()
    conn.close()

    assert row is not None, "No NW-directional row found after ingest"
    assert "NW" in row[0], f"Expected directional 'NW' in street line, got: {row[0]}"
