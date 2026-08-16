# Over-Engineering Audit Cuts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the duplicated verdict logic, the dead CLI callback, and the redundant per-field profile queries found by the repo-wide over-engineering audit, without changing any observable behaviour.

**Architecture:** The central cut is that `Answer` does not carry the verdict that produced it, so three call sites re-derive it independently — `evals.py` re-runs `check_sql` after `ask()` already ran it, and `cli.py --live` reproduces `ask()`'s decision ordering by hand (its own comment admits this). Adding `Answer.kind` and a shared `decide()` makes both mirrors structural instead of hand-maintained. Everything else is local: a dead Typer callback, a redundant `Verdict.ok` field, a duplicated Literal list, and ten SQL round-trips where one would do.

**Tech Stack:** Python 3.12, pydantic v2, sqlglot, DuckDB, Typer, pytest, ruff, mypy strict, `uv`.

**Spec:** This plan's own "Findings this plan implements" section below. The audit was produced conversationally by `/ponytail-audit` and has no separate spec file, so the findings travel inline. Each task names the finding it closes.

## Global Constraints

- Python `>=3.12`. Do not add, remove, or upgrade any dependency in `pyproject.toml`.
- `ruff` line-length **100**; lint rules `E, F, I, UP, B, SIM, RUF`. Format with `uv run ruff format .`.
- `mypy` runs in **strict** mode over `packages = ["assay"]`. Every new function needs full annotations, including nested ones.
- **`make check` must be green at the end of every task**, before the commit. It runs `ruff format --check`, `ruff check`, `mypy`, `pytest`.
- **Never commit to `main`.** All work happens on the branch created in Task 0.
- **Conventional Commits.** One logical change per commit. The commit *body* explains the trade-off, not the diff.
- **No behaviour changes.** Every task is a refactor. CLI output text, log line shape, eval results, and refusal prose must be byte-identical before and after, except where a task explicitly says otherwise (none do).
- Tests are named after the behaviour they protect, not the function they call.
- Preserve existing explanatory comments when you move code. They record *why*, and the audit did not find them redundant.

---

## Findings this plan implements

| # | Finding | Task |
|---|---|---|
| 1 | `Answer` doesn't carry its verdict, so 3 call sites re-derive it | 4, 5, 6 |
| 2 | Five hand-built `Answer(...)` refusal blocks, each recomputing `elapsed_ms` | 4 |
| 3 | `--live` re-implements `ask()`'s decision order by hand | 6 |
| 4 | `@app.callback() def _main()` is dead code | 1 |
| 5 | `Verdict.ok` and `Verdict.kind` encode one fact | 2 |
| 6 | `VerdictKind` and `LogVerdict` list the same five strings twice | 3 |
| 7 | `profile()` fires 10 queries for 10 numbers | 7 |
| 10 | `generate.py` ships inside the wheel but is never imported at runtime | 8 (optional) |

Findings 8 and 9 from the audit were **rejected on closer inspection** — see "Considered and rejected" at the end of this plan. Do not implement them.

---

### Task 0: Branch

**Files:** none.

- [ ] **Step 1: Confirm the tree is clean and green before touching anything**

```bash
git status --porcelain   # expect no output
make check               # expect: 122 passed
```

If `git status` prints anything, stop and ask. This plan assumes a clean baseline.

- [ ] **Step 2: Create the branch**

```bash
git checkout -b refactor/audit-cuts
```

---

### Task 1: Delete the dead Typer callback

**Files:**
- Modify: `src/assay/cli.py:27-29`
- Test: `tests/test_cli.py` (add one test, add one import)

**Interfaces:**
- Consumes: nothing.
- Produces: nothing. `cli.app` keeps its current shape.

**Why this is safe:** Typer collapses a `Typer()` into a single-command CLI only when exactly one command is registered. `cli.py` registers four (`profile`, `ingest`, `ask`, `eval`), so no collapse can happen and the callback exists only to restate the help string already passed to `Typer(help=...)`. This was verified against a three-command Typer app: `--help` renders identically with and without the callback.

**A note on TDD shape:** this task is a pure deletion, so there is no new behaviour to drive red-green. The test below is a **characterization test** — it pins the behaviour that must survive the deletion. It passes before the change and must still pass after. Write it first anyway; deleting a callback with no test on the help output is exactly how help text silently regresses.

- [ ] **Step 1: Add the characterization test**

Add this import to the import block at the top of `tests/test_cli.py`, after `import typer`:

```python
from typer.testing import CliRunner
```

Add this test at the end of `tests/test_cli.py`:

```python
def test_the_cli_help_names_the_tool_and_all_four_commands():
    # The only thing the removed @app.callback() ever contributed was help
    # text, and Typer(help=...) already supplies it. This pins that.
    result = CliRunner().invoke(cli.app, ["--help"])

    assert result.exit_code == 0
    assert "Clean messy shipment CSVs" in result.output
    for command in ("profile", "ingest", "ask", "eval"):
        assert command in result.output
```

- [ ] **Step 2: Run it and confirm it PASSES against the current code**

Run: `uv run pytest tests/test_cli.py::test_the_cli_help_names_the_tool_and_all_four_commands -v`
Expected: **PASS**. If it fails, the help text differs from what this plan assumes — stop and report before deleting anything.

- [ ] **Step 3: Delete the callback**

In `src/assay/cli.py`, delete these three lines entirely (lines 27-29, plus the blank line that separates them from the next decorator):

```python
@app.callback()
def _main() -> None:
    """Clean messy shipment CSVs and ask questions of them."""
```

The file should now go straight from the `app = typer.Typer(...)` assignment to `@app.command("profile")`.

- [ ] **Step 4: Run the test again and the whole suite**

Run: `uv run pytest tests/test_cli.py -v`
Expected: **PASS**, including the new test — the help output is unchanged.

Run: `make check`
Expected: all green, 123 passed.

- [ ] **Step 5: Commit**

```bash
git add src/assay/cli.py tests/test_cli.py
git commit -m "refactor: delete the no-op Typer callback

Typer only collapses to a single-command CLI when exactly one command is
registered; this app registers four, so the callback could never have been
load-bearing. Its docstring duplicated the help string already passed to
Typer(help=...). The new test pins the help output so the deletion is
observed rather than assumed."
```

---

### Task 2: Derive `Verdict.ok` from `Verdict.kind`

**Files:**
- Modify: `src/assay/domain/models.py:44-51`
- Modify: `src/assay/domain/sql_guard.py:41-46` and `:139`
- Test: `tests/test_config.py:23-27` (update), and add one test

**Interfaces:**
- Consumes: nothing.
- Produces: `Verdict.ok` becomes a read-only `bool` property, `Verdict.kind` gains the default `"ok"` it already had. Every existing read (`verdict.ok` in `service.py:51`, `evals.py:55`, `cli.py:127`) keeps working untouched. Every *construction* that passed `ok=` must drop it.

**Why:** `ok` and `kind` encode one fact — `ok` is true exactly when `kind == "ok"`. Two fields for one fact is two things that can disagree.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_config.py`, after `test_a_refusal_carries_its_reason_and_no_rows`:

```python
def test_a_verdict_is_ok_exactly_when_its_kind_says_so():
    # ok and kind cannot disagree, because there is only one of them.
    assert Verdict().ok
    assert not Verdict(kind="unsafe", reason="only SELECT is allowed").ok
    assert not Verdict(kind="unknown_identifier", reason="no such column").ok
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_config.py::test_a_verdict_is_ok_exactly_when_its_kind_says_so -v`
Expected: **FAIL** with a pydantic `ValidationError: 1 validation error for Verdict / ok / Field required`. `ok: bool` currently has no default, so `Verdict()` cannot be constructed at all.

- [ ] **Step 3: Make `ok` a property**

In `src/assay/domain/models.py`, replace the body of `Verdict` (lines 49-51) so the class reads:

```python
class Verdict(BaseModel):
    """The guardrails' answer. `kind` exists so an eval can assert *why* something
    was refused — a suite that only checks "rejected" cannot tell a schema
    rejection from a parse failure."""

    kind: VerdictKind = "ok"
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.kind == "ok"
```

- [ ] **Step 4: Drop the now-invalid `ok=` arguments**

In `src/assay/domain/sql_guard.py`, update the two factories (lines 41-46):

```python
def _unsafe(reason: str) -> Verdict:
    return Verdict(kind="unsafe", reason=reason)


def _unknown(reason: str) -> Verdict:
    return Verdict(kind="unknown_identifier", reason=reason)
```

And the success return at the end of `_check_identifiers` (line 139):

```python
    return Verdict()
```

In `tests/test_config.py:24`, update the construction inside `test_a_refusal_carries_its_reason_and_no_rows`:

```python
    v = Verdict(kind="unsafe", reason="only SELECT is allowed")
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_config.py tests/test_sql_guard.py -v`
Expected: **PASS** — all of `test_sql_guard.py`'s 40+ cases still pass, because they read `.ok` and never construct a `Verdict`.

Run: `make check`
Expected: all green, 124 passed.

- [ ] **Step 6: Commit**

```bash
git add src/assay/domain/models.py src/assay/domain/sql_guard.py tests/test_config.py
git commit -m "refactor: derive Verdict.ok from Verdict.kind

ok was true exactly when kind was 'ok', so the two fields encoded one fact
and could be constructed to disagree. Making ok a property removes that
possibility; every read site is unchanged."
```

---

### Task 3: Define `LogVerdict` from `VerdictKind` instead of restating it

**Files:**
- Modify: `src/assay/domain/models.py:9-15`

**Interfaces:**
- Consumes: nothing.
- Produces: `LogVerdict` stays the same union of five strings and stays importable under the same name. `VerdictKind` stays the narrower three. Nothing else changes.

**Why:** the two aliases list `"ok"`, `"unsafe"`, `"unknown_identifier"` twice. Composing the wide one from the narrow one removes the duplication while *keeping* both precisions — `check_sql` still cannot be typed as returning `"execution_error"`. Do not merge them into one alias; that would lose real type information.

- [ ] **Step 1: Compose the alias**

In `src/assay/domain/models.py`, replace lines 9-15 with:

```python
VerdictKind = Literal["ok", "unsafe", "unknown_identifier"]

# Every value _log() can ever emit: the guardrail's own VerdictKind, plus the two
# refusal causes that never touch check_sql at all — one decided before it ever
# runs (unanswerable), one only discoverable after both guardrails already said
# yes and the database itself refused (execution_error). Composed from
# VerdictKind rather than restated, so the two cannot drift apart.
LogVerdict = VerdictKind | Literal["unanswerable", "execution_error"]
```

- [ ] **Step 2: Verify mypy still resolves both aliases**

Run: `make check`
Expected: all green, 124 passed. `mypy` proves the composition is equivalent — `service.py:92`'s `verdict: LogVerdict` parameter and `_log`'s callers are all still accepted.

- [ ] **Step 3: Commit**

```bash
git add src/assay/domain/models.py
git commit -m "refactor: compose LogVerdict from VerdictKind

The two aliases listed the same three strings twice, so adding a verdict kind
meant editing two places. Composing keeps both precisions — check_sql still
cannot be typed as returning execution_error — while stating the shared three
once."
```

---

### Task 4: `Answer` carries the verdict that produced it, and `ask()` refuses through one helper

**Files:**
- Modify: `src/assay/domain/models.py:54-61`
- Modify: `src/assay/service.py` (rewrite `ask()` and `_log()`)
- Test: `tests/test_service.py` (add two tests)

**Interfaces:**
- Consumes: `LogVerdict` from Task 3, `Verdict.ok` property from Task 2.
- Produces:
  - `Answer.kind: LogVerdict = "ok"` — a new field, defaulted, so no existing `Answer(...)` construction in tests breaks.
  - `_log(answer: Answer) -> None` — **signature change**, was `_log(answer, verdict)`. It now reads `answer.kind`.
  - `ask()`'s signature and return type are unchanged.

**Why:** `Answer` is what `ask()` hands to every caller, and it currently omits the single most important thing `ask()` decided. Tasks 5 and 6 depend on this field existing. The five hand-built `Answer(...)` blocks that each recompute `int((time.monotonic() - started) * 1000)` collapse into one nested `refuse()` at the same time, because they are the same edit to the same function.

- [ ] **Step 1: Write the failing tests**

Add both to the end of `tests/test_service.py`:

```python
def test_the_answer_carries_the_verdict_that_produced_it():
    # Callers should not have to re-derive why ask() decided what it decided.
    # evals.py used to re-run check_sql to recover exactly this.
    warehouse = FakeWarehouse()

    good = ask("good", FakeLLM(sql="SELECT origin FROM shipments"), warehouse, max_rows=200)
    unsafe = ask("bad", FakeLLM(sql="DROP TABLE shipments"), warehouse, max_rows=200)
    bogus = ask("huh", FakeLLM(sql="SELECT nope FROM shipments"), warehouse, max_rows=200)
    cannot = ask(
        "impossible",
        FakeLLM(sql="SELECT origin FROM shipments", answerable=False),
        warehouse,
        max_rows=200,
    )

    assert good.kind == "ok"
    assert unsafe.kind == "unsafe"
    assert bogus.kind == "unknown_identifier"
    assert cannot.kind == "unanswerable"


def test_a_refused_answer_never_reports_an_ok_verdict():
    """The invariant evals.py used to re-check per case: refused and kind cannot
    disagree. Asserting it once here is what lets the eval runner trust
    answer.kind instead of computing a second opinion from check_sql."""

    class BrokenWarehouse:
        def schema(self) -> dict[str, set[str]]:
            return SCHEMA

        def run(self, sql: str, max_rows: int) -> tuple[list[str], list[list[Any]]]:
            raise WarehouseError("boom")

    answers = [
        ask("good", FakeLLM(sql="SELECT origin FROM shipments"), FakeWarehouse(), max_rows=200),
        ask("bad", FakeLLM(sql="DROP TABLE shipments"), FakeWarehouse(), max_rows=200),
        ask("huh", FakeLLM(sql="SELECT nope FROM shipments"), FakeWarehouse(), max_rows=200),
        ask(
            "impossible",
            FakeLLM(sql="SELECT origin FROM shipments", answerable=False),
            FakeWarehouse(),
            max_rows=200,
        ),
        ask("broken", FakeLLM(sql="SELECT origin FROM shipments"), BrokenWarehouse(), max_rows=200),
    ]

    for answer in answers:
        assert answer.refused == (answer.kind != "ok")
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_service.py -k "carries_the_verdict or never_reports_an_ok" -v`
Expected: **FAIL**, both, with `AttributeError: 'Answer' object has no attribute 'kind'`.

- [ ] **Step 3: Add the field to `Answer`**

In `src/assay/domain/models.py`, add `kind` to `Answer` (after `refused`), and extend the docstring — the class currently has none:

```python
class Answer(BaseModel):
    """What ask() hands back, including *why* it decided what it decided.

    `kind` is the verdict, carried rather than re-derived: the eval runner and
    the live eval both need it, and computing a second opinion from check_sql
    gave two routes to one answer that had to be kept in agreement by hand.
    """

    question: str
    sql: str = ""
    columns: list[str] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    prose: str = ""
    refused: bool = False
    kind: LogVerdict = "ok"
    elapsed_ms: int = 0
```

- [ ] **Step 4: Rewrite `ask()` and `_log()`**

Replace everything in `src/assay/service.py` from `def ask(` to the end of the file with:

```python
def ask(question: str, llm: LLM, warehouse: Warehouse, max_rows: int) -> Answer:
    """Question in, prose out — refusing before execution if the SQL is not safe
    and does not match the real schema."""
    started = time.monotonic()

    def refuse(prose: str, kind: LogVerdict, sql: str = "") -> Answer:
        answer = Answer(
            question=question,
            sql=sql,
            prose=prose,
            refused=True,
            kind=kind,
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )
        _log(answer)
        return answer

    try:
        schema = warehouse.schema()
    except WarehouseError as err:
        # Fetching the catalogue is itself a database call — a locked file, an
        # I/O error, a corrupted database — and it happens before any SQL has
        # even been generated. Same failure shape as a run() error, so the
        # same refusal and the same verdict.
        return refuse(
            f"That query was valid, but the database could not run it: {err}",
            "execution_error",
        )

    generated = llm.generate_sql(question, schema)

    if not generated.answerable:
        return refuse(
            f"{generated.rationale} I did not run a substitute query, "
            "since a number answering a different question is worse than no answer.",
            "unanswerable",
            generated.sql,
        )

    verdict = check_sql(generated.sql, schema)
    if not verdict.ok:
        return refuse(f"I did not run that query. {verdict.reason}", verdict.kind, generated.sql)

    try:
        columns, rows = warehouse.run(generated.sql, max_rows)
    except WarehouseError as err:
        # Both guardrails said yes — the SQL is safe and every identifier is
        # real — but the data underneath did not cooperate (a CAST that met a
        # value it cannot convert, for one). That is not a hallucination and
        # not an attack, so it gets its own verdict rather than being folded
        # into either guardrail's.
        return refuse(
            f"That query was valid, but the database could not run it: {err}",
            "execution_error",
            generated.sql,
        )

    answer = Answer(
        question=question,
        sql=generated.sql,
        columns=columns,
        rows=rows,
        prose=llm.summarise(question, generated.sql, columns, rows),
        elapsed_ms=int((time.monotonic() - started) * 1000),
    )
    _log(answer)
    return answer


def _log(answer: Answer) -> None:
    log.info(
        json.dumps(
            {
                "event": "ask",
                "question": answer.question,
                "sql": answer.sql,
                "verdict": answer.kind,
                "refused": answer.refused,
                "rows": len(answer.rows),
                "elapsed_ms": answer.elapsed_ms,
            }
        )
    )
```

Then fix the import line at the top of `src/assay/service.py` — `LogVerdict` is still used (by `refuse`), so line 9 stays as it is:

```python
from assay.domain.models import Answer, LogVerdict
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_service.py -v`
Expected: **PASS**, all 16 tests. The existing log-shape tests (`test_a_database_execution_error_is_logged_with_its_own_verdict`, `test_the_log_line_is_valid_json_carrying_what_observability_needs`) must pass untouched — the JSON key is still `"verdict"` and the values are unchanged.

Run: `make check`
Expected: all green, 126 passed.

- [ ] **Step 6: Commit**

```bash
git add src/assay/domain/models.py src/assay/service.py tests/test_service.py
git commit -m "refactor: carry the verdict on Answer and refuse through one helper

ask() returned the decision but not the reason for it, so callers that needed
the reason computed a second opinion of their own. Carrying kind on Answer
makes the eval runner and the live eval read the decision that was actually
made rather than reconstruct one.

The five Answer(...) blocks collapse into refuse() in the same change because
they are the same edit: each recomputed elapsed_ms by hand, which is four
chances to compute it from the wrong start time."
```

---

### Task 5: The eval runner reads the verdict instead of recomputing it

**Files:**
- Modify: `src/assay/evals.py:27-77`
- Test: `tests/test_evals.py` (no changes needed — verify)

**Interfaces:**
- Consumes: `Answer.kind` from Task 4.
- Produces: `run_evals(cases_path: Path, warehouse: Warehouse) -> list[EvalResult]` — signature unchanged. `EvalResult` fields unchanged. `EvalResult.reason` now always carries `answer.prose`; previously it carried `verdict.reason` for answerable cases and `answer.prose` for the unanswerable one. Both are only ever *printed on failure* by `cli.py:108`, and the guard's reason is embedded verbatim inside the prose (`"I did not run that query. {verdict.reason}"`), so failure output stays as informative.

**Why:** `run_evals` calls `ask()` — which runs `check_sql` — and then runs `check_sql` a second time itself, then cross-checks the two answers for agreement. One route, one answer.

- [ ] **Step 1: Confirm the existing tests still describe the behaviour**

Run: `uv run pytest tests/test_evals.py -v`
Expected: **PASS**, 5 tests. These are the tests that must stay green through this task; they are the specification. Do not modify them.

- [ ] **Step 2: Rewrite `run_evals`**

In `src/assay/evals.py`, replace the whole `run_evals` function (lines 27-77) with:

```python
def run_evals(cases_path: Path, warehouse: Warehouse) -> list[EvalResult]:
    """Judge every case in cases.yaml against the verdict ask() actually reached.

    The verdict is read off the Answer rather than recomputed from check_sql,
    which matters most for the unanswerable case: its SQL is completely valid
    on its own, so check_sql alone would call it "ok". Only ask() sees the
    `answerable` flag that refuses it, so only ask()'s verdict is the truth.
    `service.ask` guarantees refused and kind agree, so this runner does not
    re-check that per case — tests/test_service.py asserts the invariant once.
    """
    cases: list[dict[str, Any]] = yaml.safe_load(cases_path.read_text())
    results = []
    for case in cases:
        answer = ask(
            str(case["question"]),
            FakeLLM(sql=str(case["sql"]), answerable=bool(case.get("answerable", True))),
            warehouse,
            max_rows=10,
        )
        results.append(
            EvalResult(
                id=str(case["id"]),
                guards=str(case["guards"]).strip(),
                expect=str(case["expect"]),
                got=answer.kind,
                passed=answer.kind == str(case["expect"]),
                reason=answer.prose,
            )
        )
    return results
```

- [ ] **Step 3: Drop the now-unused import**

In `src/assay/evals.py`, delete line 12:

```python
from assay.domain.sql_guard import check_sql
```

`ruff` (rule `F401`) will fail the build if you forget. Leave `yaml`, `FakeLLM`, `Warehouse`, and `ask` imports alone.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_evals.py tests/test_cli.py -v`
Expected: **PASS**. In particular `test_every_shipped_eval_case_passes` must still pass all 8 cases, and `test_the_suite_also_covers_the_low_confidence_routing_path` must still report `got == "unanswerable"` — that case is the whole reason the old two-route version existed.

Run: `uv run assay eval`
Expected: `8/8 passed`, exit code 0. (Requires `make ingest` to have been run at least once. If the warehouse is missing, run `make ingest` first.)

Run: `make check`
Expected: all green, 126 passed.

- [ ] **Step 5: Commit**

```bash
git add src/assay/evals.py
git commit -m "refactor: read the eval verdict off the Answer, not a second check_sql

run_evals called ask() — which runs check_sql — and then ran check_sql again
itself, then asserted the two agreed. That is two routes to one answer, kept
in sync by a per-case consistency check. Reading answer.kind gives the verdict
the running system actually reached, which is the thing an eval should be
grading. The refused/kind invariant is now asserted once in test_service.py
instead of re-derived on every case."
```

---

### Task 6: `--live` and `ask()` share one decision order

**Files:**
- Modify: `src/assay/service.py` (add `decide()`, use it in `ask()`)
- Modify: `src/assay/cli.py:113-131`
- Test: `tests/test_service.py` (add one test)

**Interfaces:**
- Consumes: `LogVerdict` (Task 3), `Answer.kind` (Task 4).
- Produces: `decide(generated: GeneratedSQL, schema: Schema) -> tuple[LogVerdict, str]` in `service.py` — returns the verdict kind and the human-readable refusal prose, `("ok", "")` when nothing objects. `ask()` and `cli.eval_cmd`'s `--live` block both route through it.

**Why:** the `--live` block carries the comment *"Mirror service.ask's decision order"*. A mirror maintained by hand drifts; a shared function cannot. Specifically it must stay true that `answerable` is consulted **before** `check_sql`, or `--live` reports "allowed" for a question the running system refuses.

**What this deliberately does NOT do:** it does not route `--live` through `ask()`. Doing so would execute the generated SQL and call `summarise()` for every allowed case, doubling live API spend and turning a read-only preview into a full run. `--live` keeps calling `generate_sql` only.

- [ ] **Step 1: Write the failing test**

Add to the end of `tests/test_service.py`:

First extend the imports at the top of `tests/test_service.py`:

```python
from assay.domain.models import GeneratedSQL
from assay.service import ask, decide
```

(replacing the existing `from assay.service import ask`), then add the test at the end of the file:

```python
def test_an_unanswerable_flag_outranks_a_perfectly_valid_query():
    """decide() is what keeps `assay eval --live` honest. The SQL below is valid
    — real table, real column, one SELECT — so check_sql alone calls it ok. If
    the answerable flag were consulted second, --live would print "allowed" for
    a question the running system actually refuses."""
    valid = GeneratedSQL(sql="SELECT origin FROM shipments", rationale="x", answerable=False)
    kind, prose = decide(valid, SCHEMA)
    assert kind == "unanswerable"
    assert "x" in prose

    answerable = GeneratedSQL(sql="SELECT origin FROM shipments", rationale="fine")
    kind, prose = decide(answerable, SCHEMA)
    assert kind == "ok"
    assert prose == ""
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_service.py::test_an_unanswerable_flag_outranks_a_perfectly_valid_query -v`
Expected: **FAIL** with `ImportError: cannot import name 'decide' from 'assay.service'`.

- [ ] **Step 3: Add `decide()` and route `ask()` through it**

In `src/assay/service.py`, add `GeneratedSQL` and `Schema` to the existing `domain.models` import. Both are defined there; leave the `ports` import line alone:

```python
from assay.domain.models import Answer, GeneratedSQL, LogVerdict, Schema
from assay.domain.sql_guard import check_sql
from assay.ports import LLM, Warehouse, WarehouseError
```

Add `decide()` immediately **above** `ask()`:

```python
def decide(generated: GeneratedSQL, schema: Schema) -> tuple[LogVerdict, str]:
    """The verdict for a generated query, and the sentence explaining it.

    Both ask() and `assay eval --live` consult this, so the order the two
    signals are read in cannot drift between them. The order is load-bearing:
    an unanswerable question's SQL is usually valid on its own, so consulting
    check_sql first would call it allowed and hide the refusal the running
    system actually makes.
    """
    if not generated.answerable:
        return "unanswerable", (
            f"{generated.rationale} I did not run a substitute query, "
            "since a number answering a different question is worse than no answer."
        )
    verdict = check_sql(generated.sql, schema)
    if not verdict.ok:
        return verdict.kind, f"I did not run that query. {verdict.reason}"
    return "ok", ""
```

Then, inside `ask()`, replace the two separate blocks — the `if not generated.answerable:` block and the `verdict = check_sql(...)` block written in Task 4 — with this single block, leaving everything before `generated = llm.generate_sql(...)` and everything after untouched:

```python
    kind, prose = decide(generated, schema)
    if kind != "ok":
        return refuse(prose, kind, generated.sql)
```

- [ ] **Step 4: Run the service tests**

Run: `uv run pytest tests/test_service.py tests/test_evals.py -v`
Expected: **PASS**, all of them. The refusal prose is unchanged because `decide()` returns the exact strings `ask()` used to build inline — `test_an_unanswerable_refusal_names_what_was_missing_not_a_number` and `test_a_hallucinated_column_is_refused_with_the_real_schema_in_the_message` are what prove that.

- [ ] **Step 5: Rewrite the `--live` block**

In `src/assay/cli.py`, replace the `if live:` block (lines 113-131) with:

```python
    if live:
        typer.echo("\n--- live: what the real model actually emits ---")
        try:
            llm = OpenAILLM(config.assay_generation_model, config.openai_api_key)
            schema = warehouse.schema()
            for case in yaml.safe_load(Path("evals/cases.yaml").read_text()):
                generated = llm.generate_sql(str(case["question"]), schema)
                kind, _ = decide(generated, schema)
                state = "allowed" if kind == "ok" else f"refused ({kind})"
                typer.echo(f"  {case['id']:32} {state:26} {generated.sql[:70]}")
        except (FileNotFoundError, WarehouseError, LLMError) as err:
            typer.echo(f"\n{err}\n")
            raise typer.Exit(code=1) from err
```

Update the imports at the top of `src/assay/cli.py` — `check_sql` is no longer used there, and `decide` is:

```python
from assay.service import ask as service_ask
from assay.service import decide
```

and delete this line:

```python
from assay.domain.sql_guard import check_sql
```

- [ ] **Step 6: Run the CLI tests and the whole suite**

Run: `uv run pytest tests/test_cli.py -v`
Expected: **PASS**, 8 tests, including `test_a_live_eval_llm_failure_prints_the_plain_message_not_a_traceback` — the `try/except` around the live block is unchanged.

Run: `make check`
Expected: all green, 127 passed.

- [ ] **Step 7: Commit**

```bash
git add src/assay/service.py src/assay/cli.py tests/test_service.py
git commit -m "refactor: share one decision order between ask() and --live

The --live block carried the comment 'Mirror service.ask's decision order',
which is an accurate description of a mirror maintained by hand. decide()
makes it structural. The order matters and is now stated once: an
unanswerable question's SQL is usually valid on its own, so consulting
check_sql first reports 'allowed' for a question the system refuses.

--live still calls generate_sql only. Routing it through ask() would have
been less code but would execute every allowed query and call summarise on
it, doubling the spend of the one command that costs real money."
```

---

### Task 7: `profile()` counts nulls in one query, not ten

**Files:**
- Modify: `src/assay/ingest/pipeline.py:252-257`
- Test: `tests/test_profile.py` (no changes needed — verify)

**Interfaces:**
- Consumes: nothing.
- Produces: `profile()`'s return shape is unchanged — `report["nulls"]` is still `dict[str, int]` keyed by canonical field name.

**Why:** ten `SELECT count(*)` round-trips to fetch ten numbers, when the two blocks immediately below already show the pattern for fetching several counts in one statement.

- [ ] **Step 1: Confirm the existing tests specify the behaviour**

Run: `uv run pytest tests/test_profile.py -v`
Expected: **PASS**, 5 tests. `test_profiling_counts_the_null_markers_it_finds` asserts `report["nulls"]["delivered_date"] == 1` and `report["nulls"]["cost_usd"] == 1`; that is the contract this task must not change.

- [ ] **Step 2: Replace the per-field loop with one statement**

In `src/assay/ingest/pipeline.py`, inside `profile()`, replace the `nulls = {...}` dict comprehension (lines 252-257) with:

```python
        null_counts = _fetchone(
            con,
            "SELECT "
            + ", ".join(f"count(*) FILTER (WHERE {scrub(f, rules)} IS NULL)" for f in CANONICAL)
            + " FROM raw",
        )
        nulls = {field: int(null_counts[i]) for i, field in enumerate(CANONICAL)}
```

This matches the `counts = _fetchone(...)` idiom already used twenty lines below it.

- [ ] **Step 3: Run the tests**

Run: `uv run pytest tests/test_profile.py -v`
Expected: **PASS**, 5 tests, unchanged.

- [ ] **Step 4: Confirm the real output is identical**

Run: `uv run assay profile`
Expected: the `missing values:` line for each file reports the same dict as before the change. Compare against `git stash && uv run assay profile > /tmp/before.txt && git stash pop && uv run assay profile > /tmp/after.txt && diff /tmp/before.txt /tmp/after.txt` if you want it proven rather than eyeballed — `diff` must be empty.

Run: `make check`
Expected: all green, 127 passed.

- [ ] **Step 5: Commit**

```bash
git add src/assay/ingest/pipeline.py
git commit -m "refactor: count profile nulls in one query instead of ten

Ten round-trips for ten numbers, in a function whose next two blocks already
fetch several counts per statement. Same idiom, one statement."
```

---

### Task 8 (OPTIONAL): Move the CSV generator out of the shipped package

**Files:**
- Move: `src/assay/ingest/generate.py` → `tools/generate_raw.py`
- Modify: `pyproject.toml:49-52` (`[tool.mypy]`)
- Modify: `README.md:14`
- Modify: `docs/superpowers/specs/2026-08-13-assay-design.md:22,52,163,165`

**Interfaces:**
- Consumes: nothing.
- Produces: nothing. No Python module imports `assay.ingest.generate`; it is only ever run as `__main__`. Verified by grep.

**Why this is optional:** it saves zero lines and removes 185 lines from a wheel nobody installs — this project is run from source. Skip it without hesitation if you would rather stop after Task 7. It is included because a dev-only tool living inside the importable package is the kind of thing that gets imported by accident later.

**Trial-verified:** this exact sequence was run and reverted before this plan was written. `make check` stayed green (mypy reported 17 source files instead of 18) and the moved generator reproduced the committed CSVs.

- [ ] **Step 1: Move the file**

```bash
mkdir -p tools
git mv src/assay/ingest/generate.py tools/generate_raw.py
```

- [ ] **Step 2: Fix the path depth and the usage line in its docstring**

In `tools/generate_raw.py`, the `RAW` constant walked up three levels from `src/assay/ingest/`; from `tools/` it needs one. Change line 17:

```python
RAW = Path(__file__).resolve().parents[1] / "data" / "raw"
```

And the usage line in the module docstring (line 6):

```python
    uv run python tools/generate_raw.py
```

- [ ] **Step 3: Point mypy at the new location**

In `pyproject.toml`, replace the `[tool.mypy]` block's `packages` line:

```toml
[tool.mypy]
python_version = "3.12"
files = ["src/assay", "tools"]
mypy_path = "src"
strict = true
```

`mypy_path = "src"` is required — without it, `tools/` is checked but `assay` imports from within `src/` stop resolving.

- [ ] **Step 4: Verify the toolchain**

Run: `make check`
Expected: all green. `mypy` should report `Success: no issues found in 17 source files` (was 18 — `generate.py` left the package and `generate_raw.py` joined via `files`).

- [ ] **Step 5: Verify the generator still produces the committed data**

```bash
uv run python tools/generate_raw.py
git diff --stat data/raw
```

Expected: the three CSVs are rewritten with **identical content**. `git diff --stat` will report the files as modified with a CRLF warning and no visible hunks — `csv.writer` emits `\r\n` while the committed files are LF-normalised. **This is pre-existing behaviour, not caused by the move.** Discard the rewrite so the commit stays focused:

```bash
git checkout -- data/raw
```

- [ ] **Step 6: Update the two prose references**

In `README.md:14`, change `` (`src/assay/ingest/generate.py`) `` to `` (`tools/generate_raw.py`) ``.

In `docs/superpowers/specs/2026-08-13-assay-design.md`, update the four mentions at lines 22, 52, 163 and 165 to name `tools/generate_raw.py`. Leave `docs/superpowers/plans/2026-08-15-assay-implementation.md` alone — it is a historical record of a completed plan and should describe the tree as it was.

- [ ] **Step 7: Run the full check and commit**

Run: `make check`
Expected: all green, 127 passed.

```bash
git add -A
git commit -m "refactor: move the CSV generator out of the importable package

generate.py is a dev tool whose output is committed; nothing imports it and
it only ever runs as __main__. Living inside src/assay/ it shipped in the
wheel and was one import away from becoming a runtime dependency. mypy keeps
covering it via files/mypy_path, so it does not lose strict typing by leaving
the package."
```

---

### Task 9: Close out

**Files:**
- Modify: `README.md` (only if a claim it makes is now wrong)

- [ ] **Step 1: Check the README's architecture section against the new tree**

Read `README.md:80-105`. The dependency-arrow diagram and the bullet describing `service.py` are still accurate — `decide()` is orchestration, not a rule, and `check_sql` still lives in `domain/`. Update only if you find a specific sentence that is now false. **Do not** rewrite the section for style; the audit rated it a strength.

- [ ] **Step 2: Verify the whole deliverable end to end**

```bash
make check          # expect all green, 127 passed
make ingest         # expect the report, unchanged counts
uv run assay eval   # expect 8/8 passed
uv run assay profile
```

- [ ] **Step 3: Confirm the size of the cut**

```bash
git diff main --stat -- src/
```

Expected: roughly **-70 lines** net across `src/`, with `service.py` and `evals.py` carrying most of it. Tests grow by ~50 lines. If `src/` shrank by much less than 60, a task was probably half-applied — check Tasks 4 and 5.

- [ ] **Step 4: Merge**

```bash
git checkout main
git merge --no-ff refactor/audit-cuts
make check
```

---

## Considered and rejected

Two audit findings did not survive a closer look. They are recorded here so nobody re-derives them and "fixes" them later.

**Deduplicating `render_schema`.** `sql_guard._render_schema` joins with `", "`, `openai_llm.render_schema` joins with `"\n"`. They are one line each, and they sit in different layers — the domain cannot import the adapter, so merging them means either putting a formatting helper in `domain/models.py` (whose docstring says "No behaviour lives here") or making the adapter import from `sql_guard`, which is a cross-layer import to save two lines. Adding an abstraction to remove two lines is the thing the audit exists to prevent. **Leave both.**

**Removing `@lru_cache` from `settings()`.** The audit called it unmeasured. It isn't purposeless: Streamlit re-executes `app.py` top to bottom on every widget interaction, so `settings()` is called on every rerun, and without the cache each one re-reads `.env` from disk. The saving is tiny, but "tiny" is not "none", and the cache costs two lines. **Leave it.**

Also considered and rejected, from the audit's own marginal list: replacing `pydantic-settings` with `os.environ` (you would hand-roll `.env` parsing, which is the dependency's actual value) and replacing `typer` with `argparse` (roughly 40 more lines for the same four commands).

## Not in scope

The audit found one thing that is **not** an over-engineering issue and is not addressed here: database row values flow verbatim into `prompts/answer_formatting.v1.md`, so a poisoned cell reaches the summarising model. It is low severity — the raw rows and the SQL are always displayed beside the prose, so a reader can check the answer — but it is a correctness/security question, not a complexity one. It needs its own spec and its own eval case. Do not bolt it onto this branch.
