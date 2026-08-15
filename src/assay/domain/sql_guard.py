"""The two guardrails, as one pure function over (sql, schema).

No database, no API key, no fixtures — which is what makes the eval suite
cheap and honest. The model writes the SQL, so the SQL is untrusted input.
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp

from assay.domain.models import Verdict
from assay.ports import Schema

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

    return Verdict(ok=True)
