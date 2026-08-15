"""Fakes for the ports. A fake, not a mock: it behaves, it does not assert.

You cannot ask a real model to attack you reliably, so the malicious and
hallucinated SQL the guardrails are tested against is canned here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from assay.domain.models import GeneratedSQL
from assay.ports import Schema


@dataclass
class FakeLLM:
    """Returns one fixed SQL string. Build one per eval case."""

    sql: str
    prose: str = "A canned summary."
    answerable: bool = True
    rationale: str = "canned by FakeLLM"

    def generate_sql(self, question: str, schema: Schema) -> GeneratedSQL:
        return GeneratedSQL(sql=self.sql, rationale=self.rationale, answerable=self.answerable)

    def summarise(self, question: str, sql: str, columns: list[str], rows: list[list[Any]]) -> str:
        return self.prose
