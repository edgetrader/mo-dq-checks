"""
Generates one clean, valid month of sample data for every table in
config/tables_config.json, written to data/<source_folder> (source_folder
templates are relative, e.g. "public/YYYYMM/rqa").

Usage:
    python scripts/generate_sample_data.py 202607
    python scripts/generate_sample_data.py            # defaults to 202607

To run DQ checks against this sample data:
    python app/run_dq_check.py --yyyymm 202607 --data-root data
"""
import sys
from pathlib import Path

from sample_data_builder import write_table_file

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from config_loader import load_table_configs, resolve_path  # noqa: E402

CONFIG_PATH = PROJECT_ROOT / "config" / "tables_config.json"
DEFAULT_YYYYMM = "202607"
DATA_ROOT = PROJECT_ROOT / "data"


def main(yyyymm: str = DEFAULT_YYYYMM):
    table_cfgs = load_table_configs(str(CONFIG_PATH))
    written = []
    for table_cfg in table_cfgs:
        path = resolve_path(table_cfg, yyyymm, str(DATA_ROOT))
        write_table_file(table_cfg, yyyymm, path)
        written.append(path)

    print(f"Wrote {len(written)} sample file(s) for yyyymm={yyyymm}:")
    for p in written:
        print(" ", p)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_YYYYMM)
