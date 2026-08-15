"""The two guardrails, as one pure function over (sql, schema).

No database, no API key, no fixtures — which is what makes the eval suite
cheap and honest. The model writes the SQL, so the SQL is untrusted input.
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.scope import Scope, build_scope

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


def _sources(scope: Scope) -> dict[str, exp.Table | Scope]:
    """What this query block can actually see. `selected_sources` rather than
    `sources` because the latter includes CTEs the block never selects from,
    which would let their aliases legitimise columns elsewhere."""
    return {name.lower(): node_source[1] for name, node_source in scope.selected_sources.items()}


def _check_identifiers(root: exp.Expression, schema: Schema) -> Verdict:
    """Every table and column the query names must exist in the real catalogue.

    Checked per query block, not across the whole tree: an alias defined in one
    scope must not silently vouch for a hallucinated column in another.
    """
    root_scope = build_scope(root)
    if root_scope is None:
        # Unreachable given check_sql only ever passes a Select or Union here,
        # both of which sqlglot's scope builder always resolves — but the
        # return type is Scope | None, so this keeps mypy honest.
        return Verdict(ok=True)

    for scope in root_scope.traverse():
        tables: dict[str, set[str]] = {}
        opaque: set[str] = set()
        for name, source in _sources(scope).items():
            if not isinstance(source, exp.Table):
                opaque.add(name)  # a CTE or subquery; its columns are its own business
                continue
            real = source.name.lower()
            if not real:
                return _unknown(
                    "table functions such as read_csv() are not allowed; "
                    f"query only these tables: {sorted(schema)}"
                )
            if real not in schema:
                return _unknown(
                    f"there is no table named {real!r}; the tables are {sorted(schema)}"
                )
            tables[name] = schema[real]

        # `count(*) AS n` makes `n` referenceable — but only inside this block.
        # `scope.expression` is a Query (Select or Union) for every scope build_scope
        # produces; the isinstance narrows the wider `exp.Expr` type for mypy.
        block = scope.expression
        selects = block.selects if isinstance(block, exp.Query) else []
        local = {s.alias.lower() for s in selects if isinstance(s, exp.Alias) and s.alias}
        anywhere: set[str] = set().union(*tables.values()) if tables else set()

        for column in scope.columns:
            if isinstance(column.this, exp.Star):
                continue  # `s.*` names no single column
            qualifier, name = column.table.lower(), column.name.lower()
            if not qualifier:
                if opaque:
                    continue
                allowed = anywhere
            elif qualifier in opaque:
                continue
            elif qualifier in tables:
                allowed = tables[qualifier]
            else:
                # A correlated reference reaching into an enclosing block.
                outer, found = scope.parent, None
                while outer is not None and found is None:
                    found = _sources(outer).get(qualifier)
                    outer = outer.parent
                if found is None:
                    return _unknown(f"{qualifier!r} is not a table or alias in this query")
                if not isinstance(found, exp.Table):
                    continue
                allowed = schema.get(found.name.lower(), set())
            if name not in allowed and name not in local:
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
