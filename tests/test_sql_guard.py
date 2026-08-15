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


def test_a_cte_query_is_allowed():
    v = check_sql("WITH recent AS (SELECT * FROM shipments) SELECT * FROM recent", SCHEMA)
    assert v.ok, v.reason


def test_a_write_statement_smuggled_inside_a_cte_body_is_refused():
    v = check_sql("WITH x AS (DELETE FROM shipments RETURNING *) SELECT * FROM x", SCHEMA)
    assert not v.ok
    assert v.kind == "unsafe"


def test_a_column_that_does_not_exist_is_refused_with_the_real_columns_named():
    v = check_sql("SELECT delivery_delay_days FROM shipments", SCHEMA)
    assert not v.ok
    assert v.kind == "unknown_identifier"
    # The error must be actionable: it names the column that IS there.
    assert "delivery_delay_days" in v.reason
    assert "delay_days" in v.reason


def test_a_table_that_does_not_exist_is_refused_with_the_real_tables_named():
    v = check_sql("SELECT * FROM deliveries", SCHEMA)
    assert not v.ok
    assert v.kind == "unknown_identifier"
    assert "shipments" in v.reason and "carriers" in v.reason


def test_a_hallucinated_column_behind_a_table_alias_is_still_caught():
    v = check_sql("SELECT s.bogus FROM shipments s", SCHEMA)
    assert not v.ok
    assert v.kind == "unknown_identifier"


def test_a_qualifier_that_names_no_table_in_the_query_is_refused():
    v = check_sql("SELECT q.origin FROM shipments s", SCHEMA)
    assert not v.ok
    assert v.kind == "unknown_identifier"


def test_reading_a_file_from_disk_is_refused_as_an_unknown_table():
    # read_csv() parses as a table with an empty name. The two guardrails
    # compose: the safety check sees a plain SELECT, the schema check does not.
    v = check_sql("SELECT * FROM read_csv('/etc/passwd')", SCHEMA)
    assert not v.ok
    assert v.kind == "unknown_identifier"


def test_a_valid_table_alias_is_not_mistaken_for_a_hallucinated_table():
    v = check_sql("SELECT s.origin FROM shipments AS s", SCHEMA)
    assert v.ok, v.reason


def test_a_cte_name_is_not_mistaken_for_a_hallucinated_table():
    v = check_sql(
        "WITH late AS (SELECT * FROM shipments WHERE delay_days > 0) SELECT count(*) FROM late",
        SCHEMA,
    )
    assert v.ok, v.reason


def test_an_alias_on_a_cte_is_not_mistaken_for_a_hallucinated_table():
    v = check_sql("WITH late AS (SELECT origin FROM shipments) SELECT l.origin FROM late l", SCHEMA)
    assert v.ok, v.reason


def test_a_subquery_alias_is_not_mistaken_for_a_hallucinated_table():
    v = check_sql("SELECT x.origin FROM (SELECT origin FROM shipments) AS x", SCHEMA)
    assert v.ok, v.reason


def test_a_column_alias_defined_in_the_query_may_be_referenced():
    v = check_sql("WITH t AS (SELECT count(*) AS n FROM shipments) SELECT n FROM t", SCHEMA)
    assert v.ok, v.reason


def test_function_names_are_not_mistaken_for_columns():
    v = check_sql(
        "SELECT date_trunc('month', shipped_date) AS m, count(*) FROM shipments GROUP BY 1",
        SCHEMA,
    )
    assert v.ok, v.reason


def test_a_hallucinated_column_is_not_legitimised_by_a_same_named_alias_in_a_sibling_scope():
    v = check_sql(
        "SELECT delivery_delay_days FROM shipments WHERE shipment_id IN "
        "(SELECT count(*) AS delivery_delay_days FROM carriers)",
        SCHEMA,
    )
    assert not v.ok
    assert v.kind == "unknown_identifier"


def test_a_column_borrowed_from_a_cte_the_query_never_selects_from_is_refused():
    v = check_sql(
        "WITH recent AS (SELECT avg(delay_days) AS avg_delay FROM shipments) "
        "SELECT avg_delay FROM carriers",
        SCHEMA,
    )
    assert not v.ok
    assert v.kind == "unknown_identifier"


def test_a_subquery_alias_shadowing_an_outer_alias_does_not_reject_the_outer_query():
    v = check_sql(
        "SELECT s.origin FROM shipments s WHERE EXISTS "
        "(SELECT 1 FROM carriers s WHERE s.carrier_code IS NOT NULL)",
        SCHEMA,
    )
    assert v.ok, v.reason


def test_a_correlated_reference_to_an_outer_table_is_allowed():
    v = check_sql(
        "SELECT s.origin FROM shipments s WHERE EXISTS "
        "(SELECT 1 FROM carriers c WHERE c.carrier_code = s.carrier_code)",
        SCHEMA,
    )
    assert v.ok, v.reason


def test_a_qualified_star_is_allowed():
    v = check_sql("SELECT s.* FROM shipments s", SCHEMA)
    assert v.ok, v.reason


def test_an_unqualified_hallucinated_column_is_refused_even_when_a_subquery_is_also_joined():
    v = check_sql(
        "SELECT bogus_column FROM shipments JOIN (SELECT origin FROM shipments) AS x ON true",
        SCHEMA,
    )
    assert not v.ok
    assert v.kind == "unknown_identifier"


def test_an_unqualified_hallucinated_column_is_refused_even_when_a_cte_is_also_in_the_from_list():
    v = check_sql(
        "WITH x AS (SELECT origin FROM shipments) SELECT bogus_column FROM shipments, x", SCHEMA
    )
    assert not v.ok
    assert v.kind == "unknown_identifier"


def test_a_column_a_subquery_does_not_expose_is_refused():
    v = check_sql("SELECT x.bogus FROM (SELECT origin FROM shipments) x", SCHEMA)
    assert not v.ok
    assert v.kind == "unknown_identifier"


def test_a_column_from_a_star_cte_is_allowed_because_its_columns_cannot_be_enumerated():
    # Deliberate limit of the guard, not an oversight: SELECT * inside a CTE makes
    # its output columns un-enumerable, so we defer to the database rather than guess.
    v = check_sql(
        "WITH late AS (SELECT * FROM shipments) SELECT whatever_it_exposes FROM late", SCHEMA
    )
    assert v.ok, v.reason
