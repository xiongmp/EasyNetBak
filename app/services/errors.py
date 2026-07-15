from __future__ import annotations

from typing import Any


class ServiceError(Exception):
    def __init__(
        self,
        message: str,
        *,
        code: str = "SERVICE_ERROR",
        status_code: int = 400,
        context: dict[str, Any] | None = None,
        message_key: str | None = None,
        params: dict[str, Any] | None = None,
    ):
        self.message = message
        self.code = code
        self.status_code = status_code
        self.context = context or {}
        self.message_key = message_key
        self.params = params or self.context
        super().__init__(message)
