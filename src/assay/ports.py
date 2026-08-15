"""The boundary. Adapters implement these; the domain never imports them."""

from __future__ import annotations

from typing import Any, Protocol

from assay.domain.models import GeneratedSQL, Schema

__all__ = ["LLM", "GeneratedSQL", "Schema", "Warehouse", "WarehouseError"]


class LLM(Protocol):
    def generate_sql(self, question: str, schema: Schema) -> GeneratedSQL: ...

    def summarise(
        self, question: str, sql: str, columns: list[str], rows: list[list[Any]]
    ) -> str: ...


class WarehouseError(Exception):
    """A query that passed both guardrails but the database could not run.

    Both guardrails only know what a query *says*; they cannot know whether
    the data underneath will cooperate — a CAST that meets a value it cannot
    convert, for one. This is the port-level shape of that failure. Adapters
    translate whatever vendor exception they raise into this one, so the
    layers above never learn what a `duckdb.Error` is.
    """


class Warehouse(Protocol):
    def schema(self) -> Schema: ...

    def run(self, sql: str, max_rows: int) -> tuple[list[str], list[list[Any]]]:
        """Raises WarehouseError if the database could not run the query."""
        ...
