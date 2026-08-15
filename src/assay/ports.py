"""The boundary. Adapters implement these; the domain never imports them."""

from __future__ import annotations

from typing import Any, Protocol

from assay.domain.models import GeneratedSQL, Schema

__all__ = ["LLM", "GeneratedSQL", "Schema", "Warehouse"]


class LLM(Protocol):
    def generate_sql(self, question: str, schema: Schema) -> GeneratedSQL: ...

    def summarise(
        self, question: str, sql: str, columns: list[str], rows: list[list[Any]]
    ) -> str: ...


class Warehouse(Protocol):
    def schema(self) -> Schema: ...

    def run(self, sql: str, max_rows: int) -> tuple[list[str], list[list[Any]]]: ...
