"""
Shared row-generation logic for scripts/generate_sample_data.py and
scripts/generate_test_data.py. Builds one clean, valid month of rows for a
single table, designed to pass every check in dq_checks (valid dates, valid
analytics values, unique keys, all required KPI analytics present, correct
val_amt type).
"""
import pandas as pd

SOURCE_COLUMN = "source"


def build_sample_df(table_cfg: dict, yyyymm: str) -> pd.DataFrame:
    colname_map = table_cfg["colname_map"]
    reverse_map = {std: raw for raw, std in colname_map.items()}
    has_mandate = "mandate_id" in colname_map.values()
    mandate_ids = ["M001", "M002"] if has_mandate else [None]

    analytics_values = table_cfg["expected_analytics"] or table_cfg["kpi_analytics"] or ["sample_metric"]
    report_date = (pd.Timestamp(f"{yyyymm[:4]}-{yyyymm[4:]}-01") + pd.offsets.MonthEnd(0)).strftime("%Y-%m-%d")

    rows = []
    for m_idx, mandate_id in enumerate(mandate_ids):
        for a_idx, analytic in enumerate(analytics_values):
            row = {"report_date": report_date, "analytics": analytic}
            if has_mandate:
                row["mandate_id"] = mandate_id
            if table_cfg["val_amt_type"] == "Numeric":
                row["val_amt"] = round(100 + a_idx * 1.25 + m_idx * 50, 2)
            else:
                row["val_amt"] = f"sample_value_{a_idx + 1}"
            rows.append(row)

    df = pd.DataFrame(rows)
    df = df.rename(columns=reverse_map)

    # Not part of colname_map -- an extra column the real extracts carry,
    # naming the system and the month the data was produced for. It's what
    # source_timeliness reads, and it also keeps the fixtures honest about
    # unmapped columns being tolerated.
    df[SOURCE_COLUMN] = f"{table_cfg.get('provided_by', 'SRC')}_{yyyymm}"
    return df


def write_table_file(table_cfg: dict, yyyymm: str, path) -> None:
    df = build_sample_df(table_cfg, yyyymm)
    path.parent.mkdir(parents=True, exist_ok=True)
    sheet_name = table_cfg["sheet_name"] or "Sheet1"
    with pd.ExcelWriter(path) as writer:
        df.to_excel(writer, sheet_name=sheet_name, index=False)
