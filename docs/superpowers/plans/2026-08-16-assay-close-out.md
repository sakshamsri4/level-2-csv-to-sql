# Assay Close-Out Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the last verification gap on the `feat/assay` branch — the one
untested `--live` failure path — then run the outstanding review and merge gates.

**Architecture:** No new files, no new modules, no new dependencies. One
`try/except` in `cli.py` and one test beside the five that already cover the
same shape. Everything else in this plan is a gate to pass, not code to write.

**Tech Stack:** Python 3.12, typer, pytest, ruff, mypy strict. Already installed.

**Spec:** `docs/superpowers/specs/2026-08-13-assay-design.md`

**Prior plan:** `docs/superpowers/plans/2026-08-15-assay-implementation.md` —
Tasks 1-11 complete, 24 commits, head `4900c78`.

## Why this plan is one task long

The branch is feature-complete and verified at HEAD: `make check` green
(111 tests, mypy strict over 18 files, ruff over 34), `assay eval` 8/8,
`make ingest` reconciling 448 = 427 + 13 + 8, live `assay ask` answering
against the real model, Streamlit serving HTTP 200.

Nine Minor findings are parked with written rulings and stay parked — the
final review triaged them as fine-to-defer, and two of them as *not to be
"fixed"* because the current behaviour is what makes the README's arithmetic
reconcile. Reopening them buys nothing and risks the numbers.

That leaves exactly one defect: `eval_cmd`'s `--live` branch calls
`llm.generate_sql` outside any `except`, so a real API failure there prints a
traceback. It is the only code in this plan.

## Global Constraints

Copied from the prior plan; every task's requirements implicitly include these.

- `src/assay/domain/` imports nothing outside `domain/`. No `duckdb`, no
  `openai`, no `assay.ports`.
- `openai` may be imported only under `src/assay/adapters/`.
- The default test suite runs offline: no API key, no network. Live tests carry
  the `live` marker and are deselected by `addopts = "-m 'not live'"`.
- `make check` must be green at every commit.
- Conventional Commits. One logical change per commit. Never commit to `main`.
- Work continues on the existing `feat/assay` branch — do not branch again.

---

### Task 1: The `--live` eval path fails as plainly as every other path

**Files:**
- Modify: `src/assay/cli.py:113-121`
- Test: `tests/test_cli.py` (append; reuse the existing `_RaisingLLM` stub at
  `tests/test_cli.py:21-32` — do not write a second stub)

**Interfaces:**
- Consumes: `LLMError` from `assay.ports` (already imported at `cli.py:19`),
  `_RaisingLLM` and `_warehouse_db` from `tests/test_cli.py`.
- Produces: nothing new. No signature changes.

**Context:** `assay ask` and the offline `assay eval` both catch
`(FileNotFoundError, WarehouseError, LLMError)` and print one sentence
(`cli.py:83`, `cli.py:102`). The `--live` block added the only call site that
does not. The whole point of the branch is that this system fails legibly, so
the one path that dumps a traceback is the one worth closing.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cli.py`:

```python
def test_a_live_eval_llm_failure_prints_the_plain_message_not_a_traceback(
    tmp_path, monkeypatch, capsys
):
    # --live is the only path that calls the model outside a try/except. It
    # needs a real key to reach, which is exactly why no run has ever hit it.
    fake_settings = Settings(openai_api_key="test-key", assay_warehouse=_warehouse_db(tmp_path))
    monkeypatch.setattr(cli, "settings", lambda: fake_settings)
    monkeypatch.setattr(cli, "OpenAILLM", lambda model, api_key: _RaisingLLM("rate limit exceeded"))
    monkeypatch.setattr(cli, "run_evals", lambda cases_path, warehouse: [])

    with pytest.raises(typer.Exit) as excinfo:
        cli.eval_cmd(live=True)

    assert excinfo.value.exit_code == 1
    out = capsys.readouterr().out
    assert "rate limit exceeded" in out
    assert "Traceback" not in out
```

- [ ] **Step 2: Run it and watch it fail**

```bash
uv run pytest tests/test_cli.py::test_a_live_eval_llm_failure_prints_the_plain_message_not_a_traceback -v
```

Expected: FAIL — `LLMError: rate limit exceeded` propagates out of
`cli.eval_cmd` instead of `typer.Exit`. If it fails for any other reason, or
passes, stop and report: the test is wrong, not the code.

- [ ] **Step 3: Wrap the live block**

Replace `src/assay/cli.py:113-121` with:

```python
    if live:
        typer.echo("\n--- live: what the real model actually emits ---")
        try:
            llm = OpenAILLM(config.assay_generation_model, config.openai_api_key)
            schema = warehouse.schema()
            for case in yaml.safe_load(Path("evals/cases.yaml").read_text()):
                generated = llm.generate_sql(str(case["question"]), schema)
                verdict = check_sql(generated.sql, schema)
                state = "allowed" if verdict.ok else f"refused ({verdict.kind})"
                typer.echo(f"  {case['id']:32} {state:26} {generated.sql[:70]}")
        except (WarehouseError, LLMError) as err:
            typer.echo(f"\n{err}\n")
            raise typer.Exit(code=1) from err
```

The offline results printed above this block stay on screen — a live failure
must not erase the eight offline verdicts the user already earned.

- [ ] **Step 4: Run the test and the full suite**

```bash
uv run pytest tests/test_cli.py -v
make check
```

Expected: the new test PASSES, the other five `test_cli.py` tests still pass,
`make check` green at 112 tests.

- [ ] **Step 5: Mutate — prove the test can fail**

A test written after the code has only ever been observed passing. Break the
code deliberately and confirm the test catches it:

```bash
# Temporarily narrow the except to WarehouseError only, then:
uv run pytest tests/test_cli.py::test_a_live_eval_llm_failure_prints_the_plain_message_not_a_traceback -v
```

Expected: FAIL. Restore the `except (WarehouseError, LLMError)` tuple and
confirm PASS again. Record both outputs in the report.

- [ ] **Step 6: Commit**

```bash
git add src/assay/cli.py tests/test_cli.py
git commit -m "fix: catch LLM failures on the --live eval path too

--live was the only call site that reached the model outside a try/except,
so a rate limit or expired key there printed a traceback while every other
entry point printed one sentence. It needs a real key to reach, which is why
no run had ever hit it."
```

---

### Task 2: Run `--live` once for real and record what the model actually emits

**Files:**
- Modify: `README.md` — the evals section (`## The two guardrails`, subsection
  `### The suite is proven able to fail` at `README.md:170`), append the live
  result beneath it.

**Interfaces:**
- Consumes: Task 1's `--live` path.
- Produces: nothing code-facing.

**Context:** The offline suite is proven. `assay eval --live` — which sends the
five adversarial *questions* to the real model and checks we refuse whatever it
genuinely emits — has never been run at any commit. Its value is that it is the
only evidence the guardrails hold against real model output rather than against
`FakeLLM` strings I wrote myself. Costs roughly a cent.

**This task spends money and needs a real key.** It is the one task here that
cannot run offline.

- [ ] **Step 1: Run it**

```bash
uv run assay eval --live 2>&1 | tee /tmp/assay-live.txt
```

Expected: the eight offline verdicts, then a `--- live:` block with one line
per case showing `allowed` or `refused (<kind>)` and the real SQL.

- [ ] **Step 2: Read the output before writing anything down**

Every adversarial case must show `refused`. If any shows `allowed`, that is a
real guardrail hole — stop, report it, and do not paper over it in the README.
If a *control* case shows `refused`, that is a false positive and equally worth
reporting.

- [ ] **Step 3: Record the verbatim result in the README**

Append beneath `### The suite is proven able to fail`, using the actual output
from Step 1 — never a remembered or plausible-looking one:

```markdown
### And proven against the real model, not just against fakes

The offline suite drives `FakeLLM` with SQL strings I wrote, which proves the
validator works but not that it works on what a real model emits. `assay eval
--live` sends the adversarial *questions* to the API and checks the verdict on
whatever comes back:

<paste the verbatim --live block here>
```

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: record what the real model emits for the adversarial questions

The offline suite proves the validator against strings I wrote. This is the
only evidence it holds against real model output."
```

---

## Close-out gates — controller work, not implementer tasks

Not tasks: no implementer is dispatched for these, and none of them produce a
diff. Listed here so the branch is not declared done with one still open.

- [ ] **Scoped re-review of `4900c78`.** The final review's fix wave is a 27 KB
  single-commit diff that touched `sql_guard.py` — a graded file — and both
  entry points, and nothing but its own author's report attests to it. Package:
  `.superpowers/sdd/2026-08-15-assay-implementation/review-6d138fc..4900c78.diff`
  (already generated). One re-review, per skill: there is no second fix wave.
- [ ] **Adjudicate residuals.** Anything the re-review opens gets a written
  ruling — fixed, or parked with what it costs if wrong.
- [ ] **Delete the SDD workspace** `.superpowers/sdd/2026-08-15-assay-implementation/`
  once the re-review is clean. It is git-ignored scratch; the ledger's value
  ends when the branch merges.
- [ ] **`superpowers:finishing-a-development-branch`** — merge `feat/assay` to
  `main` with `--no-ff`. This is a shared-branch write: confirm before merging.
- [ ] **Hand the user the rulings list.** Every `Ruling:` line from the ledger,
  in order, with what each costs if wrong. These were decisions made on the
  user's behalf while they were not in the loop, and this is the only place they
  surface. Mandatory — the branch is not done until this is delivered.

---

## Self-review

**Spec coverage:** The spec's three deliverables are complete and verified at
HEAD; this plan adds no spec surface. The spec's "make eval-live sends the
adversarial questions to the real model" line (`spec:135`) is the one clause
never actually exercised — Task 2 closes it.

**Placeholder scan:** One deliberate placeholder — `<paste the verbatim --live
block here>` in Task 2, Step 3. It cannot be pre-filled: writing plausible
model output into a plan and having someone paste it in as evidence is exactly
the failure the task exists to prevent.

**Type consistency:** No new types, signatures, or names. Task 1 reuses
`LLMError`, `WarehouseError`, `_RaisingLLM`, and `_warehouse_db`, all of which
exist today at the cited lines.
