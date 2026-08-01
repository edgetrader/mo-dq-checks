# CLAUDE.md

Guidance for Claude Code when working in this repository. See `README.md`
for user-facing usage; this file is for things that aren't obvious from
reading the code.

## Environment

The system Python/Anaconda on this machine has a broken numpy/pandas/pyarrow
ABI (`numpy.dtype size changed` / `_ARRAY_API not found` errors). Always use
the project's own virtualenv, not the system interpreter:

```bash
source .venv/bin/activate
```

If `.venv` doesn't exist yet: `python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`.

## Code layout / import convention

`src/` holds flat modules (`checks.py`, `config_loader.py`, `runner.py`) —
deliberately **not** a package (no `dq_checks/` subfolder, no `__init__.py`).
Anything that imports them adds `src/` itself to `sys.path` and imports the
modules directly (`from runner import run_dq_checks`, not
`from dq_checks.runner import ...`). See the top of `app/run_dq_check.py`
and `scripts/generate_test_data.py` for the pattern. Keep this flat — it was
an explicit user preference, not an oversight.

`scripts/` are plain scripts, runnable directly (`python scripts/foo.py`);
Python auto-adds a script's own directory to `sys.path`, so sibling modules
like `sample_data_builder.py` and `defect_injector.py` import without extra
path setup.

## Config (`config/tables_config.json`)

Generated from `ref/reference.xlsx` by `scripts/build_config_from_reference.py`.
Re-run that script after editing `reference.xlsx` — don't hand-edit the JSON
for structural changes (though editing config directly, e.g. adjusting
`expected_analytics`, is fine for quick iteration).

`source_folder` values are **literal relative paths** (e.g. `public/YYYYMM/rqa`),
joined directly with `--data-root`/`data_root` at run time. This was
deliberately simplified from the original `reference.xlsx` values (which had
a confusing hardcoded `../data/...` prefix baked in) — don't reintroduce that
indirection. If you regenerate the config from a changed `reference.xlsx`,
`build_config_from_reference.py`'s `clean_source_folder()` strips that prefix
automatically.

The primary key for each table is **derived**, not read from
`reference.xlsx`'s (unreliable/inconsistently-cased) `primary_key` column —
it's every value in `colname_map` except `val_amt`. See
`config_loader.primary_key_columns()`.

## Known behaviors (not bugs)

- **Early-return checks**: `file_exists`, `sheet_exists`, `required_columns`,
  and `row_count` (zero rows) short-circuit the rest of that table's checks —
  there's no dataframe to check anything else against. All other checks run
  independently, so a single file commonly fails more than one check at once.
- **`val_amt` nulls are not flagged.** `val_amt_dtype` only validates the
  *type* of non-null values; a null `val_amt` currently passes. This was a
  deliberate scope decision (never requested), not an oversight — confirm
  with the user before changing it.
- **A row where every cell is blank is silently dropped** by
  pandas/openpyxl on the Excel write→read round-trip — it never reaches the
  checker at all. Verified empirically while building test fixtures (see
  `defect_injector.multi_field_corruption`'s docstring). If you need a test
  fixture with a "corrupted" row, leave at least one cell non-blank or it
  won't survive being written to `.xlsx`.
- **`sheet_exists`'s except clause is intentionally broad** (`except
  Exception`, not `except ValueError`). A truncated/corrupted `.xlsx` can
  raise `zipfile.BadZipFile` or other exception types, not just `ValueError`
  — this was a real crash found during validation (one bad file used to take
  down the whole batch run). Don't narrow this back to `ValueError` only.

## Test data generation — cascade risk when adding new defects

`scripts/defect_injector.py` + `scripts/generate_test_data.py`'s `DEFECT_PLAN`
seed specific (table, month) pairs with specific defects. When adding a new
seeded defect, watch for **unintended cascades into `kpi_completeness`**:
corrupting a row's `mandate_id`, `report_date`, or `analytics` value can
shift which group that row belongs to, which can make a *different* group
look incomplete — a real bug class found twice during development (see git
history / prior session: `COMMITMENT` and `PERF_CHAINLINK` both hit this).
Rules of thumb:
- `null_mandate_id` and `invalid_date` fragment the completeness-grouping
  key itself — only assign these to tables where `kpi_analytics` is `None`.
- `null_analytics` / `unexpected_analytics` are safe as long as the row you
  mutate doesn't hold a value that's also in `kpi_analytics` — use
  `defect_injector._safe_row_index()` rather than hardcoding row 0.
- `non_numeric_val`, `duplicate_key`, `missing_column`, `zero_rows`,
  `wrong_sheet`, `missing_file` are unconditionally safe regardless of KPI
  structure.

After changing the defect plan, always regenerate and re-audit rather than
assuming it's correct:

```bash
python scripts/generate_test_data.py
python app/run_dq_check.py --yyyymm <month> --data-root test_data
```

An independent audit script (compares actual failing checks per table
against the `DEFECT_PLAN`'s expected set) was used during development to
catch the cascade bugs above — it's not checked into the repo (built
ad hoc in the session scratchpad), but worth rebuilding if you touch the
defect plan again: for every `(table, yyyymm)` in `DEFECT_PLAN`, assert the
actual `FAIL`ed check names equal exactly the expected set; for every table
*not* in the plan, assert zero failures.

## Running the full multi-month test suite

Looping `run_dq_check.py` over all 10 months in a single shell command can
exceed a 2-minute default tool timeout — pass a longer explicit timeout or
run months individually/in smaller batches.

## Generated / gitignored directories

`data/`, `test_data/`, `output/`, `.venv/` are all generated at runtime and
gitignored. Never assume they exist — regenerate with the relevant
`scripts/generate_*.py` before relying on their contents.
