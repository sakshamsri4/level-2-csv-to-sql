from assay.adapters.fakes import FakeLLM
from assay.adapters.openai_llm import PROMPT_DIR, render_schema

SCHEMA = {"shipments": {"origin", "delay_days"}, "carriers": {"carrier_code"}}


def test_the_schema_is_rendered_so_the_model_can_see_every_real_column():
    text = render_schema(SCHEMA)
    assert "shipments(delay_days, origin)" in text
    assert "carriers(carrier_code)" in text


def test_the_prompts_are_files_on_disk_not_string_literals():
    for name in ("sql_generation.v1.md", "answer_formatting.v1.md"):
        assert (PROMPT_DIR / name).is_file()


def test_the_sql_prompt_has_a_slot_for_the_real_schema():
    assert "{schema}" in (PROMPT_DIR / "sql_generation.v1.md").read_text()


def test_the_fake_returns_whatever_sql_the_test_asked_for():
    fake = FakeLLM(sql="SELECT 1")
    assert fake.generate_sql("anything", SCHEMA).sql == "SELECT 1"
    assert fake.summarise("q", "SELECT 1", ["a"], [[1]])
