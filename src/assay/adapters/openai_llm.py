"""The only module allowed to import openai.

The schema is rendered into the prompt from DuckDB's own catalogue: a model
cannot avoid hallucinating a column it was never shown.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from openai import OpenAI, OpenAIError
from pydantic import ValidationError

from assay.domain.models import GeneratedSQL
from assay.ports import LLMError, Schema

PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"

# Everything this adapter turns into LLMError, so the port's one promise holds.
# OpenAIError is the SDK's base class and covers auth, rate limits, timeouts and
# bad requests. The rest are the non-SDK ways this method can still fail:
# ValidationError if the response does not satisfy GeneratedSQL (pydantic raises
# it inside .parse(), and it is a ValueError, not an OpenAIError); LookupError
# for an empty `choices` list or a stray brace in a versioned prompt file; OSError
# if a prompt file is missing. Without these, the docstring's claim that every
# failure is translated would be false in four ways.
_TRANSLATED = (OpenAIError, ValidationError, LookupError, OSError)


def render_schema(schema: Schema) -> str:
    return "\n".join(f"{table}({', '.join(sorted(schema[table]))})" for table in sorted(schema))


class OpenAILLM:
    def __init__(self, model: str, api_key: str) -> None:
        if not api_key:
            raise LLMError("OPENAI_API_KEY is not set — add it to .env")
        self._model = model
        self._client = OpenAI(api_key=api_key)

    def generate_sql(self, question: str, schema: Schema) -> GeneratedSQL:
        try:
            system = (
                (PROMPT_DIR / "sql_generation.v1.md")
                .read_text()
                .format(schema=render_schema(schema))
            )
            completion = self._client.chat.completions.parse(
                model=self._model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": question},
                ],
                response_format=GeneratedSQL,
                temperature=0,
            )
            parsed = completion.choices[0].message.parsed
        except _TRANSLATED as err:
            raise LLMError(str(err)) from err
        if parsed is None:
            raise LLMError("the model returned no structured output")
        return parsed

    def summarise(self, question: str, sql: str, columns: list[str], rows: list[list[Any]]) -> str:
        try:
            prompt = (
                (PROMPT_DIR / "answer_formatting.v1.md")
                .read_text()
                .format(
                    question=question,
                    sql=sql,
                    result=json.dumps({"columns": columns, "rows": rows}, default=str),
                )
            )
            completion = self._client.chat.completions.create(
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
            )
            return completion.choices[0].message.content or ""
        except _TRANSLATED as err:
            raise LLMError(str(err)) from err
