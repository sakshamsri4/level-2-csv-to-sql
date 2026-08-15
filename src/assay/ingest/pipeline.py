"""Reads the raw extracts, reports what is wrong with them, and loads them clean.

Every rule this module applies comes from config/cleaning_rules.yaml. The
cleaning itself is DuckDB SQL — the database already casts, coalesces and
normalises, and reaching for a dataframe library to do that would be a large
dependency doing what we already have.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb
import yaml

Rules = dict[str, Any]

CANONICAL = [
    "shipment_id",
    "carrier_code",
    "origin",
    "destination",
    "shipped_date",
    "promised_date",
    "delivered_date",
    "weight_kg",
    "cost_usd",
    "status",
]
DATE_FIELDS = ["shipped_date", "promised_date", "delivered_date"]
LOCATION_FIELDS = ["origin", "destination"]


def load_rules(path: Path) -> Rules:
    rules: Rules = yaml.safe_load(path.read_text())
    return rules


def _quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def scrub(column: str, rules: Rules) -> str:
    """Trim, then turn every configured null marker into a real NULL."""
    markers = ", ".join(_quote(str(m).lower()) for m in rules["null_markers"])
    return f"CASE WHEN lower(trim({column})) IN ({markers}) THEN NULL ELSE trim({column}) END"


def to_date(column: str, rules: Rules) -> str:
    """Try each configured format in order; NULL if none of them fit."""
    inner = scrub(column, rules)
    attempts = ", ".join(f"try_strptime({inner}, {_quote(f)})" for f in rules["date_formats"])
    return f"coalesce({attempts})::DATE"


def _alias_pairs(rules: Rules) -> list[tuple[str, str]]:
    return [(a, code) for code, aliases in rules["locations"].items() for a in aliases]


def _fetchone(con: duckdb.DuckDBPyConnection, sql: str) -> tuple[Any, ...]:
    """`fetchone()` types as Optional; a scalar SELECT never actually returns None."""
    row = con.execute(sql).fetchone()
    assert row is not None
    return row


def _headers(path: Path, con: duckdb.DuckDBPyConnection) -> list[str]:
    cursor = con.execute(f"SELECT * FROM read_csv({_quote(str(path))}, all_varchar=true) LIMIT 0")
    return [d[0] for d in cursor.description or []]


def _select_canonical(path: Path, rules: Rules, con: duckdb.DuckDBPyConnection) -> str:
    """One SELECT that renames a file's headers to the canonical field names.

    Files that lack a field get an explicit NULL, so every file unions cleanly.
    """
    present = {h.lower(): h for h in _headers(path, con)}
    projections = []
    for field in CANONICAL:
        source = next(
            (present[c.lower()] for c in rules["columns"][field] if c.lower() in present), None
        )
        projections.append(f'"{source}" AS {field}' if source else f"NULL AS {field}")
    return (
        f"SELECT {', '.join(projections)}, {_quote(path.name)} AS source_file "
        f"FROM read_csv({_quote(str(path))}, all_varchar=true)"
    )


def shipment_files(raw_dir: Path) -> list[Path]:
    return sorted(p for p in raw_dir.glob("*.csv") if p.name != "carriers.csv")


def profile(raw_dir: Path, rules: Rules) -> list[dict[str, Any]]:
    """Report what is wrong with each raw file, before anything is fixed."""
    con = duckdb.connect()
    aliases = {a for a, _ in _alias_pairs(rules)}
    reports: list[dict[str, Any]] = []

    for path in shipment_files(raw_dir):
        con.execute(f"CREATE OR REPLACE TEMP VIEW raw AS {_select_canonical(path, rules, con)}")

        nulls = {
            field: int(
                _fetchone(con, f"SELECT count(*) FROM raw WHERE {scrub(field, rules)} IS NULL")[0]
            )
            for field in CANONICAL
        }
        formats = {
            fmt: int(
                _fetchone(
                    con,
                    "SELECT "
                    + " + ".join(
                        f"count(try_strptime({scrub(f, rules)}, {_quote(fmt)}))"
                        for f in DATE_FIELDS
                    )
                    + " FROM raw",
                )[0]
            )
            for fmt in rules["date_formats"]
        }
        unmapped = [
            str(row[0])
            for row in con.execute(
                "SELECT DISTINCT v FROM ("
                + " UNION ALL ".join(
                    f"SELECT {scrub(f, rules)} AS v FROM raw" for f in LOCATION_FIELDS
                )
                + ") WHERE v IS NOT NULL AND lower(trim(v)) NOT IN "
                + f"({', '.join(_quote(a) for a in sorted(aliases))})"
            ).fetchall()
        ]
        counts = _fetchone(
            con,
            "SELECT count(*), "
            "count(*) - count(DISTINCT shipment_id), "
            f"count(*) FILTER (WHERE try_cast({scrub('weight_kg', rules)} AS DOUBLE) < 0), "
            f"count(*) FILTER (WHERE {to_date('delivered_date', rules)} "
            f"                       < {to_date('shipped_date', rules)}) "
            "FROM raw",
        )

        reports.append(
            {
                "file": path.name,
                "rows": int(counts[0]),
                "duplicate_ids": int(counts[1]),
                "negative_weight": int(counts[2]),
                "delivered_before_shipped": int(counts[3]),
                "nulls": nulls,
                "date_formats": formats,
                "unmapped_locations": sorted(unmapped),
            }
        )
    con.close()
    return reports
