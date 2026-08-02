"""
Covers the split between reading a table and checking it:

  read_table()            does the I/O and the structural checks
  run_checks_for_table()  checks a frame, reading one only if not given

The point of the split is that a frame sourced from somewhere other than a
local Excel file (a Databricks table, a differently-mounted share, an
in-memory build) can be checked with the same logic.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from checks import read_table, run_checks_for_table  # noqa: E402
from config_loader import resolve_path  # noqa: E402

RAW_HEADERS = {
    "report_date": "REPORT DATE",
    "mandate_id": "MANDATE_ID",
    "analytics": "ANALYTICS",
    "val_amt": "VALUES",
}


@pytest.fixture
def table_cfg() -> dict:
    return {
        "table_name": "SAMPLE",
        "source_folder": "sub/YYYYMM",
        "file_name": "YYYYMM_SAMPLE.xlsx",
        "sheet_name": None,
        "colname_map": {v: k for k, v in RAW_HEADERS.items()},
        "val_amt_type": "Numeric",
        "expected_analytics": ["alpha", "beta"],
        "kpi_analytics": None,
    }


@pytest.fixture
def frame() -> pd.DataFrame:
    """A clean, already-normalised frame -- what read_table would hand back."""
    return pd.DataFrame(
        {
            "report_date": ["2026-07-31"] * 4,
            "mandate_id": ["M001", "M001", "M002", "M002"],
            "analytics": ["alpha", "beta", "alpha", "beta"],
            "val_amt": [1.0, 2.0, 3.0, 4.0],
        }
    )


@pytest.fixture
def written(tmp_path, table_cfg, frame):
    """The same data on disk, with the table's raw Excel headers."""
    path = resolve_path(table_cfg, "202607", str(tmp_path))
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.rename(columns=RAW_HEADERS).to_excel(path, index=False)
    return tmp_path, path


def statuses(results) -> dict[str, str]:
    return {r.check_name: r.status for r in results}


# --------------------------------------------------------------------------
# read_table
# --------------------------------------------------------------------------


def test_read_table_returns_frame_and_source_attributes(table_cfg, written):
    root, path = written
    loaded = read_table(table_cfg, "202607", str(root))

    assert loaded.ok
    assert loaded.path == path
    assert loaded.sheet_name == 0  # no sheet configured -> first sheet
    assert loaded.raw_columns == list(RAW_HEADERS.values())
    assert loaded.row_count == 4
    # The frame comes back normalised, ready to check.
    assert set(loaded.frame.columns) == set(RAW_HEADERS)


def test_read_table_reports_the_structural_checks(table_cfg, written):
    root, _ = written
    loaded = read_table(table_cfg, "202607", str(root))

    assert statuses(loaded.results) == {
        "file_exists": "PASS",
        "sheet_exists": "PASS",
        "required_columns": "PASS",
        "row_count": "PASS",
    }


def test_read_table_on_missing_file(table_cfg, tmp_path):
    loaded = read_table(table_cfg, "202607", str(tmp_path))

    assert not loaded.ok
    assert loaded.frame is None
    assert loaded.path is not None  # resolved, just absent
    assert statuses(loaded.results) == {"file_exists": "FAIL"}


def test_read_table_on_unreadable_file(table_cfg, tmp_path):
    path = resolve_path(table_cfg, "202607", str(tmp_path))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"PK\x03\x04 not actually a workbook")

    loaded = read_table(table_cfg, "202607", str(tmp_path))

    assert not loaded.ok
    assert statuses(loaded.results)["sheet_exists"] == "FAIL"


def test_read_table_rejects_a_malformed_month(table_cfg, tmp_path):
    loaded = read_table(table_cfg, "2026", str(tmp_path))

    assert not loaded.ok
    assert loaded.path is None
    assert statuses(loaded.results) == {"yyyymm_format": "FAIL"}


# --------------------------------------------------------------------------
# run_checks_for_table with a supplied frame
# --------------------------------------------------------------------------


def test_supplied_frame_gets_the_same_data_checks_as_a_read_one(table_cfg, written, frame):
    root, _ = written
    from_file = statuses(run_checks_for_table(table_cfg, "202607", str(root)))
    from_frame = statuses(run_checks_for_table(table_cfg, df=frame))

    # File-level checks only apply when there was a file.
    assert set(from_file) - set(from_frame) == {"file_exists", "sheet_exists"}
    # Everything else must agree exactly.
    for check, status in from_frame.items():
        assert from_file[check] == status


def test_supplied_frame_detects_a_data_problem(table_cfg, frame):
    frame.loc[0, "analytics"] = "unexpected_value"
    results = statuses(run_checks_for_table(table_cfg, df=frame))

    assert results["analytics_membership"] == "FAIL"


def test_supplied_frame_missing_a_column_is_reported_not_raised(table_cfg, frame):
    """Without this guard the data checks would raise KeyError instead."""
    results = statuses(run_checks_for_table(table_cfg, df=frame.drop(columns=["analytics"])))

    assert results == {"required_columns": "FAIL"}


def test_supplied_empty_frame(table_cfg, frame):
    results = statuses(run_checks_for_table(table_cfg, df=frame.iloc[0:0]))

    assert results["row_count"] == "FAIL"
    # No misleading passes from checking nothing.
    assert "primary_key_uniqueness" not in results


def test_frame_from_a_non_excel_source(table_cfg):
    """
    The motivating case: data that never came from a local folder at all --
    here built in memory, but equally a Spark/Databricks frame converted to
    pandas. It only has to use the standard column names.
    """
    frame = pd.DataFrame(
        {
            "report_date": ["2026-07-31", "2026-07-31"],
            "mandate_id": ["M001", "M001"],
            "analytics": ["alpha", "alpha"],  # duplicate key
            "val_amt": [1.0, 2.0],
        }
    )
    results = statuses(run_checks_for_table(table_cfg, df=frame))

    assert results["primary_key_uniqueness"] == "FAIL"


# --------------------------------------------------------------------------
# Signature
# --------------------------------------------------------------------------


def test_existing_positional_call_still_works(table_cfg, written):
    """runner.py and any scheduled job call it this way."""
    root, _ = written
    assert statuses(run_checks_for_table(table_cfg, "202607", str(root)))["row_count"] == "PASS"


def test_needs_either_a_frame_or_a_location(table_cfg):
    with pytest.raises(ValueError, match="either df, or both yyyymm and data_root"):
        run_checks_for_table(table_cfg)
