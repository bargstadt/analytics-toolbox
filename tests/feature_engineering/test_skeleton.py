"""Task 1 acceptance: public surface importable and pyproject extra wired."""

import importlib


def test_module_importable():
    mod = importlib.import_module("analytics_toolbox.feature_engineering")
    assert mod is not None


def test_public_api_exported():
    from analytics_toolbox.feature_engineering import (  # noqa: F401
        Agg,
        Guardrails,
        compute_features,
        join_features,
    )
