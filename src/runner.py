from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd

from checks import CheckResult, run_checks_for_table
from config_loader import load_table_configs

DEFAULT_CONFIG_PATH = "config/tables_config.json"
DEFAULT_OUTPUT_DIR = "output"


class DQCheckFailure(Exception):
    """Raised when one or more data quality checks fail."""


def _print_report(yyyymm: str, results: list[CheckResult]) -> None:
    width = 64
    print("=" * width)
    print(f"DQ CHECK RUN — yyyymm={yyyymm} — {datetime.now():%Y-%m-%d %H:%M:%S}")
    print("=" * width)

    for r in results:
        line = f"[{r.status}] {r.table_name:<28} | {r.check_name}"
        if r.message:
            line += f" : {r.message}"
        print(line)

    passed = sum(1 for r in results if r.status == "PASS")
    failed = sum(1 for r in results if r.status == "FAIL")
    tables_checked = len({r.table_name for r in results})

    print("-" * width)
    print(f"SUMMARY: {tables_checked} table(s) | {len(results)} check(s) | {passed} passed | {failed} failed")

    if failed:
        print("FAILED CHECKS:")
        for r in results:
            if r.status == "FAIL":
                print(f"  - {r.table_name}.{r.check_name}: {r.message}")

    print("=" * width)


# Preferred left-to-right column order in the report; any check not listed
# here (e.g. a new check added later) is appended alphabetically.
CHECK_COLUMN_ORDER = [
    "file_exists",
    "sheet_exists",
    "required_columns",
    "row_count",
    "report_date_dtype",
    "mandate_id_completeness",
    "analytics_completeness",
    "analytics_membership",
    "val_amt_dtype",
    "primary_key_uniqueness",
    "kpi_completeness",
]


def _write_excel_report(
    yyyymm: str, data_root: str, results: list[CheckResult], output_dir: str, run_time: datetime
) -> Path:
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / f"dq_check_report_{yyyymm}_{run_time:%Y%m%d_%H%M%S}.xlsx"

    details = pd.DataFrame(
        {
            "table_name": [r.table_name for r in results],
            "check_name": [r.check_name for r in results],
            "status": [r.status for r in results],
            "message": [r.message for r in results],
        }
    )

    passed = int((details["status"] == "PASS").sum())
    failed = int((details["status"] == "FAIL").sum())
    tables_with_failures = details.loc[details["status"] == "FAIL", "table_name"].nunique()
    summary = pd.DataFrame(
        {
            "metric": [
                "yyyymm",
                "data_root",
                "run_time",
                "tables_checked",
                "total_checks",
                "passed",
                "failed",
                "tables_with_failures",
            ],
            "value": [
                yyyymm,
                str(Path(data_root).resolve()),
                run_time.strftime("%Y-%m-%d %H:%M:%S"),
                details["table_name"].nunique(),
                len(details),
                passed,
                failed,
                int(tables_with_failures),
            ],
        }
    )

    results_table = _build_results_table(details)

    with pd.ExcelWriter(report_path, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="summary", index=False)
        results_table.to_excel(writer, sheet_name="results", index=False)
        _format_results_sheet(writer.sheets["results"], results_table)

    return report_path


def _build_results_table(details: pd.DataFrame) -> pd.DataFrame:
    present_checks = list(details["check_name"].unique())
    ordered_checks = [c for c in CHECK_COLUMN_ORDER if c in present_checks]
    ordered_checks += sorted(c for c in present_checks if c not in ordered_checks)

    status_pivot = details.pivot(index="table_name", columns="check_name", values="status")
    status_pivot = status_pivot.reindex(columns=ordered_checks)

    file_paths = {}
    for _, row in details[details["check_name"] == "file_exists"].iterrows():
        prefix = "File not found: "
        msg = row["message"]
        file_paths[row["table_name"]] = msg[len(prefix):] if msg.startswith(prefix) else msg

    comments = {}
    for table_name, group in details.groupby("table_name"):
        by_check = dict(zip(group["check_name"], group["message"]))
        parts = [f"{c}: {by_check[c]}" for c in ordered_checks if by_check.get(c)]
        comments[table_name] = "\n".join(parts)

    results_table = status_pivot.reset_index()
    results_table.insert(1, "file_path", results_table["table_name"].map(file_paths).fillna(""))
    results_table["comments"] = results_table["table_name"].map(comments).fillna("")
    return results_table.fillna("")


def _format_results_sheet(ws, results_table: pd.DataFrame) -> None:
    from openpyxl.styles import Alignment

    ws.freeze_panes = "A2"
    comments_col_idx = results_table.columns.get_loc("comments") + 1

    for row in ws.iter_rows(min_row=2, min_col=comments_col_idx, max_col=comments_col_idx):
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")

    for idx, col in enumerate(results_table.columns, start=1):
        col_letter = ws.cell(row=1, column=idx).column_letter
        if col == "comments":
            ws.column_dimensions[col_letter].width = 60
        elif col == "file_path":
            ws.column_dimensions[col_letter].width = 55
        else:
            max_len = max([len(str(col))] + [len(str(v)) for v in results_table[col]])
            ws.column_dimensions[col_letter].width = min(max(max_len + 2, 10), 30)


def run_dq_checks(
    yyyymm: str,
    data_root: str = ".",
    config_path: str = DEFAULT_CONFIG_PATH,
    table_names: list[str] | None = None,
    output_dir: str = DEFAULT_OUTPUT_DIR,
    raise_on_failure: bool = True,
) -> list[CheckResult]:
    """
    Run data quality checks for the given YYYYMM across all tables in config
    (or a subset via table_names).

    Writes an Excel report to output_dir, named
    dq_check_report_<YYYYMMDD_HHMMSS>.xlsx using the run's timestamp: a
    "summary" sheet with run-level counts, and a "results" sheet with one
    row per table/file checked, one column per check, PASS/FAIL in each
    cell, and a trailing "comments" column with every check's message.

    Raises DQCheckFailure if any check fails and raise_on_failure=True (default),
    so this fails a Databricks job/notebook on bad data. Set raise_on_failure=False
    to inspect results programmatically instead.
    """
    run_time = datetime.now()
    table_cfgs = load_table_configs(config_path, table_names)

    results: list[CheckResult] = []
    for table_cfg in table_cfgs:
        results.extend(run_checks_for_table(table_cfg, yyyymm, data_root))

    _print_report(yyyymm, results)

    report_path = _write_excel_report(yyyymm, data_root, results, output_dir, run_time)
    print(f"Report written to: {report_path}")

    failed = [r for r in results if r.status == "FAIL"]
    if failed and raise_on_failure:
        raise DQCheckFailure(f"{len(failed)} data quality check(s) failed for yyyymm={yyyymm}")

    return results
