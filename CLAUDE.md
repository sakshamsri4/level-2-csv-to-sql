# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

**Assay** — an ingestion pipeline that cleans messy legacy shipment CSVs into DuckDB, plus a natural-language interface that turns a business question into SQL, runs it, and answers in prose.

Level 2 of a three-part case study. Level 1 (`level-1-support-deflector`) is a sibling repo and shares no code with this one — deliberately. There is no `common/` package: the two share a *style* (ports, pure domain, offline-first tests), not logic, and extracting an abstraction from two samples would couple two independent submissions.

## The three deliverables

From the brief, verbatim in intent:

1. A pipeline that ingests dirty raw CSVs, standardises fields, and loads them into a local database.
2. A natural-language query interface where a user asks a business question, the agent translates it to SQL, runs it, and returns a formatted human-readable answer.
3. An evals framework: 5 sample queries demonstrating how the system guards against **SQL injection** and **hallucinated column names**.

Deliverable 3 is not a footnote — it is the reason the architecture below looks the way it does.

## Commands

Requires `uv`.

```bash
make setup                                        # venv + deps + .env
make profile                                      # what's wrong with the raw CSVs
make ingest                                       # clean + load into DuckDB
make ask Q="which route had the most delays?"     # one question
make app                                          # Streamlit interface
make eval                                         # eval suite, offline
make check                                        # lint + typecheck + tests
```

## Architecture

Same dependency rule as Level 1: **inward only.**

```
cli.py  app.py  ──►  service.py  ──►  ports.py  ──►  domain/
                                          ▲
adapters/  ───────────────────────────────┘
```

- `domain/` is pure. **The two guardrails live here as plain functions** — SQL safety and schema validation. Both are testable with no database and no model, which is what makes deliverable 3 cheap and honest.
- `ingest/` is the cleaning pipeline. It touches disk and DuckDB but no LLM.
- `adapters/` is the only place `openai` may be imported.
- `service.py` orchestrates and holds no rules.

### The two guardrails, concretely

**SQL injection.** The model writes SQL, so treat its output as untrusted input, not as code you asked for. Validate before executing: single statement only, `SELECT` (or `WITH`) only, no DDL/DML keywords, no multiple statements, no `ATTACH`/`COPY`/`INSTALL`/`LOAD` (DuckDB can read and write the filesystem). Belt and braces: open the connection **read-only** so a validator bug cannot become data loss.

**Hallucinated column names.** Parse the generated SQL, extract every table and column identifier, and check each against the real DuckDB schema *before* executing. A query naming `delivery_delay_days` when the column is `delay_days` must fail loudly with the actual schema in the error — not return an empty result the user reads as "no delays".

Both are pure functions over `(sql: str, schema: dict)`. No fixtures, no database, no key.

## Constraints that shape the code

- **Structured outputs.** The generated SQL comes back through a Pydantic schema, never scraped out of prose or a code fence.
- **The schema goes in the prompt.** A model cannot avoid hallucinating a column it was never shown. Render the real table and column names into the prompt from DuckDB's own catalogue, so it never drifts from the database.
- **Cleaning rules are data, not code.** Date formats, location aliases, and null markers belong in a config file that a domain expert could read, not buried in `if` branches.
- **Cleaning is observable.** `make profile` reports what is wrong *before* it is fixed, and ingestion reports what it changed. A pipeline that silently drops 12% of rows is worse than one that fails.
- **Prompts are versioned files**, not string literals.
- **Evals ship with the feature.** A guardrail whose test was written afterwards has only ever been observed passing.

## Things worth deciding early

- **Cleaning in SQL vs a dataframe library.** DuckDB reads CSVs and can cast, coalesce and normalise in SQL. Reaching for pandas or polars adds a large dependency to do what the database already does — justify it with a concrete case before adding it.
- **Fuzzy location matching.** "unstandardized location names" is the messiest requirement. An alias table is boring, auditable, and correct; fuzzy string matching is clever and silently merges two real cities. Start with the alias table.
- **Read-only connection for query execution, read-write only for ingest.** Two connections, different privileges, one guardrail you cannot forget to call.

## Testing: TDD

**Write the failing test first.** Red, then green, then refactor. The guardrails are the graded deliverable, and a guardrail whose test was written afterwards has only ever been observed passing.

- A test that has never failed is not evidence. If you write a test after the code, **break the code and watch the test fail**, then restore it.
- Every port gets a fake, not a mock. A `FakeLLM` returning a fixed SQL string — including a *malicious* one and a *hallucinated-column* one on demand — is how the guardrails get tested offline with no key. You cannot ask a real model to attack you reliably.
- Tests are named after the behaviour they protect, not the function they call.
- `make check` must be green at every commit.

## Git workflow

- **Never commit directly to `main`.** Branch first, always.
- **Branch names:** `<type>/<short-description>` — e.g. `feat/sql-safety-validator`.
- **Conventional Commits:** `feat: reject non-SELECT statements before execution`.
- **One logical change per commit**, and every commit leaves `make check` green.
- **Commit bodies explain *why*.** The diff shows what changed; the body records the trade-off that will not be obvious in six months.
- **Merge with `--no-ff`** so the branch structure stays visible.
