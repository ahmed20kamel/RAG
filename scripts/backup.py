"""Back up the knowledge base, or check that a backup restores.

    python scripts/backup.py                 # take a backup now
    python scripts/backup.py --verify        # check the newest archive opens and matches
    python scripts/backup.py --drill         # verify, then restore the vector snapshots
                                             # into throw-away collections and count them

Safe to run while the application is serving: the database is copied through SQLite's
online backup API and the vectors through Qdrant's snapshot API.

Exit code 0 on success, 1 on any failure, so a scheduler can alert on it.
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

from app.config import get_settings  # noqa: E402
from app.services.backup import BackupPlan, drill, latest_archive, run_backup, verify  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true", help="verify the newest archive")
    parser.add_argument("--drill", action="store_true", help="verify and trial-restore it")
    parser.add_argument("--archive", type=Path, help="a specific archive to verify")
    args = parser.parse_args()

    log_dir = ROOT / "data" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(log_dir / "backup.log", encoding="utf-8"),
                  logging.StreamHandler(sys.stdout)],
    )
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    plan = BackupPlan.from_settings(get_settings(), ROOT)
    try:
        if args.verify or args.drill:
            archive = args.archive or latest_archive(plan.destination)
            if archive is None:
                logging.error("No archive under %s", plan.destination)
                return 1
            report = verify(archive)
            logging.info("Verified %s: %s", archive.name, json.dumps(report, ensure_ascii=False))
            if args.drill:
                result = drill(archive, plan)
                logging.info("Restore drill passed: %s", json.dumps(result, ensure_ascii=False))
            return 0

        result = run_backup(plan)
        logging.info(
            "Backup written: %s (%.1f MB in %.0fs) tables=%s points=%s",
            result.archive, result.size / 1e6, result.seconds,
            result.manifest["tables"], result.manifest["points"],
        )
        if result.mirrored_to:
            logging.info("Mirrored to %s", result.mirrored_to)
        else:
            logging.warning("No BACKUP_MIRROR_DIR set — this backup is on the same disk as the data")
        if result.pruned:
            logging.info("Pruned %s old archive(s)", len(result.pruned))
        return 0
    except Exception:  # noqa: BLE001
        logging.exception("Backup failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
