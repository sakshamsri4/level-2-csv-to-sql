"""Runs the eval cases offline against FakeLLM — no key, no spend, no flakiness."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from assay.adapters.fakes import FakeLLM
from assay.domain.sql_guard import check_sql
from assay.ports import Warehouse
from assay.service import ask


@dataclass
class EvalResult:
    id: str
    guards: str
    expect: str
    got: str
    passed: bool
    reason: str


def run_evals(cases_path: Path, warehouse: Warehouse) -> list[EvalResult]:
    """Judge every case in cases.yaml.

    Most cases can be judged by check_sql alone: the SQL is either malicious,
    hallucinated, or clean, and check_sql answers all three straight from the
    schema. One case is different — a question whose metric the schema lacks,
    with `answerable: false` in the case. Its SQL is completely valid on its
    own, so check_sql would call it "ok"; the refusal is a decision only
    ask() can see, made before check_sql is ever consulted. For that case the
    verdict is read off the Answer ask() actually produced, not off check_sql.
    """
    cases: list[dict[str, Any]] = yaml.safe_load(cases_path.read_text())
    schema = warehouse.schema()
    results = []
    for case in cases:
        sql = str(case["sql"])
        answerable = bool(case.get("answerable", True))
        answer = ask(
            str(case["question"]),
            FakeLLM(sql=sql, answerable=answerable),
            warehouse,
            max_rows=10,
        )

        got: str
        reason: str
        if answerable:
            verdict = check_sql(sql, schema)
            got = verdict.kind if not verdict.ok else "ok"
            reason = verdict.reason
        else:
            # check_sql never sees this cause — ask() refuses on
            # generated.answerable before check_sql is called at all — so the
            # verdict has to come from what ask() actually did.
            got = "unanswerable" if answer.refused else "ok"
            reason = answer.prose

        # Refusal and execution must agree: a case that says "ok" must actually
        # have run, and a refused case must not have produced rows.
        consistent = answer.refused == (got != "ok")
        results.append(
            EvalResult(
                id=str(case["id"]),
                guards=str(case["guards"]).strip(),
                expect=str(case["expect"]),
                got=got,
                passed=got == str(case["expect"]) and consistent,
                reason=reason,
            )
        )
    return results
