from __future__ import annotations

import json
import re
from datetime import datetime
from urllib.parse import quote
from difflib import SequenceMatcher
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import func
from sqlmodel import select

from app import crud
from app.db import session_scope
from app.models import BackupRecord, Device
from app.platforms import normalize_platform_id
from app.routers.common import _current_user, _dt_local_str, _layout_context, _log_action, _require_permission, templates


router = APIRouter(tags=["备份管理 (Backups)"])


@router.get("/api/tasks/backups", summary="获取备份任务", description="查询正在执行或历史备份任务")
def api_tasks_backups(request: Request, ids: str = "", limit: int = 20):
    _require_permission(request, "backups.view")
    offset_minutes = int(getattr(request.state, "tz_offset_minutes", 0))
    limit = max(1, min(int(limit or 20), 200))
    wanted: list[UUID] = []
    for raw in (ids or "").split(","):
        v = raw.strip()
        if not v:
            continue
        try:
            wanted.append(UUID(v))
        except Exception:
            continue

    with session_scope() as session:
        items: dict[str, dict[str, Any]] = {}

        def _add(rows) -> None:
            for rec, dev in rows:
                items[str(rec.id)] = {
                    "id": str(rec.id),
                    "device": {
                        "id": int(dev.id) if dev and dev.id else int(rec.device_id),
                        "name": dev.name if dev else f"device-{rec.device_id}",
                        "host": dev.host if dev else "",
                    },
                    "started_at": _dt_local_str(rec.started_at, offset_minutes=offset_minutes),
                    "finished_at": _dt_local_str(rec.finished_at, offset_minutes=offset_minutes) if rec.finished_at else None,
                    "success": bool(rec.success),
                    "error_message": rec.error_message or "",
                }

        if wanted:
            stmt = (
                select(BackupRecord, Device)
                .join(Device, Device.id == BackupRecord.device_id, isouter=True)
                .where(BackupRecord.id.in_(wanted))
            )
            _add(session.exec(stmt).all())

        running_stmt = (
            select(BackupRecord, Device)
            .join(Device, Device.id == BackupRecord.device_id, isouter=True)
            .where(BackupRecord.finished_at.is_(None))
            .order_by(BackupRecord.started_at.desc())
            .limit(100)
        )
        _add(session.exec(running_stmt).all())

        recent_stmt = (
            select(BackupRecord, Device)
            .join(Device, Device.id == BackupRecord.device_id, isouter=True)
            .where(BackupRecord.finished_at.is_not(None))
            .order_by(BackupRecord.started_at.desc())
            .limit(limit)
        )
        _add(session.exec(recent_stmt).all())

    ordered = sorted(items.values(), key=lambda x: x.get("started_at") or "", reverse=True)
    running_count = sum(1 for x in ordered if not x.get("finished_at"))
    return {"items": ordered, "running": running_count}


@router.get("/api/tasks/celery", summary="获取Celery任务", description="查询异步后台任务状态")
def api_tasks_celery(request: Request, ids: str = ""):
    _require_permission(request, "backups.view")

    task_ids = [v.strip() for v in (ids or "").split(",") if v.strip()]
    if not task_ids:
        return {"enabled": False, "items": []}

    try:
        from celery.result import AsyncResult

        from app.celery_app import celery_app
        from app.celery_tasks import celery_enabled

        if not celery_enabled():
            return {"enabled": False, "items": [{"id": tid, "state": "DISABLED"} for tid in task_ids]}

        items: list[dict[str, object]] = []
        for tid in task_ids:
            ar = AsyncResult(str(tid), app=celery_app)
            items.append(
                {
                    "id": str(tid),
                    "state": str(ar.state),
                    "ready": bool(ar.ready()),
                    "successful": bool(ar.successful()) if ar.ready() else False,
                    "failed": bool(ar.failed()) if ar.ready() else False,
                }
            )
        return {"enabled": True, "items": items}
    except Exception:
        return {"enabled": False, "items": [{"id": tid, "state": "UNKNOWN"} for tid in task_ids]}


@router.get("/api/devices/{device_id}/backups", summary="获取设备备份记录", description="查询指定设备的历史配置备份")
def api_device_backups(request: Request, device_id: int, page: int = 1, limit: int = 10):
    _require_permission(request, "backups.view")
    
    page = max(1, page)
    limit = max(1, min(limit, 200))
    offset = (page - 1) * limit
    
    offset_minutes = int(getattr(request.state, "tz_offset_minutes", 0))
    with session_scope() as session:
        device = crud.get_device(session, device_id)
        if device is None:
            raise HTTPException(status_code=404)
        
        total = crud.count_device_backups(session, device_id)
        backups = crud.list_device_backups(session, device_id, limit=limit, offset=offset)
        
        device_data = {
            "id": device.id,
            "name": device.name,
            "host": device.host,
            "platform": device.platform,
            "backups": [
                {
                    "id": str(b.id),
                    "started_at": _dt_local_str(b.started_at, offset_minutes=offset_minutes),
                    "finished_at": _dt_local_str(b.finished_at, offset_minutes=offset_minutes) if b.finished_at else None,
                    "success": b.success,
                    "error_message": b.error_message,
                    "config_snapshot_hash": b.config_snapshot_hash,
                }
                for b in backups
            ],
            "pagination": {
                "total": total,
                "page": page,
                "limit": limit,
                "total_pages": (total + limit - 1) // limit
            }
        }
    return device_data


@router.get("/api/backups/{backup_id}", summary="获取备份详情", description="查询指定备份记录的详细内容")
def api_backup_view(request: Request, backup_id: UUID):
    _require_permission(request, "backups.view")
    offset_minutes = int(getattr(request.state, "tz_offset_minutes", 0))
    with session_scope() as session:
        record = crud.get_backup(session, backup_id)
        if record is None:
            raise HTTPException(status_code=404)
        device = crud.get_device(session, record.device_id)
        
        device_data = {
            "id": int(device.id or 0) if device else int(record.device_id),
            "name": device.name if device else "",
            "host": device.host if device else "",
            "port": int(device.port) if device else 0,
            "platform": device.platform if device else "",
        }
        record_data = {
            "id": str(record.id),
            "device_id": int(record.device_id),
            "started_at": _dt_local_str(record.started_at, offset_minutes=offset_minutes),
            "finished_at": _dt_local_str(record.finished_at, offset_minutes=offset_minutes) if record.finished_at else None,
            "success": bool(record.success),
            "error_message": record.error_message or "",
            "config_text": record.config_text or "",
        }

    return {
        "device": device_data,
        "record": record_data,
    }


DEFAULT_NOISE_RULES = [
    r"^!.*last configuration",
    r"^ntp clock-period",
    r"^!.*NVRAM config last updated",
]
DEFAULT_DIFF_RULES = [
    {"scope": "global", "targets": [], "patterns": DEFAULT_NOISE_RULES},
]
DIFF_RULES_SETTING_KEY = "diff_ignore_rules"
NOISE_PATTERNS = [re.compile(p, re.IGNORECASE) for p in DEFAULT_NOISE_RULES]

def _normalize_rule_targets(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    if isinstance(value, (list, tuple, set)):
        out = []
        for item in value:
            v = str(item).strip()
            if v:
                out.append(v)
        return out
    return []


def _normalize_rule_patterns(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [v.strip() for v in value.splitlines() if v.strip()]
    if isinstance(value, (list, tuple, set)):
        out = []
        for item in value:
            v = str(item).strip()
            if v:
                out.append(v)
        return out
    return []


def _normalize_diff_rules(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    out: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        scope = str(item.get("scope") or "global").strip().lower()
        if scope not in {"global", "platform", "group"}:
            continue
        targets = _normalize_rule_targets(item.get("targets"))
        patterns = _normalize_rule_patterns(item.get("patterns"))
        if scope != "global" and not targets:
            continue
        if not patterns:
            continue
        if scope == "global":
            targets = []
        out.append({"scope": scope, "targets": targets, "patterns": patterns})
    return out


def _load_diff_rules(session) -> list[dict[str, Any]]:
    raw = crud.get_setting(session, key=DIFF_RULES_SETTING_KEY)
    if not raw:
        return DEFAULT_DIFF_RULES
    try:
        data = json.loads(raw)
    except Exception:
        return DEFAULT_DIFF_RULES
    normalized = _normalize_diff_rules(data)
    return normalized or DEFAULT_DIFF_RULES


def _collect_rule_patterns(rules: list[dict[str, Any]], device: Device | None) -> list[str]:
    patterns: list[str] = []
    device_platform = normalize_platform_id(device.platform) if device else ""
    device_group = str(device.group_id) if device and device.group_id else ""
    for rule in rules:
        scope = rule.get("scope")
        if scope == "global":
            patterns.extend(rule.get("patterns") or [])
            continue
        targets = rule.get("targets") or []
        if scope == "platform":
            target_set = {normalize_platform_id(t) for t in targets}
            if device_platform and device_platform in target_set:
                patterns.extend(rule.get("patterns") or [])
        elif scope == "group":
            if device_group and device_group in {str(t) for t in targets}:
                patterns.extend(rule.get("patterns") or [])
    return patterns


def _compile_noise_patterns(patterns: list[str]) -> list[re.Pattern[str]]:
    out: list[re.Pattern[str]] = []
    for raw in patterns:
        try:
            out.append(re.compile(raw, re.IGNORECASE))
        except re.error:
            continue
    return out


def _build_noise_patterns(rules: list[dict[str, Any]], device_a: Device | None, device_b: Device | None) -> list[re.Pattern[str]]:
    combined: list[str] = []
    combined.extend(_collect_rule_patterns(rules, device_a))
    combined.extend(_collect_rule_patterns(rules, device_b))
    seen = set()
    uniq: list[str] = []
    for p in combined:
        if p in seen:
            continue
        seen.add(p)
        uniq.append(p)
    return _compile_noise_patterns(uniq)


def _normalize_lines(
    text: str | None,
    *,
    ignore_noise_lines: bool = False,
    noise_patterns: list[re.Pattern[str]] | None = None,
) -> list[str]:
    lines = (text or "").splitlines()

    if ignore_noise_lines:
        patterns = noise_patterns if noise_patterns is not None else NOISE_PATTERNS
        if not patterns:
            return lines
        filtered = []
        for ln in lines:
            stripped = ln.strip()
            is_noise = False
            for p in patterns:
                if p.search(stripped):
                    is_noise = True
                    break
            if not is_noise:
                filtered.append(ln)
        return filtered

    return lines


def _build_unified_diff_payload(
    a_lines: list[str],
    b_lines: list[str],
    *,
    only_changed_lines: bool,
    context_lines: int = 2,
) -> list[dict[str, Any]]:
    sm = SequenceMatcher(a=a_lines, b=b_lines)
    full: list[dict[str, Any]] = []
    a_ln = 1
    b_ln = 1
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            count = i2 - i1
            for k in range(count):
                full.append({"type": "context", "a_lineno": a_ln, "b_lineno": b_ln, "text": a_lines[i1 + k]})
                a_ln += 1
                b_ln += 1
            continue

        if tag in {"delete", "replace"}:
            for k in range(i2 - i1):
                full.append({"type": "add", "a_lineno": a_ln, "b_lineno": None, "text": a_lines[i1 + k]})
                a_ln += 1
        if tag in {"insert", "replace"}:
            for k in range(j2 - j1):
                full.append({"type": "del", "a_lineno": None, "b_lineno": b_ln, "text": b_lines[j1 + k]})
                b_ln += 1

    if not only_changed_lines:
        return full

    context = max(0, min(int(context_lines or 0), 20))
    keep = [False] * len(full)
    for idx, row in enumerate(full):
        if row.get("type") in {"add", "del"}:
            lo = max(0, idx - context)
            hi = min(len(full) - 1, idx + context)
            for j in range(lo, hi + 1):
                keep[j] = True

    out: list[dict[str, Any]] = []
    skipped = 0
    for idx, row in enumerate(full):
        if keep[idx]:
            if skipped:
                out.append({"type": "skip", "count": skipped})
                skipped = 0
            out.append(row)
            continue
        if row.get("type") == "context":
            skipped += 1
    if skipped:
        out.append({"type": "skip", "count": skipped})
    return out


def _build_split_diff_payload(
    a_lines: list[str],
    b_lines: list[str],
    *,
    only_changed_lines: bool,
    context_lines: int = 2,
) -> list[dict[str, Any]]:
    sm = SequenceMatcher(a=a_lines, b=b_lines)
    full: list[dict[str, Any]] = []
    a_ln = 1
    b_ln = 1
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            count = i2 - i1
            for k in range(count):
                full.append(
                    {
                        "type": "row",
                        "a": {"lineno": a_ln, "text": a_lines[i1 + k], "kind": "context"},
                        "b": {"lineno": b_ln, "text": b_lines[j1 + k], "kind": "context"},
                    }
                )
                a_ln += 1
                b_ln += 1
            continue

        if tag == "delete":
            for k in range(i2 - i1):
                full.append(
                    {
                        "type": "row",
                        "a": {"lineno": a_ln, "text": a_lines[i1 + k], "kind": "add"},
                        "b": {"lineno": None, "text": "", "kind": "empty"},
                    }
                )
                a_ln += 1
            continue

        if tag == "insert":
            for k in range(j2 - j1):
                full.append(
                    {
                        "type": "row",
                        "a": {"lineno": None, "text": "", "kind": "empty"},
                        "b": {"lineno": b_ln, "text": b_lines[j1 + k], "kind": "del"},
                    }
                )
                b_ln += 1
            continue

        a_chunk = a_lines[i1:i2]
        b_chunk = b_lines[j1:j2]
        rows = max(len(a_chunk), len(b_chunk))
        for idx in range(rows):
            has_a = idx < len(a_chunk)
            has_b = idx < len(b_chunk)
            a_text = a_chunk[idx] if has_a else ""
            b_text = b_chunk[idx] if has_b else ""

            if has_a and has_b:
                a_info = {"lineno": a_ln, "text": a_text, "kind": "chg"}
                b_info = {"lineno": b_ln, "text": b_text, "kind": "chg"}
            elif has_a:
                a_info = {"lineno": a_ln, "text": a_text, "kind": "add"}
                b_info = {"lineno": None, "text": "", "kind": "empty"}
            else:
                a_info = {"lineno": None, "text": "", "kind": "empty"}
                b_info = {"lineno": b_ln, "text": b_text, "kind": "del"}

            full.append({"type": "row", "a": a_info, "b": b_info})
            if has_a:
                a_ln += 1
            if has_b:
                b_ln += 1

    if not only_changed_lines:
        return full

    context = max(0, min(int(context_lines or 0), 20))
    keep = [False] * len(full)
    for idx, row in enumerate(full):
        if row.get("type") != "row":
            continue
        a_kind = (row.get("a") or {}).get("kind")
        b_kind = (row.get("b") or {}).get("kind")
        if a_kind in {"add", "del", "chg"} or b_kind in {"add", "del", "chg"}:
            lo = max(0, idx - context)
            hi = min(len(full) - 1, idx + context)
            for j in range(lo, hi + 1):
                keep[j] = True

    out: list[dict[str, Any]] = []
    skipped = 0
    for idx, row in enumerate(full):
        if keep[idx]:
            if skipped:
                out.append({"type": "skip", "count": skipped})
                skipped = 0
            out.append(row)
            continue
        if row.get("type") == "row":
            a_kind = (row.get("a") or {}).get("kind")
            b_kind = (row.get("b") or {}).get("kind")
            if a_kind == "context" and b_kind == "context":
                skipped += 1
    if skipped:
        out.append({"type": "skip", "count": skipped})
    return out


@router.get("/api/backups/{backup_id}/diff/{other_id}", summary="对比备份差异", description="对比两次设备配置备份的差异")
def api_backup_diff(
    request: Request,
    backup_id: UUID,
    other_id: UUID,
    mode: str = "unified",
    only_changed_lines: int = 1,
    ignore_noise_lines: int = 0,
    context_lines: int = 2,
):
    _require_permission(request, "backups.view")
    offset_minutes = int(getattr(request.state, "tz_offset_minutes", 0))
    mode = (mode or "unified").strip().lower()
    if mode not in {"unified", "split"}:
        mode = "unified"
    only_changed = bool(int(only_changed_lines or 0))
    ignore_noise = bool(int(ignore_noise_lines or 0))
    with session_scope() as session:
        a = crud.get_backup(session, backup_id)
        b = crud.get_backup(session, other_id)
        if a is None or b is None:
            raise HTTPException(status_code=404)

        device_a = crud.get_device(session, a.device_id)
        device_b = crud.get_device(session, b.device_id)
        diff_rules = _load_diff_rules(session)

    if a.started_at and b.started_at and a.started_at < b.started_at:
        a, b = b, a
        device_a, device_b = device_b, device_a

    noise_patterns = _build_noise_patterns(diff_rules, device_a, device_b) if ignore_noise else None
    a_lines = _normalize_lines(
        a.config_text,
        ignore_noise_lines=ignore_noise,
        noise_patterns=noise_patterns,
    )
    b_lines = _normalize_lines(
        b.config_text,
        ignore_noise_lines=ignore_noise,
        noise_patterns=noise_patterns,
    )

    payload: dict[str, Any] = {
        "mode": mode,
        "only_changed_lines": only_changed,
        "ignore_noise_lines": ignore_noise,
        "device_a": {
            "id": int(device_a.id or 0) if device_a else int(a.device_id),
            "name": device_a.name if device_a else f"device-{a.device_id}",
            "host": device_a.host if device_a else "",
            "port": device_a.port if device_a else 0,
            "platform": device_a.platform if device_a else "",
        },
        "device_b": {
            "id": int(device_b.id or 0) if device_b else int(b.device_id),
            "name": device_b.name if device_b else f"device-{b.device_id}",
            "host": device_b.host if device_b else "",
            "port": device_b.port if device_b else 0,
            "platform": device_b.platform if device_b else "",
        },
        "device": {
            "id": int(device_a.id or 0) if device_a else int(a.device_id),
            "name": device_a.name if device_a else f"device-{a.device_id}",
            "host": device_a.host if device_a else "",
            "port": device_a.port if device_a else 0,
            "platform": device_a.platform if device_a else "",
        },
        "a": {
            "id": str(a.id),
            "started_at": _dt_local_str(a.started_at, offset_minutes=offset_minutes),
            "finished_at": _dt_local_str(a.finished_at, offset_minutes=offset_minutes) if a.finished_at else None,
            "success": bool(a.success),
        },
        "b": {
            "id": str(b.id),
            "started_at": _dt_local_str(b.started_at, offset_minutes=offset_minutes),
            "finished_at": _dt_local_str(b.finished_at, offset_minutes=offset_minutes) if b.finished_at else None,
            "success": bool(b.success),
        },
    }

    if mode == "split":
        payload["rows"] = _build_split_diff_payload(
            a_lines,
            b_lines,
            only_changed_lines=only_changed,
            context_lines=context_lines,
        )
    else:
        payload["lines"] = _build_unified_diff_payload(
            a_lines,
            b_lines,
            only_changed_lines=only_changed,
            context_lines=context_lines,
        )
    return payload


@router.get("/diff-rules", summary="Diff规则页面", description="查看配置对比忽略规则")
def diff_rules_page(request: Request):
    _require_permission(request, "diff_rules.view")
    msg = (request.query_params.get("msg") or "").strip()
    err = (request.query_params.get("err") or "").strip()
    with session_scope() as session:
        rules = _load_diff_rules(session)
        groups = [{"id": int(g.id), "name": g.name} for g in crud.list_groups(session) if g.id is not None]
    return templates.TemplateResponse(
        request=request,
        name="diff_rules.html",
        context={
            **_layout_context(request=request, active="diff_rules"),
            "page_title": "Diff 忽略规则",
            "page_subtitle": "配置在对比配置差异时需要忽略的行 (支持正则表达式)",
            "rules": rules,
            "default_rules": DEFAULT_DIFF_RULES,
            "groups": groups,
            "msg": msg,
            "err": err,
        },
    )


@router.post("/diff-rules", summary="创建/更新Diff规则", description="新增或修改对比忽略规则")
def update_diff_rules(request: Request, rules_json: str = Form("")):
    _require_permission(request, "diff_rules.update")
    try:
        payload = json.loads(rules_json or "[]")
    except Exception:
        return RedirectResponse(url="/diff-rules?err=规则解析失败", status_code=303)
    normalized = _normalize_diff_rules(payload)
    with session_scope() as session:
        crud.set_setting(session, key=DIFF_RULES_SETTING_KEY, value=json.dumps(normalized, ensure_ascii=False))
        _log_action(request, session, "UPDATE_DIFF_RULES", "settings", None, f"Rules: {len(normalized)}")
    return RedirectResponse(url="/diff-rules?msg=已保存", status_code=303)


@router.get("/backups", summary="备份历史页面", description="查看全局设备备份记录")
def backups_page(request: Request):
    _require_permission(request, "backups.view")
    with session_scope() as session:
        devices = [d for d in crud.list_devices(session) if d.id]
        count_rows = session.exec(
            select(BackupRecord.device_id, func.count(BackupRecord.id)).group_by(BackupRecord.device_id)
        ).all()
        backup_counts = {int(device_id): int(cnt) for device_id, cnt in count_rows}
        device_rows = [
            {
                "id": int(d.id),
                "name": d.name,
                "host": d.host,
                "port": d.port,
                "platform": d.platform,
                "backup_count": backup_counts.get(int(d.id), 0),
            }
            for d in devices
        ]
    return templates.TemplateResponse(
        request=request,
        name="backups.html",
        context={**_layout_context(request=request, active="backups"), "device_rows": device_rows},
    )


@router.post("/api/backups/{backup_id}/delete", summary="删除备份记录", description="删除指定的设备备份记录")
def api_delete_backup(request: Request, backup_id: UUID):
    _require_permission(request, "backups.delete")
    with session_scope() as session:
        record = crud.get_backup(session, backup_id)
        if record is None:
            raise HTTPException(status_code=404, detail="备份记录不存在")
        device = crud.get_device(session, record.device_id)
        deleted = crud.bulk_delete_backups(session, [backup_id])
        if deleted <= 0:
            raise HTTPException(status_code=404, detail="备份记录不存在")
        device_name = device.name if device and device.name else f"device-{record.device_id}"
        _log_action(request, session, "DELETE_BACKUP", "backup", str(backup_id), f"Device: {device_name}")
    return {"success": True, "deleted": int(deleted), "id": str(backup_id)}


@router.get("/config-search", summary="配置搜索页面", description="全文搜索设备的最新配置内容", tags=["备份管理 (Backups)"])
def config_search_page(
    request: Request,
    q: str = "",
    scope: str = "latest",
):
    _require_permission(request, "config_search.view")

    page_raw = (request.query_params.get("page") or "1").strip()
    page = int(page_raw) if page_raw.isdigit() and int(page_raw) > 0 else 1
    limit_raw = (request.query_params.get("limit") or "50").strip()
    limit = int(limit_raw) if limit_raw.isdigit() and int(limit_raw) > 0 else 50
    if limit > 500:
        limit = 500

    offset = (page - 1) * limit
    latest_only = scope == "latest"

    with session_scope() as session:
        records = crud.search_config(session, q=q, latest_only=latest_only, limit=limit, offset=offset)
        total = crud.count_config_search_results(session, q=q, latest_only=latest_only)

        device_ids = sorted({int(r.device_id) for r in records if r.device_id is not None})
        device_map = {}
        if device_ids:
            devices = session.exec(select(Device).where(Device.id.in_(device_ids))).all()
            device_map = {int(d.id): d for d in devices if d.id is not None}
        grouped_records = {}

        for r in records:
            if r.device_id in device_map:
                if r.device_id not in grouped_records:
                    grouped_records[r.device_id] = []
                grouped_records[r.device_id].append(r)

    total_pages = max(1, (total + limit - 1) // limit)
    pagination_base = f"/config-search?q={q}&scope={scope}&limit={limit}&page="
    if not request.query_params.get("limit"):
         if limit != 10:
             pagination_base = f"/config-search?q={q}&scope={scope}&limit={limit}&page="
         else:
             pagination_base = f"/config-search?q={q}&scope={scope}&page="

    return templates.TemplateResponse(
        request=request,
        name="config_search.html",
        context={
            **_layout_context(request=request, active="config_search"),
            "page_title": "配置搜索",
            "page_subtitle": "在备份配置中搜索关键词",
            "q": q,
            "scope": scope,
            "records": records,
            "total": total,
            "pagination": {
                "page": page,
                "limit": limit,
                "total": total,
                "total_pages": total_pages,
            },
            "pagination_base": pagination_base,
            "device_map": device_map,
            "grouped_records": grouped_records,
        },
    )


@router.get("/tasks", summary="任务页面", description="查看任务状态")
def tasks_page(request: Request):
    return RedirectResponse(url="/backups", status_code=302)


@router.get("/backups/{backup_id}/download", summary="下载备份配置", description="下载指定的设备备份配置文件")
def download_backup(request: Request, backup_id: UUID):
    with session_scope() as session:
        record = crud.get_backup(session, backup_id)
        if record is None:
            raise HTTPException(status_code=404)
        device = crud.get_device(session, record.device_id)
    device_id = record.device_id if record.device_id is not None else 0
    base_name = (device.name or "") if device else f"device-{device_id}"
    host = (device.host or "") if device else ""
    safe_host = "".join(ch for ch in str(host) if ch.isascii() and (ch.isalnum() or ch in ("-", "_", "."))).strip()
    offset_minutes = int(getattr(request.state, "tz_offset_minutes", 0))
    if isinstance(record.started_at, datetime):
        ts = _dt_local_str(record.started_at, offset_minutes=offset_minutes).replace(":", "").replace(" ", "_")
    else:
        ts = "unknown_time"
    if not ts:
        ts = "unknown_time"
    suffix = f"_{safe_host}" if safe_host else ""
    filename_utf8 = f"{base_name}{suffix}_{ts}.txt"
    filename_ascii = "".join(
        ch for ch in str(filename_utf8) if ch.isascii() and (ch.isalnum() or ch in ("-", "_", "."))
    ).strip() or "backup.txt"
    content_disposition = f"attachment; filename=\"{filename_ascii}\"; filename*=UTF-8''{quote(filename_utf8)}"
    if isinstance(record.config_text, bytes):
        config_text = record.config_text.decode("utf-8", errors="replace")
    elif isinstance(record.config_text, str):
        config_text = record.config_text
    else:
        config_text = "" if record.config_text is None else str(record.config_text)
    content_bytes = config_text.encode("utf-8", errors="replace")
    return Response(
        content=content_bytes,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": content_disposition},
    )
