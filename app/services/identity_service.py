from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlmodel import Session

from app import crud
from app.services import pagination_service, resource_service
from app.services.auth import generate_recovery_codes, hash_recovery_code
from app.services.errors import ServiceError


PERMISSION_GROUP_LABELS = {
    "dashboard": "仪表盘",
    "devices": "设备管理",
    "groups": "分组管理",
    "credentials": "凭据管理",
    "templates": "模板管理",
    "backups": "备份历史",
    "config_search": "配置搜索",
    "schedules": "定时任务",
    "audit_logs": "操作日志",
    "webshell_records": "Webshell回放",
    "login_logs": "登录日志",
    "diff_rules": "Diff 规则",
    "notifications": "系统管理",
    "settings": "系统管理",
    "storage_settings": "系统管理",
    "api_keys": "系统管理",
    "users": "用户管理",
    "roles": "角色管理",
}


@dataclass(slots=True)
class UserMutationResult:
    user: Any | None
    action: str
    recovery_codes: list[str]


def ensure_default_roles(session: Session) -> None:
    crud.ensure_default_roles(session)


def is_admin_role_code(code: str | None) -> bool:
    return crud.is_admin_role_code(code)


def get_effective_permission_codes(user) -> set[str]:
    return crud.get_effective_permission_codes(user)


def list_permission_catalog() -> list[dict[str, str]]:
    return crud.list_permission_catalog()


def normalize_permission_codes(codes) -> list[str]:
    return crud.normalize_permission_codes(codes)


def get_user(session: Session, user_id: int):
    return crud.get_user(session, user_id)


def count_users(session: Session) -> int:
    return crud.count_users(session)


def create_user(
    session: Session,
    *,
    username: str,
    password: str,
    role: str,
    password_expired: bool = False,
    group_access_type: str = "all",
    allowed_group_ids: str | None = None,
    mfa_enabled: bool = False,
    mfa_secret: str | None = None,
    enable_watermark: bool = True,
    locale: str = "zh-CN",
):
    try:
        return crud.create_user(
            session,
            username=username,
            password=password,
            role=role,
            password_expired=password_expired,
            group_access_type=group_access_type,
            allowed_group_ids=allowed_group_ids,
            mfa_enabled=mfa_enabled,
            mfa_secret=mfa_secret,
            enable_watermark=enable_watermark,
            locale=locale,
        )
    except RuntimeError as exc:
        raise ServiceError(str(exc), code="USER_SAVE_FAILED") from exc


def build_users_page_payload(
    session: Session,
    *,
    page: int,
    limit: int,
    limit_in_query: bool,
    edit_id: int | None = None,
) -> dict[str, Any]:
    ensure_default_roles(session)
    params = pagination_service.normalize_pagination_params(
        page=page,
        limit=limit,
        limit_in_query=limit_in_query,
    )

    total = crud.count_users(session)
    items = crud.list_users(session, limit=params.limit, offset=params.offset)
    
    # 获取分组树并扁平化，便于前端缩进展示
    tree = resource_service.list_group_tree(session)
    flat_groups = []
    def _flatten(nodes):
        for node in nodes:
            flat_groups.append(node)
            if node.get("children"):
                _flatten(node["children"])
    _flatten(tree)
    
    roles = crud.list_roles(session)
    role_map = {role.code: role for role in roles}

    current = None
    current_allowed_ids: set[str] = set()
    current_recovery_count = 0
    if edit_id is not None and int(edit_id) > 0:
        current = crud.get_user(session, int(edit_id))
        if current and current.allowed_group_ids:
            current_allowed_ids = {x.strip() for x in current.allowed_group_ids.split(",") if x.strip()}
        if current and is_admin_role_code(getattr(current, "role", "")):
            current_recovery_count = len(getattr(current, "recovery_codes", []) or [])

    pagination = pagination_service.build_pagination_data(
        page=params.page,
        limit=params.limit,
        total=total,
    )
    pagination_base = pagination_service.build_pagination_base(
        path="/users",
        params={},
        limit=pagination.limit,
        limit_explicit=params.limit_explicit,
    )

    return {
        "items": items,
        "current": current,
        "groups": flat_groups,
        "roles": roles,
        "role_map": role_map,
        "admin_role_codes": list(getattr(crud, "ROLE_ADMIN_CODES", set())),
        "current_allowed_ids": current_allowed_ids,
        "recovery_codes_count": current_recovery_count,
        "pagination": pagination.as_dict(),
        "pagination_base": pagination_base,
    }


def build_roles_page_payload(session: Session) -> dict[str, Any]:
    ensure_default_roles(session)
    items = crud.list_roles(session)
    usage = {role.code: crud.role_usage_count(session, role.code) for role in items}
    admin_codes = set(getattr(crud, "ROLE_ADMIN_CODES", set()))
    for role in items:
        if role.code in admin_codes:
            continue
        normalized = crud.normalize_permission_codes((role.permissions or "").split(","))
        role.permissions = ",".join(normalized) if normalized else None

    return {
        "items": items,
        "usage": usage,
        "permission_catalog": crud.list_permission_catalog(),
        "permission_group_labels": PERMISSION_GROUP_LABELS,
        "admin_role_codes": list(admin_codes),
    }


def upsert_role(
    session: Session,
    *,
    role_id: int = 0,
    code: str,
    name: str,
    permission_codes: list[str],
):
    ensure_default_roles(session)
    role_code = (code or "").strip().lower()
    role_name = (name or "").strip()
    if not role_name:
        raise ServiceError("角色名称不能为空", code="ROLE_NAME_REQUIRED")

    normalized_permissions = crud.normalize_permission_codes(permission_codes)
    permissions_str = ",".join(normalized_permissions) if normalized_permissions else None

    if role_id and int(role_id) > 0:
        role = crud.get_role(session, int(role_id))
        if role is None:
            raise ServiceError("角色不存在", code="ROLE_NOT_FOUND", status_code=404)
        if is_admin_role_code(role.code):
            if role_code and role_code != role.code:
                raise ServiceError("系统管理员角色标识不可修改", code="ROLE_ADMIN_CODE_IMMUTABLE")
            crud.update_role(session, int(role_id), name=role_name, permissions=None)
            return role, "update"
        if not role_code:
            raise ServiceError("角色标识不能为空", code="ROLE_CODE_REQUIRED")
        try:
            updated = crud.update_role(
                session,
                int(role_id),
                code=role_code,
                name=role_name,
                permissions=permissions_str,
            )
        except RuntimeError as exc:
            raise ServiceError(str(exc), code="ROLE_SAVE_FAILED") from exc
        if updated is None:
            raise ServiceError("角色不存在", code="ROLE_NOT_FOUND", status_code=404)
        return updated, "update"

    if not role_code:
        raise ServiceError("角色标识不能为空", code="ROLE_CODE_REQUIRED")
    if is_admin_role_code(role_code):
        raise ServiceError("系统管理员角色不可新建", code="ROLE_ADMIN_CREATE_FORBIDDEN")
    try:
        role = crud.create_role(
            session,
            code=role_code,
            name=role_name,
            permissions=permissions_str,
            is_system=False,
            is_admin=False,
        )
    except RuntimeError as exc:
        raise ServiceError(str(exc), code="ROLE_SAVE_FAILED") from exc
    return role, "create"


def delete_role(session: Session, role_id: int):
    ensure_default_roles(session)
    role = crud.get_role(session, int(role_id))
    if role is None:
        raise ServiceError("角色不存在", code="ROLE_NOT_FOUND", status_code=404)
    if is_admin_role_code(role.code):
        raise ServiceError("系统管理员角色不可删除", code="ROLE_ADMIN_DELETE_FORBIDDEN")
    usage = crud.role_usage_count(session, role.code)
    if usage > 0:
        raise ServiceError(f"该角色已分配给 {usage} 个用户，无法删除", code="ROLE_IN_USE")
    crud.delete_role(session, int(role_id))
    return role


def update_user_recovery_codes(
    session: Session,
    *,
    user_id: int,
    recovery_codes: list[str],
) -> None:
    crud.update_user(session, int(user_id), recovery_codes=recovery_codes)


def upsert_user(
    session: Session,
    *,
    user_id: int = 0,
    username: str,
    role: str,
    password: str,
    group_access_type: str,
    allowed_group_ids: list[int],
    enable_mfa: bool,
    reset_mfa: bool,
    generate_recovery: bool,
    enable_recovery: bool,
    enable_watermark: bool = True,
    locale: str | None = None,
) -> UserMutationResult:
    ensure_default_roles(session)
    role = (role or "").strip().lower()
    username = (username or "").strip()

    allowed_ids_str = None
    if group_access_type == "specific":
        unique_ids = sorted(set(int(x) for x in (allowed_group_ids or [])))
        allowed_ids_str = ",".join(str(x) for x in unique_ids) if unique_ids else ""

    if user_id and int(user_id) > 0:
        target = crud.get_user(session, int(user_id))
        if target is None:
            raise ServiceError("用户不存在", code="USER_NOT_FOUND", status_code=404)

        update_payload: dict[str, Any] = {
            "username": username,
            "role": role,
            "password": password or None,
            "group_access_type": group_access_type,
            "allowed_group_ids": allowed_ids_str,
            "enable_watermark": enable_watermark,
        }
        if locale is not None:
            from app.i18n.validators import validate_locale
            update_payload["locale"] = validate_locale(locale)

        if target.username == "admin":
            update_payload = {"mfa_enabled": enable_mfa, "enable_watermark": enable_watermark}
            if locale is not None:
                from app.i18n.validators import validate_locale
                update_payload["locale"] = validate_locale(locale)
            if enable_mfa:
                if reset_mfa:
                    update_payload["mfa_secret"] = None
            else:
                update_payload["mfa_secret"] = None
            if password:
                update_payload["password"] = password
            update_payload["recovery_codes_enabled"] = enable_recovery
            if not enable_recovery:
                update_payload["recovery_codes"] = []
            recovery_codes: list[str] = []
            if generate_recovery:
                recovery_codes = generate_recovery_codes()
                update_payload["recovery_codes"] = [hash_recovery_code(code) for code in recovery_codes]
                update_payload["recovery_codes_enabled"] = True
            try:
                updated = crud.update_user(session, int(user_id), **update_payload)
            except RuntimeError as exc:
                raise ServiceError(str(exc), code="USER_SAVE_FAILED") from exc
            return UserMutationResult(user=updated, action="update", recovery_codes=recovery_codes)

        if not enable_mfa:
            update_payload["mfa_enabled"] = False
            update_payload["mfa_secret"] = None
            try:
                updated = crud.update_user(session, int(user_id), **update_payload)
            except RuntimeError as exc:
                raise ServiceError(str(exc), code="USER_SAVE_FAILED") from exc
            return UserMutationResult(user=updated, action="update", recovery_codes=[])

        update_payload["mfa_enabled"] = True
        if not target.mfa_enabled or reset_mfa:
            update_payload["mfa_secret"] = None

        recovery_codes = []
        if is_admin_role_code(target.role):
            if generate_recovery:
                recovery_codes = generate_recovery_codes()
                update_payload["recovery_codes"] = [hash_recovery_code(code) for code in recovery_codes]
                update_payload["recovery_codes_enabled"] = True
            else:
                update_payload["recovery_codes_enabled"] = enable_recovery
                if not enable_recovery:
                    update_payload["recovery_codes"] = []

        try:
            updated = crud.update_user(session, int(user_id), **update_payload)
        except RuntimeError as exc:
            raise ServiceError(str(exc), code="USER_SAVE_FAILED") from exc
        return UserMutationResult(user=updated, action="update", recovery_codes=recovery_codes)

    if not password:
        raise ServiceError("新建用户必须设置密码", code="USER_PASSWORD_REQUIRED")
    user = create_user(
        session,
        username=username,
        password=password,
        role=role,
        group_access_type=group_access_type,
        allowed_group_ids=allowed_ids_str,
        mfa_enabled=enable_mfa,
        mfa_secret=None,
        enable_watermark=enable_watermark,
        locale=locale or "zh-CN",
    )
    return UserMutationResult(user=user, action="create", recovery_codes=[])


def delete_user(session: Session, *, actor_user_id: int, user_id: int):
    if int(actor_user_id) == int(user_id):
        raise ServiceError("不能删除当前登录用户", code="USER_DELETE_SELF_FORBIDDEN")
    user = crud.get_user(session, user_id)
    if user and user.username == "admin":
        raise ServiceError("admin 用户不可删除", code="USER_DELETE_ADMIN_FORBIDDEN")
    username = user.username if user else f"ID: {user_id}"
    crud.delete_user(session, user_id)
    return username
