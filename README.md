# mo-dq-checks

Data quality checks for a set of monthly Excel table exports (RQA/BS/PRA
reporting data). Designed to run as a scheduled job (cron, Databricks Job,
Airflow, etc.) — it exits non-zero the moment any check fails, and also
works as a plain Python import inside a Databricks notebook.

## What it checks

For every table listed in `config/tables_config.json`, for a given month
(`YYYYMM`):

| Check | What it verifies |
|---|---|
| `file_exists` | The expected file is present for that month |
| `sheet_exists` | The expected sheet can be read (also catches corrupted/unreadable files) |
| `required_columns` | All expected raw columns are present (extra columns are ignored) |
| `row_count` | The file has at least one data row |
| `report_date_dtype` | Every `report_date` value parses as a date |
| `mandate_id_completeness` | No nulls in `mandate_id` (only for tables that have it) |
| `analytics_completeness` | No nulls in `analytics` |
| `analytics_membership` | Every `analytics` value is in the table's expected set |
| `val_amt_dtype` | `val_amt` is numeric (skipped for tables where `val_amt_type` is `Text`) |
| `primary_key_uniqueness` | No duplicate rows on the table's key columns |
| `kpi_completeness` | Every required KPI analytic is present per report_date/mandate_id group (only for tables with `kpi_analytics` defined) |

A table's check chain stops early on `file_exists`, `sheet_exists`,
`required_columns`, or `row_count` failures (nothing downstream can be
checked if the file can't even be read), but otherwise every applicable
check runs independently — a file can fail multiple checks at once.

## Project layout

```
config/tables_config.json   Table definitions (see "Config" below)
src/                        Check engine: checks.py, config_loader.py, runner.py
app/run_dq_check.py         CLI entry point for scheduled jobs
scripts/
  build_config_from_reference.py   Regenerates config from ref/reference.xlsx
  generate_sample_data.py          One clean month of data -> data/
  generate_test_data.py            Multiple months incl. seeded defects -> test_data/
  sample_data_builder.py           Shared clean-row generator
  defect_injector.py               Deliberate defect mutators for test data
ref/reference.xlsx          Original source spreadsheet the config was built from
data/, test_data/, output/  Generated at runtime (gitignored)
```

## Setup

The environment's system Python/Anaconda may have a broken numpy/pandas/pyarrow
ABI. Use an isolated virtualenv:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt        # or requirements-dev.txt to run the tests
```

## Running checks

```bash
python app/run_dq_check.py --yyyymm 202607 --data-root /path/to/data
```

Arguments:

| Flag | Default | Purpose |
|---|---|---|
| `--yyyymm` | *(required)* | Target month, e.g. `202607` |
| `--data-root` | `.` | Base folder that `source_folder` paths in the config are relative to |
| `--config` | `config/tables_config.json` | Path to the table config |
| `--output-dir` | `output/` | Where the Excel report is written |
| `--tables` | *(all)* | Comma-separated `table_name` list to check a subset |

Exits `0` if everything passes, `1` if anything fails (with a
`DQ CHECK FAILED: ...` message on stderr for job schedulers to catch).

### From a notebook (e.g. Databricks)

```python
import sys
sys.path.insert(0, "/path/to/mo-dq-checks/src")
from runner import run_dq_checks, DQCheckFailure

run_dq_checks(yyyymm="202607", data_root="/dbfs/mnt/.../data")
```

Raises `DQCheckFailure` on any failed check (so the job/notebook run fails),
or pass `raise_on_failure=False` to just inspect the returned results.

### Checking a DataFrame you already have

Reading the data and checking it are separate, so a frame that didn't come
from a local Excel file — a Databricks/Spark table converted to pandas, a
share mounted somewhere else, something built in memory — can be checked
with the same logic:

```python
from checks import run_checks_for_table
from config_loader import load_table_configs

cfg = next(c for c in load_table_configs("config/tables_config.json")
           if c["table_name"] == "PERF_GROSS")

for result in run_checks_for_table(cfg, df=my_dataframe):
    print(result.status, result.check_name, result.message)
```

The frame must use the standard column names (`report_date`, `mandate_id`,
`analytics`, `val_amt`) — applying each table's `colname_map` is the reader's
job, not the checker's. `file_exists` and `sheet_exists` are skipped, since
there's no file; everything else runs exactly as it would on a read file.

To do the reading yourself and inspect what came back:

```python
from checks import read_table

loaded = read_table(cfg, "202607", data_root="test_data")
loaded.frame        # normalised DataFrame, or None if unreadable
loaded.results      # file_exists / sheet_exists / required_columns / row_count
loaded.path         # resolved file path
loaded.sheet_name   # sheet actually requested
loaded.raw_columns  # headers as found in the file
loaded.row_count
```

## Output

Every run writes `output/dq_check_report_<YYYYMM>_<timestamp>.xlsx`, with
three sheets in the order you'd read them:

**`Summary`** — a verdict banner (green *"ALL n CHECKS PASSED"* or red
*"n OF m CHECKS FAILED ACROSS k TABLES"*), the run details (month, data
root, run time), the headline counts, and a **by-check breakdown** showing
how many tables each check failed, with a clean-rate data bar. That last
table is what tells you whether one file is broken or something systemic
has gone wrong across the whole month.

**`Results`** — the full matrix: one row per table, one column per check.
Green `PASS`, red `FAIL`, grey `–` where a check never ran (either it
doesn't apply to that table, or an earlier structural failure stopped the
chain). A ✔/✖ status column flags failing rows at a glance. Check headers
are rotated vertically so the grid stays narrow, the header row and table
names are frozen, and an autofilter is applied.

**`Failures`** — just the problems, as a flat `Table | Check | What went
wrong` list. This is the sheet to work from; on a clean run it says so.

Cells hold the plain text `PASS`/`FAIL`, not symbols, so the sheet stays
filterable and readable by other tools — the colour does the visual work.
Sheet tabs are colour-coded green or red for the overall outcome.

## Config (`config/tables_config.json`)

Generated from `ref/reference.xlsx` via:

```bash
python scripts/build_config_from_reference.py
```

Re-run this whenever `reference.xlsx` changes. Each table entry:

```json
{
  "table_name": "KPI_STRESS_SALES",
  "source_folder": "public/YYYYMM/rqa",
  "file_name": "YYYYMM_KPI_STRESS_SALES.xlsx",
  "sheet_name": "template",
  "colname_map": {"REPORT DATE": "report_date", "MANDATE_ID": "mandate_id", "ANALYTICS": "analytics", "VALUES": "val_amt"},
  "val_amt_type": "Numeric",
  "expected_analytics": ["port_stress_sales_ytd", "port_stress_sales_3y"],
  "kpi_analytics": ["port_stress_sales_ytd", "port_stress_sales_3y"]
}
```

`source_folder` and `file_name` are literal, relative path templates —
`YYYYMM` gets substituted with the target month at run time, and the result
is joined with `--data-root` (if not already absolute). `colname_map` maps
each table's actual raw Excel header to the standard field names the
checker uses; the primary key is derived automatically as every mapped
column except `val_amt`.

## Generating data for testing

```bash
python scripts/generate_sample_data.py 202607        # one clean month -> data/
python scripts/generate_test_data.py                 # 10 months incl. seeded defects -> test_data/
python scripts/generate_test_data.py --months 202601-202612
```

`generate_test_data.py`'s `DEFECT_PLAN` seeds specific (table, month) pairs
with a specific defect (wrong sheet, missing column, invalid date, null
key, duplicate key, etc.) so the generated set exercises every FAIL branch
at least once, while most files stay clean. See the comments at the top of
that file for the full plan and the reasoning behind which tables are safe
to use for which defect (some combinations can cascade into an unintended
second failure — e.g. corrupting `mandate_id` on a table with KPI
requirements will also break `kpi_completeness`; see `defect_injector.py`).

Try it:

```bash
python app/run_dq_check.py --yyyymm 202608 --data-root test_data
```

## Tests

```bash
pip install -r requirements-dev.txt
pytest              # ~2s: audits five months, covering every check
pytest -m slow      # ~3s: the same assertions across all ten months
```

`tests/test_defect_plan.py` generates the defect-seeded fixtures into a temp
folder, runs the real checker over them, and asserts that **each seeded file
fails exactly the checks its defect targets** while **every other file stays
clean**. Both halves matter: the first catches a check that stops detecting
its defect, the second catches one that starts firing on good data.

It also pins `CHECK_NAMES` (in `src/checks.py`) to what the checker actually
emits, so adding a check without listing it fails the tests rather than
silently dropping a column from the Excel report.
