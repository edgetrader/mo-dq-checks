# CLAUDE.md

Guidance for Claude Code when working in this repository. See `README.md`
for user-facing usage; this file is for things that aren't obvious from
reading the code.

## Branches

- **`development`** (this branch) is the full project: source, config, the
  generation scripts, the reference spreadsheet and the test suite. Do all
  work here.
- **`main`** is production: nine files, being `src/`, `app/`, `config/`,
  `README.md`, `requirements.txt` and `.gitignore` -- everything needed to
  run a check and nothing else. Its `.gitignore` also excludes `ref/`,
  `scripts/`, `tests/`, `CLAUDE.md`, `pytest.ini` and `requirements-dev.txt`,
  all of which live here instead.

**Never merge `development` into `main`.** A merge brings every file with
it, which would put the tests and scripts back on the production branch and
defeat the split. Promote by copying just the production paths instead:

```bash
git checkout main
git checkout development -- src app config README.md requirements.txt
git commit -m "Promote <what> from development"
```

Note what the list leaves out: `.gitignore` differs per branch by design,
and `CLAUDE.md` is development-only -- copying either would undo the split.

Going the other way (`main` -> `development`) is a normal merge and is
fine, though in practice main shouldn't gain commits of its own.

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

## Reading is separate from checking

`src/checks.py` has two halves, and the split is load-bearing:

- **`read_table(table_cfg, yyyymm, data_root) -> LoadedTable`** is the only
  code that touches the filesystem. It performs the four structural checks
  (`file_exists`, `sheet_exists`, `required_columns`, `row_count`), applies
  `colname_map`, and returns the normalised frame alongside the facts it
  learned about the source (`path`, `sheet_name`, `raw_columns`,
  `row_count`). `LoadedTable.ok` is False when the frame is unusable.
- **`run_checks_for_table(table_cfg, yyyymm=None, data_root=None, df=None)`**
  calls `read_table` only when `df` is not supplied. Pass a frame and it
  checks that instead — the point being that data which never came from a
  local folder (Databricks, a differently-mounted share, an in-memory build)
  gets the same checks.

A supplied frame must already use the standard column names; `colname_map`
is the reader's responsibility. Such a frame still gets `required_columns`
and `row_count` — without that guard, a frame missing a column would raise
`KeyError` out of the data checks rather than reporting a FAIL.

Keep `_run_frame_checks` free of any I/O or path knowledge. If a new check
needs something about the file, put it on `LoadedTable` rather than reaching
for the filesystem from a check.

## Three result statuses

Checks emit `PASS`, `FAIL` or `WARN`. A warning is a real finding that
shouldn't fail the job: it is counted separately, coloured amber, listed on
the Issues sheet, and **never** affects the exit code -- only `FAIL` raises
`DQCheckFailure`. Two checks are warning-level: `source_timeliness` and
`kpi_completeness`. Both describe data that is usable but wants a human
look -- stale vintage, or a mandate with no value this month -- rather
than a file that's broken.

If you add another, remember the fixture audit compares *non-passing*
checks, not just failures, so a warning-producing defect works there
unchanged.

## Known behaviors (not bugs)

- **Early-return checks**: `file_exists`, `sheet_exists`, `required_columns`,
  and `row_count` (zero rows) short-circuit the rest of that table's checks —
  there's no dataframe to check anything else against. All other checks run
  independently, so a single file commonly fails more than one check at once.
- **`analytics_completeness` and `analytics_membership` both always
  report.** Membership excludes nulls (`dropna()`) so one empty cell isn't
  counted twice, as both "missing" and "not a recognised value" -- but a
  table with a null *and* an unknown value legitimately fails both. An
  earlier version emitted one name or the other from a single function,
  which left `analytics_completeness` reading n/a on every healthy table.
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
- **`source_timeliness` accepts `YYYYMM` and `YYYY-MM` only.** The user
  confirmed the two forms mean the same month; both normalise to `YYYYMM`
  for comparison and reporting. Other separators (`/`, `_`) and embedded
  full dates (`2026-04-15`) are deliberately *not* recognised -- an
  earlier attempt added them and was reverted as out of scope. Don't
  widen this without asking.
- **A `source` value with no month in it does not warn.** `source_timeliness`
  only warns when a month is present *and* differs. Sources that never carry
  a date (`RQA`, a system name) would otherwise warn on every run for ever;
  the count of undated rows is reported in the message instead.
- **The `source` column is not in any `colname_map`.** It survives
  `df.rename` untouched and is found case-insensitively by name, which is
  why the check is skipped rather than failed when a table doesn't have one.
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

That guard is now automated. `pytest` runs `tests/test_defect_plan.py`,
which generates the fixtures into a temp folder and asserts every seeded
defect fails *exactly* its intended checks while every other table stays
clean — which is precisely what the cascade bugs violated. Run it after any
change to the checks or the defect plan:

```bash
pytest              # five months, covers every check (~2s)
pytest -m slow      # all ten months
```

Don't verify check changes by eyeballing a console run; the audit compares
against the plan table-by-table and reports the exact mismatch.

## The Excel report

`src/report.py` owns the whole workbook; `runner.py` just calls
`write_excel_report(...)` and stays about orchestration. `organise()` turns
the flat result list into a `ReportData` the sheets read from — do
aggregation there rather than inside a sheet builder.

Two decisions worth not undoing by accident:

- **Matrix cells hold the strings `PASS`/`FAIL`, not symbols.** Colour does
  the visual work. Swapping in ✔/✖ would look tidier but breaks filtering
  and anything reading the sheet programmatically. Icons are used only in
  the verdict banner and the row-status column, where they can't be
  mistaken for data.
- **The by-check "clean rate" is measured over every table checked**, not
  over the tables a check emitted a result for. `analytics_completeness`
  only reports when it fails, so an "of those that ran" rate would show it
  as 0% clean whenever it appears at all — alarming and wrong.
  `test_clean_rate_is_measured_against_every_table` pins this.

`report.organise()` still recovers the file path by string-slicing the
`file_exists` message. `read_table` exposes it properly as
`LoadedTable.path`, but `run_checks_for_table` returns only results, so the
path doesn't reach the reporting layer yet. Fixing it properly means
letting the runner carry `LoadedTable` alongside the results.

## Check names live in one place

`CHECK_NAMES` in `src/checks.py` is the single source of truth for both the
set of checks and their left-to-right order in the Excel report. It sits
next to the functions that emit those names, and `runner.py` imports it —
an earlier version kept a second copy in the report module, which was a
standing drift hazard (add a check, forget the list, lose the column).

`test_check_names_matches_what_the_checker_emits` pins the two together, so
the drift now fails a test instead of silently shipping.

Note that `yyyymm_format` is deliberately *not* in `CHECK_NAMES`: it only
fires on a malformed `--yyyymm`, where the run is aborted and the report is
meaningless anyway. The console output and the non-zero exit code carry that
case.

## Running many months from the shell

Looping `run_dq_check.py` over all 10 months in a single shell command can
exceed a 2-minute default tool timeout — pass a longer explicit timeout or
run months in smaller batches. `pytest` covers the same ground far faster.

## Generated / gitignored directories

`data/`, `test_data/`, `output/`, `.venv/` are all generated at runtime and
gitignored. Never assume they exist — regenerate with the relevant
`scripts/generate_*.py` before relying on their contents.
