"""Streamlit interface. A form, a call into the service, and a rendering.

All the logic is in service.py; keeping this thin is what makes the CLI and
the app impossible to disagree with each other.
"""

from __future__ import annotations

import streamlit as st

from assay.adapters.duckdb_warehouse import DuckDBWarehouse
from assay.adapters.openai_llm import OpenAILLM
from assay.config import settings
from assay.ports import WarehouseError
from assay.service import ask

st.set_page_config(page_title="Assay", page_icon="🚚")
st.title("Assay")
st.caption("Ask the shipment warehouse a question in plain English.")

config = settings()
warehouse = DuckDBWarehouse(config.assay_warehouse)

try:
    schema = warehouse.schema()
except (FileNotFoundError, WarehouseError) as err:
    st.error(str(err))
    st.stop()

with st.sidebar:
    st.subheader("What the warehouse holds")
    for table in sorted(schema):
        st.write(f"**{table}**")
        st.caption(", ".join(sorted(schema[table])))

question = st.text_input(
    "Question", placeholder="which route had the highest delay rate last quarter?"
)

if question:
    with st.spinner("Generating SQL, checking it, running it…"):
        answer = ask(
            question,
            OpenAILLM(config.assay_generation_model, config.openai_api_key),
            warehouse,
            config.assay_max_rows,
        )

    if answer.refused:
        st.error(answer.prose)
        st.caption(
            "This query was not run. A refusal can mean the SQL was unsafe, it named "
            "a column that doesn't exist, the model judged the schema couldn't answer "
            "the question, or the database itself rejected a query that passed both "
            "checks — the message above says which."
        )
    else:
        st.success(answer.prose)
        if answer.rows:
            st.dataframe({c: [r[i] for r in answer.rows] for i, c in enumerate(answer.columns)})

    with st.expander("The SQL that was generated"):
        st.code(answer.sql or "(none)", language="sql")
        st.caption(
            f"{'refused before execution' if answer.refused else 'validated and executed'} "
            f"· {answer.elapsed_ms} ms"
        )
