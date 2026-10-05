"""Backup and restore of everything the system cannot rebuild from its source code.

Four things, and each is lost for good without a copy:

* **The database** — users, documents, chunks, taught knowledge, answer traces and the
  ERP integration clients. Copied with SQLite's online backup API, which produces a
  consistent snapshot while the application keeps writing; copying the file directly
  can capture a half-written page, and with WAL enabled it silently misses everything
  still in the -wal file.
* **The uploaded files** — the originals every chunk was cut from. Re-indexing needs
  them; the index alone cannot recreate them.
* **The vector collections** — snapshotted through Qdrant's own API, so the copy is
  consistent and restorable into any Qdrant of the same major version. Rebuilding them
  instead means re-embedding every document, which on this hardware takes hours.
* **The `.env` file** — above all `INTEGRATION_MASTER_KEY`. Integration secrets are
  derived from it and stored nowhere, so losing it invalidates every ERP credential
  ever issued. Included by default for that reason; the archive therefore holds secrets
  and must be stored accordingly.

One archive per run, with a manifest of SHA-256 digests and row and point counts. A
backup that has never been restored is a hope, not a backup, so `verify()` opens the
archived database and checks it, and `drill()` restores the vector snapshots into
throw-away collections and counts the points.

Nothing here is shipped anywhere. Archives are written to a local directory and,
optionally, copied to a second one — another disk or a network share inside the
company.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import sqlite3
import tempfile
import time
import zipfile
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

ARCHIVE_PREFIX = "rag-backup-"
STATUS_FILE = "backup_status.json"
#: Tables whose row counts go into the manifest and are checked on verify.
COUNTED_TABLES = ("documents", "chunks", "users", "knowledge_items", "answer_traces",
                  "integration_clients", "question_log")


@dataclass
class BackupPlan:
    """Where things are and where they go. Built from the application settings."""

    database: Path
    uploads: Path
    env_file: Path | None
    qdrant_url: str
    qdrant_api_key: str | None
    collections: list[str]
    destination: Path
    mirror: Path | None = None
    keep_daily: int = 7
    keep_weekly: int = 4
    status_dir: Path | None = None

    @classmethod
    def from_settings(cls, settings, root: Path) -> BackupPlan:
        database = Path(settings.database_url.replace("sqlite:///", "", 1))
        destination = Path(settings.backup_dir)
        if not destination.is_absolute():
            destination = root / destination
        mirror = Path(settings.backup_mirror_dir) if settings.backup_mirror_dir else None
        env = root / ".env"
        return cls(
            database=database,
            uploads=Path(settings.upload_dir),
            env_file=env if settings.backup_include_env and env.exists() else None,
            qdrant_url=settings.qdrant_url.rstrip("/"),
            qdrant_api_key=settings.qdrant_api_key,
            collections=[settings.qdrant_collection, settings.qdrant_knowledge_collection],
            destination=destination,
            mirror=mirror,
            keep_daily=settings.backup_keep_daily,
            keep_weekly=settings.backup_keep_weekly,
            status_dir=database.parent / "logs",
        )


@dataclass
class BackupResult:
    archive: Path
    size: int
    seconds: float
    manifest: dict = field(default_factory=dict)
    mirrored_to: Path | None = None
    pruned: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _counts(database: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    with closing(sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)) as connection:
        present = {r[0] for r in connection.execute("select name from sqlite_master where type='table'")}
        for table in COUNTED_TABLES:
            if table in present:
                counts[table] = connection.execute(f"select count(*) from {table}").fetchone()[0]
    return counts


def _qdrant(plan: BackupPlan) -> httpx.Client:
    headers = {"api-key": plan.qdrant_api_key} if plan.qdrant_api_key else {}
    return httpx.Client(base_url=plan.qdrant_url, headers=headers, timeout=600)


def _points(client: httpx.Client, collection: str) -> int | None:
    response = client.get(f"/collections/{collection}")
    if response.status_code == 404:
        return None
    response.raise_for_status()
    result = response.json()["result"]
    return result.get("points_count") or result.get("vectors_count") or 0


def _write_status(plan: BackupPlan, **fields) -> None:
    if plan.status_dir is None:
        return
    plan.status_dir.mkdir(parents=True, exist_ok=True)
    path = plan.status_dir / STATUS_FILE
    try:
        current = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, ValueError):
        current = {}
    current.update(fields)
    path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")


def read_status(status_dir: Path) -> dict:
    """The last recorded outcome, for the health report. Empty if never run."""
    path = status_dir / STATUS_FILE
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, ValueError):
        return {}


# ---------------------------------------------------------------------------
# backup
# ---------------------------------------------------------------------------


def run_backup(plan: BackupPlan) -> BackupResult:
    started = time.perf_counter()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    _write_status(plan, last_attempt=datetime.now().isoformat(timespec="seconds"))
    plan.destination.mkdir(parents=True, exist_ok=True)

    try:
        with tempfile.TemporaryDirectory(prefix="rag-backup-") as scratch:
            work = Path(scratch)
            files: dict[str, Path] = {}

            # 1. database, through the online backup API
            db_copy = work / "rag.db"
            with closing(sqlite3.connect(plan.database.as_posix())) as source, \
                    closing(sqlite3.connect(db_copy.as_posix())) as target:
                source.backup(target)
            with closing(sqlite3.connect(db_copy.as_posix())) as check:
                verdict = check.execute("pragma integrity_check").fetchone()[0]
            if verdict != "ok":
                raise RuntimeError(f"database copy failed integrity check: {verdict}")
            files["db/rag.db"] = db_copy

            # 2. uploaded originals
            if plan.uploads.exists():
                for path in sorted(plan.uploads.rglob("*")):
                    if path.is_file():
                        files[f"uploads/{path.relative_to(plan.uploads).as_posix()}"] = path

            # 3. vector collections, as Qdrant snapshots
            points: dict[str, int] = {}
            with _qdrant(plan) as client:
                for collection in plan.collections:
                    count = _points(client, collection)
                    if count is None:
                        logger.info("Collection %s does not exist; skipped", collection)
                        continue
                    created = client.post(f"/collections/{collection}/snapshots", params={"wait": "true"})
                    created.raise_for_status()
                    name = created.json()["result"]["name"]
                    target = work / f"{collection}.snapshot"
                    try:
                        with client.stream("GET", f"/collections/{collection}/snapshots/{name}") as stream:
                            stream.raise_for_status()
                            with target.open("wb") as handle:
                                for chunk in stream.iter_bytes(1 << 20):
                                    handle.write(chunk)
                    finally:
                        # The snapshot also sits inside the container's volume; left there,
                        # every nightly run would add another copy until the disk filled.
                        client.delete(f"/collections/{collection}/snapshots/{name}")
                    files[f"qdrant/{collection}.snapshot"] = target
                    points[collection] = count

            # 4. configuration, for the integration master key
            if plan.env_file is not None:
                files["config/.env"] = plan.env_file

            manifest = {
                "created": datetime.now().isoformat(timespec="seconds"),
                "format": 1,
                "tables": _counts(db_copy),
                "points": points,
                "includes_env": plan.env_file is not None,
                "files": {name: {"sha256": _sha256(path), "size": path.stat().st_size}
                          for name, path in files.items()},
            }

            archive = plan.destination / f"{ARCHIVE_PREFIX}{stamp}.zip"
            partial = archive.with_suffix(".zip.partial")
            with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
                bundle.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
                for name, path in files.items():
                    bundle.write(path, name)
            partial.replace(archive)  # never leave a half-written archive under the real name

        result = BackupResult(archive=archive, size=archive.stat().st_size,
                              seconds=time.perf_counter() - started, manifest=manifest)

        if plan.mirror is not None:
            plan.mirror.mkdir(parents=True, exist_ok=True)
            mirrored = plan.mirror / archive.name
            shutil.copy2(archive, mirrored)
            if _sha256(mirrored) != _sha256(archive):
                raise RuntimeError(f"mirror copy at {mirrored} does not match the archive")
            result.mirrored_to = mirrored
            result.pruned += prune(plan.mirror, plan.keep_daily, plan.keep_weekly)

        result.pruned += prune(plan.destination, plan.keep_daily, plan.keep_weekly)
        _write_status(
            plan,
            last_success=manifest["created"], last_error="", last_archive=str(archive),
            last_size=result.size, last_seconds=round(result.seconds, 1),
            last_mirror=str(result.mirrored_to or ""), tables=manifest["tables"],
            points=manifest["points"],
        )
        return result
    except Exception as exc:
        _write_status(plan, last_error=f"{type(exc).__name__}: {exc}"[:400],
                      last_error_at=datetime.now().isoformat(timespec="seconds"))
        raise


def _archive_time(path: Path) -> datetime | None:
    try:
        return datetime.strptime(path.stem.removeprefix(ARCHIVE_PREFIX), "%Y%m%d-%H%M%S")
    except ValueError:
        return None


def prune(directory: Path, keep_daily: int, keep_weekly: int) -> list[str]:
    """Keep the newest archive of each of the last `keep_daily` days and of each of the
    last `keep_weekly` weeks before them. Delete the rest. Returns the names deleted.

    Only files this module wrote — the fixed prefix and a parseable timestamp — are ever
    considered, so a directory shared with anything else is safe.
    """
    archives = sorted(
        ((when, path) for path in directory.glob(f"{ARCHIVE_PREFIX}*.zip")
         if (when := _archive_time(path)) is not None),
        reverse=True,
    )
    keep: set[Path] = set()
    days: list = []
    weeks: list = []
    for when, path in archives:
        day = when.date()
        if day not in days and len(days) < keep_daily:
            days.append(day)
            keep.add(path)
            continue
        week = when.isocalendar()[:2]
        if day in days:
            continue
        if week not in weeks and len(weeks) < keep_weekly:
            weeks.append(week)
            keep.add(path)
    removed = []
    for _, path in archives:
        if path not in keep:
            path.unlink(missing_ok=True)
            removed.append(path.name)
    return removed


def latest_archive(directory: Path) -> Path | None:
    archives = [p for p in directory.glob(f"{ARCHIVE_PREFIX}*.zip") if _archive_time(p)]
    return max(archives, key=_archive_time) if archives else None


# ---------------------------------------------------------------------------
# verify and restore
# ---------------------------------------------------------------------------


def verify(archive: Path) -> dict:
    """Every digest matches, and the archived database opens, passes its integrity
    check and holds the rows the manifest says it held. Raises on any mismatch."""
    report: dict = {"archive": str(archive)}
    with zipfile.ZipFile(archive) as bundle:
        manifest = json.loads(bundle.read("manifest.json"))
        for name, expected in manifest["files"].items():
            digest = hashlib.sha256()
            with bundle.open(name) as member:
                for block in iter(lambda: member.read(1 << 20), b""):
                    digest.update(block)
            if digest.hexdigest() != expected["sha256"]:
                raise RuntimeError(f"{name}: digest mismatch — the archive is damaged")
        report["files_checked"] = len(manifest["files"])

        with tempfile.TemporaryDirectory(prefix="rag-verify-") as scratch:
            database = Path(scratch) / "rag.db"
            with bundle.open("db/rag.db") as member, database.open("wb") as handle:
                shutil.copyfileobj(member, handle)
            with closing(sqlite3.connect(database.as_posix())) as connection:
                verdict = connection.execute("pragma integrity_check").fetchone()[0]
            if verdict != "ok":
                raise RuntimeError(f"archived database fails integrity check: {verdict}")
            counts = _counts(database)
    if counts != manifest["tables"]:
        raise RuntimeError(f"row counts {counts} differ from manifest {manifest['tables']}")
    report.update(created=manifest["created"], tables=counts, points=manifest["points"],
                  includes_env=manifest["includes_env"])
    return report


def _upload_snapshot(client: httpx.Client, collection: str, snapshot: Path) -> None:
    with snapshot.open("rb") as handle:
        response = client.post(
            f"/collections/{collection}/snapshots/upload",
            params={"priority": "snapshot", "wait": "true"},
            files={"snapshot": (snapshot.name, handle, "application/octet-stream")},
        )
    response.raise_for_status()


def drill(archive: Path, plan: BackupPlan, suffix: str = "__restore_drill") -> dict:
    """Restore the vector snapshots into throw-away collections, count, and delete them.

    Proves the snapshots restore into this Qdrant without touching the live
    collections. The database half of the drill is `verify()`.
    """
    results: dict[str, dict] = {}
    with zipfile.ZipFile(archive) as bundle, tempfile.TemporaryDirectory(prefix="rag-drill-") as scratch, \
            _qdrant(plan) as client:
        manifest = json.loads(bundle.read("manifest.json"))
        for collection, expected in manifest["points"].items():
            snapshot = Path(scratch) / f"{collection}.snapshot"
            with bundle.open(f"qdrant/{collection}.snapshot") as member, snapshot.open("wb") as handle:
                shutil.copyfileobj(member, handle)
            trial = f"{collection}{suffix}"
            try:
                _upload_snapshot(client, trial, snapshot)
                restored = _points(client, trial)
            finally:
                client.delete(f"/collections/{trial}")
            results[collection] = {"expected": expected, "restored": restored,
                                   "ok": restored == expected}
    if not all(r["ok"] for r in results.values()):
        raise RuntimeError(f"restore drill point counts differ: {results}")
    return results


def restore(archive: Path, plan: BackupPlan, root: Path) -> dict:
    """Replace the live data with the archive. The application must be stopped.

    The current files are moved aside, never deleted, into `data/pre-restore-<time>/`,
    so a restore of the wrong archive can itself be undone.
    """
    verify(archive)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    aside = plan.database.parent / f"pre-restore-{stamp}"
    aside.mkdir(parents=True)
    for suffix in ("", "-wal", "-shm"):
        current = Path(f"{plan.database}{suffix}")
        if current.exists():
            shutil.move(str(current), aside / current.name)
    if plan.uploads.exists():
        shutil.move(str(plan.uploads), aside / "uploads")

    report: dict = {"moved_aside": str(aside)}
    with zipfile.ZipFile(archive) as bundle, tempfile.TemporaryDirectory(prefix="rag-restore-") as scratch:
        manifest = json.loads(bundle.read("manifest.json"))
        with bundle.open("db/rag.db") as member, plan.database.open("wb") as handle:
            shutil.copyfileobj(member, handle)
        plan.uploads.mkdir(parents=True, exist_ok=True)
        restored_files = 0
        for name in manifest["files"]:
            if name.startswith("uploads/"):
                target = plan.uploads / name.removeprefix("uploads/")
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(name) as member, target.open("wb") as handle:
                    shutil.copyfileobj(member, handle)
                restored_files += 1
        report["uploads"] = restored_files

        with _qdrant(plan) as client:
            for collection in manifest["points"]:
                snapshot = Path(scratch) / f"{collection}.snapshot"
                with bundle.open(f"qdrant/{collection}.snapshot") as member, snapshot.open("wb") as handle:
                    shutil.copyfileobj(member, handle)
                _upload_snapshot(client, collection, snapshot)
                report[f"points:{collection}"] = _points(client, collection)

        if manifest.get("includes_env"):
            # Written beside the live file, never over it: which key to run with is an
            # operator's decision, and overwriting silently could lock out every client
            # issued since the backup was taken.
            target = root / f".env.restored-{stamp}"
            target.write_bytes(bundle.read("config/.env"))
            report["env"] = str(target)
    report["tables"] = _counts(plan.database)
    return report


def backup_age(status: dict) -> timedelta | None:
    last = status.get("last_success")
    if not last:
        return None
    try:
        return datetime.now() - datetime.fromisoformat(last)
    except ValueError:
        return None
