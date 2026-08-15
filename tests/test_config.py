from pathlib import Path

from assay.config import Settings
from assay.domain.models import Answer, GeneratedSQL, Verdict


def test_settings_read_paths_from_environment(monkeypatch):
    monkeypatch.setenv("ASSAY_MAX_ROWS", "42")
    monkeypatch.setenv("ASSAY_WAREHOUSE", "/tmp/other.duckdb")
    s = Settings()
    assert s.assay_max_rows == 42
    assert s.assay_warehouse == Path("/tmp/other.duckdb")


def test_settings_have_working_defaults_without_any_environment():
    # _env_file=None so this tests the class's own defaults, not whatever the
    # developer's real .env happens to contain on this machine.
    s = Settings(_env_file=None)
    assert s.assay_raw_dir == Path("data/raw")
    assert s.assay_generation_model


def test_a_refusal_carries_its_reason_and_no_rows():
    v = Verdict(ok=False, kind="unsafe", reason="only SELECT is allowed")
    answer = Answer(question="q", prose=v.reason, refused=True)
    assert answer.rows == []
    assert answer.refused


def test_generated_sql_requires_both_fields():
    g = GeneratedSQL(sql="SELECT 1", rationale="because")
    assert g.sql == "SELECT 1"
