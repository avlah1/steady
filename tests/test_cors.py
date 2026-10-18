"""The dashboard runs in a browser on another origin; these mimic the browser's CORS checks."""

from app.config import Settings


def preflight(client, origin: str):
    # Sent by the browser before a request with a custom header like X-API-Key.
    return client.options(
        "/api/v1/incidents",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "X-API-Key",
        },
    )


def test_dashboard_origin_allowed(client):
    resp = preflight(client, "http://localhost:5173")

    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert "x-api-key" in resp.headers["access-control-allow-headers"].lower()


def test_unknown_origin_rejected(client):
    resp = preflight(client, "http://evil.example")

    assert "access-control-allow-origin" not in resp.headers


def test_cors_origins_parsed_from_comma_separated_string():
    settings = Settings(api_key="k", cors_origins=" http://a:1 , http://b:2,")

    assert settings.cors_origin_list == ["http://a:1", "http://b:2"]
