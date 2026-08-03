"""
Orchestration: run every configured table for one month, report the
results, and decide whether the job passed.

Deliberately thin -- the checks live in checks.py and the workbook in
report.py, so this stays about sequencing and the pass/fail decision.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from checks import CheckResult, run_checks_for_table
from config_loader import load_table_configs
from report import write_excel_report

DEFAULT_CONFIG_PATH = "config/tables_config.json"
DEFAULT_OUTPUT_DIR = "output"


class DQCheckFailure(Exception):
    """Raised when one or more data quality checks fail."""


def _print_report(yyyymm: str, results: list[CheckResult]) -> None:
    """
    Print the run to stdout, which is what a scheduler captures in its log.

    Every result is listed, then a summary, then the failures and warnings
    repeated on their own so they can be read without scrolling back
    through several hundred passing lines.
    """
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
    warned = sum(1 for r in results if r.status == "WARN")
    tables_checked = len({r.table_name for r in results})

    print("-" * width)
    print(
        f"SUMMARY: {tables_checked} table(s) | {len(results)} check(s) | "
        f"{passed} passed | {failed} failed | {warned} warned"
    )

    if failed:
        print("FAILED CHECKS:")
        for r in results:
            if r.status == "FAIL":
                print(f"  - {r.table_name}.{r.check_name}: {r.message}")

    # Listed separately so they're visible without being mistaken for
    # failures -- warnings never affect the exit code.
    if warned:
        print("WARNINGS (do not fail the run):")
        for r in results:
            if r.status == "WARN":
                print(f"  - {r.table_name}.{r.check_name}: {r.message}")

    print("=" * width)


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

    Writes a timestamped Excel report to output_dir with three sheets:
    Summary (verdict, run details, per-check breakdown), Results (the
    table x check matrix), and Issues (a flat list of failures and
    warnings).

    Raises DQCheckFailure if any check FAILS and raise_on_failure=True
    (the default), so bad data fails a Databricks job or notebook cell.
    Warnings never raise. Set raise_on_failure=False to inspect the results
    programmatically instead.
    """
    # Captured once, so the report's filename and its "run at" agree even
    # if the run takes a while.
    run_time = datetime.now()
    table_cfgs = load_table_configs(config_path, table_names)

    # Every table is checked regardless of what the others did: one bad
    # file must not hide the state of the other twenty-one.
    results: list[CheckResult] = []
    for table_cfg in table_cfgs:
        results.extend(run_checks_for_table(table_cfg, yyyymm, data_root))

    _print_report(yyyymm, results)

    # Written before the raise below, or a failing run -- the one you most
    # want to look at -- would produce no report at all.
    report_path = write_excel_report(yyyymm, data_root, results, output_dir, run_time)
    print(f"Report written to: {report_path}")

    failed = [r for r in results if r.status == "FAIL"]
    if failed and raise_on_failure:
        raise DQCheckFailure(f"{len(failed)} data quality check(s) failed for yyyymm={yyyymm}")

    return results
