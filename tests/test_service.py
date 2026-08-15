from typing import Any

import pytest

from assay.adapters.fakes import FakeLLM
from assay.service import ask

SCHEMA = {
    "shipments": {"shipment_id", "origin", "destination", "delay_days"},
    "carriers": {"carrier_code", "carrier_name"},
}


class FakeWarehouse:
    def __init__(self) -> None:
        self.executed: list[str] = []

    def schema(self) -> dict[str, set[str]]:
        return SCHEMA

    def run(self, sql: str, max_rows: int) -> tuple[list[str], list[list[Any]]]:
        self.executed.append(sql)
        return ["origin", "rate"], [["SEA", 0.5]]


def test_a_good_question_is_answered_from_the_rows_that_came_back():
    warehouse = FakeWarehouse()
    answer = ask(
        "which route is worst?",
        FakeLLM(sql="SELECT origin, avg(delay_days) FROM shipments GROUP BY 1", prose="SEA is."),
        warehouse,
        max_rows=200,
    )
    assert not answer.refused
    assert answer.prose == "SEA is."
    assert answer.rows == [["SEA", 0.5]]
    assert len(warehouse.executed) == 1


@pytest.mark.parametrize(
    "malicious",
    [
        "DROP TABLE shipments",
        "SELECT 1; DELETE FROM shipments",
        "COPY shipments TO '/tmp/exfil.csv'",
    ],
)
def test_unsafe_sql_never_reaches_the_warehouse(malicious):
    warehouse = FakeWarehouse()
    answer = ask("anything", FakeLLM(sql=malicious), warehouse, max_rows=200)
    assert answer.refused
    assert warehouse.executed == []  # the guardrail runs BEFORE execution


def test_a_hallucinated_column_is_refused_with_the_real_schema_in_the_message():
    warehouse = FakeWarehouse()
    answer = ask(
        "how late?",
        FakeLLM(sql="SELECT delivery_delay_days FROM shipments"),
        warehouse,
        max_rows=200,
    )
    assert answer.refused
    assert warehouse.executed == []
    assert "delay_days" in answer.prose


def test_a_refusal_returns_no_rows_that_could_be_read_as_an_answer():
    answer = ask("q", FakeLLM(sql="SELECT * FROM deliveries"), FakeWarehouse(), max_rows=200)
    assert answer.refused
    assert answer.rows == []
