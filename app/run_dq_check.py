"""
CLI entry point for scheduled jobs.

Example:
    python app/run_dq_check.py --yyyymm 202607 --data-root /mnt/network_drive/data

Exits non-zero if any data quality check fails, so job schedulers
(cron, Databricks Jobs, Airflow, etc.) can detect and alert on failure.
"""
import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from runner import DQCheckFailure, run_dq_checks  # noqa: E402

DEFAULT_CONFIG_PATH = str(PROJECT_ROOT / "config" / "tables_config.json")
DEFAULT_OUTPUT_DIR = str(PROJECT_ROOT / "output")


def main():
    parser = argparse.ArgumentParser(description="Run data quality checks against monthly Excel table exports.")
    parser.add_argument("--yyyymm", required=True, help="Target month in YYYYMM format, e.g. 202607")
    parser.add_argument("--data-root", default=".", help="Base folder for relative source_folder paths in config")
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH, help="Path to tables_config.json")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help="Folder to write the Excel report into")
    parser.add_argument(
        "--tables",
        default=None,
        help="Comma-separated list of table_name values to check (default: all tables in config)",
    )
    args = parser.parse_args()

    table_names = [t.strip() for t in args.tables.split(",")] if args.tables else None

    try:
        run_dq_checks(
            yyyymm=args.yyyymm,
            data_root=args.data_root,
            config_path=args.config,
            table_names=table_names,
            output_dir=args.output_dir,
        )
    except DQCheckFailure as e:
        print(f"\nDQ CHECK FAILED: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
