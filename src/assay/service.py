"""Orchestration. Holds no rules — it calls the domain for every decision."""

from __future__ import annotations

import json
import logging
import time
import uuid

from assay.domain.models import Answer, GeneratedSQL, LogVerdict, Schema
from assay.domain.sql_guard import check_sql
from assay.ports import LLM, Warehouse, WarehouseError

log = logging.getLogger("assay")

# One id per process, stamped on every log line. There is exactly one line per
# ask, so a line carries no way to be grouped with its neighbours on its own —
# and "it refused my question" is a report about a sitting, not about one
# question. This is what makes those lines readable in order afterwards.
SESSION_ID = uuid.uuid4().hex[:8]


def decide(generated: GeneratedSQL, schema: Schema) -> tuple[LogVerdict, str]:
    """The verdict for a generated query, and the sentence explaining it.

    Both ask() and `assay eval --live` consult this, so the order the two
    signals are read in cannot drift between them. The order is load-bearing:
    an unanswerable question's SQL is usually valid on its own, so consulting
    check_sql first would call it allowed and hide the refusal the running
    system actually makes.
    """
    if not generated.answerable:
        return "unanswerable", (
            f"{generated.rationale} I did not run a substitute query, "
            "since a number answering a different question is worse than no answer."
        )
    verdict = check_sql(generated.sql, schema)
    if not verdict.ok:
        return verdict.kind, f"I did not run that query. {verdict.reason}"
    return "ok", ""


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

    kind, prose = decide(generated, schema)
    if kind != "ok":
        return refuse(prose, kind, generated.sql)

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
                "session": SESSION_ID,
                "question": answer.question,
                "sql": answer.sql,
                "verdict": answer.kind,
                "refused": answer.refused,
                "rows": len(answer.rows),
                "elapsed_ms": answer.elapsed_ms,
            }
        )
    )
