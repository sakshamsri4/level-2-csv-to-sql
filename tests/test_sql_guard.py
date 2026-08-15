import pytest

from assay.domain.sql_guard import check_sql

SCHEMA = {
    "shipments": {
        "shipment_id",
        "carrier_code",
        "origin",
        "destination",
        "shipped_date",
        "promised_date",
        "delivered_date",
        "delay_days",
        "weight_kg",
        "cost_usd",
        "status",
    },
    "carriers": {"carrier_code", "carrier_name", "service_tier"},
}


def test_an_ordinary_aggregate_question_is_allowed():
    v = check_sql("SELECT origin, destination, avg(delay_days) FROM shipments GROUP BY 1,2", SCHEMA)
    assert v.ok, v.reason


def test_a_join_between_the_two_real_tables_is_allowed():
    v = check_sql(
        "SELECT c.carrier_name, count(*) AS trips FROM shipments s "
        "JOIN carriers c ON s.carrier_code = c.carrier_code GROUP BY 1 ORDER BY trips DESC",
        SCHEMA,
    )
    assert v.ok, v.reason


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE shipments",
        "UPDATE shipments SET origin = 'x'",
        "INSERT INTO shipments SELECT * FROM carriers",
        "CREATE TABLE evil AS SELECT 1",
    ],
)
def test_statements_that_change_data_are_refused(sql):
    v = check_sql(sql, SCHEMA)
    assert not v.ok
    assert v.kind == "unsafe"


def test_a_second_statement_smuggled_after_a_select_is_refused():
    v = check_sql("SELECT 1; DELETE FROM shipments", SCHEMA)
    assert not v.ok
    assert v.kind == "unsafe"
    assert "one statement" in v.reason


def test_writing_the_table_out_to_the_filesystem_is_refused():
    # Verified: a read_only DuckDB connection executes COPY ... TO happily and
    # writes the file. This check is the only thing standing between the model
    # and data exfiltration.
    v = check_sql("COPY shipments TO '/tmp/exfil.csv'", SCHEMA)
    assert not v.ok
    assert v.kind == "unsafe"


@pytest.mark.parametrize(
    "sql", ["ATTACH '/tmp/evil.db' AS e", "INSTALL httpfs", "LOAD httpfs", "PRAGMA database_list"]
)
def test_statements_that_reach_outside_the_warehouse_are_refused(sql):
    v = check_sql(sql, SCHEMA)
    assert not v.ok
    assert v.kind == "unsafe"


def test_input_that_is_not_sql_at_all_is_refused_rather_than_raising():
    v = check_sql("not sql at all", SCHEMA)
    assert not v.ok
    assert v.kind == "unsafe"


def test_empty_sql_is_refused_rather_than_raising():
    v = check_sql("", SCHEMA)
    assert not v.ok


def test_a_union_of_two_selects_is_still_a_read():
    v = check_sql(
        "SELECT origin FROM shipments UNION ALL SELECT destination FROM shipments", SCHEMA
    )
    assert v.ok, v.reason
