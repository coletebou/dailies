import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from dailies_server.app import create_app
from dailies_server.auth import AccessVerifier

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def token(key=KEY, **over):
    claims = {"aud": ["test-aud"], "iss": "https://team.example.com", "email": "reviewer@example.com",
              "iat": int(time.time()), "exp": int(time.time()) + 600, **over}
    return jwt.encode(claims, key, algorithm="RS256")


@pytest.fixture()
def pub(settings):
    v = AccessVerifier(settings, key_resolver=lambda _t: KEY.public_key())
    with TestClient(create_app(settings, verifier=v, start_workers=False), base_url="https://dailies.example.com") as c:
        yield c


def test_public_host_requires_access_jwt(pub):
    assert pub.get("/api/health").status_code == 403
    assert pub.get("/media/build/edl.json").status_code == 403
    ok = pub.get("/api/health", headers={"Cf-Access-Jwt-Assertion": token()})
    assert ok.status_code == 200 and ok.json()["ok"]
    pub.cookies.set("CF_Authorization", token())
    assert pub.get("/api/health").status_code == 200


@pytest.mark.parametrize("tok", [
    lambda: token(aud=["someone-else"]),
    lambda: token(iss="https://evil.example.com"),
    lambda: token(email="mallory@example.com"),
    lambda: token(exp=int(time.time()) - 3600),
    lambda: token(key=OTHER),
    lambda: "not-a-jwt",
    lambda: jwt.encode({"aud": "test-aud", "iss": "https://team.example.com", "email": "reviewer@example.com",
                        "iat": int(time.time()), "exp": int(time.time()) + 600}, "secret", algorithm="HS256"),
])
def test_bad_tokens_rejected(pub, tok):
    assert pub.get("/api/health", headers={"Cf-Access-Jwt-Assertion": tok()}).status_code == 403


def test_fails_closed_without_aud(settings):
    settings.access_aud = ""
    v = AccessVerifier(settings, key_resolver=lambda _t: KEY.public_key())
    with TestClient(create_app(settings, verifier=v, start_workers=False), base_url="https://dailies.example.com") as c:
        assert c.get("/api/health", headers={"Cf-Access-Jwt-Assertion": token()}).status_code == 403


def test_unknown_host_refused(settings):
    with TestClient(create_app(settings, start_workers=False), base_url="http://evil.example.net") as c:
        assert c.get("/api/health").status_code == 421


def test_testserver_is_only_a_test_host():
    from dailies_server.config import Settings
    assert "testserver" not in Settings().local_hosts
    assert {"127.0.0.1", "localhost"} <= set(Settings().local_hosts)


def test_local_host_needs_no_token(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.headers["x-frame-options"] == "DENY"


def test_fails_closed_without_team_domain(settings):
    settings.access_team_domain = ""
    v = AccessVerifier(settings, key_resolver=lambda _t: KEY.public_key())
    with TestClient(create_app(settings, verifier=v, start_workers=False), base_url="https://dailies.example.com") as c:
        assert c.get("/api/health", headers={"Cf-Access-Jwt-Assertion": token()}).status_code == 403


def test_defaults_are_empty_and_local_only(monkeypatch, tmp_path):
    """No hostnames, team domain, AUD or emails are baked in: a fresh install answers loopback only."""
    from dailies_server.config import Settings
    for k in ("DAILIES_PUBLIC_HOSTS", "DAILIES_ACCESS_TEAM_DOMAIN", "DAILIES_ACCESS_AUD", "DAILIES_ACCESS_EMAILS"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DAILIES_PROJECT_DIR", raising=False)
    s = Settings()
    assert (s.public_hosts, s.access_team_domain, s.access_aud, s.access_emails) == ((), "", "", ())
    assert s.root == tmp_path.resolve()


def test_env_interface(monkeypatch, tmp_path):
    import importlib
    from dailies_server import config
    monkeypatch.setenv("DAILIES_PROJECT_DIR", str(tmp_path))
    monkeypatch.setenv("DAILIES_PUBLIC_HOSTS", "a.example.com, b.example.com")
    monkeypatch.setenv("DAILIES_ACCESS_TEAM_DOMAIN", "team.example.com")
    monkeypatch.setenv("DAILIES_ACCESS_AUD", "aud123")
    monkeypatch.setenv("DAILIES_ACCESS_EMAILS", "A@example.com,b@example.com")
    monkeypatch.setenv("DAILIES_PORT", "9999")
    importlib.reload(config)       # host/port are read at import time
    try:
        s = config.Settings()
        assert s.root == tmp_path.resolve() and s.port == 9999
        assert s.public_hosts == ("a.example.com", "b.example.com")
        assert (s.access_team_domain, s.access_aud) == ("team.example.com", "aud123")
        assert s.access_emails == ("a@example.com", "b@example.com")
    finally:
        monkeypatch.undo()
        importlib.reload(config)
