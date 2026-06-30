import textwrap
from pathlib import Path

import pytest

from analytics_toolbox._config import AnalyticsToolboxConfig, load_config
from analytics_toolbox.geospatial._config import (
    GeospatialConfig,
    MatcherConfig,
    NadConfig,
    StorageConfig,
    TigerConfig,
)


@pytest.fixture
def config_file(tmp_path: Path) -> Path:
    content = textwrap.dedent("""
        storage:
          data_dir: ~/.local/share/analytics_toolbox/
          connection: ~/.local/share/analytics_toolbox/analytics_toolbox.duckdb

        geospatial:
          nad:
            states: [OR, WA]
            force_refresh: false
          tiger:
            vintage: 2024
            force_refresh: false
          matching:
            confidence_threshold: 90
    """)
    p = tmp_path / "config.yaml"
    p.write_text(content)
    return p


def test_valid_config_parses(config_file: Path) -> None:
    cfg = load_config(config_file)
    assert isinstance(cfg, AnalyticsToolboxConfig)
    assert isinstance(cfg.geospatial, GeospatialConfig)
    assert isinstance(cfg.geospatial.nad, NadConfig)
    assert isinstance(cfg.geospatial.tiger, TigerConfig)
    assert isinstance(cfg.geospatial.matching, MatcherConfig)
    assert isinstance(cfg.storage, StorageConfig)


def test_nad_fields(config_file: Path) -> None:
    cfg = load_config(config_file)
    assert cfg.geospatial.nad.states == ["OR", "WA"]
    assert cfg.geospatial.nad.force_refresh is False


def test_tiger_fields(config_file: Path) -> None:
    cfg = load_config(config_file)
    assert cfg.geospatial.tiger.vintage == 2024
    assert cfg.geospatial.tiger.force_refresh is False


def test_matching_fields(config_file: Path) -> None:
    cfg = load_config(config_file)
    assert cfg.geospatial.matching.confidence_threshold == 90


def test_storage_paths_expanded(config_file: Path) -> None:
    cfg = load_config(config_file)
    assert isinstance(cfg.storage.data_dir, Path)
    assert not str(cfg.storage.data_dir).startswith("~")
    assert isinstance(cfg.storage.connection, str)
    assert not cfg.storage.connection.startswith("~")


def test_defaults_applied(tmp_path: Path) -> None:
    minimal = textwrap.dedent("""
        storage:
          data_dir: /tmp/atb/
          connection: /tmp/atb/atb.duckdb
        geospatial:
          nad:
            states: [TX]
    """)
    p = tmp_path / "minimal.yaml"
    p.write_text(minimal)
    cfg = load_config(p)
    assert cfg.geospatial.nad.force_refresh is False
    assert cfg.geospatial.tiger.vintage == 2024
    assert cfg.geospatial.tiger.force_refresh is False
    assert cfg.geospatial.matching.confidence_threshold == 90


def test_unknown_keys_ignored(tmp_path: Path) -> None:
    content = textwrap.dedent("""
        storage:
          data_dir: /tmp/
          connection: /tmp/atb.duckdb
        geospatial:
          nad:
            states: [CA]
            force_refresh: false
            future_key: ignored
          tiger:
            vintage: 2023
        unknown_section:
          foo: bar
    """)
    p = tmp_path / "extra.yaml"
    p.write_text(content)
    cfg = load_config(p)
    assert cfg.geospatial.nad.states == ["CA"]


def test_missing_nad_states_raises(tmp_path: Path) -> None:
    content = textwrap.dedent("""
        storage:
          data_dir: /tmp/
          connection: /tmp/atb.duckdb
        geospatial:
          tiger:
            vintage: 2024
    """)
    p = tmp_path / "bad.yaml"
    p.write_text(content)
    with pytest.raises(ValueError, match="geospatial.nad.states"):
        load_config(p)


def test_missing_storage_connection_raises(tmp_path: Path) -> None:
    content = textwrap.dedent("""
        storage:
          data_dir: /tmp/
        geospatial:
          nad:
            states: [OR]
    """)
    p = tmp_path / "bad2.yaml"
    p.write_text(content)
    with pytest.raises(ValueError, match="storage.connection"):
        load_config(p)


def test_missing_storage_section_raises(tmp_path: Path) -> None:
    content = textwrap.dedent("""
        geospatial:
          nad:
            states: [OR]
    """)
    p = tmp_path / "bad3.yaml"
    p.write_text(content)
    with pytest.raises(ValueError, match="storage"):
        load_config(p)


def test_motherduck_connection_string(tmp_path: Path) -> None:
    content = textwrap.dedent("""
        storage:
          data_dir: /tmp/
          connection: "md:analytics_toolbox"
        geospatial:
          nad:
            states: [OR]
    """)
    p = tmp_path / "md.yaml"
    p.write_text(content)
    cfg = load_config(p)
    assert cfg.storage.connection == "md:analytics_toolbox"


def test_accepts_string_path(config_file: Path) -> None:
    cfg = load_config(str(config_file))
    assert cfg.geospatial.nad.states == ["OR", "WA"]


def test_geospatial_config_on_storage(config_file: Path) -> None:
    """geospatial slice carries its own storage reference (same object)."""
    cfg = load_config(config_file)
    assert cfg.geospatial.storage is cfg.storage
