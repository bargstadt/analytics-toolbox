import dataclasses

import pytest

from analytics_toolbox.feature_engineering._types import Agg, Guardrails


class TestAgg:
    def test_fields(self):
        a = Agg(name="paid_sum", expr="SUM(paid_amount)")
        assert a.name == "paid_sum"
        assert a.expr == "SUM(paid_amount)"

    def test_frozen(self):
        a = Agg(name="paid_sum", expr="SUM(paid_amount)")
        with pytest.raises(dataclasses.FrozenInstanceError):
            a.name = "other"

    def test_field_names(self):
        names = {f.name for f in dataclasses.fields(Agg)}
        assert names == {"name", "expr"}


class TestGuardrails:
    def test_defaults(self):
        g = Guardrails()
        assert g.max_fanout_rows == 50_000_000
        assert g.on_fanout_exceed == "raise"
        assert g.require_unique_spine is True

    def test_frozen(self):
        g = Guardrails()
        with pytest.raises(dataclasses.FrozenInstanceError):
            g.max_fanout_rows = 1

    def test_on_fanout_exceed_warn(self):
        g = Guardrails(on_fanout_exceed="warn")
        assert g.on_fanout_exceed == "warn"

    def test_on_fanout_exceed_invalid(self):
        with pytest.raises(ValueError, match="on_fanout_exceed"):
            Guardrails(on_fanout_exceed="ignore")

    def test_max_fanout_rows_none(self):
        g = Guardrails(max_fanout_rows=None)
        assert g.max_fanout_rows is None

    def test_field_names(self):
        names = {f.name for f in dataclasses.fields(Guardrails)}
        assert names == {"max_fanout_rows", "on_fanout_exceed", "require_unique_spine"}
