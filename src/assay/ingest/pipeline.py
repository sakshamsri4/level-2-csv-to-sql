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
        if source:
            escaped = source.replace('"', '""')
            projections.append(f'"{escaped}" AS {field}')
        else:
            projections.append(f"NULL AS {field}")
    return (
        f"SELECT {', '.join(projections)}, {_quote(path.name)} AS source_file "
        f"FROM read_csv({_quote(str(path))}, all_varchar=true)"
    )


def shipment_files(raw_dir: Path) -> list[Path]:
    return sorted(p for p in raw_dir.glob("*.csv") if p.name != "carriers.csv")


def ingest(raw_dir: Path, warehouse: Path, rules: Rules) -> dict[str, Any]:
    """Clean every raw extract into `shipments`, `carriers` and `rejects`.

    Nothing is silently discarded. A row that cannot be cleaned lands in
    `rejects` with the reason, and every count in the returned report adds up
    to the number of rows read.
    """
    warehouse.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(warehouse))

    pairs = ", ".join(f"({_quote(a)}, {_quote(c)})" for a, c in _alias_pairs(rules))
    con.execute(
        f"CREATE OR REPLACE TABLE locations AS SELECT * FROM (VALUES {pairs}) t(alias, code)"
    )

    union = " UNION ALL ".join(
        _select_canonical(path, rules, con) for path in shipment_files(raw_dir)
    )
    con.execute(f"CREATE OR REPLACE TABLE staging AS {union}")
    rows_read = int(_fetchone(con, "SELECT count(*) FROM staging")[0])

    delivered = ", ".join(_quote(s) for s in rules["statuses"]["delivered"])
    in_transit = ", ".join(_quote(s) for s in rules["statuses"]["in_transit"])

    # One pass: scrub, parse, canonicalise, derive, and label anything unusable.
    con.execute(f"""
        CREATE OR REPLACE TABLE typed AS
        SELECT
            {scrub("s.shipment_id", rules)}                       AS shipment_id,
            {scrub("s.carrier_code", rules)}                      AS carrier_code,
            o.code                                                AS origin,
            d.code                                                AS destination,
            s.origin                                              AS raw_origin,
            s.destination                                         AS raw_destination,
            {to_date("s.shipped_date", rules)}                    AS shipped_date,
            {to_date("s.promised_date", rules)}                   AS promised_date,
            {to_date("s.delivered_date", rules)}                  AS delivered_date,
            date_diff('day', {to_date("s.promised_date", rules)},
                             {to_date("s.delivered_date", rules)}) AS delay_days,
            CASE WHEN try_cast({scrub("s.weight_kg", rules)} AS DOUBLE) >= 0
                 THEN try_cast({scrub("s.weight_kg", rules)} AS DOUBLE) END AS weight_kg,
            try_cast({scrub("s.cost_usd", rules)} AS DECIMAL(12,2))         AS cost_usd,
            CASE WHEN lower(trim(s.status)) IN ({delivered})  THEN 'delivered'
                 WHEN lower(trim(s.status)) IN ({in_transit}) THEN 'in_transit' END AS status,
            try_cast({scrub("s.weight_kg", rules)} AS DOUBLE) < 0 AS weight_was_negative,
            CASE WHEN {scrub("s.status", rules)} IS NOT NULL
                      AND lower(trim(s.status)) NOT IN ({delivered})
                      AND lower(trim(s.status)) NOT IN ({in_transit})
                 THEN true ELSE false END AS status_was_unmapped,
            CASE
                WHEN {scrub("s.shipment_id", rules)} IS NULL     THEN 'missing shipment_id'
                WHEN {to_date("s.shipped_date", rules)} IS NULL  THEN 'unparseable shipped_date'
                WHEN {to_date("s.promised_date", rules)} IS NULL THEN 'unparseable promised_date'
                WHEN o.code IS NULL                             THEN 'unmapped origin'
                WHEN d.code IS NULL                             THEN 'unmapped destination'
                WHEN {to_date("s.delivered_date", rules)}
                     < {to_date("s.shipped_date", rules)}        THEN 'delivered before shipped'
            END AS reject_reason
        FROM staging s
        LEFT JOIN locations o ON lower(trim(s.origin)) = o.alias
        LEFT JOIN locations d ON lower(trim(s.destination)) = d.alias
    """)

    con.execute("""
        CREATE OR REPLACE TABLE rejects AS
        SELECT shipment_id, origin, destination, raw_origin, raw_destination,
               shipped_date, reject_reason
        FROM typed WHERE reject_reason IS NOT NULL
    """)
    con.execute("""
        CREATE OR REPLACE TABLE shipments AS
        SELECT shipment_id, carrier_code, origin, destination, shipped_date, promised_date,
               delivered_date, delay_days, weight_kg, cost_usd, status
        FROM typed
        WHERE reject_reason IS NULL
        QUALIFY row_number() OVER (PARTITION BY shipment_id ORDER BY shipped_date) = 1
    """)

    carriers_csv = raw_dir / "carriers.csv"
    con.execute(f"""
        CREATE OR REPLACE TABLE carriers AS
        SELECT trim(carrier_code) AS carrier_code, trim(name) AS carrier_name,
               lower(trim(tier))  AS service_tier
        FROM read_csv({_quote(str(carriers_csv))}, all_varchar=true)
    """)

    counts = _fetchone(
        con,
        """
        SELECT (SELECT count(*) FROM shipments),
               (SELECT count(*) FROM rejects),
               (SELECT count(*) FROM typed WHERE weight_was_negative),
               (SELECT count(*) FROM typed WHERE status_was_unmapped),
               (SELECT count(*) FROM carriers),
               (SELECT count(*) FROM shipments WHERE carrier_code IS NULL)
        """,
    )
    # Two ways a shipment_id can collapse to one row: a byte-identical duplicate
    # (safe to drop silently, so long as it is counted) and a genuine collision — the
    # same id with different data, which is a data-entry error, not a duplicate, and
    # must be reported rather than merged away indistinguishably from a real duplicate.
    dupes = _fetchone(
        con,
        """
        WITH ok AS (
            SELECT shipment_id, carrier_code, origin, destination, shipped_date,
                   promised_date, delivered_date, delay_days, weight_kg, cost_usd, status
            FROM typed WHERE reject_reason IS NULL
        ),
        distinct_ok AS (SELECT DISTINCT * FROM ok)
        SELECT
            (SELECT count(*) FROM ok) - (SELECT count(*) FROM distinct_ok),
            (SELECT count(*) FROM distinct_ok) - (SELECT count(DISTINCT shipment_id) FROM ok),
            (SELECT list(shipment_id) FROM (
                SELECT shipment_id FROM distinct_ok GROUP BY shipment_id HAVING count(*) > 1
            ))
        """,
    )
    reasons = {
        str(r[0]): int(r[1])
        for r in con.execute(
            "SELECT reject_reason, count(*) FROM rejects GROUP BY 1 ORDER BY 2 DESC"
        ).fetchall()
    }
    con.execute("DROP TABLE staging")
    con.execute("DROP TABLE typed")
    con.execute("DROP TABLE locations")
    con.close()

    return {
        "rows_read": rows_read,
        "rows_loaded": int(counts[0]),
        "rows_rejected": int(counts[1]),
        "duplicates_removed": int(dupes[0]),
        "conflicting_rows_dropped": int(dupes[1]),
        "conflicting_shipment_ids": sorted(str(sid) for sid in (dupes[2] or [])),
        "weights_nulled": int(counts[2]),
        "statuses_unmapped": int(counts[3]),
        "carriers_loaded": int(counts[4]),
        "shipments_without_carrier": int(counts[5]),
        "reject_reasons": reasons,
    }


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
