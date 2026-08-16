"""Runs the eval cases offline against FakeLLM — no key, no spend, no flakiness."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from assay.adapters.fakes import FakeLLM
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
    """Judge every case in cases.yaml against the verdict ask() actually reached.

    The verdict is read off the Answer rather than recomputed from check_sql,
    which matters most for the unanswerable case: its SQL is completely valid
    on its own, so check_sql alone would call it "ok". Only ask() sees the
    `answerable` flag that refuses it, so only ask()'s verdict is the truth.
    `service.ask` guarantees refused and kind agree, so this runner does not
    re-check that per case — tests/test_service.py asserts the invariant once.
    """
    cases: list[dict[str, Any]] = yaml.safe_load(cases_path.read_text())
    results = []
    for case in cases:
        answer = ask(
            str(case["question"]),
            FakeLLM(sql=str(case["sql"]), answerable=bool(case.get("answerable", True))),
            warehouse,
            max_rows=10,
        )
        results.append(
            EvalResult(
                id=str(case["id"]),
                guards=str(case["guards"]).strip(),
                expect=str(case["expect"]),
                got=answer.kind,
                passed=answer.kind == str(case["expect"]),
                reason=answer.prose,
            )
        )
    return results
