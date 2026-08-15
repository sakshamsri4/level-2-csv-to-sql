# Assay — messy CSVs to natural-language answers

Cleans disorganised legacy shipment records into a queryable warehouse, then answers
business questions in plain English: *"which route had the highest delay rate last
quarter?"*

> **Status: scaffolding.** Structure and tooling only — no pipeline, no query interface,
> no evals yet. This README grows into the client-facing document as they land.

## Quickstart

```bash
make setup                                          # venv + deps + .env
# add OPENAI_API_KEY to .env
make profile                                        # what's wrong with the raw data
make ingest                                         # clean it and load DuckDB
make ask Q="which route had the most delays?"       # one question
make app                                            # Streamlit interface
make check                                          # lint + typecheck + tests
```

## What it will do

1. **Ingest** dirty CSVs from `data/raw/` — inconsistent date formats, missing fields,
   unstandardised location names — and load them into DuckDB with a reported record of
   every change made.
2. **Answer** business questions by generating SQL, validating it, running it, and
   formatting the result as prose.
3. **Refuse** SQL that is unsafe or references columns that do not exist, rather than
   returning an empty result the reader mistakes for a real answer.

## Design decisions

Recorded as they are made, with the measurement behind each. See [CLAUDE.md](CLAUDE.md)
for the working rules.

- **DuckDB, not SQLite or Postgres.** Reads CSVs natively, columnar so aggregate
  questions are fast, and it is a file rather than a service to run.
- **The generated SQL is untrusted input.** It is validated as a hostile string before
  execution and executed on a read-only connection, so a validator bug cannot become
  data loss.
- **The database schema is rendered into the prompt** from DuckDB's own catalogue, so it
  cannot drift out of date with the tables it describes.
- **The read-only connection is a second, independent line of defence — demonstrated,
  not just asserted.** With `check_sql`'s validator forced to approve everything, the
  eval suite's `DROP TABLE` case still could not do damage: DuckDB itself raised
  `Cannot execute statement of type "DROP" ... attached in read-only mode!` before any
  data was touched. It does **not** cover every case that read-only sounds like it
  should — `COPY shipments TO '/tmp/...'` was verified to succeed from a read-only
  connection, so for exfiltration the validator remains the only defence. A query that
  passes both guardrails can still fail against the real data (a `CAST` DuckDB cannot
  perform, for one); the adapter translates that vendor error into the port-level
  `WarehouseError`, and `service.ask` turns it into a refusal instead of a traceback.
