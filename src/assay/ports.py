"""The boundary. Adapters implement these; the domain never imports them."""

from __future__ import annotations

from typing import Any, Protocol

from assay.domain.models import GeneratedSQL, Schema

__all__ = ["LLM", "GeneratedSQL", "LLMError", "Schema", "Warehouse", "WarehouseError"]


class LLMError(Exception):
    """The model could not be reached or returned nothing usable — a missing key,
    an expired key, a rate limit, a bad model name, or a malformed response."""


class LLM(Protocol):
    def generate_sql(self, question: str, schema: Schema) -> GeneratedSQL:
        """Raises LLMError if the model could not be reached or returned no
        usable output."""
        ...

    def summarise(self, question: str, sql: str, columns: list[str], rows: list[list[Any]]) -> str:
        """Raises LLMError if the model could not be reached or returned no
        usable output."""
        ...


class WarehouseError(Exception):
    """A query that passed both guardrails but the database could not run.

    Both guardrails only know what a query *says*; they cannot know whether
    the data underneath will cooperate — a CAST that meets a value it cannot
    convert, for one. This is the port-level shape of that failure. Adapters
    translate whatever vendor exception they raise into this one, so the
    layers above never learn what a `duckdb.Error` is.
    """


class Warehouse(Protocol):
    def schema(self) -> Schema:
        """A missing warehouse raises FileNotFoundError with an actionable
        message (run `make ingest` first) — deliberately not translated into
        WarehouseError, since doing so would produce the misleading "That
        query was valid, but the database could not run it" for a warehouse
        that was never built at all."""
        ...

    def run(self, sql: str, max_rows: int) -> tuple[list[str], list[list[Any]]]:
        """Raises WarehouseError if the database could not run the query."""
        ...
