# Session Refactor Progress

## Scope

This file tracks the migration from manual `with session_scope()` blocks in route
handlers to FastAPI dependency injection with `session: Session = Depends(get_session)`.
It is intended to avoid duplicate work across sessions.

## Completed

- [x] Centralize transaction boundary in `app/db.py`
- [x] Move `app/crud.py` write operations from scattered `commit()` calls to
  `flush()` / `flush() + refresh()`
- [x] Align test session fixtures with the new transaction boundary
- [x] Add `get_session()` dependency in `app/db.py`
- [x] Refactor `app/routers/public_api/v1/groups.py`
- [x] Refactor `app/routers/public_api/v1/credentials.py`
- [x] Refactor `app/routers/public_api/v1/templates.py`
- [x] Refactor `app/routers/public_api/v1/stats.py`
- [x] Refactor `app/routers/public_api/v1/backups.py`
- [x] Refactor `app/routers/public_api/v1/devices.py`
- [x] Refactor `app/routers/internal_api/backups.py`
- [x] Refactor `app/routers/internal_api/devices.py`
- [x] Refactor `app/routers/internal_api/schedules.py`
- [x] Refactor `app/routers/web/backups.py`
- [x] Refactor `app/routers/web/schedules.py`
- [x] Refactor `app/routers/web/dashboard.py`
- [x] Refactor `app/routers/web/resources.py`
- [x] Refactor `app/routers/web/system.py`
- [x] Refactor `app/routers/web/auth.py`
- [x] Refactor `app/routers/web/devices.py` route handlers
- [x] Fix Celery visibility issue by committing backup/schedule state before
  dispatching async workers

## Remaining

- [x] No remaining HTTP route handlers using manual `with session_scope()`

## Notes

- Keep `session_scope()` as the canonical transaction boundary.
- Allow explicit early `session.commit()` only when cross-process visibility is
  required before dispatching background workers.
- `app/routers/web/devices.py` still retains `session_scope()` in one internal
  helper and in WebSocket lifecycle code because they do not run through the
  standard FastAPI request dependency chain.
