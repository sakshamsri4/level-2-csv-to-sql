"""Orchestration. Holds no rules — it calls the domain for every decision."""

from __future__ import annotations

import json
import logging
import time

from assay.domain.models import Answer, LogVerdict
from assay.domain.sql_guard import check_sql
from assay.ports import LLM, Warehouse, WarehouseError

log = logging.getLogger("assay")


def ask(question: str, llm: LLM, warehouse: Warehouse, max_rows: int) -> Answer:
    """Question in, prose out — refusing before execution if the SQL is not safe
    and does not match the real schema."""
    started = time.monotonic()
    try:
        schema = warehouse.schema()
    except WarehouseError as err:
        # Fetching the catalogue is itself a database call — a locked file, an
        # I/O error, a corrupted database — and it happens before any SQL has
        # even been generated. Same failure shape as a run() error, so the
        # same refusal and the same verdict.
        answer = Answer(
            question=question,
            prose=f"That query was valid, but the database could not run it: {err}",
            refused=True,
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )
        _log(answer, "execution_error")
        return answer

    generated = llm.generate_sql(question, schema)

    if not generated.answerable:
        answer = Answer(
            question=question,
            sql=generated.sql,
            prose=f"{generated.rationale} I did not run a substitute query, "
            "since a number answering a different question is worse than no answer.",
            refused=True,
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )
        _log(answer, "unanswerable")
        return answer

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

    try:
        columns, rows = warehouse.run(generated.sql, max_rows)
    except WarehouseError as err:
        # Both guardrails said yes — the SQL is safe and every identifier is
        # real — but the data underneath did not cooperate (a CAST that met a
        # value it cannot convert, for one). That is not a hallucination and
        # not an attack, so it gets its own verdict rather than being folded
        # into either guardrail's.
        answer = Answer(
            question=question,
            sql=generated.sql,
            prose=f"That query was valid, but the database could not run it: {err}",
            refused=True,
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )
        _log(answer, "execution_error")
        return answer

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


def _log(answer: Answer, verdict: LogVerdict) -> None:
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
