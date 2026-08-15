"""Read side of the warehouse. Opened read-only, always.

Read-only blocks CREATE and ATTACH. It does NOT block COPY ... TO, which was
verified to write a file from a read-only connection — that is sql_guard's job.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb

from assay.ports import Schema


class DuckDBWarehouse:
    def __init__(self, path: Path) -> None:
        self._path = path

    def _connect(self) -> duckdb.DuckDBPyConnection:
        if not self._path.exists():
            raise FileNotFoundError(f"no warehouse at {self._path} — run `make ingest` first")
        return duckdb.connect(str(self._path), read_only=True)

    def schema(self) -> Schema:
        with self._connect() as con:
            rows = con.execute(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema = 'main' ORDER BY table_name, ordinal_position"
            ).fetchall()
        schema: Schema = {}
        for table, column in rows:
            schema.setdefault(str(table), set()).add(str(column))
        return schema

    def run(self, sql: str, max_rows: int) -> tuple[list[str], list[list[Any]]]:
        with self._connect() as con:
            cursor = con.execute(sql)
            columns = [d[0] for d in cursor.description or []]
            rows = cursor.fetchmany(max_rows)
        return columns, [list(r) for r in rows]
