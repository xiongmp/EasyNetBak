from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlmodel import Session, select

from app import crud
from app.models import BackupTemplate, Credential, DeviceGroup
from app.platforms import DEFAULT_COMMANDS, normalize_platform_id
from app.schemas.inputs import (
    CredentialCreateInput,
    CredentialUpdateInput,
    GroupCreateInput,
    GroupUpdateInput,
    TemplateCreateInput,
    TemplateUpdateInput,
)


from app.services.errors import ServiceError


def _ensure_unique_credential_name(
    session: Session,
    name: str,
    *,
    exclude_credential_id: int | None = None,
) -> None:
    stmt = select(Credential).where(Credential.name == name)
    if exclude_credential_id is not None:
        stmt = stmt.where(Credential.id != exclude_credential_id)
    existing = session.exec(stmt).first()
    if existing:
        raise ServiceError(
            f"Credential name already exists: {name}",
            code="RESOURCE_CREDENTIAL_NAME_EXISTS",
            status_code=400,
            context={"name": name},
        )


def _ensure_unique_group_name(
    session: Session,
    name: str,
    *,
    exclude_group_id: int | None = None,
) -> None:
    stmt = select(DeviceGroup).where(DeviceGroup.name == name)
    if exclude_group_id is not None:
        stmt = stmt.where(DeviceGroup.id != exclude_group_id)
    existing = session.exec(stmt).first()
    if existing:
        raise ServiceError(
            f"Group name already exists: {name}",
            code="RESOURCE_GROUP_NAME_EXISTS",
            status_code=400,
            context={"name": name},
        )


def _normalize_template_commands(platform: str, commands: str | None) -> str:
    normalized_commands = (commands or "").strip()
    if normalized_commands:
        return normalized_commands
    return DEFAULT_COMMANDS.get(normalize_platform_id(platform), "")


def _build_group_tree(groups: list[DeviceGroup]) -> list[dict[str, Any]]:
    nodes: dict[int, dict[str, Any]] = {}
    roots: list[dict[str, Any]] = []
    for group in groups:
        if not group.id:
            continue
        nodes[int(group.id)] = {
            "id": int(group.id),
            "name": group.name,
            "parent_id": int(group.parent_id) if group.parent_id else None,
            "path": group.path,
            "depth": int(group.depth or 0),
            "sort_order": int(group.sort_order or 0),
            "created_at": group.created_at,
            "children": [],
        }

    for group in groups:
        if not group.id:
            continue
        node = nodes[int(group.id)]
        parent_id = int(group.parent_id) if group.parent_id else None
        parent = nodes.get(parent_id) if parent_id else None
        if parent is None:
            roots.append(node)
        else:
            parent["children"].append(node)

    def _sort_nodes(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        items.sort(key=lambda item: (int(item["sort_order"]), item["path"], int(item["id"])))
        for item in items:
            _sort_nodes(item["children"])
        return items

    return _sort_nodes(roots)


def list_group_tree(session: Session) -> list[dict[str, Any]]:
    return _build_group_tree(crud.list_groups(session))


def create_credential(session: Session, data: CredentialCreateInput) -> Credential:
    name = (data.name or "").strip()
    username = (data.username or "").strip()
    _ensure_unique_credential_name(session, name)

    cred = Credential(
        name=name,
        username=username,
        password=data.password,
        enable_password=data.enable_password,
        remarks=data.remarks,
    )
    return crud.create_credential(session, credential=cred)


def update_credential(session: Session, credential_id: int, data: CredentialUpdateInput) -> Credential:
    credential = crud.get_credential(session, credential_id)
    if credential is None:
        raise ServiceError(
            "Credential not found",
            code="RESOURCE_CREDENTIAL_NOT_FOUND",
            status_code=404,
            context={"credential_id": credential_id},
        )

    name = (data.name if data.name is not None else credential.name).strip()
    username = (data.username if data.username is not None else credential.username).strip()
    _ensure_unique_credential_name(session, name, exclude_credential_id=credential_id)

    remarks = data.remarks if data.remarks is not None else credential.remarks
    return crud.update_credential(
        session,
        credential_id,
        name=name,
        username=username,
        password=data.password,
        enable_password=data.enable_password,
        remarks=remarks,
    )


def delete_credential(session: Session, credential_id: int) -> Credential:
    credential = crud.get_credential(session, credential_id)
    if credential is None:
        raise ServiceError(
            "Credential not found",
            code="RESOURCE_CREDENTIAL_NOT_FOUND",
            status_code=404,
            context={"credential_id": credential_id},
        )
    try:
        crud.delete_credential(session, credential_id)
    except RuntimeError as exc:
        raise ServiceError(
            str(exc),
            code="RESOURCE_CREDENTIAL_IN_USE",
            status_code=400,
            context={"credential_id": credential_id},
        ) from exc
    return credential


def create_group(session: Session, data: GroupCreateInput) -> DeviceGroup:
    name = (data.name or "").strip()
    _ensure_unique_group_name(session, name)
    try:
        return crud.create_group(session, name=name, parent_id=data.parent_id)
    except RuntimeError as exc:
        raise ServiceError(
            str(exc),
            code="RESOURCE_GROUP_SAVE_FAILED",
            status_code=400,
            context={"parent_id": data.parent_id},
        ) from exc


def update_group(session: Session, group_id: int, data: GroupUpdateInput) -> DeviceGroup:
    group = crud.get_group(session, group_id)
    if group is None:
        raise ServiceError(
            "Group not found",
            code="RESOURCE_GROUP_NOT_FOUND",
            status_code=404,
            context={"group_id": group_id},
        )

    name = (data.name if data.name is not None else group.name).strip()
    _ensure_unique_group_name(session, name, exclude_group_id=group_id)
    try:
        updated = crud.update_group(
            session,
            group_id,
            name=name,
            parent_id=data.parent_id,
        )
    except RuntimeError as exc:
        raise ServiceError(
            str(exc),
            code="RESOURCE_GROUP_SAVE_FAILED",
            status_code=400,
            context={"group_id": group_id, "parent_id": data.parent_id},
        ) from exc
    if updated is None:
        raise ServiceError(
            "Group not found",
            code="RESOURCE_GROUP_NOT_FOUND",
            status_code=404,
            context={"group_id": group_id},
        )
    return updated


def delete_group(session: Session, group_id: int) -> DeviceGroup:
    group = crud.get_group(session, group_id)
    if group is None:
        raise ServiceError(
            "Group not found",
            code="RESOURCE_GROUP_NOT_FOUND",
            status_code=404,
            context={"group_id": group_id},
        )

    child_count = crud.group_child_count(session, group_id)
    if child_count > 0:
        raise ServiceError(
            f"Group has {child_count} child group(s)",
            code="RESOURCE_GROUP_HAS_CHILDREN",
            status_code=400,
            context={"group_id": group_id, "child_count": child_count},
        )

    usage_count = crud.group_subtree_usage_count(session, group_id)
    if usage_count > 0:
        raise ServiceError(
            f"Group is in use by {usage_count} device(s)",
            code="RESOURCE_GROUP_IN_USE",
            status_code=400,
            context={"group_id": group_id, "usage_count": usage_count},
        )

    crud.delete_group(session, group_id)
    return group


def create_template(session: Session, data: TemplateCreateInput) -> BackupTemplate:
    template = BackupTemplate(
        name=(data.name or "").strip(),
        platform=data.platform,
        commands=_normalize_template_commands(data.platform, data.commands),
    )
    return crud.create_template(session, template=template)


def update_template(session: Session, template_id: int, data: TemplateUpdateInput) -> BackupTemplate:
    template = crud.get_template(session, template_id)
    if template is None:
        raise ServiceError(
            "Template not found",
            code="RESOURCE_TEMPLATE_NOT_FOUND",
            status_code=404,
            context={"template_id": template_id},
        )

    name = (data.name if data.name is not None else template.name).strip()
    platform = data.platform if data.platform is not None else template.platform
    commands = _normalize_template_commands(platform, data.commands if data.commands is not None else template.commands)

    updated = crud.update_template(
        session,
        template_id,
        name=name,
        platform=platform,
        commands=commands,
    )
    if updated is None:
        raise ServiceError(
            "Template not found",
            code="RESOURCE_TEMPLATE_NOT_FOUND",
            status_code=404,
            context={"template_id": template_id},
        )
    return updated


def delete_template(session: Session, template_id: int) -> BackupTemplate:
    template = crud.get_template(session, template_id)
    if template is None:
        raise ServiceError(
            "Template not found",
            code="RESOURCE_TEMPLATE_NOT_FOUND",
            status_code=404,
            context={"template_id": template_id},
        )
    crud.delete_template(session, template_id)
    return template
