"""cli.py holds no rules — these tests only check that it presents failures
plainly instead of letting a traceback reach the terminal."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
import pytest
import typer

from assay import cli
from assay.config import Settings
from assay.domain.models import GeneratedSQL, Schema
from assay.evals import EvalResult
from assay.ports import LLMError


@dataclass
class _RaisingLLM:
    """A FakeLLM-style stub whose generate_sql raises LLMError, standing in for
    a real OpenAILLM hitting an expired key, a rate limit, or a bad model name."""

    message: str = "rate limit exceeded"

    def generate_sql(self, question: str, schema: Schema) -> GeneratedSQL:
        raise LLMError(self.message)

    def summarise(self, question: str, sql: str, columns: list[str], rows: list[list[Any]]) -> str:
        raise LLMError(self.message)


def _warehouse_db(tmp_path: Path) -> Path:
    db = tmp_path / "w.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE shipments AS SELECT 'SHP-1' AS shipment_id, 3 AS delay_days")
    con.close()
    return db


def test_no_api_key_prints_the_plain_message_not_a_traceback(monkeypatch, capsys):
    # This is the branch's most visible defect: `make setup` writes a .env
    # with a placeholder key, and `make ask` is the very next command the
    # README shows. Deliberately does NOT pass openai_api_key="test-key" —
    # that would skip past the exact constructor branch this test exists to
    # prove, and hide the bug it is meant to catch.
    fake_settings = Settings(openai_api_key="", assay_warehouse=Path("unused.duckdb"))
    monkeypatch.setattr(cli, "settings", lambda: fake_settings)

    with pytest.raises(typer.Exit) as excinfo:
        cli.ask("which route had the highest delay rate last quarter?")

    assert excinfo.value.exit_code == 1
    out = capsys.readouterr().out
    assert "OPENAI_API_KEY is not set" in out
    assert "Traceback" not in out


def test_an_llm_failure_during_generation_prints_the_plain_message_not_a_traceback(
    tmp_path, monkeypatch, capsys
):
    fake_settings = Settings(openai_api_key="test-key", assay_warehouse=_warehouse_db(tmp_path))
    monkeypatch.setattr(cli, "settings", lambda: fake_settings)
    monkeypatch.setattr(cli, "OpenAILLM", lambda model, api_key: _RaisingLLM("rate limit exceeded"))

    with pytest.raises(typer.Exit) as excinfo:
        cli.ask("which route had the highest delay rate last quarter?")

    assert excinfo.value.exit_code == 1
    out = capsys.readouterr().out
    assert "rate limit exceeded" in out
    assert "Traceback" not in out


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


def test_a_failing_eval_case_prints_got_and_reason_not_just_fail(tmp_path, monkeypatch, capsys):
    # EvalResult.reason is computed for every case but, before this fix, was
    # never printed — so on the one occasion the graded artifact matters, the
    # output said only "FAIL" with no way to tell why.
    fake_settings = Settings(openai_api_key="test-key", assay_warehouse=_warehouse_db(tmp_path))
    monkeypatch.setattr(cli, "settings", lambda: fake_settings)
    canned = [
        EvalResult(
            id="broken_case",
            guards="a guardrail that should have refused this",
            expect="unsafe",
            got="ok",
            passed=False,
            reason="the query was wrongly allowed to run",
        )
    ]
    monkeypatch.setattr(cli, "run_evals", lambda cases_path, warehouse: canned)

    with pytest.raises(typer.Exit):
        cli.eval_cmd(live=False)

    out = capsys.readouterr().out
    assert "FAIL" in out and "broken_case" in out
    assert "got: ok" in out
    assert "the query was wrongly allowed to run" in out


def test_a_passing_eval_case_does_not_print_got_or_reason(tmp_path, monkeypatch, capsys):
    # PASS lines are already legible; a wall of reasons on every line would
    # bury the failures that actually need attention.
    fake_settings = Settings(openai_api_key="test-key", assay_warehouse=_warehouse_db(tmp_path))
    monkeypatch.setattr(cli, "settings", lambda: fake_settings)
    canned = [
        EvalResult(
            id="fine_case",
            guards="a control case",
            expect="ok",
            got="ok",
            passed=True,
            reason="",
        )
    ]
    monkeypatch.setattr(cli, "run_evals", lambda cases_path, warehouse: canned)

    cli.eval_cmd(live=False)

    out = capsys.readouterr().out
    assert "PASS" in out and "fine_case" in out
    assert "got:" not in out
    assert "reason:" not in out
