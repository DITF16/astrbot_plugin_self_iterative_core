"""Recover plugin files while AstrBot is stopped, including this plugin itself."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from utils.file_manager import FileManager  # noqa: E402

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plugin-root", required=True)
    parser.add_argument(
        "--data-dir", required=True, help="self_iterative_core runtime data directory"
    )
    parser.add_argument("--plugin", required=True, help="Plugin directory name")
    parser.add_argument("--backup-id", help="Omit to list backups")
    args = parser.parse_args()
    manager = FileManager(args.plugin_root, args.data_dir)
    result = (
        manager.rollback(args.plugin, args.backup_id)
        if args.backup_id
        else manager.history(args.plugin)
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
