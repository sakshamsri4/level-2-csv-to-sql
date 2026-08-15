"""The two guardrails, as one pure function over (sql, schema).

No database, no API key, no fixtures — which is what makes the eval suite
cheap and honest. The model writes the SQL, so the SQL is untrusted input.
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.optimizer.qualify import qualify

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


def _render_schema(schema: Schema) -> str:
    return ", ".join(f"{table}({', '.join(sorted(schema[table]))})" for table in sorted(schema))


def _check_identifiers(root: exp.Expression, schema: Schema) -> Verdict:
    """Every table and column the query names must exist in the real catalogue.

    We validate the tables ourselves and hand the columns to sqlglot's own
    resolver, which understands scope, aliases, CTEs and derived tables far
    better than a hand-rolled walk does.
    """
    ctes = {cte.alias_or_name.lower() for cte in root.find_all(exp.CTE)}
    for table in root.find_all(exp.Table):
        name = table.name.lower()
        if name in ctes:
            continue  # a name the query defines for itself, not a real table
        if not name:
            return _unknown(
                "table functions such as read_csv() are not allowed; "
                f"query only these tables: {sorted(schema)}"
            )
        # sqlglot puts a two-part reference's qualifier in `.db` and a
        # three-part reference's leading qualifier in `.catalog`. Either one
        # naming something other than "main" reaches outside this warehouse —
        # ATTACH is refused, so nothing else should be reachable this way.
        qualifier = (table.catalog or table.db or "").lower()
        if qualifier and qualifier != "main":
            return _unknown(
                f"table {name!r} is qualified with {qualifier!r}; "
                f"only unqualified tables are allowed: {sorted(schema)}"
            )
        if name not in schema:
            return _unknown(f"there is no table named {name!r}; the tables are {sorted(schema)}")

    try:
        # qualify() rewrites the tree, so give it a copy — the SQL we execute
        # must stay byte-for-byte what was validated.
        qualify(
            root.copy(),
            dialect=DIALECT,
            schema={table: dict.fromkeys(columns, "VARCHAR") for table, columns in schema.items()},
            validate_qualify_columns=True,
        )
    except SqlglotError as err:
        # sqlglot names the offending column but not the real ones, and naming
        # the real ones is the entire point of this guardrail.
        return _unknown(f"{err}; the columns are {_render_schema(schema)}")

    # sqlglot's resolver skips unqualified columns under HAVING and QUALIFY —
    # it cannot tell them apart from select-list aliases, so it lets them be.
    # Check them against every name the query could possibly mean. Loose on
    # purpose: qualify() already did the precise work, and this only has to
    # catch a name that exists nowhere at all.
    known = set().union(*schema.values()) if schema else set()
    known |= {alias.alias.lower() for alias in root.find_all(exp.Alias) if alias.alias}
    for clause in root.find_all(exp.Having, exp.Qualify):
        for column in clause.find_all(exp.Column):
            if column.table or isinstance(column.this, exp.Star):
                continue  # qualified columns and stars were resolved above
            if column.name.lower() not in known:
                return _unknown(
                    f"there is no column named {column.name.lower()!r}; "
                    f"the columns are {_render_schema(schema)}"
                )

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
