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

    def refuse(prose: str, kind: LogVerdict, sql: str = "") -> Answer:
        answer = Answer(
            question=question,
            sql=sql,
            prose=prose,
            refused=True,
            kind=kind,
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )
        _log(answer)
        return answer

    try:
        schema = warehouse.schema()
    except WarehouseError as err:
        # Fetching the catalogue is itself a database call — a locked file, an
        # I/O error, a corrupted database — and it happens before any SQL has
        # even been generated. Same failure shape as a run() error, so the
        # same refusal and the same verdict.
        return refuse(
            f"That query was valid, but the database could not run it: {err}",
            "execution_error",
        )

    generated = llm.generate_sql(question, schema)

    if not generated.answerable:
        return refuse(
            f"{generated.rationale} I did not run a substitute query, "
            "since a number answering a different question is worse than no answer.",
            "unanswerable",
            generated.sql,
        )

    verdict = check_sql(generated.sql, schema)
    if not verdict.ok:
        return refuse(f"I did not run that query. {verdict.reason}", verdict.kind, generated.sql)

    try:
        columns, rows = warehouse.run(generated.sql, max_rows)
    except WarehouseError as err:
        # Both guardrails said yes — the SQL is safe and every identifier is
        # real — but the data underneath did not cooperate (a CAST that met a
        # value it cannot convert, for one). That is not a hallucination and
        # not an attack, so it gets its own verdict rather than being folded
        # into either guardrail's.
        return refuse(
            f"That query was valid, but the database could not run it: {err}",
            "execution_error",
            generated.sql,
        )

    answer = Answer(
        question=question,
        sql=generated.sql,
        columns=columns,
        rows=rows,
        prose=llm.summarise(question, generated.sql, columns, rows),
        elapsed_ms=int((time.monotonic() - started) * 1000),
    )
    _log(answer)
    return answer


def _log(answer: Answer) -> None:
    log.info(
        json.dumps(
            {
                "event": "ask",
                "question": answer.question,
                "sql": answer.sql,
                "verdict": answer.kind,
                "refused": answer.refused,
                "rows": len(answer.rows),
                "elapsed_ms": answer.elapsed_ms,
            }
        )
    )
