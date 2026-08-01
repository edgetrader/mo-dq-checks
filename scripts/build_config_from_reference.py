"""
One-off / repeatable converter: reads ref/reference.xlsx (sheet "data") and
produces config/tables_config.json, the config consumed by dq_checks.runner.

Re-run this whenever reference.xlsx is updated with new/changed table definitions:

    python scripts/build_config_from_reference.py
"""
import ast
import json
import re
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REFERENCE_XLSX = PROJECT_ROOT / "ref" / "reference.xlsx"
OUTPUT_CONFIG = PROJECT_ROOT / "config" / "tables_config.json"


def _parse_literal(value, default=None):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return default
    value = value.strip()
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return ast.literal_eval(value)


def derive_table_name(file_name: str) -> str:
    name = re.sub(r"^YYYYMM_", "", file_name)
    name = re.sub(r"\.xlsx$", "", name, flags=re.IGNORECASE)
    return name


def clean_source_folder(source_folder: str) -> str:
    """
    reference.xlsx authors source_folder as "../data/public/YYYYMM/rqa", relative
    to wherever that spreadsheet happened to sit. That leading "../data/" is not
    meaningful on its own -- strip it so source_folder is just the path *under*
    whatever --data-root is configured at run time (e.g. "public/YYYYMM/rqa"),
    with no implied folder name or directory-traversal baked in.
    """
    return re.sub(r"^(\.\./)*data/", "", source_folder)


def build_config() -> list[dict]:
    df = pd.read_excel(REFERENCE_XLSX, sheet_name="data")
    tables = []
    for _, row in df.iterrows():
        colname_map = _parse_literal(row["colname_map"], default={})
        expected_analytics = _parse_literal(row["expected_analytics"], default=[])
        kpi_analytics = _parse_literal(row["kpi_analytics"], default=None)
        sheet_name = row["sheet_name"]
        if pd.isna(sheet_name):
            sheet_name = None

        tables.append(
            {
                "table_name": derive_table_name(row["file_name"]),
                "source_folder": clean_source_folder(row["source_folder"]),
                "file_name": row["file_name"],
                "sheet_name": sheet_name,
                "provided_by": row["provided_by"],
                "file_frequency": row["file_frequency"],
                "colname_map": colname_map,
                "val_amt_type": row["val_amt_type"],
                "expected_analytics": expected_analytics,
                "kpi_analytics": kpi_analytics,
            }
        )
    return tables


def main():
    tables = build_config()
    OUTPUT_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_CONFIG, "w") as f:
        json.dump(tables, f, indent=2)
    print(f"Wrote {len(tables)} table definitions to {OUTPUT_CONFIG}")


if __name__ == "__main__":
    main()
