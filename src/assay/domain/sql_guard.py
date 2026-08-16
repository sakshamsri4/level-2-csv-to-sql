"""The two guardrails, as one pure function over (sql, schema).

No database, no API key, no fixtures — which is what makes the eval suite
cheap and honest. The model writes the SQL, so the SQL is untrusted input.
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import build_scope

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
    return Verdict(kind="unsafe", reason=reason)


def _unknown(reason: str) -> Verdict:
    return Verdict(kind="unknown_identifier", reason=reason)


def _render_schema(schema: Schema) -> str:
    return ", ".join(f"{table}({', '.join(sorted(schema[table]))})" for table in sorted(schema))


def _check_identifiers(root: exp.Expression, schema: Schema) -> Verdict:
    """Every table and column the query names must exist in the real catalogue.

    We validate the tables ourselves and hand the columns to sqlglot's own
    resolver, which understands scope, aliases, CTEs and derived tables far
    better than a hand-rolled walk does.
    """
    # Which CTE names are visible AT each table reference, not tree-wide. A
    # flat set let a CTE buried in a nested subquery exempt the same bare name
    # in an outer scope, where DuckDB resolves it to a real catalogue object
    # (`sqlite_master` and friends). sqlglot's scope walker already knows this;
    # the previous hand-rolled version of this function is why it is trusted.
    #
    # Fails closed by construction: a table the walker never reports gets an
    # empty visible-set, so it is checked against the schema with no exemption.
    visible: dict[int, set[str]] = {}
    scope_root = build_scope(root)
    if scope_root is not None:
        for scope in scope_root.traverse():
            names = {name.lower() for name in scope.cte_sources}
            for scoped in scope.tables:
                visible[id(scoped)] = names

    for table in root.find_all(exp.Table):
        name = table.name.lower()
        # Qualifiers are judged BEFORE the CTE exemption, and this ordering is
        # load-bearing: a CTE reference is never qualified, so anything carrying
        # a qualifier is a real table regardless of what the query defines for
        # itself. Checking the exemption first let `WITH shipments AS (...)
        # SELECT ... FROM other_db.shipments` shadow the bare name and skip both
        # this check and the schema check below.
        #
        # sqlglot splits a reference across `.catalog` and `.db` by arity, so
        # both positions are checked — testing only the first non-empty one let
        # a real leading catalog hide a foreign schema behind it.
        qualifiers = {q.lower() for q in (table.catalog, table.db) if q}
        foreign = qualifiers - {"main"}
        if foreign:
            return _unknown(
                f"table {name!r} is qualified with {sorted(foreign)}; "
                f"only unqualified tables are allowed: {sorted(schema)}"
            )
        # Unqualified only: a CTE cannot be referenced with a qualifier, so
        # `main.deliveries` is a real-table reference even when a CTE shares
        # the bare name.
        if not qualifiers and name in visible.get(id(table), set()):
            continue  # a name the query defines for itself, not a real table
        if not name:
            return _unknown(
                "table functions such as read_csv() are not allowed; "
                f"query only these tables: {sorted(schema)}"
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

    return Verdict()


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
