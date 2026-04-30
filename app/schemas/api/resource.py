from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict
from app.schemas.inputs import (
    CredentialCreateInput,
    CredentialUpdateInput,
    GroupCreateInput,
    GroupUpdateInput,
    TemplateCreateInput,
    TemplateUpdateInput,
)


class OperationStatusSchema(BaseModel):
    status: str


class GroupResponseSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    parent_id: int | None = None
    path: str
    depth: int
    sort_order: int
    created_at: datetime


class GroupTreeNodeSchema(GroupResponseSchema):
    children: list["GroupTreeNodeSchema"] = []


GroupTreeNodeSchema.model_rebuild()


class GroupCreateSchema(GroupCreateInput):
    pass


class GroupUpdateSchema(GroupUpdateInput):
    pass


class CredentialResponseSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    username: str
    remarks: str | None = None
    created_at: datetime


class CredentialCreateSchema(CredentialCreateInput):
    pass


class CredentialUpdateSchema(CredentialUpdateInput):
    pass


class TemplateResponseSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    platform: str
    commands: str
    created_at: datetime


class TemplateCreateSchema(TemplateCreateInput):
    pass


class TemplateUpdateSchema(TemplateUpdateInput):
    pass
