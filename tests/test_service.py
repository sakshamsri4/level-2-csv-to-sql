import json
import logging
from typing import Any

import pytest

from assay.adapters.fakes import FakeLLM
from assay.ports import WarehouseError
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


def test_an_unanswerable_question_is_refused_and_never_reaches_the_warehouse():
    # This SQL is fully valid on its own — real table, real columns, single SELECT —
    # so check_sql would approve it if asked. The `answerable` flag has to be the
    # thing doing the refusing here, not a coincidental identifier rejection.
    warehouse = FakeWarehouse()
    answer = ask(
        "what is the average customer satisfaction score by carrier?",
        FakeLLM(
            sql="SELECT origin, avg(delay_days) FROM shipments GROUP BY 1",
            answerable=False,
            rationale="the schema has no customer satisfaction score column",
        ),
        warehouse,
        max_rows=200,
    )
    assert answer.refused
    assert warehouse.executed == []


def test_an_unanswerable_refusal_names_what_was_missing_not_a_number():
    answer = ask(
        "what is the average customer satisfaction score by carrier?",
        FakeLLM(
            sql="SELECT carrier_code, avg(delay_days) FROM shipments GROUP BY 1",
            answerable=False,
            rationale="the schema has no customer satisfaction score column",
        ),
        FakeWarehouse(),
        max_rows=200,
    )
    assert answer.refused
    assert "customer satisfaction score" in answer.prose


def test_an_answerable_question_with_a_legitimately_empty_result_is_not_unanswerable():
    class EmptyWarehouse:
        def __init__(self) -> None:
            self.executed: list[str] = []

        def schema(self) -> dict[str, set[str]]:
            return SCHEMA

        def run(self, sql: str, max_rows: int) -> tuple[list[str], list[list[Any]]]:
            self.executed.append(sql)
            return ["origin"], []

    warehouse = EmptyWarehouse()
    answer = ask(
        "which shipments went to Mars?",
        FakeLLM(
            sql="SELECT origin FROM shipments WHERE destination = 'MARS'",
            prose="No shipments matched that destination.",
        ),
        warehouse,
        max_rows=200,
    )
    assert not answer.refused
    assert answer.rows == []
    assert len(warehouse.executed) == 1


def test_a_query_the_database_cannot_run_is_refused_not_raised():
    # Both guardrails can only judge what a query *says* — they cannot know
    # whether the data underneath will cooperate. A warehouse that raises
    # WarehouseError stands in for a CAST that meets a value it cannot
    # convert: valid SQL, real identifiers, and the database says no anyway.
    class BrokenWarehouse:
        def schema(self) -> dict[str, set[str]]:
            return SCHEMA

        def run(self, sql: str, max_rows: int) -> tuple[list[str], list[list[Any]]]:
            raise WarehouseError("Could not convert string 'DFW' to INT32")

    answer = ask(
        "what is the average origin code?",
        FakeLLM(sql="SELECT CAST(origin AS INTEGER) FROM shipments"),
        BrokenWarehouse(),
        max_rows=200,
    )
    assert answer.refused
    assert answer.rows == []
    assert "DFW" in answer.prose


def test_a_database_execution_error_is_logged_with_its_own_verdict(caplog):
    caplog.set_level(logging.INFO, logger="assay")

    class BrokenWarehouse:
        def schema(self) -> dict[str, set[str]]:
            return SCHEMA

        def run(self, sql: str, max_rows: int) -> tuple[list[str], list[list[Any]]]:
            raise WarehouseError("boom")

    ask("q", FakeLLM(sql="SELECT origin FROM shipments"), BrokenWarehouse(), max_rows=200)

    lines = [json.loads(record.message) for record in caplog.records]
    assert len(lines) == 1
    assert lines[0]["verdict"] == "execution_error"
    assert lines[0]["refused"] is True


def test_a_schema_lookup_that_fails_is_refused_not_raised():
    # schema() is the very first call ask() makes, before any SQL exists to
    # guard. A locked file or a corrupted database can fail here exactly as
    # easily as inside run() — same WarehouseError, same refusal shape.
    class UnreadableWarehouse:
        def schema(self) -> dict[str, set[str]]:
            raise WarehouseError("IO Error: database is locked")

        def run(self, sql: str, max_rows: int) -> tuple[list[str], list[list[Any]]]:
            raise AssertionError("run() must never be reached if schema() failed")

    answer = ask("anything", FakeLLM(sql="SELECT 1"), UnreadableWarehouse(), max_rows=200)
    assert answer.refused
    assert answer.rows == []
    assert "locked" in answer.prose


def test_a_schema_lookup_failure_is_logged_with_its_own_verdict(caplog):
    caplog.set_level(logging.INFO, logger="assay")

    class UnreadableWarehouse:
        def schema(self) -> dict[str, set[str]]:
            raise WarehouseError("boom")

        def run(self, sql: str, max_rows: int) -> tuple[list[str], list[list[Any]]]:
            raise AssertionError("run() must never be reached if schema() failed")

    ask("q", FakeLLM(sql="SELECT 1"), UnreadableWarehouse(), max_rows=200)

    lines = [json.loads(record.message) for record in caplog.records]
    assert len(lines) == 1
    assert lines[0]["verdict"] == "execution_error"
    assert lines[0]["refused"] is True


def test_the_log_line_is_valid_json_carrying_what_observability_needs(caplog):
    """The JSON log line is the only observability artifact this project ships, so
    its shape is worth pinning directly rather than trusting _log()'s implementation
    by eye. One refusal and one success are enough here to pin the line's shape;
    the other values LogVerdict can hold — "unknown_identifier", "unanswerable",
    "execution_error" — are each exercised by their own dedicated test above."""
    caplog.set_level(logging.INFO, logger="assay")
    warehouse = FakeWarehouse()

    refusal = ask("bad", FakeLLM(sql="DROP TABLE shipments"), warehouse, max_rows=200)
    success = ask(
        "good",
        FakeLLM(sql="SELECT origin FROM shipments", prose="ok"),
        warehouse,
        max_rows=200,
    )

    lines = [json.loads(record.message) for record in caplog.records]
    assert len(lines) == 2
    refusal_line, success_line = lines

    for line, answer in ((refusal_line, refusal), (success_line, success)):
        assert line["event"] == "ask"
        assert line["question"] == answer.question
        assert line["sql"] == answer.sql
        assert line["rows"] == len(answer.rows)
        assert line["elapsed_ms"] == answer.elapsed_ms
        assert isinstance(line["elapsed_ms"], int)

    assert refusal_line["verdict"] == "unsafe"
    assert refusal_line["refused"] is True
    assert success_line["verdict"] == "ok"
    assert success_line["refused"] is False


def test_the_answer_carries_the_verdict_that_produced_it():
    # Callers should not have to re-derive why ask() decided what it decided.
    # evals.py used to re-run check_sql to recover exactly this.
    warehouse = FakeWarehouse()

    good = ask("good", FakeLLM(sql="SELECT origin FROM shipments"), warehouse, max_rows=200)
    unsafe = ask("bad", FakeLLM(sql="DROP TABLE shipments"), warehouse, max_rows=200)
    bogus = ask("huh", FakeLLM(sql="SELECT nope FROM shipments"), warehouse, max_rows=200)
    cannot = ask(
        "impossible",
        FakeLLM(sql="SELECT origin FROM shipments", answerable=False),
        warehouse,
        max_rows=200,
    )

    assert good.kind == "ok"
    assert unsafe.kind == "unsafe"
    assert bogus.kind == "unknown_identifier"
    assert cannot.kind == "unanswerable"


def test_a_refused_answer_never_reports_an_ok_verdict():
    """The invariant evals.py used to re-check per case: refused and kind cannot
    disagree. Asserting it once here is what lets the eval runner trust
    answer.kind instead of computing a second opinion from check_sql."""

    class BrokenWarehouse:
        def schema(self) -> dict[str, set[str]]:
            return SCHEMA

        def run(self, sql: str, max_rows: int) -> tuple[list[str], list[list[Any]]]:
            raise WarehouseError("boom")

    answers = [
        ask("good", FakeLLM(sql="SELECT origin FROM shipments"), FakeWarehouse(), max_rows=200),
        ask("bad", FakeLLM(sql="DROP TABLE shipments"), FakeWarehouse(), max_rows=200),
        ask("huh", FakeLLM(sql="SELECT nope FROM shipments"), FakeWarehouse(), max_rows=200),
        ask(
            "impossible",
            FakeLLM(sql="SELECT origin FROM shipments", answerable=False),
            FakeWarehouse(),
            max_rows=200,
        ),
        ask("broken", FakeLLM(sql="SELECT origin FROM shipments"), BrokenWarehouse(), max_rows=200),
    ]

    for answer in answers:
        assert answer.refused == (answer.kind != "ok")
