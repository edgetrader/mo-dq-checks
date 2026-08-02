from __future__ import annotations

import re
import warnings
from dataclasses import dataclass

import pandas as pd

from config_loader import primary_key_columns, resolve_path

YYYYMM_PATTERN = re.compile(r"^\d{6}$")

# Every check this module emits, in the order it should appear in the report.
#
# This lives here, next to the functions that emit the names, rather than in
# the report module -- one list, one place. `test_dq_checks.py` asserts it
# stays in step with what the checker actually produces, so adding a check
# without listing it here fails the tests instead of silently vanishing from
# the Excel report.
#
# `yyyymm_format` is deliberately excluded: it only fires when the operator
# passes a malformed --yyyymm, in which case the run is aborted and the
# report is meaningless anyway (the console output and exit code carry it).
CHECK_NAMES = (
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
)


@dataclass
class CheckResult:
    table_name: str
    check_name: str
    status: str  # "PASS" or "FAIL"
    message: str = ""


def _pass(table_name, check_name, message=""):
    return CheckResult(table_name, check_name, "PASS", message)


def _fail(table_name, check_name, message):
    return CheckResult(table_name, check_name, "FAIL", message)


def run_checks_for_table(table_cfg: dict, yyyymm: str, data_root: str) -> list[CheckResult]:
    table_name = table_cfg["table_name"]
    results: list[CheckResult] = []

    if not YYYYMM_PATTERN.match(yyyymm):
        results.append(_fail(table_name, "yyyymm_format", f"yyyymm '{yyyymm}' is not in YYYYMM format"))
        return results

    file_path = resolve_path(table_cfg, yyyymm, data_root)

    if not file_path.exists():
        results.append(_fail(table_name, "file_exists", f"File not found: {file_path}"))
        return results
    results.append(_pass(table_name, "file_exists", str(file_path)))

    sheet_name = table_cfg["sheet_name"] if table_cfg["sheet_name"] else 0
    sheet_label = table_cfg["sheet_name"] or "default/first sheet"
    try:
        df = pd.read_excel(file_path, sheet_name=sheet_name, header=0)
    except Exception as e:
        # Broad on purpose: a truncated/corrupted .xlsx can raise things other
        # than ValueError (e.g. zipfile.BadZipFile), and one bad file must not
        # crash the whole batch run -- it should just fail this one table.
        results.append(_fail(table_name, "sheet_exists", f"Could not read '{sheet_label}': {e}"))
        return results
    results.append(_pass(table_name, "sheet_exists"))

    colname_map = table_cfg["colname_map"]
    expected_raw_cols = set(colname_map.keys())
    actual_cols = set(df.columns)
    missing_cols = expected_raw_cols - actual_cols
    if missing_cols:
        results.append(
            _fail(table_name, "required_columns", f"Missing expected column(s): {sorted(missing_cols)}")
        )
        return results
    results.append(_pass(table_name, "required_columns"))

    df = df.rename(columns=colname_map)

    if len(df) == 0:
        results.append(_fail(table_name, "row_count", "File has header but no data rows"))
        return results
    results.append(_pass(table_name, "row_count", f"{len(df)} rows"))

    results.append(_check_report_date(df, table_name))

    if "mandate_id" in colname_map.values():
        results.append(_check_mandate_id(df, table_name))

    results.append(_check_analytics(df, table_name, table_cfg["expected_analytics"]))

    results.append(_check_val_amt(df, table_name, table_cfg["val_amt_type"]))

    pk_cols = primary_key_columns(colname_map)
    results.append(_check_primary_key_uniqueness(df, table_name, pk_cols))

    if table_cfg["kpi_analytics"]:
        results.append(_check_kpi_completeness(df, table_name, pk_cols, table_cfg["kpi_analytics"]))

    return results


def _check_report_date(df: pd.DataFrame, table_name: str) -> CheckResult:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        parsed = pd.to_datetime(df["report_date"], errors="coerce")
    invalid_count = parsed.isna().sum()
    if invalid_count > 0:
        return _fail(table_name, "report_date_dtype", f"{invalid_count} value(s) not parseable as a date")
    return _pass(table_name, "report_date_dtype")


def _check_mandate_id(df: pd.DataFrame, table_name: str) -> CheckResult:
    null_count = df["mandate_id"].isna().sum()
    if null_count > 0:
        return _fail(table_name, "mandate_id_completeness", f"{null_count} null mandate_id value(s)")
    return _pass(table_name, "mandate_id_completeness")


def _check_analytics(df: pd.DataFrame, table_name: str, expected_analytics: list[str]) -> CheckResult:
    null_count = df["analytics"].isna().sum()
    if null_count > 0:
        return _fail(table_name, "analytics_completeness", f"{null_count} null analytics value(s)")

    if expected_analytics:
        actual_values = set(df["analytics"].unique())
        unexpected = actual_values - set(expected_analytics)
        if unexpected:
            return _fail(table_name, "analytics_membership", f"Unexpected analytics value(s): {sorted(unexpected)}")

    return _pass(table_name, "analytics_membership")


def _check_val_amt(df: pd.DataFrame, table_name: str, val_amt_type: str) -> CheckResult:
    if val_amt_type != "Numeric":
        return _pass(table_name, "val_amt_dtype", f"val_amt_type={val_amt_type}, numeric check skipped")

    coerced = pd.to_numeric(df["val_amt"], errors="coerce")
    invalid_mask = df["val_amt"].notna() & coerced.isna()
    invalid_count = invalid_mask.sum()
    if invalid_count > 0:
        return _fail(table_name, "val_amt_dtype", f"{invalid_count} non-numeric val_amt value(s)")
    return _pass(table_name, "val_amt_dtype")


def _check_primary_key_uniqueness(df: pd.DataFrame, table_name: str, pk_cols: list[str]) -> CheckResult:
    dup_count = df.duplicated(subset=pk_cols, keep=False).sum()
    if dup_count > 0:
        return _fail(
            table_name,
            "primary_key_uniqueness",
            f"{dup_count} row(s) share a duplicate key on {pk_cols}",
        )
    return _pass(table_name, "primary_key_uniqueness", f"unique on {pk_cols}")


def _check_kpi_completeness(
    df: pd.DataFrame, table_name: str, pk_cols: list[str], kpi_analytics: list[str]
) -> CheckResult:
    group_cols = [c for c in pk_cols if c != "analytics"]
    if not group_cols:
        actual = set(df["analytics"].unique())
        missing = set(kpi_analytics) - actual
        if missing:
            return _fail(table_name, "kpi_completeness", f"Missing required KPI analytics: {sorted(missing)}")
        return _pass(table_name, "kpi_completeness")

    missing_by_group = {}
    for key, group in df.groupby(group_cols, dropna=False):
        actual = set(group["analytics"].unique())
        missing = set(kpi_analytics) - actual
        if missing:
            missing_by_group[key] = sorted(missing)

    if missing_by_group:
        sample = dict(list(missing_by_group.items())[:5])
        more = f" (+{len(missing_by_group) - 5} more)" if len(missing_by_group) > 5 else ""
        return _fail(
            table_name,
            "kpi_completeness",
            f"{len(missing_by_group)} group(s) missing required KPI analytics, e.g. {sample}{more}",
        )
    return _pass(table_name, "kpi_completeness")
