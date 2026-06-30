import sys


def test_geocoder_is_stubbed_not_real() -> None:
    """Guards against someone "cleaning up" the _scourgify_compat import later.

    Importing analytics_toolbox.geospatial triggers the stub (via
    address_normalizer's import chain). If this stub is ever removed and
    the real `geocoder` package isn't installed, scourgify itself becomes
    unimportable — see _scourgify_compat.py for why we avoid depending on
    the real package at all.
    """
    import analytics_toolbox.geospatial  # noqa: F401  (triggers the import chain)

    assert "geocoder" in sys.modules
    # It's a stub, not the real package — the real one has a `google`
    # attribute (among others) that an empty stub module won't have.
    assert not hasattr(sys.modules["geocoder"], "google")
