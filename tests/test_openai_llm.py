"""OpenAILLM must translate every way the SDK can fail into the port-level
LLMError, the same way DuckDBWarehouse translates duckdb.Error into
WarehouseError — so the layers above never have to know openai exists, and
so no failure mode reaches the terminal as a raw traceback."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import openai
import pytest

from assay.adapters.openai_llm import OpenAILLM
from assay.ports import LLMError

SCHEMA = {"shipments": {"origin", "delay_days"}}


def test_a_missing_key_is_reported_as_llm_error_not_runtime_error():
    with pytest.raises(LLMError, match="OPENAI_API_KEY is not set"):
        OpenAILLM("gpt-4o-mini", "")


def test_generate_sql_wraps_an_sdk_exception_as_llm_error_with_the_original_message():
    llm = OpenAILLM("gpt-4o-mini", "test-key")

    def _boom(*args: object, **kwargs: object) -> None:
        # openai.OpenAIError is the SDK's base exception class — every
        # concrete failure (auth, rate limit, timeout, bad request) is a
        # subclass of it, so catching it at the adapter boundary catches all
        # of them without enumerating each one.
        raise openai.OpenAIError("rate limit exceeded")

    llm._client.chat.completions.parse = _boom  # type: ignore[method-assign]

    with pytest.raises(LLMError, match="rate limit exceeded"):
        llm.generate_sql("how many shipments were late?", SCHEMA)


@dataclass
class _Message:
    parsed: Any = None


@dataclass
class _Choice:
    message: _Message = field(default_factory=_Message)


@dataclass
class _Completion:
    choices: list[_Choice] = field(default_factory=lambda: [_Choice()])


def test_generate_sql_raises_llm_error_when_the_model_returns_no_parsed_output():
    llm = OpenAILLM("gpt-4o-mini", "test-key")

    llm._client.chat.completions.parse = lambda *a, **kw: _Completion()  # type: ignore[method-assign]

    with pytest.raises(LLMError, match="no structured output"):
        llm.generate_sql("how many shipments were late?", SCHEMA)


def test_summarise_wraps_an_sdk_exception_as_llm_error_with_the_original_message():
    llm = OpenAILLM("gpt-4o-mini", "test-key")

    def _boom(*args: object, **kwargs: object) -> None:
        raise openai.OpenAIError("the connection timed out")

    llm._client.chat.completions.create = _boom  # type: ignore[method-assign]

    with pytest.raises(LLMError, match="the connection timed out"):
        llm.summarise("q", "SELECT 1", ["a"], [[1]])
