"""Orchestration. Holds no rules — it calls the domain for every decision."""

from __future__ import annotations

import json
import logging
import time

from assay.domain.models import Answer
from assay.domain.sql_guard import check_sql
from assay.ports import LLM, Warehouse

log = logging.getLogger("assay")


def ask(question: str, llm: LLM, warehouse: Warehouse, max_rows: int) -> Answer:
    """Question in, prose out — refusing before execution if the SQL is not safe
    and does not match the real schema."""
    started = time.monotonic()
    schema = warehouse.schema()
    generated = llm.generate_sql(question, schema)

    verdict = check_sql(generated.sql, schema)
    if not verdict.ok:
        answer = Answer(
            question=question,
            sql=generated.sql,
            prose=f"I did not run that query. {verdict.reason}",
            refused=True,
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )
        _log(answer, verdict.kind)
        return answer

    columns, rows = warehouse.run(generated.sql, max_rows)
    answer = Answer(
        question=question,
        sql=generated.sql,
        columns=columns,
        rows=rows,
        prose=llm.summarise(question, generated.sql, columns, rows),
        elapsed_ms=int((time.monotonic() - started) * 1000),
    )
    _log(answer, "ok")
    return answer


def _log(answer: Answer, verdict: str) -> None:
    log.info(
        json.dumps(
            {
                "event": "ask",
                "question": answer.question,
                "sql": answer.sql,
                "verdict": verdict,
                "refused": answer.refused,
                "rows": len(answer.rows),
                "elapsed_ms": answer.elapsed_ms,
            }
        )
    )
