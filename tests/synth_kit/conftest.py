"""Shared fixtures for synth_kit tests.

All tests use in-memory DuckDB via SQLAlchemy — no network calls, no real data.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine


@pytest.fixture
def engine() -> Engine:
    """In-memory DuckDB engine via SQLAlchemy."""
    return create_engine("duckdb:///:memory:")


@pytest.fixture
def populated_engine(engine: Engine) -> Engine:
    """DuckDB engine pre-loaded with a multi-column test table.

    Schema:
        age       INTEGER   — 80 non-null integers 20–79, 20 NULLs
        salary    DOUBLE    — 100 non-null floats
        score     DOUBLE    — 100 non-null floats, no NULLs
        category  VARCHAR   — 3 values: 'A'(50), 'B'(30), 'C'(20)
        status    VARCHAR   — 2 values: 'active'(70), 'inactive'(30)
        join_date DATE      — 100 non-null dates
        patient_name VARCHAR  — PHI column (detected by name)
        email_address VARCHAR — PHI column (detected by name)
    """
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE test_source AS
            SELECT
                CASE WHEN i % 5 = 0 THEN NULL ELSE (20 + (i % 60))::INTEGER END AS age,
                (30000.0 + i * 500.0 + (i % 7) * 1000.0)::DOUBLE              AS salary,
                (i * 1.5 % 100.0)::DOUBLE                                      AS score,
                CASE WHEN i % 10 < 5 THEN 'A'
                     WHEN i % 10 < 8 THEN 'B'
                     ELSE 'C' END                                               AS category,
                CASE WHEN i % 10 < 7 THEN 'active' ELSE 'inactive' END         AS status,
                (DATE '2020-01-01' + INTERVAL (i) DAY)::DATE                   AS join_date,
                'Patient ' || i::VARCHAR                                        AS patient_name,
                'user' || i::VARCHAR || '@example.com'                         AS email_address
            FROM generate_series(1, 100) t(i)
        """))
    return engine
