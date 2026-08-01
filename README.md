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
pip install -r requirements.txt
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

## Output

Every run writes `output/dq_check_report_<YYYYMM>_<timestamp>.xlsx`:

- **`summary`** sheet — `yyyymm`, `data_root`, run time, tables checked, pass/fail counts.
- **`results`** sheet — one row per table, one column per check (`PASS`/`FAIL`,
  blank if not applicable), a `file_path` column, and a trailing `comments`
  column combining every check's message for that table.

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
