[OPEN] Docker upgrade DB error

## Symptom
- Docker rebuild/restart after upgrading latest version fails on remote host `192.168.159.10`.
- Log snippet shows SQLAlchemy querying `user.enable_watermark` for user id `1`, then raising error with reference `sqlalche.me/e/20/f405`.

## Initial Hypotheses
- Hypothesis 1: Application model added `user.enable_watermark`, but production database schema was not migrated, so query hits a missing column.
- Hypothesis 2: Container points to an older or unexpected database instance whose schema is behind the current code.
- Hypothesis 3: Migration files exist locally, but image startup path does not execute Alembic upgrade before serving requests.
- Hypothesis 4: Remote deployment mounted persistent database volume from an older release, and rebuild did not include a schema upgrade step.
- Hypothesis 5: The runtime error is not a missing column but another database compatibility issue surfaced during ORM row loading.

## Evidence Plan
- Inspect local user model and migrations for `enable_watermark`.
- Inspect deployment/startup scripts for Alembic upgrade execution.
- If possible, collect remote container logs and database schema evidence from `192.168.159.10`.

## Status
- Session opened; no business logic changes made.

## Evidence
- `app/models.py` defines `User.enable_watermark`.
- `app/templates/base.html` reads `current_user.enable_watermark`, so user loading will query this column during authenticated requests.
- `migrations/versions/495966c382b0_add_enable_watermark_to_user.py` exists but both `upgrade()` and `downgrade()` are empty (`pass`), so schema change was never applied.
- `app/db.py` runs `init_db()` -> `SQLModel.metadata.create_all(engine)` -> `alembic upgrade head`, which means startup does invoke Alembic.
- `docker-compose.yml` starts `web` with `uvicorn app.main:app`; there is no separate schema-fix command in Compose.

## Conclusion
- Confirmed Hypothesis 1: code expects `user.enable_watermark`, but the migration that should add it is a no-op.
- Rejected Hypothesis 3 as primary cause: startup does run Alembic, but Alembic had no effective DDL to execute for this column.
- Hypothesis 2 / 4 remain possible deployment-side amplifiers, but they are not required to explain the failure.

## Fix
- Added `migrations/versions/b7c8d9e0f1a2_backfill_enable_watermark_column.py`.
- The new migration safely adds `user.enable_watermark` only when missing, so it works for environments that already recorded revision `495966c382b0`.

## Verification
- Ran app-style initialization against a temporary SQLite database and confirmed the final `user` table contains `enable_watermark`.
