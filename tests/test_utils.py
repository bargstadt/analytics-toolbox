"""Tests for analytics_toolbox.utils save helpers."""

from __future__ import annotations

import logging

import duckdb
import pandas as pd
import pytest

from analytics_toolbox import utils
from analytics_toolbox.utils import (
    in_memory_con,
    on_disk_con,
    save_csv,
    save_table,
)


@pytest.fixture
def df() -> pd.DataFrame:
    return pd.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]})


class TestConnectionHelpers:
    def test_in_memory_con_is_classified_memory(self):
        con = in_memory_con()
        assert utils._con_target(con) == ("memory", None)

    def test_on_disk_con_is_classified_disk(self, tmp_path):
        con = on_disk_con(tmp_path / "x.duckdb")
        kind, path = utils._con_target(con)
        assert kind == "disk"
        assert path is not None and path.endswith("x.duckdb")
        con.close()

    def test_on_disk_con_rejects_motherduck(self):
        with pytest.raises(ValueError, match="MotherDuck"):
            on_disk_con("md:my_cloud_db")

    def test_on_disk_con_expands_user(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        con = on_disk_con("~/db.duckdb")
        _, path = utils._con_target(con)
        assert str(tmp_path) in path
        con.close()


class TestInMemoryWrites:
    def test_no_certification_needed(self, df):
        con = in_memory_con()
        save_table(df, "patients", con=con)  # no certify_no_phi — and that's fine
        assert con.execute("SELECT count(*) FROM patients").fetchone()[0] == 3

    def test_in_memory_log_says_not_persisted(self, df, caplog):
        con = in_memory_con()
        with caplog.at_level(logging.INFO, logger="analytics_toolbox.utils"):
            save_table(df, "patients", con=con)
        msg = " ".join(r.getMessage() for r in caplog.records)
        assert "in-memory" in msg and "not persisted" in msg
        assert "certified" not in msg  # no certification claim for memory


class TestDiskWrites:
    def test_disk_requires_certification(self, df, tmp_path):
        con = on_disk_con(tmp_path / "x.duckdb")
        with pytest.raises(ValueError, match="certify_no_phi"):
            save_table(df, "t", con=con)
        con.close()

    def test_disk_write_persists(self, df, tmp_path):
        db = tmp_path / "x.duckdb"
        con = on_disk_con(db)
        save_table(df, "patients", con=con, certify_no_phi=True)
        con.close()
        reopened = duckdb.connect(str(db), read_only=True)
        try:
            assert reopened.execute("SELECT count(*) FROM patients").fetchone()[0] == 3
        finally:
            reopened.close()

    def test_disk_audit_log_includes_path_and_certification(self, df, tmp_path, caplog):
        con = on_disk_con(tmp_path / "x.duckdb")
        with caplog.at_level(logging.INFO, logger="analytics_toolbox.utils"):
            save_table(df, "patients", con=con, certify_no_phi=True)
        msg = " ".join(r.getMessage() for r in caplog.records)
        assert "3 rows" in msg and "2 cols" in msg
        assert "patients" in msg
        assert "x.duckdb" in msg
        assert "certified no PII/PHI" in msg
        con.close()


class TestCloudLockdown:
    """MotherDuck/cloud writes are locked down hardest — data leaves the machine."""

    def _force_cloud(self, monkeypatch):
        monkeypatch.setattr(utils, "_con_target", lambda con: ("cloud", "md:demo"))

    def test_cloud_requires_certification(self, df, monkeypatch):
        self._force_cloud(monkeypatch)
        con = in_memory_con()  # real con; classification is monkeypatched to "cloud"
        with pytest.raises(ValueError, match="certify_no_phi"):
            save_table(df, "t", con=con, allow_cloud_egress=True)

    def test_cloud_requires_explicit_egress_flag(self, df, monkeypatch):
        self._force_cloud(monkeypatch)
        con = in_memory_con()
        with pytest.raises(ValueError, match="allow_cloud_egress"):
            save_table(df, "t", con=con, certify_no_phi=True)

    def test_cloud_write_warns_when_fully_authorized(self, df, monkeypatch, caplog):
        self._force_cloud(monkeypatch)
        con = in_memory_con()
        with caplog.at_level(logging.WARNING, logger="analytics_toolbox.utils"):
            save_table(df, "t", con=con, certify_no_phi=True, allow_cloud_egress=True)
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert warnings, "expected a cloud-egress warning"
        assert "LEAVING THE MACHINE" in warnings[0].getMessage()


class TestIfExists:
    def test_error_is_default(self, df):
        con = in_memory_con()
        save_table(df, "t", con=con)
        with pytest.raises(ValueError, match="already exists"):
            save_table(df, "t", con=con)

    def test_replace(self, df):
        con = in_memory_con()
        save_table(df, "t", con=con)
        save_table(df.head(1), "t", con=con, if_exists="replace")
        assert con.execute("SELECT count(*) FROM t").fetchone()[0] == 1

    def test_append(self, df):
        con = in_memory_con()
        save_table(df, "t", con=con)
        save_table(df, "t", con=con, if_exists="append")
        assert con.execute("SELECT count(*) FROM t").fetchone()[0] == 6

    def test_append_creates_when_absent(self, df):
        con = in_memory_con()
        save_table(df, "t", con=con, if_exists="append")
        assert con.execute("SELECT count(*) FROM t").fetchone()[0] == 3

    def test_invalid_if_exists_raises(self, df):
        con = in_memory_con()
        with pytest.raises(ValueError, match="if_exists"):
            save_table(df, "t", con=con, if_exists="upsert")


class TestValidation:
    def test_rejects_injection_in_table_name(self, df):
        con = in_memory_con()
        with pytest.raises(ValueError, match="valid table identifier"):
            save_table(df, "t; DROP TABLE x; --", con=con)

    def test_allows_schema_qualified_name(self, df):
        con = in_memory_con()
        con.execute("CREATE SCHEMA s")
        save_table(df, "s.patients", con=con)
        assert con.execute("SELECT count(*) FROM s.patients").fetchone()[0] == 3


class TestAuditLogNoData:
    def test_log_carries_counts_not_cell_values(self, tmp_path, caplog):
        secret = pd.DataFrame({"name": ["ZZQQX_SENTINEL"], "ssn": ["000-00-9999"]})
        con = on_disk_con(tmp_path / "x.duckdb")
        with caplog.at_level(logging.INFO, logger="analytics_toolbox.utils"):
            save_table(secret, "patients", con=con, certify_no_phi=True)
        msg = " ".join(r.getMessage() for r in caplog.records)
        assert "1 rows" in msg and "2 cols" in msg
        assert "ZZQQX_SENTINEL" not in msg
        assert "000-00-9999" not in msg
        con.close()


class TestSaveCsv:
    def test_without_certification_raises(self, df, tmp_path):
        with pytest.raises(ValueError, match="certify_no_phi must be set to True"):
            save_csv(df, tmp_path / "out.csv")
        assert not (tmp_path / "out.csv").exists()

    def test_truthy_non_true_is_rejected(self, df, tmp_path):
        with pytest.raises(ValueError, match="certify_no_phi"):
            save_csv(df, tmp_path / "out.csv", certify_no_phi=1)

    def test_writes_file(self, df, tmp_path):
        path = tmp_path / "out.csv"
        save_csv(df, path, certify_no_phi=True)
        back = pd.read_csv(path)
        assert list(back.columns) == ["a", "b"]
        assert len(back) == 3

    def test_index_omitted_by_default(self, df, tmp_path):
        path = tmp_path / "out.csv"
        save_csv(df, path, certify_no_phi=True)
        assert "Unnamed: 0" not in pd.read_csv(path).columns

    def test_refuses_to_clobber(self, df, tmp_path):
        path = tmp_path / "out.csv"
        save_csv(df, path, certify_no_phi=True)
        with pytest.raises(FileExistsError):
            save_csv(df, path, certify_no_phi=True)

    def test_overwrite_allowed_when_requested(self, df, tmp_path):
        path = tmp_path / "out.csv"
        save_csv(df, path, certify_no_phi=True)
        save_csv(df.head(1), path, certify_no_phi=True, overwrite=True)
        assert len(pd.read_csv(path)) == 1
