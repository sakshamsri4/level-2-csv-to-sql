from pathlib import Path

import duckdb
import pytest

from assay.adapters.duckdb_warehouse import DuckDBWarehouse


@pytest.fixture
def warehouse(tmp_path: Path) -> DuckDBWarehouse:
    db = tmp_path / "w.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE shipments AS SELECT 'SHP-1' AS shipment_id, 3 AS delay_days")
    con.execute("CREATE TABLE carriers AS SELECT 'BLZ' AS carrier_code, 'Blizzard' AS carrier_name")
    con.close()
    return DuckDBWarehouse(db)


def test_the_schema_comes_from_the_real_catalogue(warehouse):
    schema = warehouse.schema()
    assert schema == {
        "shipments": {"shipment_id", "delay_days"},
        "carriers": {"carrier_code", "carrier_name"},
    }


def test_a_query_returns_its_column_names_alongside_its_rows(warehouse):
    columns, rows = warehouse.run("SELECT shipment_id, delay_days FROM shipments", max_rows=10)
    assert columns == ["shipment_id", "delay_days"]
    assert rows == [["SHP-1", 3]]


def test_the_row_cap_is_applied_so_a_huge_result_cannot_reach_the_model(warehouse):
    _, rows = warehouse.run("SELECT * FROM range(500) t(i)", max_rows=5)
    assert len(rows) == 5


def test_the_connection_cannot_write_to_the_database(warehouse):
    with pytest.raises(duckdb.Error):
        warehouse.run("CREATE TABLE sneaky (i INTEGER)", max_rows=10)


def test_opening_a_warehouse_that_was_never_built_says_so_plainly(tmp_path):
    with pytest.raises(FileNotFoundError, match="make ingest"):
        DuckDBWarehouse(tmp_path / "missing.duckdb").schema()
