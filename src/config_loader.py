from __future__ import annotations

import json
from pathlib import Path


def load_table_configs(config_path: str, table_names: list[str] | None = None) -> list[dict]:
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
    source_folder = table_cfg["source_folder"].replace("YYYYMM", yyyymm)
    file_name = table_cfg["file_name"].replace("YYYYMM", yyyymm)

    folder = Path(source_folder)
    if not folder.is_absolute():
        folder = Path(data_root) / folder

    return folder / file_name


def primary_key_columns(colname_map: dict) -> list[str]:
    return [v for v in colname_map.values() if v != "val_amt"]
