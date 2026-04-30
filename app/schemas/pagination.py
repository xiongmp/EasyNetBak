from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class PaginationParams:
    page: int
    limit: int
    offset: int
    limit_explicit: bool


@dataclass(slots=True, frozen=True)
class PaginationData:
    page: int
    limit: int
    total: int
    total_pages: int

    def as_dict(self) -> dict[str, int]:
        return {
            "page": self.page,
            "limit": self.limit,
            "total": self.total,
            "total_pages": self.total_pages,
        }
