"""
The two analytics checks.

They are independent: completeness asks "is a value present", membership
asks "is the value one we recognise". Both report on every table, so a
healthy table shows PASS for each rather than a column of n/a.
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
        "expected_analytics": ["alpha", "beta"],
        "kpi_analytics": None,
    }


def frame(analytics: list) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "report_date": ["2026-07-31"] * len(analytics),
            "mandate_id": [f"M{i:03d}" for i in range(1, len(analytics) + 1)],
            "analytics": analytics,
            "val_amt": [1.0] * len(analytics),
        }
    )


def statuses(table_cfg, df) -> dict[str, str]:
    return {r.check_name: r.status for r in run_checks_for_table(table_cfg, df=df)}


def test_both_report_on_a_healthy_table(table_cfg):
    """Neither should be n/a just because there's nothing wrong."""
    result = statuses(table_cfg, frame(["alpha", "beta"]))

    assert result["analytics_completeness"] == "PASS"
    assert result["analytics_membership"] == "PASS"


def test_a_null_fails_completeness_only(table_cfg):
    """
    One bad cell, one finding. Membership ignores nulls so the same problem
    isn't also reported as an unrecognised value.
    """
    result = statuses(table_cfg, frame(["alpha", None]))

    assert result["analytics_completeness"] == "FAIL"
    assert result["analytics_membership"] == "PASS"


def test_an_unknown_value_fails_membership_only(table_cfg):
    result = statuses(table_cfg, frame(["alpha", "gamma"]))

    assert result["analytics_completeness"] == "PASS"
    assert result["analytics_membership"] == "FAIL"


def test_both_can_fail_independently(table_cfg):
    """A null and an unknown value are genuinely two different problems."""
    result = statuses(table_cfg, frame(["alpha", None, "gamma"]))

    assert result["analytics_completeness"] == "FAIL"
    assert result["analytics_membership"] == "FAIL"


def test_membership_passes_when_no_expected_set_is_configured(table_cfg):
    table_cfg["expected_analytics"] = []
    results = {r.check_name: r for r in run_checks_for_table(table_cfg, df=frame(["anything"]))}

    assert results["analytics_membership"].status == "PASS"
    assert "no expected_analytics configured" in results["analytics_membership"].message


def test_membership_message_names_the_offending_values(table_cfg):
    results = {r.check_name: r for r in run_checks_for_table(table_cfg, df=frame(["gamma", "delta"]))}

    assert results["analytics_membership"].message == (
        "Unexpected analytics value(s): ['delta', 'gamma']"
    )
