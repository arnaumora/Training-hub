"""Login, session and throttle tests. No database needed."""

import importlib

import pytest
from fastapi.testclient import TestClient

PASSWORD = "correct horse battery"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from app import auth

    hash_file = tmp_path / "app_password_hash"
    hash_file.write_text(auth.hash_password(PASSWORD) + "\n")
    monkeypatch.setenv("APP_PASSWORD_HASH_FILE", str(hash_file))
    monkeypatch.setenv("SESSION_SECRET", "x" * 64)

    importlib.reload(auth)
    from app import main

    importlib.reload(main)
    # https base URL: the session cookie is Secure and is only sent back over HTTPS.
    return TestClient(main.app, base_url="https://testserver")


def test_me_requires_login(client):
    assert client.get("/api/me").status_code == 401


def test_wrong_password_is_rejected(client):
    r = client.post("/api/login", json={"password": "nope"})
    assert r.status_code == 401
    assert client.get("/api/me").status_code == 401


def test_login_sets_secure_httponly_cookie_and_grants_access(client):
    r = client.post("/api/login", json={"password": PASSWORD})
    assert r.status_code == 204
    cookie = r.headers["set-cookie"].lower()
    assert "th_session=" in cookie
    assert "httponly" in cookie
    assert "secure" in cookie
    assert "samesite=lax" in cookie
    assert client.get("/api/me").json() == {"authenticated": True}


def test_logout_ends_session(client):
    client.post("/api/login", json={"password": PASSWORD})
    assert client.post("/api/logout").status_code == 204
    assert client.get("/api/me").status_code == 401


def test_throttle_locks_after_five_failures_even_for_right_password(client):
    for _ in range(5):
        assert client.post("/api/login", json={"password": "nope"}).status_code == 401
    assert client.post("/api/login", json={"password": PASSWORD}).status_code == 429


def test_health_does_not_require_login(client):
    # No database in tests, so 503 is expected; the point is that it is not 401.
    assert client.get("/health").status_code != 401


def test_startup_refuses_missing_hash_file(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_PASSWORD_HASH_FILE", str(tmp_path / "missing"))
    monkeypatch.setenv("SESSION_SECRET", "x" * 64)
    from app import auth

    importlib.reload(auth)
    with pytest.raises(RuntimeError, match="not found"):
        from app import main

        importlib.reload(main)


def test_startup_refuses_short_session_secret(tmp_path, monkeypatch):
    from app import auth

    hash_file = tmp_path / "app_password_hash"
    hash_file.write_text(auth.hash_password(PASSWORD) + "\n")
    monkeypatch.setenv("APP_PASSWORD_HASH_FILE", str(hash_file))
    monkeypatch.setenv("SESSION_SECRET", "short")
    importlib.reload(auth)
    with pytest.raises(RuntimeError, match="SESSION_SECRET"):
        from app import main

        importlib.reload(main)
