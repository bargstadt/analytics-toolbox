"""Geospatial utilities: address geocoding, public-data wrappers, and block group crosswalks."""

from analytics_toolbox.geospatial import (
    _scourgify_compat,  # noqa: F401  (must run before any submodule below imports scourgify)
)
from analytics_toolbox.geospatial.address_normalizer import normalize_addresses
from analytics_toolbox.geospatial.nad_preprocess_ingest import sample_nad_addresses
from analytics_toolbox.geospatial.pipeline import geocode_address_table

__all__ = ["normalize_addresses", "geocode_address_table", "sample_nad_addresses"]

