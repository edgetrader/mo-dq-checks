from __future__ import annotations

import re
import warnings
from dataclasses import dataclass, field
from pathlib import Path

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
    "source_timeliness",
)

# Checks report PASS, FAIL, or WARN. A warning is a finding worth surfacing
# that shouldn't fail the job -- it never affects the exit code.
PASS, FAIL, WARN = "PASS", "FAIL", "WARN"


@dataclass
class CheckResult:
    table_name: str
    check_name: str
    status: str  # PASS, FAIL or WARN
    message: str = ""


def _pass(table_name, check_name, message=""):
    return CheckResult(table_name, check_name, "PASS", message)


def _fail(table_name, check_name, message):
    return CheckResult(table_name, check_name, "FAIL", message)


def _warn(table_name, check_name, message):
    return CheckResult(table_name, check_name, WARN, message)


@dataclass
class LoadedTable:
    """
    What `read_table` found: the frame, the checks performed while reading
    it, and the facts about the source discovered along the way.

    `frame` is None when the file couldn't be read far enough to check
    anything else -- `results` then explains why.
    """

    results: list[CheckResult] = field(default_factory=list)
    frame: pd.DataFrame | None = None
    # --- attributes of the source, as actually read ---
    path: Path | None = None  # resolved file path (None if yyyymm was junk)
    sheet_name: str | int | None = None  # the sheet pandas was asked for
    raw_columns: list[str] = field(default_factory=list)  # headers as found
    row_count: int = 0

    @property
    def ok(self) -> bool:
        return self.frame is not None


def read_table(table_cfg: dict, yyyymm: str, data_root: str) -> LoadedTable:
    """
    Locate and read one table's file, and report the structural checks.

    This is the only part of the checking that touches the filesystem, so a
    caller reading from somewhere else (a Databricks table, a network share
    mounted differently, an in-memory frame) can skip it entirely and hand
    the frame straight to `run_checks_for_table` instead.

    The returned frame has been renamed to the standard column names
    (report_date / mandate_id / analytics / val_amt).
    """
    table_name = table_cfg["table_name"]
    loaded = LoadedTable()

    if not YYYYMM_PATTERN.match(yyyymm):
        loaded.results.append(
            _fail(table_name, "yyyymm_format", f"yyyymm '{yyyymm}' is not in YYYYMM format")
        )
        return loaded

    loaded.path = resolve_path(table_cfg, yyyymm, data_root)

    if not loaded.path.exists():
        loaded.results.append(_fail(table_name, "file_exists", f"File not found: {loaded.path}"))
        return loaded
    loaded.results.append(_pass(table_name, "file_exists", str(loaded.path)))

    loaded.sheet_name = table_cfg["sheet_name"] if table_cfg["sheet_name"] else 0
    sheet_label = table_cfg["sheet_name"] or "default/first sheet"
    try:
        df = pd.read_excel(loaded.path, sheet_name=loaded.sheet_name, header=0)
    except Exception as e:
        # Broad on purpose: a truncated/corrupted .xlsx can raise things other
        # than ValueError (e.g. zipfile.BadZipFile), and one bad file must not
        # crash the whole batch run -- it should just fail this one table.
        loaded.results.append(
            _fail(table_name, "sheet_exists", f"Could not read '{sheet_label}': {e}")
        )
        return loaded
    loaded.results.append(_pass(table_name, "sheet_exists"))
    loaded.raw_columns = list(df.columns)

    colname_map = table_cfg["colname_map"]
    missing_cols = set(colname_map.keys()) - set(df.columns)
    if missing_cols:
        loaded.results.append(
            _fail(table_name, "required_columns", f"Missing expected column(s): {sorted(missing_cols)}")
        )
        return loaded
    loaded.results.append(_pass(table_name, "required_columns"))

    df = df.rename(columns=colname_map)
    loaded.row_count = len(df)

    if len(df) == 0:
        loaded.results.append(_fail(table_name, "row_count", "File has header but no data rows"))
        return loaded
    loaded.results.append(_pass(table_name, "row_count", f"{len(df)} rows"))

    loaded.frame = df
    return loaded


def run_checks_for_table(
    table_cfg: dict,
    yyyymm: str | None = None,
    data_root: str | None = None,
    df: pd.DataFrame | None = None,
) -> list[CheckResult]:
    """
    Run every applicable check for one table.

    Pass `df` to check a frame you already have -- it must use the standard
    column names, since `read_table` is what applies `colname_map`. Omit it
    and the table is read from `data_root` for `yyyymm` as before.
    """
    table_name = table_cfg["table_name"]

    if df is None:
        if yyyymm is None or data_root is None:
            raise ValueError("run_checks_for_table needs either df, or both yyyymm and data_root")
        loaded = read_table(table_cfg, yyyymm, data_root)
        if not loaded.ok:
            return loaded.results
        results = loaded.results
        df = loaded.frame
    else:
        # A caller-supplied frame skips the file-level checks (there's no
        # file to check), but it still has to be structurally usable or the
        # checks below would raise instead of reporting.
        results = _check_supplied_frame(df, table_cfg)
        if any(r.status == "FAIL" for r in results):
            return results

    results.extend(_run_frame_checks(df, table_cfg, yyyymm))
    return results


def _check_supplied_frame(df: pd.DataFrame, table_cfg: dict) -> list[CheckResult]:
    """The subset of the structural checks that still apply to a given frame."""
    table_name = table_cfg["table_name"]
    results = []

    expected = set(table_cfg["colname_map"].values())
    missing = expected - set(df.columns)
    if missing:
        results.append(
            _fail(table_name, "required_columns", f"Missing expected column(s): {sorted(missing)}")
        )
        return results
    results.append(_pass(table_name, "required_columns"))

    if len(df) == 0:
        results.append(_fail(table_name, "row_count", "Frame has no rows"))
        return results
    results.append(_pass(table_name, "row_count", f"{len(df)} rows"))

    return results


def _run_frame_checks(
    df: pd.DataFrame, table_cfg: dict, yyyymm: str | None = None
) -> list[CheckResult]:
    """
    The checks that are purely about the data, once it's loaded and
    normalised. Independent of each other and of where the frame came from.

    `yyyymm` is the month the run is for; only the timeliness check needs it,
    and that check is skipped when it isn't known.
    """
    table_name = table_cfg["table_name"]
    colname_map = table_cfg["colname_map"]
    results = [_check_report_date(df, table_name)]

    if "mandate_id" in colname_map.values():
        results.append(_check_mandate_id(df, table_name))

    results.append(_check_analytics_completeness(df, table_name))
    results.append(_check_analytics_membership(df, table_name, table_cfg["expected_analytics"]))
    results.append(_check_val_amt(df, table_name, table_cfg["val_amt_type"]))

    pk_cols = primary_key_columns(colname_map)
    results.append(_check_primary_key_uniqueness(df, table_name, pk_cols))

    if table_cfg["kpi_analytics"]:
        results.append(_check_kpi_completeness(df, table_name, pk_cols, table_cfg["kpi_analytics"]))

    timeliness = _check_source_timeliness(df, table_name, yyyymm)
    if timeliness is not None:
        results.append(timeliness)

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


def _check_analytics_completeness(df: pd.DataFrame, table_name: str) -> CheckResult:
    null_count = int(df["analytics"].isna().sum())
    if null_count:
        return _fail(table_name, "analytics_completeness", f"{null_count} null analytics value(s)")
    return _pass(table_name, "analytics_completeness")


def _check_analytics_membership(
    df: pd.DataFrame, table_name: str, expected_analytics: list[str]
) -> CheckResult:
    if not expected_analytics:
        return _pass(table_name, "analytics_membership", "no expected_analytics configured")

    # Nulls are the completeness check's business. Excluding them here keeps
    # one bad cell from being reported twice, as both "missing" and "not a
    # recognised value", while still letting membership judge the rest.
    present = df["analytics"].dropna()
    unexpected = sorted(set(present.unique()) - set(expected_analytics))
    if unexpected:
        return _fail(table_name, "analytics_membership", f"Unexpected analytics value(s): {unexpected}")
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
            missing_by_group[key] = tuple(sorted(missing))

    if missing_by_group:
        return _fail(
            table_name, "kpi_completeness", _describe_missing_kpis(missing_by_group, group_cols)
        )
    return _pass(table_name, "kpi_completeness")


# How much of a long list to spell out before summarising the remainder.
MAX_IDS_LISTED = 12
MAX_PATTERNS_LISTED = 5

# A source value like "RQA_202611_extract" carries the month it was produced
# for, written either as YYYYMM or YYYY-MM -- the two mean the same month.
# Both are bounded by non-digits so a longer number can't match by accident;
# the dashed form also refuses a trailing dash, so a full date like
# 2026-04-15 is not read as a month.
SOURCE_MONTH = re.compile(r"(?<!\d)(\d{4})-(\d{2})(?![\d-])|(?<!\d)(\d{6})(?!\d)")
SOURCE_COLUMN = "source"


def _months_in(value) -> list[str]:
    """Every month named in a source value, normalised to YYYYMM."""
    return [
        f"{year}{month}" if year else solid
        for year, month, solid in SOURCE_MONTH.findall(str(value))
    ]


def _format_ids(ids: list[str]) -> str:
    """Spell out a bounded number of ids, then summarise the rest."""
    ids = sorted(ids)
    shown = ", ".join(ids[:MAX_IDS_LISTED])
    if len(ids) > MAX_IDS_LISTED:
        shown += f", +{len(ids) - MAX_IDS_LISTED} more"
    return shown


def _find_source_column(df: pd.DataFrame) -> str | None:
    """
    The source column isn't in any table's colname_map, so it arrives under
    whatever the file calls it and simply survives the rename. Matched
    case-insensitively; absent on most tables, which is why the check is
    skipped rather than failed when it isn't there.
    """
    for column in df.columns:
        if str(column).strip().lower() == SOURCE_COLUMN:
            return column
    return None


def _check_source_timeliness(
    df: pd.DataFrame, table_name: str, yyyymm: str | None
) -> CheckResult | None:
    """
    Warn when a row's source says it was produced for a different month.

    Stale data that is otherwise well-formed passes every other check, so
    without this a whole month of last month's numbers would look clean.
    It's a warning rather than a failure: the file is usable, it may just be
    the wrong vintage, and that's a judgement call for whoever reads it.

    Returns None when the check can't apply -- no source column, or no month
    to compare against (a caller-supplied frame with no yyyymm).
    """
    column = _find_source_column(df)
    if column is None or yyyymm is None:
        return None

    labels = df["mandate_id"] if "mandate_id" in df.columns else None
    noun = "mandate" if labels is not None else "row"

    stale: dict[str, list[str]] = {}
    unverifiable = 0

    for position, value in enumerate(df[column]):
        months = _months_in(value) if pd.notna(value) else []
        if not months:
            # No month in the source at all -- nothing to contradict the run.
            # Counted, but deliberately not warned on, or a source that never
            # carries a date would warn on every single run.
            unverifiable += 1
            continue
        if yyyymm in months:
            continue
        label = str(labels.iloc[position]) if labels is not None else f"row {position + 2}"
        stale.setdefault(months[0], []).append(label)

    note = f" ({unverifiable} row(s) had no month in source)" if unverifiable else ""

    if not stale:
        return _pass(table_name, "source_timeliness", f"source month matches {yyyymm}{note}")

    affected = sum(len(ids) for ids in stale.values())
    by_month = sorted(stale.items(), key=lambda item: (-len(item[1]), item[0]))
    described = "; ".join(
        f"{month} for {len(ids)} {noun}(s): {_format_ids(ids)}"
        for month, ids in by_month[:MAX_PATTERNS_LISTED]
    )
    if len(by_month) > MAX_PATTERNS_LISTED:
        described += f"; +{len(by_month) - MAX_PATTERNS_LISTED} further month(s)"

    return _warn(
        table_name,
        "source_timeliness",
        f"{affected} {noun}(s) sourced from a month other than {yyyymm} — {described}{note}",
    )


def _describe_missing_kpis(missing_by_group: dict, group_cols: list[str]) -> str:
    """
    Describe incomplete groups by the gap they share, not one line each.

    Typically many mandates are missing the same analytic, so listing them
    per mandate repeats the same list over and over. Inverting it -- which
    analytics are missing, and for whom -- says the same thing in a fraction
    of the space and makes a systemic gap obvious at a glance.
    """
    # groupby yields a scalar key for a single column and a tuple for several.
    normalised = {
        (key if isinstance(key, tuple) else (key,)): missing
        for key, missing in missing_by_group.items()
    }

    label_of, noun = _group_labeller(normalised, group_cols)

    ids_by_gap: dict[tuple, list[str]] = {}
    for key, missing in normalised.items():
        ids_by_gap.setdefault(missing, []).append(label_of(key))

    # Widest-reaching gap first -- that's the one worth acting on.
    patterns = sorted(ids_by_gap.items(), key=lambda item: (-len(item[1]), item[0]))
    described = []
    for missing, ids in patterns[:MAX_PATTERNS_LISTED]:
        described.append(f"{list(missing)} for {len(ids)} {noun}(s): {_format_ids(ids)}")

    if len(patterns) > MAX_PATTERNS_LISTED:
        described.append(f"+{len(patterns) - MAX_PATTERNS_LISTED} further pattern(s)")

    return (
        f"{len(normalised)} {noun}(s) missing required KPI analytics — "
        + "; ".join(described)
    )


def _group_labeller(normalised: dict, group_cols: list[str]):
    """
    How to name each incomplete group in the message.

    Where the file covers a single report_date -- the normal case for a
    monthly extract -- naming the mandate alone is enough. If several dates
    are involved, the date has to stay in or the message would merge groups
    that are genuinely different.
    """
    if "mandate_id" in group_cols:
        mandate_at = group_cols.index("mandate_id")
        others = [i for i in range(len(group_cols)) if i != mandate_at]
        one_value_each = all(len({key[i] for key in normalised}) <= 1 for i in others)
        if one_value_each:
            return (lambda key: str(key[mandate_at])), "mandate"

    return (lambda key: "/".join(str(part) for part in key)), "group"
