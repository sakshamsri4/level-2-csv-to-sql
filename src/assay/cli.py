"""Command line entry point. Holds no rules — it prints what service and
pipeline return."""

from __future__ import annotations

import logging
from pathlib import Path

import typer
import yaml

from assay.adapters.duckdb_warehouse import DuckDBWarehouse
from assay.adapters.openai_llm import OpenAILLM
from assay.config import settings
from assay.evals import run_evals
from assay.ingest.pipeline import ingest as pipeline_ingest
from assay.ingest.pipeline import load_rules, profile
from assay.ports import LLMError, WarehouseError
from assay.service import ask as service_ask
from assay.service import decide

RULES_PATH = Path("config/cleaning_rules.yaml")

app = typer.Typer(add_completion=False, help="Clean messy shipment CSVs and ask questions of them.")


@app.command("profile")
def profile_raw() -> None:
    """Report what is wrong with the raw CSVs, before anything is fixed."""
    config = settings()
    for report in profile(config.assay_raw_dir, load_rules(RULES_PATH)):
        typer.echo(f"\n{report['file']} — {report['rows']} rows")
        typer.echo(f"  duplicate shipment ids     {report['duplicate_ids']}")
        typer.echo(f"  negative weights           {report['negative_weight']}")
        typer.echo(f"  delivered before shipped   {report['delivered_before_shipped']}")
        typer.echo("  date formats in use:")
        for fmt, count in report["date_formats"].items():
            typer.echo(f"    {fmt:12} {count}")
        missing = {f: n for f, n in report["nulls"].items() if n}
        typer.echo(f"  missing values: {missing or 'none'}")
        if report["unmapped_locations"]:
            typer.echo(f"  UNKNOWN LOCATIONS: {report['unmapped_locations']}")


@app.command()
def ingest() -> None:
    """Clean data/raw/*.csv and load them into DuckDB."""
    config = settings()
    report = pipeline_ingest(config.assay_raw_dir, config.assay_warehouse, load_rules(RULES_PATH))
    typer.echo(f"read       {report['rows_read']}")
    typer.echo(f"loaded     {report['rows_loaded']}")
    typer.echo(f"duplicates {report['duplicates_removed']}")
    typer.echo(f"id collisions (kept 1, different data) {report['conflicting_rows_dropped']}")
    if report["conflicting_shipment_ids"]:
        typer.echo(f"    ids: {report['conflicting_shipment_ids']}")
    typer.echo(f"rejected   {report['rows_rejected']}")
    for reason, count in report["reject_reasons"].items():
        typer.echo(f"    {reason}: {count}")
    typer.echo(f"weights nulled (negative)   {report['weights_nulled']}")
    typer.echo(f"statuses unmapped           {report['statuses_unmapped']}")
    typer.echo(f"shipments with no carrier   {report['shipments_without_carrier']}")
    typer.echo(f"carriers                    {report['carriers_loaded']}")
    typer.echo(f"\n-> {config.assay_warehouse}")


@app.command()
def ask(question: str) -> None:
    """Ask one business question."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    config = settings()
    try:
        answer = service_ask(
            question,
            OpenAILLM(config.assay_generation_model, config.openai_api_key),
            DuckDBWarehouse(config.assay_warehouse),
            config.assay_max_rows,
        )
    except (FileNotFoundError, WarehouseError, LLMError) as err:
        typer.echo(f"\n{err}\n")
        raise typer.Exit(code=1) from err
    typer.echo(f"\n{answer.prose}\n")
    if answer.sql:
        typer.echo(f"  SQL: {answer.sql}")
    if answer.refused:
        raise typer.Exit(code=1)


@app.command("eval")
def eval_cmd(
    live: bool = typer.Option(False, "--live", help="Send the questions to the real model"),
) -> None:
    """Run the eval suite. Offline by default: no key, no spend."""
    config = settings()
    warehouse = DuckDBWarehouse(config.assay_warehouse)
    try:
        results = run_evals(Path("evals/cases.yaml"), warehouse)
    except (FileNotFoundError, WarehouseError, LLMError) as err:
        typer.echo(f"\n{err}\n")
        raise typer.Exit(code=1) from err
    for r in results:
        typer.echo(f"  {'PASS' if r.passed else 'FAIL'}  {r.id:32} {r.expect:20} {r.guards[:60]}")
        if not r.passed:
            typer.echo(f"        got: {r.got}")
            typer.echo(f"        reason: {r.reason}")
    failed = [r for r in results if not r.passed]
    typer.echo(f"\n{len(results) - len(failed)}/{len(results)} passed")

    if live:
        typer.echo("\n--- live: what the real model actually emits ---")
        try:
            llm = OpenAILLM(config.assay_generation_model, config.openai_api_key)
            schema = warehouse.schema()
            for case in yaml.safe_load(Path("evals/cases.yaml").read_text()):
                generated = llm.generate_sql(str(case["question"]), schema)
                kind, _ = decide(generated, schema)
                state = "allowed" if kind == "ok" else f"refused ({kind})"
                typer.echo(f"  {case['id']:32} {state:26} {generated.sql[:70]}")
        except (FileNotFoundError, WarehouseError, LLMError) as err:
            typer.echo(f"\n{err}\n")
            raise typer.Exit(code=1) from err

    if failed:
        raise typer.Exit(code=1)
