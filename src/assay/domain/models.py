"""Data the layers pass between each other. No behaviour lives here."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

VerdictKind = Literal["ok", "unsafe", "unknown_identifier"]

# table name -> its column names. Both guardrails take exactly this and nothing more.
Schema = dict[str, set[str]]


class GeneratedSQL(BaseModel):
    """What the model returns. Structured, never scraped out of a code fence.

    `answerable` exists because structured output otherwise forces the model to
    return *some* SQL no matter what: asked for a column the schema lacks, its only
    legal move is to substitute a different one and hope the rationale is read — and
    a precise, confident number answering a different question than the one asked is
    worse than a refusal. This field gives it a way to say "I can't" instead.
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
