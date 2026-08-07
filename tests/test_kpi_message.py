"""
How an incomplete-KPI failure is described.

Many mandates usually share the same gap, so the message groups by the
missing analytics and lists who is affected, rather than repeating the same
list once per mandate.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from checks import run_checks_for_table  # noqa: E402

KPIS = ["kpi_a", "kpi_b"]


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
        "expected_analytics": KPIS,
        "kpi_analytics": KPIS,
    }


def frame(rows, report_date="2026-07-31"):
    """rows: {mandate_id: [analytics it actually has]}."""
    records = [
        {"report_date": report_date, "mandate_id": mandate, "analytics": analytic, "val_amt": 1.0}
        for mandate, analytics in rows.items()
        for analytic in analytics
    ]
    return pd.DataFrame(records)


def kpi_message(table_cfg, df) -> str:
    results = {r.check_name: r for r in run_checks_for_table(table_cfg, df=df)}
    return results["kpi_completeness"].message


def test_mandates_sharing_a_gap_are_listed_together(table_cfg):
    df = frame({"M001": ["kpi_a"], "M002": ["kpi_a"], "M003": KPIS})

    message = kpi_message(table_cfg, df)

    assert message == (
        "2 mandate(s) missing required KPI analytics — "
        "['kpi_b'] for 2 mandate(s): M001, M002"
    )


def test_different_gaps_are_reported_separately(table_cfg):
    df = frame({"M001": ["kpi_a"], "M002": ["kpi_a"], "M003": ["kpi_b"]})

    message = kpi_message(table_cfg, df)

    # Widest-reaching gap first.
    assert message.index("['kpi_b']") < message.index("['kpi_a']")
    assert "['kpi_b'] for 2 mandate(s): M001, M002" in message
    assert "['kpi_a'] for 1 mandate(s): M003" in message


def test_a_mandate_missing_every_kpi(table_cfg):
    """A mandate present in the file but carrying none of the required KPIs."""
    table_cfg["expected_analytics"] = KPIS + ["other_metric"]
    df = frame({"M001": KPIS, "M002": ["other_metric"]})

    message = kpi_message(table_cfg, df)

    assert "['kpi_a', 'kpi_b'] for 1 mandate(s): M002" in message


def test_long_mandate_lists_are_truncated(table_cfg):
    df = frame({f"M{i:03d}": ["kpi_a"] for i in range(1, 21)})

    message = kpi_message(table_cfg, df)

    assert "20 mandate(s)" in message
    assert "+8 more" in message          # 12 listed, 8 summarised
    assert "M001, M002" in message


def test_several_report_dates_keep_the_date_in_the_label(table_cfg):
    """Dropping the date would merge groups that are genuinely different."""
    july = frame({"M001": ["kpi_a"]}, report_date="2026-07-31")
    august = frame({"M001": ["kpi_a"]}, report_date="2026-08-31")

    message = kpi_message(table_cfg, pd.concat([july, august], ignore_index=True))

    assert "group(s)" in message
    assert "2026-07-31/M001" in message
    assert "2026-08-31/M001" in message


def test_table_without_mandate_id_labels_by_its_group(table_cfg):
    table_cfg["colname_map"] = {
        "REPORT_DATE": "report_date",
        "ANALYTICS": "analytics",
        "VAL_AMT": "val_amt",
    }
    df = pd.DataFrame(
        [{"report_date": "2026-07-31", "analytics": "kpi_a", "val_amt": 1.0}]
    )

    message = kpi_message(table_cfg, df)

    assert "['kpi_b'] for 1 group(s): 2026-07-31" in message


def test_no_mandate_is_listed_twice(table_cfg):
    """Each mandate is one group, so it can only appear once per gap."""
    import re

    df = frame({f"M{i:03d}": ["kpi_a"] for i in range(1, 6)})

    message = kpi_message(table_cfg, df)
    listed = re.findall(r"M\d{3}", message)

    assert listed == sorted(set(listed))


def test_a_missing_kpi_warns_rather_than_fails(table_cfg):
    """
    A missing KPI is usually a legitimate gap -- a mandate with no value
    this month -- so it's surfaced for judgement, not treated as a broken
    file. It must not affect the exit code.
    """
    results = {r.check_name: r for r in run_checks_for_table(
        table_cfg, df=frame({"M001": ["kpi_a"], "M002": KPIS}))}

    assert results["kpi_completeness"].status == "WARN"
    assert not [r for r in results.values() if r.status == "FAIL"]


def test_complete_data_produces_no_failure(table_cfg):
    df = frame({"M001": KPIS, "M002": KPIS})
    results = {r.check_name: r for r in run_checks_for_table(table_cfg, df=df)}

    assert results["kpi_completeness"].status == "PASS"
