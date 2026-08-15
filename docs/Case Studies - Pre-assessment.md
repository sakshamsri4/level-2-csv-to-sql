# The Candidate Case Studies


## Level 2 (Medium): The Messy CSV to AI Data Pipeline

**The Scenario**: An enterprise logistics client stores legacy shipment records across disorganized CSV files containing inconsistent date formats, missing fields, and unstandardized location names. Executives want natural language answers to business questions like *"Which route had the highest delay rate last quarter?"*

- **Objective**: Build an end-to-end data ingestion and Text-to-SQL (or Text-to-Pandas) pipeline that cleans incoming data and enables natural language querying.

- **Deliverables**:
   1. A pipeline script that ingests dirty raw CSV datasets, standardizes fields, and loads them into a local database (SQLite/DuckDB).
   2. A natural language query interface (CLI, API, or Streamlit app) where a user asks a business question and the agent translates it to SQL, runs it, and returns a formatted human-readable answer.
   3. An Evals framework: A small test suite of 5 sample queries demonstrating how the system guards against SQL injection and hallucinated column names.

- **What this tests**: Data engineering basics, SQL/Pandas fluency, structured AI outputs, and defensive system design.

# Rubric: How the Team Should Grade Submissions

Evaluate submissions across four distinct vectors instead of focusing solely on code cleanliness:

| Dimension | Weight | What "Great" Looks Like |
| --- | --- | --- |
| System Architecture & Robustness | 35% | Handles bad inputs gracefully without crashing; clear separation of business logic, AI prompts, and data pipelines; includes basic logging/observability. |
| AI Evals & Reliability | 25% | Uses structured outputs (e.g., Pydantic); includes mechanisms to catch hallucinations, guard against prompt injection, or route low-confidence outputs. |
| Client Readme & Communication | 25% | The documentation is written for humans. Explains architecture trade-offs, setup steps, and how the business measures ROI. |
| Speed to Value | 15% | Uses the right tools/libraries to ship a functioning end-to-end prototype over a single weekend rather than over-engineering infrastructure. |
