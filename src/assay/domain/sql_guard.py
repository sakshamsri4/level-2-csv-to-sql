"""The two guardrails, as one pure function over (sql, schema).

No database, no API key, no fixtures — which is what makes the eval suite
cheap and honest. The model writes the SQL, so the SQL is untrusted input.
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp

from assay.domain.models import Schema, Verdict

DIALECT = "duckdb"

# Default-deny on the statement type, then deny again on any dangerous node
# anywhere in the tree. find_all() includes the root, so these overlap on
# purpose — the overlap is free and the omission would not be.
ALLOWED_ROOTS = (exp.Select, exp.Union)
FORBIDDEN = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Drop,
    exp.Create,
    exp.Alter,
    exp.Attach,
    exp.Detach,
    exp.Copy,
    exp.Command,
    exp.Pragma,
    exp.Set,
    exp.Install,
    exp.Use,
)


def _unsafe(reason: str) -> Verdict:
    return Verdict(ok=False, kind="unsafe", reason=reason)


def _unknown(reason: str) -> Verdict:
    return Verdict(ok=False, kind="unknown_identifier", reason=reason)


def _check_identifiers(root: exp.Expression, schema: Schema) -> Verdict:
    """Every table and column the query names must exist in the real catalogue.

    A query naming `delivery_delay_days` when the column is `delay_days` must
    fail loudly with the real schema, not return an empty result the reader
    mistakes for "no delays".
    """
    # Names the query defines for itself. We cannot cheaply verify what columns
    # they expose, so qualifiers pointing at them are accepted unchecked — the
    # tables they are built from are checked on their own.
    ctes = {c.alias_or_name.lower() for c in root.find_all(exp.CTE)}
    opaque = {s.alias.lower() for s in root.find_all(exp.Subquery) if s.alias}

    tables: dict[str, set[str]] = {}  # how the query refers to it -> its real columns
    for table in root.find_all(exp.Table):
        name = table.name.lower()
        if name in ctes:
            opaque.add((table.alias or name).lower())
            continue
        if not name:
            return _unknown(
                "table functions such as read_csv() are not allowed; "
                f"query only these tables: {sorted(schema)}"
            )
        if name not in schema:
            return _unknown(f"there is no table named {name!r}; the tables are {sorted(schema)}")
        tables[(table.alias or name).lower()] = schema[name]

    anywhere: set[str] = set().union(*tables.values()) if tables else set()
    # `count(*) AS n` makes `n` a legitimate name to reference later.
    local = {a.alias.lower() for a in root.find_all(exp.Alias) if a.alias}

    for column in root.find_all(exp.Column):
        qualifier, name = column.table.lower(), column.name.lower()
        if qualifier and qualifier not in tables and qualifier not in opaque:
            return _unknown(f"{qualifier!r} is not a table or alias in this query")
        if qualifier in opaque or name in local:
            continue
        allowed = tables[qualifier] if qualifier else anywhere
        if name not in allowed:
            return _unknown(f"there is no column named {name!r}; available: {sorted(allowed)}")

    return Verdict(ok=True)


def check_sql(sql: str, schema: Schema) -> Verdict:
    """Refuse anything that is not a single, read-only SELECT over the real schema."""
    try:
        statements = [s for s in sqlglot.parse(sql, dialect=DIALECT) if s is not None]
    except sqlglot.ParseError as err:
        return _unsafe(f"could not be parsed as SQL: {err}")

    if len(statements) != 1:
        return _unsafe(f"expected exactly one statement, found {len(statements)}")

    root = statements[0]
    if not isinstance(root, ALLOWED_ROOTS):
        return _unsafe(f"only SELECT is allowed, but this is a {type(root).__name__.upper()}")

    for node in root.find_all(*FORBIDDEN):
        return _unsafe(f"{type(node).__name__.upper()} is not allowed")

    return _check_identifiers(root, schema)
