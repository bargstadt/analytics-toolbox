import duckdb
import pytest


@pytest.fixture
def con():
    return duckdb.connect()
