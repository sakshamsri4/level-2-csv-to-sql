"""The only module allowed to import openai.

The schema is rendered into the prompt from DuckDB's own catalogue: a model
cannot avoid hallucinating a column it was never shown.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from openai import OpenAI, OpenAIError

from assay.domain.models import GeneratedSQL
from assay.ports import LLMError, Schema

PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"


def render_schema(schema: Schema) -> str:
    return "\n".join(f"{table}({', '.join(sorted(schema[table]))})" for table in sorted(schema))


class OpenAILLM:
    def __init__(self, model: str, api_key: str) -> None:
        if not api_key:
            raise LLMError("OPENAI_API_KEY is not set — add it to .env")
        self._model = model
        self._client = OpenAI(api_key=api_key)

    def generate_sql(self, question: str, schema: Schema) -> GeneratedSQL:
        system = (
            (PROMPT_DIR / "sql_generation.v1.md").read_text().format(schema=render_schema(schema))
        )
        try:
            completion = self._client.chat.completions.parse(
                model=self._model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": question},
                ],
                response_format=GeneratedSQL,
                temperature=0,
            )
        except OpenAIError as err:
            raise LLMError(str(err)) from err
        parsed = completion.choices[0].message.parsed
        if parsed is None:
            raise LLMError("the model returned no structured output")
        return parsed

    def summarise(self, question: str, sql: str, columns: list[str], rows: list[list[Any]]) -> str:
        prompt = (
            (PROMPT_DIR / "answer_formatting.v1.md")
            .read_text()
            .format(
                question=question,
                sql=sql,
                result=json.dumps({"columns": columns, "rows": rows}, default=str),
            )
        )
        try:
            completion = self._client.chat.completions.create(
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
            )
        except OpenAIError as err:
            raise LLMError(str(err)) from err
        return completion.choices[0].message.content or ""
