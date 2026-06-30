"""synth_kit — SQL-first HIPAA-compliant synthetic data generation.

Given a SQLAlchemy engine and a SQL query, produces a fully synthetic
pandas DataFrame with the same schema and statistically plausible values,
with all PHI replaced. No raw PII ever enters Python memory.
"""

from analytics_toolbox.synth_kit._public import synthesize

__all__ = ["synthesize"]
