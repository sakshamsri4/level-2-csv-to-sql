"""Command line entry point. Holds no rules — it prints what service and
pipeline return."""

from __future__ import annotations

from pathlib import Path

import typer

from assay.config import settings
from assay.ingest.pipeline import load_rules, profile

RULES_PATH = Path("config/cleaning_rules.yaml")

app = typer.Typer(add_completion=False, help="Clean messy shipment CSVs and ask questions of them.")


@app.callback()
def _main() -> None:
    """Clean messy shipment CSVs and ask questions of them."""


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
