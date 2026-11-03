import pytest
from fastapi.testclient import TestClient

from market.api import create_app
from market.config import Settings
from market.contracts import MonitorConfig


def preflight(client, origin):
    return client.options(
        "/api/v1/monitors",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "PATCH",
            "Access-Control-Request-Headers": "content-type,x-admin-key",
        },
    )


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "[::1]"])
def test_local_origins_allow_loopback_aliases_for_reads_and_writes(context, host):
    factory, _, mid, _ = context
    settings = Settings(_env_file=None, web_origin=f"http://{host}:3000")
    client = TestClient(create_app(factory, settings))
    for alias in ("localhost", "127.0.0.1", "[::1]"):
        origin = f"http://{alias}:3000"
        response = preflight(client, origin)
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == origin
        response = client.get("/api/v1/monitors", headers={"Origin": origin})
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == origin
        response = client.patch(
            f"/api/v1/monitors/{mid}",
            headers={"Origin": origin},
            json=MonitorConfig(name="Updated locally").model_dump(),
        )
        assert response.status_code == 200


@pytest.mark.parametrize(
    "origin",
    [
        "http://localhost:3001",
        "https://localhost:3000",
        "http://localhost.evil.example:3000",
        "https://evil.example",
        "null",
    ],
)
def test_untrusted_origins_stay_blocked(client, context, origin):
    assert preflight(client, origin).status_code == 400
    response = client.patch(
        f"/api/v1/monitors/{context[2]}",
        headers={"Origin": origin},
        json=MonitorConfig(name="Must not change").model_dump(),
    )
    assert response.status_code == 403


def test_custom_local_port_uses_configured_port_only(context):
    settings = Settings(_env_file=None, web_origin="http://localhost:3001")
    client = TestClient(create_app(context[0], settings))
    assert preflight(client, "http://127.0.0.1:3001").status_code == 200
    assert preflight(client, "http://127.0.0.1:3000").status_code == 400


@pytest.mark.parametrize(
    "mode,origin",
    [
        ("deployed", "http://localhost:3000"),
        ("local", "https://research.example"),
    ],
)
def test_other_origins_and_deployed_mode_keep_exact_origin(context, mode, origin):
    settings = Settings(_env_file=None, app_mode=mode, web_origin=origin, admin_key="x" * 32)
    client = TestClient(create_app(context[0], settings))
    assert preflight(client, origin).status_code == 200
    assert preflight(client, "http://127.0.0.1:3000").status_code == 400
    assert client.get("/api/v1/monitors").status_code == 401
    assert client.get("/api/v1/monitors", headers={"X-Admin-Key": "x" * 32}).status_code == 200
