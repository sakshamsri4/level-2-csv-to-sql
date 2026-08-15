"""Data the layers pass between each other. No behaviour lives here."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

VerdictKind = Literal["ok", "unsafe", "unknown_identifier"]

# Every value _log() can ever emit: the guardrail's own VerdictKind, plus the one
# refusal cause that never touches check_sql at all.
LogVerdict = Literal["ok", "unsafe", "unknown_identifier", "unanswerable"]

# table name -> its column names. Both guardrails take exactly this and nothing more.
Schema = dict[str, set[str]]


class GeneratedSQL(BaseModel):
    """What the model returns. Structured, never scraped out of a code fence.

    `answerable` exists because structured output otherwise forces the model to
    return *some* SQL no matter what: asked for a column the schema lacks, its only
    legal move is to substitute a different one and hope the rationale is read — and
    a precise, confident number answering a different question than the one asked is
    worse than a refusal. This field gives it a way to say "I can't" instead.

    The Python default of True is safe, not merely convenient, ONLY because the live
    path can never reach it: the structured-output call marks `answerable` `required`
    in the strict JSON schema (see the OpenAI SDK's `to_strict_json_schema`), so the
    model must emit the field on every call. `test_the_model_is_always_forced_to_
    state_whether_it_can_answer` in tests/test_prompts.py pins that fact down — if an
    SDK change ever made the field optional again, that test fails before this
    default can silently reopen the substitute-a-column failure.
    """

    sql: str
    rationale: str
    answerable: bool = True


class Verdict(BaseModel):
    """The guardrails' answer. `kind` exists so an eval can assert *why* something
    was refused — a suite that only checks "rejected" cannot tell a schema
    rejection from a parse failure."""

    ok: bool
    kind: VerdictKind = "ok"
    reason: str = ""


class Answer(BaseModel):
    question: str
    sql: str = ""
    columns: list[str] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    prose: str = ""
    refused: bool = False
    elapsed_ms: int = 0
