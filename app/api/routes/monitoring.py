"""Monitoring: the administrator's dashboard, and a Prometheus scrape endpoint."""

from __future__ import annotations

import hmac
import shutil
from pathlib import Path

from fastapi import APIRouter, Query, Request
from fastapi.responses import PlainTextResponse

from app.api.deps import ContainerDep, SettingsDep, get_auth_service, get_current_user, require
from app.config import BASE_DIR
from app.core.permissions import Permission, permissions_for
from app.exceptions import AuthorizationError
from app.models.auth import User
from app.models.database import SessionLocal
from app.services.backup import backup_age, read_status

router = APIRouter(prefix="/api", tags=["monitoring"])

#: Below this much free space the next backup or upload may fail.
LOW_DISK_BYTES = 5 * 1024**3


def _backup_state(settings) -> dict:
    status = read_status(BASE_DIR / "data" / "logs")
    age = backup_age(status)
    hours = round(age.total_seconds() / 3600, 1) if age is not None else None
    return {
        "last_success": status.get("last_success"),
        "age_hours": hours,
        "stale": hours is None or hours > settings.backup_max_age_hours,
        "last_error": status.get("last_error", ""),
        "last_error_at": status.get("last_error_at"),
        "last_size": status.get("last_size"),
        "mirrored": bool(status.get("last_mirror")),
        "mirror_configured": bool(settings.backup_mirror_dir),
    }


def _disk_state() -> dict:
    usage = shutil.disk_usage(Path(BASE_DIR))
    return {"free_gb": round(usage.free / 1024**3, 1), "total_gb": round(usage.total / 1024**3, 1),
            "low": usage.free < LOW_DISK_BYTES}


def _reranker_state(container) -> dict:
    reranker = container.reranker
    scorer = getattr(reranker, "scorer", None)
    state = {"name": reranker.name}
    if scorer is not None:
        state.update(scorer.status.as_dict())
    return state


def operational_alerts(container, settings) -> list[dict]:
    """Alerts about the installation rather than the traffic."""
    alerts: list[dict] = []
    backup = _backup_state(settings)
    if backup["last_success"] is None:
        alerts.append({"level": "critical", "key": "backup_missing",
                       "message": "لم يُسجَّل أي نسخ احتياطي ناجح"})
    elif backup["stale"]:
        alerts.append({"level": "critical", "key": "backup_stale",
                       "message": f"آخر نسخة احتياطية ناجحة قبل {backup['age_hours']} ساعة"})
    if backup["last_error"] and (backup["last_error_at"] or "") > (backup["last_success"] or ""):
        alerts.append({"level": "critical", "key": "backup_failed",
                       "message": f"فشل آخر نسخ احتياطي: {backup['last_error'][:120]}"})
    if not backup["mirror_configured"]:
        alerts.append({"level": "warning", "key": "backup_single_disk",
                       "message": "النسخ الاحتياطية على نفس قرص البيانات — عيّن BACKUP_MIRROR_DIR"})
    if _disk_state()["low"]:
        alerts.append({"level": "warning", "key": "disk",
                       "message": "المساحة الحرة على القرص أقل من 5 جيجابايت"})
    rerank = _reranker_state(container)
    if rerank.get("error"):
        alerts.append({"level": "warning", "key": "reranker",
                       "message": f"نموذج إعادة الترتيب غير متاح، يُستخدم الترتيب العادي: {rerank['error'][:100]}"})
    return alerts


@router.get("/admin/reports/latest")
def latest_report(_user: User = require(Permission.SYSTEM_MONITOR)) -> dict:
    """The report the nightly review wrote last (scripts/nightly_review.py)."""
    latest = BASE_DIR / "data" / "reports" / "latest.md"
    if not latest.exists():
        return {"written_at": "", "markdown": ""}
    from datetime import datetime
    written = datetime.fromtimestamp(latest.stat().st_mtime).isoformat(timespec="minutes")
    return {"written_at": written, "markdown": latest.read_text(encoding="utf-8")}


@router.get("/admin/monitoring")
def monitoring(
    container: ContainerDep,
    settings: SettingsDep,
    days: int = Query(7, ge=1, le=90),
    _user: User = require(Permission.SYSTEM_MONITOR),
) -> dict:
    components = {
        "generation": container.llm.health(),
        "embeddings": container.embedder.health(),
        "vectors": container.vector_store.health(),
        "reranker": _reranker_state(container),
        "backup": _backup_state(settings),
        "disk": _disk_state(),
    }
    summary = container.metrics.summarize(days, extra_alerts=operational_alerts(container, settings))
    for name in ("generation", "embeddings", "vectors"):
        if not components[name].get("reachable"):
            summary["alerts"].insert(0, {"level": "critical", "key": f"{name}_down",
                                         "message": f"المكوّن غير متاح: {name}"})
    return {"components": components, "traffic": summary,
            "model": settings.ollama_model}


@router.get("/metrics", response_class=PlainTextResponse)
def prometheus(request: Request, container: ContainerDep, settings: SettingsDep) -> str:
    """Prometheus exposition. A configured METRICS_TOKEN, or an administrator's session."""
    header = request.headers.get("authorization", "")
    token = header[7:].strip() if header.lower().startswith("bearer ") else ""
    allowed = bool(settings.metrics_token) and hmac.compare_digest(token, settings.metrics_token)
    if not allowed:
        with SessionLocal() as session:
            user = get_current_user(request, session, get_auth_service(container), settings)
        if Permission.SYSTEM_MONITOR not in permissions_for(user.role):
            raise AuthorizationError("لا تملك صلاحية 'system.monitor'.")

    backup = _backup_state(settings)
    extra = {
        "rag_backup_age_hours": backup["age_hours"] if backup["age_hours"] is not None else -1,
        "rag_disk_free_gb": _disk_state()["free_gb"],
    }
    scorer = getattr(container.reranker, "scorer", None)
    if scorer is not None:
        extra["rag_reranker_fallbacks_total"] = scorer.status.fallbacks
    return container.metrics.prometheus(extra)
