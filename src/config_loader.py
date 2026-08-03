"""
Reading the table config, and the two derivations everything else relies on:
where a table's file for a given month lives, and what its primary key is.
"""
from __future__ import annotations

import json
from pathlib import Path

MONTH_TOKEN = "YYYYMM"
VALUE_COLUMN = "val_amt"


def load_table_configs(config_path: str, table_names: list[str] | None = None) -> list[dict]:
    """
    Load the table definitions, optionally narrowed to a named subset.

    An unrecognised name raises rather than silently checking fewer tables
    than asked for -- a typo in `--tables` would otherwise look like a
    clean run.
    """
    with open(config_path) as f:
        tables = json.load(f)

    if table_names:
        wanted = set(table_names)
        tables = [t for t in tables if t["table_name"] in wanted]
        found = {t["table_name"] for t in tables}
        missing = wanted - found
        if missing:
            raise ValueError(f"Table name(s) not found in config: {sorted(missing)}")

    return tables


def resolve_path(table_cfg: dict, yyyymm: str, data_root: str) -> Path:
    """
    Where this table's file for `yyyymm` should be.

    Both `source_folder` and `file_name` are templates carrying a literal
    YYYYMM token (e.g. "public/YYYYMM/rqa" and "YYYYMM_ORR.xlsx"), which is
    substituted with the month being run.

    An absolute `source_folder` wins outright and `data_root` is ignored,
    so a single table can be pointed at a different mount without moving
    everything else.
    """
    source_folder = table_cfg["source_folder"].replace(MONTH_TOKEN, yyyymm)
    file_name = table_cfg["file_name"].replace(MONTH_TOKEN, yyyymm)

    folder = Path(source_folder)
    if not folder.is_absolute():
        folder = Path(data_root) / folder

    return folder / file_name


def primary_key_columns(colname_map: dict) -> list[str]:
    """
    A table's key: every mapped column except the value itself.

    Derived rather than read from reference.xlsx's own `primary_key` column,
    which is inconsistently cased and doesn't always match the actual
    headers. Deriving it from `colname_map` means the key can never
    reference a column the table doesn't have.

    Tables differ here -- most key on (report_date, mandate_id, analytics),
    but KPI_TRADING_PERF has no mandate_id and keys on two columns.
    """
    return [v for v in colname_map.values() if v != VALUE_COLUMN]
