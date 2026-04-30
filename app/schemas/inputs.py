from __future__ import annotations

from collections.abc import Mapping

from pydantic import BaseModel


class BaseListQueryInput(BaseModel):
    page: int | str | None = 1
    limit: int | str | None = 10
    include_limit_param: bool = False

    @classmethod
    def from_query_params(
        cls,
        query_params: Mapping[str, object],
        *,
        default_page: int = 1,
        default_limit: int = 10,
    ) -> "BaseListQueryInput":
        return cls(
            page=query_params.get("page") or default_page,
            limit=query_params.get("limit") or default_limit,
            include_limit_param=bool(query_params.get("limit")),
        )


class DeviceListQueryInput(BaseListQueryInput):
    q: str | None = None
    login_method: str | None = None
    platform: str | None = None
    group_id: int | str | None = None
    status: str | None = None

    @classmethod
    def from_query_params(
        cls,
        query_params: Mapping[str, object],
        *,
        default_page: int = 1,
        default_limit: int = 10,
    ) -> "DeviceListQueryInput":
        base = BaseListQueryInput.from_query_params(
            query_params,
            default_page=default_page,
            default_limit=default_limit,
        )
        return cls(
            q=query_params.get("q"),
            login_method=query_params.get("login_method"),
            platform=query_params.get("platform"),
            group_id=query_params.get("group_id"),
            status=query_params.get("status"),
            page=base.page,
            limit=base.limit,
            include_limit_param=base.include_limit_param,
        )


class SearchListQueryInput(BaseListQueryInput):
    q: str | None = None

    @classmethod
    def from_query_params(
        cls,
        query_params: Mapping[str, object],
        *,
        default_page: int = 1,
        default_limit: int = 10,
    ) -> "SearchListQueryInput":
        base = BaseListQueryInput.from_query_params(
            query_params,
            default_page=default_page,
            default_limit=default_limit,
        )
        return cls(
            q=query_params.get("q"),
            page=base.page,
            limit=base.limit,
            include_limit_param=base.include_limit_param,
        )


class EditableListQueryInput(BaseListQueryInput):
    edit: str | None = None

    @classmethod
    def from_query_params(
        cls,
        query_params: Mapping[str, object],
        *,
        default_page: int = 1,
        default_limit: int = 10,
    ) -> "EditableListQueryInput":
        base = BaseListQueryInput.from_query_params(
            query_params,
            default_page=default_page,
            default_limit=default_limit,
        )
        return cls(
            edit=query_params.get("edit"),
            page=base.page,
            limit=base.limit,
            include_limit_param=base.include_limit_param,
        )


class AuditLogListQueryInput(SearchListQueryInput):
    action: str | None = None
    resource_type: str | None = None

    @classmethod
    def from_query_params(
        cls,
        query_params: Mapping[str, object],
        *,
        default_page: int = 1,
        default_limit: int = 10,
    ) -> "AuditLogListQueryInput":
        base = SearchListQueryInput.from_query_params(
            query_params,
            default_page=default_page,
            default_limit=default_limit,
        )
        return cls(
            q=base.q,
            action=query_params.get("action"),
            resource_type=query_params.get("resource_type"),
            page=base.page,
            limit=base.limit,
            include_limit_param=base.include_limit_param,
        )


class LoginLogListQueryInput(SearchListQueryInput):
    status: str | None = None

    @classmethod
    def from_query_params(
        cls,
        query_params: Mapping[str, object],
        *,
        default_page: int = 1,
        default_limit: int = 10,
    ) -> "LoginLogListQueryInput":
        base = SearchListQueryInput.from_query_params(
            query_params,
            default_page=default_page,
            default_limit=default_limit,
        )
        return cls(
            q=base.q,
            status=query_params.get("status"),
            page=base.page,
            limit=base.limit,
            include_limit_param=base.include_limit_param,
        )


class ConfigSearchListQueryInput(SearchListQueryInput):
    scope: str = "latest"

    @classmethod
    def from_query_params(
        cls,
        query_params: Mapping[str, object],
        *,
        default_page: int = 1,
        default_limit: int = 50,
    ) -> "ConfigSearchListQueryInput":
        base = SearchListQueryInput.from_query_params(
            query_params,
            default_page=default_page,
            default_limit=default_limit,
        )
        return cls(
            q=base.q,
            scope=str(query_params.get("scope") or "latest"),
            page=base.page,
            limit=base.limit,
            include_limit_param=base.include_limit_param,
        )


class DeviceCreateInput(BaseModel):
    name: str
    host: str
    port: int = 22
    login_method: str = "ssh"
    encoding: str = "utf-8"
    platform: str
    group_id: int = 0
    credential_id: int
    default_template_id: int = 0


class DeviceUpdateInput(BaseModel):
    name: str | None = None
    host: str | None = None
    port: int | None = None
    login_method: str | None = None
    encoding: str | None = None
    platform: str | None = None
    group_id: int | None = None
    credential_id: int | None = None
    default_template_id: int | None = None


class CredentialCreateInput(BaseModel):
    name: str
    username: str
    password: str | None = None
    enable_password: str | None = None
    remarks: str | None = None


class CredentialUpdateInput(BaseModel):
    name: str | None = None
    username: str | None = None
    password: str | None = None
    enable_password: str | None = None
    remarks: str | None = None


class GroupCreateInput(BaseModel):
    name: str
    parent_id: int | None = None


class GroupUpdateInput(BaseModel):
    name: str | None = None
    parent_id: int | None = None


class TemplateCreateInput(BaseModel):
    name: str
    platform: str
    commands: str | None = None


class TemplateUpdateInput(BaseModel):
    name: str | None = None
    platform: str | None = None
    commands: str | None = None
