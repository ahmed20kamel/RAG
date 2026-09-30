"""Restore the knowledge base from a backup archive.

    python scripts/restore.py backups/rag-backup-20260928-020000.zip --yes

Stop the application first. The restore replaces the live database, the uploaded files
and both vector collections. The current files are moved aside into
`data/pre-restore-<time>/`, never deleted, so restoring the wrong archive can be undone.

The archived `.env` is written beside the live one as `.env.restored-<time>`, not over
it. Whether to run with the restored INTEGRATION_MASTER_KEY is a decision: it revives
the ERP credentials that existed when the backup was taken, and invalidates any issued
since.
"""

from __future__ import annotations

import argparse
import io
import json
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

from app.config import get_settings  # noqa: E402
from app.services.backup import BackupPlan, restore, verify  # noqa: E402


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(1)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    parser.add_argument("--yes", action="store_true", help="confirm replacing the live data")
    args = parser.parse_args()

    settings = get_settings()
    plan = BackupPlan.from_settings(settings, ROOT)

    report = verify(args.archive)
    print(f"archive verified: created {report['created']}, tables {report['tables']}, "
          f"points {report['points']}")
    if not args.yes:
        print("dry run — nothing changed. Re-run with --yes to replace the live data.")
        return 0
    if port_in_use(settings.app_port):
        print(f"the application is still listening on port {settings.app_port}; stop it first")
        return 1

    result = restore(args.archive, plan, ROOT)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("restored. Start the application; the keyword index rebuilds on first use.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
