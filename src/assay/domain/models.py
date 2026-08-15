"""Data the layers pass between each other. No behaviour lives here."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

VerdictKind = Literal["ok", "unsafe", "unknown_identifier"]


class GeneratedSQL(BaseModel):
    """What the model returns. Structured, never scraped out of a code fence."""

    sql: str
    rationale: str


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
