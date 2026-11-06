"""End-to-end checks for the local shared-password session endpoints."""

from fastapi import FastAPI
from fastapi.testclient import TestClient
from infra.auth.local import SESSION_COOKIE, LocalAuthProvider
from routes.auth_local import router


def _client(password: str = "hunter2") -> TestClient:
    app = FastAPI()
    app.state.auth_provider = LocalAuthProvider("local-user", password)
    app.include_router(router)
    return TestClient(app)


def test_status_reports_that_login_is_required():
    response = _client().get("/v1/auth/status")

    assert response.status_code == 200
    assert response.json() == {"required": True, "authenticated": False}


def test_loopback_workspace_needs_no_login():
    client = _client(password="")

    assert client.get("/v1/auth/status").json() == {
        "required": False,
        "authenticated": True,
    }
    assert client.post("/v1/auth/login", json={"password": "anything"}).status_code == 200


def test_wrong_password_is_rejected_without_setting_a_cookie():
    client = _client()

    response = client.post("/v1/auth/login", json={"password": "nope"})

    assert response.status_code == 401
    assert SESSION_COOKIE not in response.cookies


def test_correct_password_sets_an_httponly_session_cookie():
    client = _client()

    response = client.post("/v1/auth/login", json={"password": "hunter2"})

    assert response.status_code == 200
    assert response.json() == {"required": True, "authenticated": True}

    set_cookie = response.headers["set-cookie"].lower()
    assert SESSION_COOKIE in set_cookie
    assert "httponly" in set_cookie
    assert "samesite=lax" in set_cookie

    # The client now carries the cookie, so the session is recognised.
    assert client.get("/v1/auth/status").json()["authenticated"] is True


def test_logout_clears_the_session():
    client = _client()
    client.post("/v1/auth/login", json={"password": "hunter2"})

    response = client.post("/v1/auth/logout")

    assert response.status_code == 204
    assert client.get("/v1/auth/status").json()["authenticated"] is False


def test_empty_password_is_rejected_by_request_validation():
    assert _client().post("/v1/auth/login", json={"password": ""}).status_code == 422
