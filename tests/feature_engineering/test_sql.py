"""Tests for _sql.py — pure string generation, no DuckDB execution."""

import re

from analytics_toolbox.feature_engineering._sql import (
    build_feature_sql,
    col_name,
    filter_expr,
    window_predicate,
)
from analytics_toolbox.feature_engineering._types import Agg


def norm(sql: str) -> str:
    """Collapse all whitespace to single spaces for structural assertions."""
    return re.sub(r"\s+", " ", sql).strip()


# ---------------------------------------------------------------------------
# col_name
# ---------------------------------------------------------------------------

class TestColName:
    def test_integer_window(self):
        assert col_name("rx", "paid_sum", 30) == "rx__paid_sum_30d"

    def test_integer_window_365(self):
        assert col_name("rx", "ndc_ndist", 365) == "rx__ndc_ndist_365d"

    def test_all_window(self):
        assert col_name("med", "claims_cnt", "all") == "med__claims_cnt_all"

    def test_double_underscore_delimiter(self):
        result = col_name("rx", "paid_sum", 90)
        ns, rest = result.split("__", 1)
        assert ns == "rx"
        assert rest == "paid_sum_90d"


# ---------------------------------------------------------------------------
# window_predicate
# ---------------------------------------------------------------------------

class TestWindowPredicate:
    def test_integer_30(self):
        assert (
            window_predicate("claim_date", "as_of_date", 30)
            == '"claim_date" >= "as_of_date" - INTERVAL 30 DAY'
        )

    def test_integer_90(self):
        assert (
            window_predicate("claim_date", "as_of_date", 90)
            == '"claim_date" >= "as_of_date" - INTERVAL 90 DAY'
        )

    def test_all_returns_none(self):
        assert window_predicate("claim_date", "as_of_date", "all") is None


# ---------------------------------------------------------------------------
# filter_expr
# ---------------------------------------------------------------------------

class TestFilterExpr:
    def test_with_predicate(self):
        pred = '"claim_date" >= "as_of_date" - INTERVAL 30 DAY'
        result = filter_expr("SUM(paid_amount)", pred, "rx__paid_sum_30d")
        assert result == (
            'SUM(paid_amount) FILTER (WHERE "claim_date" >= "as_of_date" - INTERVAL 30 DAY)'
            ' AS "rx__paid_sum_30d"'
        )

    def test_no_predicate_omits_filter(self):
        result = filter_expr("COUNT(*)", None, "rx__claims_cnt_all")
        assert result == 'COUNT(*) AS "rx__claims_cnt_all"'
        assert "FILTER" not in result


# ---------------------------------------------------------------------------
# build_feature_sql — structural assertions
# ---------------------------------------------------------------------------

_BASE_AGGS = [Agg("paid_sum", "SUM(paid_amount)"), Agg("claims_cnt", "COUNT(*)")]


def _build(**overrides):
    defaults = dict(
        spine_tbl="spine",
        base_tbl="rx_claims",
        entity_keys=["member_id"],
        as_of_col="as_of_date",
        base_date_col="claim_date",
        namespace="rx",
        aggregations=_BASE_AGGS,
        windows=[30, 90],
        group_cols=[],
        max_window=90,
    )
    defaults.update(overrides)
    return build_feature_sql(**defaults)


class TestBuildFeatureSqlStructure:
    def test_cte_names_present(self):
        sql = norm(_build())
        assert "WITH base_f AS" in sql
        assert "joined AS" in sql
        assert "agg AS" in sql

    def test_final_left_join(self):
        sql = norm(_build())
        assert "FROM spine s" in sql
        assert "LEFT JOIN agg a" in sql

    def test_upper_bound_exclusive_in_joined(self):
        sql = norm(_build())
        # The joined CTE filters: base_date < spine.as_of
        assert 'b."claim_date" < s."as_of_date"' in sql

    def test_base_prefilter_upper_bound(self):
        sql = norm(_build())
        assert 'MAX("as_of_date")' in sql
        assert '"claim_date" <' in sql

    def test_base_prefilter_lower_bound_with_max_window(self):
        sql = norm(_build(max_window=90))
        assert 'MIN("as_of_date")' in sql
        # The lower-bound INTERVAL uses max_window
        assert "INTERVAL 90 DAY" in sql

    def test_base_prefilter_no_lower_bound_when_none(self):
        sql = norm(_build(windows=["all"], max_window=None))
        assert 'MIN("as_of_date")' not in sql


class TestBuildFeatureSqlColumns:
    def test_feature_column_aliases_present(self):
        sql = norm(_build())
        assert '"rx__paid_sum_30d"' in sql
        assert '"rx__paid_sum_90d"' in sql
        assert '"rx__claims_cnt_30d"' in sql
        assert '"rx__claims_cnt_90d"' in sql

    def test_filter_clause_per_window(self):
        sql = norm(_build())
        assert "FILTER (WHERE" in sql
        assert "INTERVAL 30 DAY" in sql
        assert "INTERVAL 90 DAY" in sql

    def test_all_window_has_no_filter_clause(self):
        sql = norm(_build(windows=["all"], max_window=None))
        assert '"rx__paid_sum_all"' in sql
        assert '"rx__claims_cnt_all"' in sql
        assert "FILTER" not in sql

    def test_group_by_includes_grain(self):
        sql = norm(_build())
        assert "GROUP BY" in sql
        assert '"member_id"' in sql
        assert '"as_of_date"' in sql

    def test_exclude_grain_from_agg_output(self):
        sql = norm(_build())
        assert "EXCLUDE" in sql


class TestBuildFeatureSqlGrainVariants:
    def test_group_cols_in_sql(self):
        sql = norm(_build(group_cols=["drug_class"]))
        assert '"drug_class"' in sql

    def test_multi_entity_key(self):
        sql = norm(_build(entity_keys=["plan_id", "member_id"]))
        assert '"plan_id"' in sql
        assert '"member_id"' in sql

    def test_mixed_windows_int_and_all(self):
        sql = norm(_build(windows=[30, "all"], max_window=30))
        assert '"rx__paid_sum_30d"' in sql
        assert '"rx__paid_sum_all"' in sql
        # 30d window has FILTER; "all" does not
        assert "FILTER" in sql
        assert '"rx__paid_sum_all"' in sql
