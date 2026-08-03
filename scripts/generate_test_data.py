"""
Generates multiple months of test data for every table in
config/tables_config.json, written to test_data/<source_folder>.

Most (table, month) combinations are clean and should pass every check.
A deliberate set are seeded with a specific defect (see DEFECT_PLAN below)
so the generated set exercises every FAIL branch in dq_checks at least
once -- not just the all-pass baseline.

Usage:
    python scripts/generate_test_data.py                          # 10 months ending 202612
    python scripts/generate_test_data.py --months 202603-202612   # explicit range
    python scripts/generate_test_data.py --months 202601,202603,202612

To run DQ checks against a given test month:
    python app/run_dq_check.py --yyyymm 202605 --data-root test_data
"""
import argparse
import sys
from pathlib import Path

import pandas as pd

from defect_injector import DATAFRAME_DEFECTS
from sample_data_builder import build_sample_df

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from config_loader import load_table_configs, resolve_path  # noqa: E402

CONFIG_PATH = PROJECT_ROOT / "config" / "tables_config.json"
TEST_DATA_ROOT = PROJECT_ROOT / "test_data"
DEFAULT_END_MONTH = "202612"
DEFAULT_MONTH_COUNT = 10

# (table_name, yyyymm) -> defect_type.
#
# Round 1 (202603-202607): one instance of every failure type in dq_checks.
# Round 2 (202608-202612): a second, independent round using a different
# table per defect type (so every one of the 22 tables gets exercised by at
# least one defect across the full window), plus two new scenarios:
# messy_whitespace_analytics (case/whitespace variant, not just nonsense
# text) and multi_field_corruption (bad date + null mandate_id + null
# analytics on the same row -- deliberately triggers three independent
# checks on the same file at once).
#
# invalid_date and null_mandate_id are only ever assigned to tables with
# kpi_analytics=None: corrupting report_date or mandate_id fragments the
# grouping kpi_completeness uses, so on a kpi-bearing table it would almost
# always cascade into an extra, unintended kpi_completeness failure (this
# was a real bug caught during validation -- see COMMITMENT below).
DEFECT_PLAN = {
    # --- round 1 ---
    ("DURATION_TARGET", "202603"): "missing_file",
    ("COMMITMENT", "202603"): "null_mandate_id",
    ("PERF_GROSS", "202603"): "missing_kpi",
    ("KPI_TRADING_PERF", "202604"): "wrong_sheet",
    ("PERF_NETT", "202604"): "null_analytics",
    ("ORR", "202604"): "stale_source",
    ("MGMT_FEES", "202605"): "missing_column",
    ("PEER_RANK", "202605"): "unexpected_analytics",
    ("YIELD", "202606"): "zero_rows",
    ("NEW_PURCHASE", "202606"): "non_numeric_val",
    ("DURATION", "202607"): "invalid_date",
    ("AUM_CARVE_OUT", "202607"): "duplicate_key",
    # --- round 2 ---
    ("IRR", "202608"): "missing_file",
    ("KPI_STRESS_SALES", "202608"): "wrong_sheet",
    ("DOWNGRADE", "202608"): "missing_column",
    ("PERF_GROSS_PC", "202609"): "zero_rows",
    ("PERF_GROSS_TAA", "202609"): "invalid_date",
    ("ORR", "202609"): "null_mandate_id",
    ("PERF_CHAINLINK", "202610"): "duplicate_key",
    ("PERF_ADVISORY", "202610"): "unexpected_analytics",
    ("PERF_GROSS_AUS", "202610"): "non_numeric_val",
    ("PERF_NMY", "202611"): "null_analytics",
    ("DOWNGRADE", "202611"): "missing_kpi",
    ("PERF_CARVE_OUT", "202611"): "multi_field_corruption",
    ("IRR", "202612"): "messy_whitespace_analytics",
}


def _default_months(end_month: str, count: int) -> list[str]:
    """`count` consecutive months ending at `end_month`, oldest first."""
    end = pd.Timestamp(f"{end_month[:4]}-{end_month[4:]}-01")
    months = [end - pd.DateOffset(months=i) for i in range(count)]
    return [m.strftime("%Y%m") for m in reversed(months)]


def _parse_months(months_arg: str) -> list[str]:
    """
    Accepts a single month, a comma-separated list, or an inclusive range:
    "202601", "202601,202603" or "202601-202612".

    The range branch is only taken when there's no comma, so a list can't
    be misread as a range.
    """
    if "-" in months_arg and "," not in months_arg:
        start, end = months_arg.split("-")
        start_ts = pd.Timestamp(f"{start[:4]}-{start[4:]}-01")
        end_ts = pd.Timestamp(f"{end[:4]}-{end[4:]}-01")
        months = []
        cur = start_ts
        while cur <= end_ts:
            months.append(cur.strftime("%Y%m"))
            cur += pd.DateOffset(months=1)
        return months
    return [m.strip() for m in months_arg.split(",")]


def main(months: list[str], root=TEST_DATA_ROOT):
    # `root` is a parameter so the test suite can generate into a tmp dir
    # instead of clobbering the real test_data/ folder.
    table_cfgs = load_table_configs(str(CONFIG_PATH))
    written = []
    seeded = []

    for yyyymm in months:
        for table_cfg in table_cfgs:
            table_name = table_cfg["table_name"]
            defect_type = DEFECT_PLAN.get((table_name, yyyymm))
            path = resolve_path(table_cfg, yyyymm, str(root))

            if defect_type == "missing_file":
                seeded.append((table_name, yyyymm, defect_type, {"file_exists"}, str(path)))
                continue

            df = build_sample_df(table_cfg, yyyymm)
            sheet_name = table_cfg["sheet_name"] or "Sheet1"

            if defect_type == "wrong_sheet":
                sheet_name = "unexpected_sheet_name"
                seeded.append((table_name, yyyymm, defect_type, {"sheet_exists"}, str(path)))
            elif defect_type in DATAFRAME_DEFECTS:
                mutator, expected_checks = DATAFRAME_DEFECTS[defect_type]
                df = mutator(df, table_cfg)
                seeded.append((table_name, yyyymm, defect_type, expected_checks, str(path)))

            path.parent.mkdir(parents=True, exist_ok=True)
            with pd.ExcelWriter(path) as writer:
                df.to_excel(writer, sheet_name=sheet_name, index=False)
            written.append(path)

    print(f"Wrote {len(written)} file(s) across {len(months)} month(s) {months} to {root}")
    print(f"\nSeeded {len(seeded)} deliberate defect(s):")
    for table_name, yyyymm, defect_type, expected_checks, path in seeded:
        checks_label = ", ".join(sorted(expected_checks))
        # stale_source is reported as a warning, not a failure.
        severity = "WARN" if defect_type == "stale_source" else "FAIL"
        print(f"  {yyyymm} {table_name:<20} {defect_type:<26} -> expect {severity} on: {checks_label}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate multiple months of test data, with seeded defects.")
    parser.add_argument(
        "--months",
        default=None,
        help="Comma-separated YYYYMM list, or a YYYYMM-YYYYMM range. "
        f"Defaults to {DEFAULT_MONTH_COUNT} months ending {DEFAULT_END_MONTH}.",
    )
    args = parser.parse_args()

    months = _parse_months(args.months) if args.months else _default_months(DEFAULT_END_MONTH, DEFAULT_MONTH_COUNT)
    main(months)
