from fastapi.routing import APIRoute

from app import crud
from app.main import app


def test_openapi_public_api_paths_present():
    schema = app.openapi()
    paths = schema.get("paths", {})

    expected_paths = {
        "/api/v1/backups/{backup_id}/content",
        "/api/v1/credentials",
        "/api/v1/credentials/{credential_id}",
        "/api/v1/devices",
        "/api/v1/devices/unreachable",
        "/api/v1/devices/{device_id}",
        "/api/v1/devices/{device_id}/backups",
        "/api/v1/groups",
        "/api/v1/groups/tree",
        "/api/v1/groups/{group_id}",
        "/api/v1/stats",
        "/api/v1/templates",
        "/api/v1/templates/{template_id}",
    }

    assert expected_paths.issubset(set(paths))
    assert all(path.startswith("/api/v1/") for path in paths)


def test_runtime_router_tree_contains_web_internal_and_public_routes():
    route_paths = {
        route.path
        for route in app.routes
        if isinstance(route, APIRoute)
    }

    assert "/" in route_paths
    assert "/dashboard" in route_paths
    assert "/api/schedules/{schedule_id}/run" in route_paths
    assert "/api/schedules/runs/{run_id}/terminate" in route_paths
    assert "/api/v1/stats" in route_paths


def test_web_log_routes_are_registered_once():
    route_paths = [
        route.path
        for route in app.routes
        if isinstance(route, APIRoute)
    ]

    assert route_paths.count("/login-logs") == 1
    assert route_paths.count("/login-logs/export.csv") == 1
    assert route_paths.count("/audit-logs") == 1
    assert route_paths.count("/audit-logs/export.csv") == 1


def test_openapi_excludes_web_and_internal_routes():
    schema = app.openapi()
    paths = set(schema.get("paths", {}))

    assert "/dashboard" not in paths
    assert "/devices" not in paths
    assert "/api/schedules/{schedule_id}/run" not in paths
    assert "/api/v1/stats" in paths


def test_legacy_permission_codes_expand_to_new_codes():
    normalized = set(
        crud.normalize_permission_codes(
            [
                "resources.view",
                "resources.create",
                "resources.update",
                "resources.delete",
                "backups.trigger",
                "backups.manage",
            ]
        )
    )

    assert "groups.view" in normalized
    assert "credentials.create" in normalized
    assert "templates.update" in normalized
    assert "groups.delete" in normalized
    assert "devices.backup" in normalized
    assert "backups.view" in normalized
    assert "backups.delete" in normalized
    assert "backups.trigger" not in normalized


def test_diff_rules_delete_permission_is_registered_and_expanded():
    catalog_codes = {item["code"] for item in crud.list_permission_catalog()}
    normalized = set(crud.normalize_permission_codes(["diff_rules.manage"]))

    assert "diff_rules.delete" in catalog_codes
    assert "diff_rules.delete" in normalized
