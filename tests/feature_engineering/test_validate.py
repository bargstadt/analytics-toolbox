import pytest

from analytics_toolbox.feature_engineering._validate import (
    validate_agg_name_no_collision,
    validate_columns_present,
    validate_namespace,
    validate_no_nulls_in_grain,
    validate_spine_uniqueness,
    validate_windows,
)

# ---------------------------------------------------------------------------
# validate_namespace
# ---------------------------------------------------------------------------

class TestValidateNamespace:
    def test_valid(self):
        validate_namespace("rx")  # no raise

    def test_valid_with_digits(self):
        validate_namespace("rx2")

    def test_empty(self):
        with pytest.raises(ValueError, match="namespace"):
            validate_namespace("")

    def test_has_underscore(self):
        with pytest.raises(ValueError, match="namespace"):
            validate_namespace("rx_claims")

    def test_uppercase(self):
        with pytest.raises(ValueError, match="namespace"):
            validate_namespace("Rx")

    def test_mixed_case(self):
        with pytest.raises(ValueError, match="namespace"):
            validate_namespace("RX")


# ---------------------------------------------------------------------------
# validate_windows
# ---------------------------------------------------------------------------

class TestValidateWindows:
    def test_valid_ints(self):
        validate_windows([7, 30, 90, 365])

    def test_valid_with_all(self):
        validate_windows([30, 90, "all"])

    def test_only_all(self):
        validate_windows(["all"])

    def test_zero_window(self):
        with pytest.raises(ValueError, match="positive"):
            validate_windows([0])

    def test_negative_window(self):
        with pytest.raises(ValueError, match="positive"):
            validate_windows([-7])

    def test_invalid_string(self):
        with pytest.raises(ValueError, match="'all'"):
            validate_windows(["weekly"])

    def test_duplicate_int(self):
        with pytest.raises(ValueError, match="duplicate"):
            validate_windows([30, 30])

    def test_duplicate_all(self):
        with pytest.raises(ValueError, match="duplicate"):
            validate_windows(["all", "all"])

    def test_empty_list(self):
        with pytest.raises(ValueError, match="at least one"):
            validate_windows([])

    def test_float_rejected(self):
        with pytest.raises(ValueError, match="positive"):
            validate_windows([30.5])


# ---------------------------------------------------------------------------
# validate_columns_present
# ---------------------------------------------------------------------------

class TestValidateColumnsPresent:
    def test_all_present(self, con):
        con.execute("CREATE TABLE t (a INTEGER, b INTEGER)")
        validate_columns_present("t", ["a", "b"], "spine", con)  # no raise

    def test_missing_column(self, con):
        con.execute("CREATE TABLE t2 (a INTEGER)")
        with pytest.raises(ValueError, match="member_id") as exc:
            validate_columns_present("t2", ["a", "member_id"], "base", con)
        assert "base" in str(exc.value)

    def test_case_sensitive(self, con):
        con.execute("CREATE TABLE t3 (member_id INTEGER)")
        # DuckDB is case-insensitive by default for column names — this should pass
        validate_columns_present("t3", ["member_id"], "spine", con)


# ---------------------------------------------------------------------------
# validate_no_nulls_in_grain
# ---------------------------------------------------------------------------

class TestValidateNoNullsInGrain:
    def test_no_nulls(self, con):
        con.execute("CREATE TABLE spine_clean (member_id INTEGER, as_of DATE)")
        con.execute("INSERT INTO spine_clean VALUES (1, '2024-01-01'), (2, '2024-02-01')")
        validate_no_nulls_in_grain("spine_clean", ["member_id", "as_of"], con)

    def test_null_in_entity_key(self, con):
        con.execute("CREATE TABLE spine_null_key (member_id INTEGER, as_of DATE)")
        con.execute("INSERT INTO spine_null_key VALUES (NULL, '2024-01-01'), (2, '2024-02-01')")
        with pytest.raises(ValueError, match="member_id"):
            validate_no_nulls_in_grain("spine_null_key", ["member_id", "as_of"], con)

    def test_null_in_as_of(self, con):
        con.execute("CREATE TABLE spine_null_asof (member_id INTEGER, as_of DATE)")
        con.execute("INSERT INTO spine_null_asof VALUES (1, NULL)")
        with pytest.raises(ValueError, match="as_of"):
            validate_no_nulls_in_grain("spine_null_asof", ["member_id", "as_of"], con)


# ---------------------------------------------------------------------------
# validate_spine_uniqueness
# ---------------------------------------------------------------------------

class TestValidateSpineUniqueness:
    def test_unique(self, con):
        con.execute("CREATE TABLE spine_ok (member_id INTEGER, as_of DATE)")
        con.execute("INSERT INTO spine_ok VALUES (1, '2024-01-01'), (2, '2024-01-01')")
        validate_spine_uniqueness("spine_ok", ["member_id", "as_of"], con)

    def test_duplicate(self, con):
        con.execute("CREATE TABLE spine_dup (member_id INTEGER, as_of DATE)")
        con.execute(
            "INSERT INTO spine_dup VALUES (1, '2024-01-01'), (1, '2024-01-01'), (2, '2024-01-01')"
        )
        with pytest.raises(ValueError, match="1 duplicate") as exc:
            validate_spine_uniqueness("spine_dup", ["member_id", "as_of"], con)
        assert "duplicate" in str(exc.value).lower()

    def test_multiple_duplicates(self, con):
        con.execute("CREATE TABLE spine_multi_dup (member_id INTEGER, as_of DATE)")
        con.execute(
            "INSERT INTO spine_multi_dup VALUES "
            "(1, '2024-01-01'), (1, '2024-01-01'), "
            "(2, '2024-01-01'), (2, '2024-01-01')"
        )
        with pytest.raises(ValueError, match="2 duplicate"):
            validate_spine_uniqueness("spine_multi_dup", ["member_id", "as_of"], con)


# ---------------------------------------------------------------------------
# validate_agg_name_no_collision
# ---------------------------------------------------------------------------

class TestValidateAggNameNoCollision:
    def test_no_collision(self):
        validate_agg_name_no_collision(["paid_sum", "claims_cnt"], ["member_id", "as_of_date"])

    def test_collision_with_entity_key(self):
        with pytest.raises(ValueError, match="member_id"):
            validate_agg_name_no_collision(["member_id", "paid_sum"], ["member_id", "as_of_date"])

    def test_collision_with_as_of(self):
        with pytest.raises(ValueError, match="as_of_date"):
            validate_agg_name_no_collision(["as_of_date"], ["member_id", "as_of_date"])
