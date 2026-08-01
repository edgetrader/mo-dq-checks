"""
Deliberate defect injection for scripts/generate_test_data.py. Each defect
type mutates a clean DataFrame (built by sample_data_builder.build_sample_df)
so it fails exactly one specific dq_checks check, so test_data exercises
every FAIL branch at least once instead of being uniformly clean.
"""
import pandas as pd


def _raw_col(table_cfg: dict, standard_name: str) -> str:
    for raw, std in table_cfg["colname_map"].items():
        if std == standard_name:
            return raw
    raise KeyError(f"{standard_name} not in colname_map for {table_cfg['table_name']}")


def _safe_row_index(df: pd.DataFrame, table_cfg: dict) -> int:
    """
    Row index whose analytics value is NOT a required KPI, so mutating that
    row's analytics/mandate_id can't accidentally remove a required KPI from
    its group and cascade into an unintended kpi_completeness failure too.
    Falls back to row 0 if the table has no kpi_analytics (nothing to avoid).
    """
    kpi_analytics = table_cfg.get("kpi_analytics")
    if not kpi_analytics:
        return 0
    analytics_col = _raw_col(table_cfg, "analytics")
    for idx, value in zip(df.index, df[analytics_col]):
        if value not in kpi_analytics:
            return idx
    return df.index[0]  # every value happens to be a required KPI -- best effort


def missing_column(df: pd.DataFrame, table_cfg: dict) -> pd.DataFrame:
    return df.drop(columns=[_raw_col(table_cfg, "analytics")])


def zero_rows(df: pd.DataFrame, table_cfg: dict) -> pd.DataFrame:
    return df.iloc[0:0]


def invalid_date(df: pd.DataFrame, table_cfg: dict) -> pd.DataFrame:
    # Corrupting report_date fragments the (report_date, mandate_id) grouping
    # used by kpi_completeness, so this is restricted to no-kpi tables by the
    # defect plan (a 1-row date-fragment group will almost always look
    # "incomplete" for any real kpi list -- that's a cascade, not a bug).
    df = df.copy()
    df.loc[df.index[0], _raw_col(table_cfg, "report_date")] = "not-a-date"
    return df


def null_mandate_id(df: pd.DataFrame, table_cfg: dict) -> pd.DataFrame:
    # Same cascade risk as invalid_date (fragments the mandate_id grouping)
    # -- restricted to no-kpi tables by the defect plan.
    df = df.copy()
    df.loc[df.index[0], _raw_col(table_cfg, "mandate_id")] = None
    return df


def null_analytics(df: pd.DataFrame, table_cfg: dict) -> pd.DataFrame:
    df = df.copy()
    idx = _safe_row_index(df, table_cfg)
    df.loc[idx, _raw_col(table_cfg, "analytics")] = None
    return df


def unexpected_analytics(df: pd.DataFrame, table_cfg: dict) -> pd.DataFrame:
    df = df.copy()
    idx = _safe_row_index(df, table_cfg)
    df.loc[idx, _raw_col(table_cfg, "analytics")] = "unexpected_metric_xyz"
    return df


def messy_whitespace_analytics(df: pd.DataFrame, table_cfg: dict) -> pd.DataFrame:
    """Realistic messy-data variant: wrong case + stray whitespace, not a nonsense value."""
    df = df.copy()
    idx = _safe_row_index(df, table_cfg)
    col = _raw_col(table_cfg, "analytics")
    original = df.loc[idx, col]
    df.loc[idx, col] = f"  {str(original).upper()}  "
    return df


def non_numeric_val(df: pd.DataFrame, table_cfg: dict) -> pd.DataFrame:
    df = df.copy()
    col = _raw_col(table_cfg, "val_amt")
    df[col] = df[col].astype(object)
    df.loc[df.index[0], col] = "not_a_number"
    return df


def duplicate_key(df: pd.DataFrame, table_cfg: dict) -> pd.DataFrame:
    return pd.concat([df, df.iloc[[0]]], ignore_index=True)


def missing_kpi(df: pd.DataFrame, table_cfg: dict) -> pd.DataFrame:
    analytics_col = _raw_col(table_cfg, "analytics")
    mandate_col = _raw_col(table_cfg, "mandate_id")
    target_kpi = table_cfg["kpi_analytics"][0]
    drop_mask = (df[mandate_col] == "M001") & (df[analytics_col] == target_kpi)
    return df.loc[~drop_mask].reset_index(drop=True)


def multi_field_corruption(df: pd.DataFrame, table_cfg: dict) -> pd.DataFrame:
    """
    A row corrupted on multiple independent fields at once (bad date, null
    mandate_id, null analytics) -- e.g. a partially-filled-in manual entry.
    NOTE: a row left *entirely* blank gets silently dropped by
    pandas/openpyxl on the Excel round-trip and never reaches the checker at
    all (verified empirically) -- so report_date and val_amt are kept as
    real, non-blank values here purely so the row survives the round-trip;
    only mandate_id and analytics are actually nulled. Restricted to no-kpi
    tables by the defect plan so the resulting failure set stays predictable.
    """
    df = df.copy()
    idx = df.index[0]
    df.loc[idx, _raw_col(table_cfg, "report_date")] = "N/A"
    df.loc[idx, _raw_col(table_cfg, "mandate_id")] = None
    df.loc[idx, _raw_col(table_cfg, "analytics")] = None
    return df


# defect_type -> (mutator, expected_failing_check(s)).
# "wrong_sheet" and "missing_file" don't mutate the dataframe -- they change
# how/whether the file gets written, handled directly in generate_test_data.py.
DATAFRAME_DEFECTS = {
    "missing_column": (missing_column, {"required_columns"}),
    "zero_rows": (zero_rows, {"row_count"}),
    "invalid_date": (invalid_date, {"report_date_dtype"}),
    "null_mandate_id": (null_mandate_id, {"mandate_id_completeness"}),
    "null_analytics": (null_analytics, {"analytics_completeness"}),
    "unexpected_analytics": (unexpected_analytics, {"analytics_membership"}),
    "messy_whitespace_analytics": (messy_whitespace_analytics, {"analytics_membership"}),
    "non_numeric_val": (non_numeric_val, {"val_amt_dtype"}),
    "duplicate_key": (duplicate_key, {"primary_key_uniqueness"}),
    "missing_kpi": (missing_kpi, {"kpi_completeness"}),
    "multi_field_corruption": (multi_field_corruption, {"report_date_dtype", "analytics_completeness", "mandate_id_completeness"}),
}
