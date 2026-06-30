"""Tests for the analytics-toolbox CLI."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch


def test_help_exits_zero() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "analytics_toolbox.geospatial.cli", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "ingest-nad" in result.stdout or "ingest-nad" in result.stderr


def test_ingest_nad_help_exits_zero() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "analytics_toolbox.geospatial.cli", "ingest-nad", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0


def test_ingest_nad_missing_config_exits_nonzero() -> None:
    result = subprocess.run(
        [
            sys.executable, "-m", "analytics_toolbox.geospatial.cli",
            "ingest-nad", "--config", "/nonexistent.yaml",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0


def test_ingest_tiger_help_exits_zero() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "analytics_toolbox.geospatial.cli", "ingest-tiger", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "--config" in result.stdout or "--config" in result.stderr


def test_ingest_tiger_missing_config_exits_nonzero() -> None:
    result = subprocess.run(
        [
            sys.executable, "-m", "analytics_toolbox.geospatial.cli",
            "ingest-tiger", "--config", "/nonexistent.yaml",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0


@patch("analytics_toolbox.geospatial.nad_preprocess_ingest._ensure_national_txt")
def test_force_refresh_flag_overrides_config(
    mock_ensure: MagicMock, config_yaml_path: Path, tmp_path: Path, synthetic_nad_csv: str
) -> None:
    """--force-refresh on CLI must set force_refresh=True even when config has false."""
    from analytics_toolbox._config import load_config
    from analytics_toolbox.geospatial.cli import _run_ingest_nad

    txt_path = tmp_path / "nad_national.txt"
    txt_path.write_text(synthetic_nad_csv)
    mock_ensure.return_value = txt_path

    cfg = load_config(config_yaml_path).geospatial
    assert cfg.nad.force_refresh is False

    _run_ingest_nad(config_path=str(config_yaml_path), force_refresh=True)

    # force_refresh=True means we always read the national file for each state
    assert mock_ensure.call_count > 0
