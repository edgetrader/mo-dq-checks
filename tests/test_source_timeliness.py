"""
The timeliness check: does the source say it was produced for the month
we're running for?

Stale data that is otherwise well-formed passes every other check, so
without this a whole month of last month's numbers looks clean. It reports
a warning rather than a failure -- the file is usable, it may just be the
wrong vintage.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from checks import run_checks_for_table  # noqa: E402


@pytest.fixture
def table_cfg() -> dict:
    return {
        "table_name": "SAMPLE",
        "source_folder": "sub/YYYYMM",
        "file_name": "YYYYMM_SAMPLE.xlsx",
        "sheet_name": None,
        "colname_map": {
            "REPORT_DATE": "report_date",
            "MANDATE_ID": "mandate_id",
            "ANALYTICS": "analytics",
            "VAL_AMT": "val_amt",
        },
        "val_amt_type": "Numeric",
        "expected_analytics": ["alpha"],
        "kpi_analytics": None,
    }


def frame(sources: dict, column: str = "source") -> pd.DataFrame:
    """sources: {mandate_id: source value}."""
    return pd.DataFrame(
        [
            {
                "report_date": "2026-07-31",
                "mandate_id": mandate,
                "analytics": "alpha",
                "val_amt": 1.0,
                column: value,
            }
            for mandate, value in sources.items()
        ]
    )


def timeliness(table_cfg, df, yyyymm="202607"):
    results = {r.check_name: r for r in run_checks_for_table(table_cfg, yyyymm=yyyymm, df=df)}
    return results.get("source_timeliness")


def test_matching_source_month_passes(table_cfg):
    df = frame({"M001": "RQA_202607", "M002": "RQA_202607"})
    assert timeliness(table_cfg, df).status == "PASS"


def test_stale_source_warns_and_names_the_mandates(table_cfg):
    df = frame({"M001": "RQA_202606", "M002": "RQA_202606", "M003": "RQA_202607"})

    result = timeliness(table_cfg, df)

    assert result.status == "WARN"
    assert result.message == (
        "2 mandate(s) sourced from a month other than 202607 — "
        "202606 for 2 mandate(s): M001, M002"
    )


def test_several_stale_months_are_reported_separately(table_cfg):
    df = frame({"M001": "RQA_202605", "M002": "RQA_202606", "M003": "RQA_202606"})

    message = timeliness(table_cfg, df).message

    # Widest-reaching month first.
    assert message.index("202606") < message.index("202605")
    assert "202606 for 2 mandate(s): M002, M003" in message
    assert "202605 for 1 mandate(s): M001" in message


def test_a_source_with_no_month_does_not_warn(table_cfg):
    """
    Otherwise a source that simply never carries a date -- "RQA", a system
    name -- would warn on every single run, for ever.
    """
    df = frame({"M001": "RQA", "M002": "manual upload"})

    result = timeliness(table_cfg, df)

    assert result.status == "PASS"
    assert "2 row(s) had no month in source" in result.message


def test_undated_rows_are_noted_alongside_a_real_warning(table_cfg):
    df = frame({"M001": "RQA_202606", "M002": "RQA"})

    result = timeliness(table_cfg, df)

    assert result.status == "WARN"
    assert "1 row(s) had no month in source" in result.message


def test_check_is_skipped_when_there_is_no_source_column(table_cfg):
    """Most tables don't carry one -- absent means not applicable, not failed."""
    df = frame({"M001": "RQA_202607"}).drop(columns=["source"])
    assert timeliness(table_cfg, df) is None


def test_source_column_is_matched_case_insensitively(table_cfg):
    df = frame({"M001": "RQA_202606"}, column="SOURCE")
    assert timeliness(table_cfg, df).status == "WARN"


def test_check_is_skipped_without_a_month_to_compare_against(table_cfg):
    """A caller-supplied frame with no yyyymm has nothing to check against."""
    df = frame({"M001": "RQA_202606"})
    results = {r.check_name: r for r in run_checks_for_table(table_cfg, df=df)}
    assert "source_timeliness" not in results


def test_a_longer_number_is_not_mistaken_for_a_month(table_cfg):
    df = frame({"M001": "extract_20260715_v2"})
    assert timeliness(table_cfg, df).status == "PASS"


def test_nulls_count_as_undated(table_cfg):
    df = frame({"M001": "RQA_202607", "M002": None})

    result = timeliness(table_cfg, df)

    assert result.status == "PASS"
    assert "1 row(s) had no month in source" in result.message


def test_table_without_mandate_id_reports_rows(table_cfg):
    table_cfg["colname_map"] = {
        "REPORT_DATE": "report_date",
        "ANALYTICS": "analytics",
        "VAL_AMT": "val_amt",
    }
    df = pd.DataFrame(
        [{"report_date": "2026-07-31", "analytics": "alpha", "val_amt": 1.0, "source": "RQA_202606"}]
    )

    result = timeliness(table_cfg, df)

    assert result.status == "WARN"
    assert "1 row(s) sourced from a month other than 202607" in result.message
    assert "row 2" in result.message  # the spreadsheet row, header included


def test_a_warning_does_not_make_the_table_fail(table_cfg):
    df = frame({"M001": "RQA_202606"})
    statuses = {r.status for r in run_checks_for_table(table_cfg, yyyymm="202607", df=df)}

    assert "FAIL" not in statuses
    assert "WARN" in statuses
