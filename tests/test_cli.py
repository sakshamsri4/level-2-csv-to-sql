"""cli.py holds no rules — these tests only check that it presents failures
plainly instead of letting a traceback reach the terminal."""

from __future__ import annotations

import pytest
import typer

from assay import cli
from assay.config import Settings


def test_a_missing_warehouse_prints_the_plain_message_not_a_traceback(
    tmp_path, monkeypatch, capsys
):
    # A first-time user running `assay ask` before `assay ingest` should read
    # the same "run `make ingest` first" sentence DuckDBWarehouse already
    # writes — not a stack dump wrapped around it.
    fake_settings = Settings(openai_api_key="test-key", assay_warehouse=tmp_path / "missing.duckdb")
    monkeypatch.setattr(cli, "settings", lambda: fake_settings)

    with pytest.raises(typer.Exit) as excinfo:
        cli.ask("anything")

    assert excinfo.value.exit_code == 1
    out = capsys.readouterr().out
    assert "make ingest" in out


def test_eval_against_a_missing_warehouse_also_prints_plainly_not_a_traceback(
    tmp_path, monkeypatch, capsys
):
    fake_settings = Settings(openai_api_key="test-key", assay_warehouse=tmp_path / "missing.duckdb")
    monkeypatch.setattr(cli, "settings", lambda: fake_settings)

    with pytest.raises(typer.Exit) as excinfo:
        cli.eval_cmd(live=False)

    assert excinfo.value.exit_code == 1
    out = capsys.readouterr().out
    assert "make ingest" in out
