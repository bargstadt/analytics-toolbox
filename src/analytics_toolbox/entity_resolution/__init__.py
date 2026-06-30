"""Entity resolution (MPI) module for analytics-toolbox.

Links records for the same person across N source systems using config-driven
blocking, weighted RapidFuzz fuzzy matching, and NetworkX connected-component
clustering.

Requires the ``entity-resolution`` extra:
    pip install analytics-toolbox[entity-resolution]
"""

from analytics_toolbox.entity_resolution.resolve import resolve

__all__ = ["resolve"]
