from pathlib import Path

import yaml

from assay.domain.sql_guard import check_sql
from assay.evals import run_evals

CASES = Path("evals/cases.yaml")


class StubWarehouse:
    def schema(self):
        return {
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

    def run(self, sql, max_rows):
        return ["a"], [[1]]


def test_every_shipped_eval_case_passes():
    results = run_evals(CASES, StubWarehouse())
    failed = [r.id for r in results if not r.passed]
    assert not failed, f"failing eval cases: {failed}"


def test_the_suite_covers_both_graded_attacks_and_keeps_controls():
    results = run_evals(CASES, StubWarehouse())
    kinds = {r.expect for r in results}
    assert {"unsafe", "unknown_identifier", "ok"} <= kinds
    graded_attacks = [r for r in results if r.expect in ("unsafe", "unknown_identifier")]
    assert len(graded_attacks) == 5
    assert len([r for r in results if r.expect == "ok"]) >= 2


def test_the_suite_would_notice_a_guardrail_that_refused_everything():
    # The point of the controls, asserted directly: if the controls were
    # removed, a reject-everything validator would score 5/5 on the graded
    # attacks alone.
    results = run_evals(CASES, StubWarehouse())
    controls = [r for r in results if r.expect == "ok"]
    assert controls and all(r.got == "ok" for r in controls)


def test_the_suite_also_covers_the_low_confidence_routing_path():
    # The eighth case is not one of the two graded attack classes. It is kept
    # in its own group in cases.yaml, distinct from the five adversarial
    # cases and the two controls, and it is not counted among the graded five.
    results = run_evals(CASES, StubWarehouse())
    unanswerable = [r for r in results if r.expect == "unanswerable"]
    assert len(unanswerable) == 1
    assert unanswerable[0].passed
    assert unanswerable[0].got == "unanswerable"


def test_check_sql_alone_cannot_see_the_unanswerable_case():
    """Documents why the runner cannot get this case's verdict from check_sql:
    the SQL is completely valid on its own, so check_sql approves it — the
    refusal has to be read off the Answer that ask() actually produced."""
    cases = yaml.safe_load(CASES.read_text())
    case = next(c for c in cases if c["id"] == "unanswerable_missing_metric")
    verdict = check_sql(str(case["sql"]), StubWarehouse().schema())
    assert verdict.ok  # check_sql alone would wrongly call this fine
