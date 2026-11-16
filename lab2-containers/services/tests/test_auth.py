"""auth-service: вхід за кодом, обмеження спроб, JWT."""
import os
from datetime import timedelta

for f in ("test_auth.db",):
    if os.path.exists(f):
        os.remove(f)
os.environ.update(DATABASE_URL="sqlite:///./test_auth.db", AUTH_SECRET="test-secret-0123456789",
                  ADMIN_EMAILS="boss@example.com", AUTH_DEV_MODE="true", SMTP_HOST="")

from fastapi.testclient import TestClient  # noqa: E402

from auth import main as auth  # noqa: E402

client = TestClient(auth.app)
client.__enter__()  # запускає lifespan (створення таблиць)


def login(email):
    code = client.post("/api/auth/request-code", json={"email": email}).json()["devCode"]
    return client.post("/api/auth/verify", json={"email": email, "code": code})


def test_login_sets_httponly_cookie_and_me_works():
    r = login("Client@Example.com ")
    assert r.status_code == 200
    assert r.json() == {"email": "client@example.com", "role": "CLIENT"}
    cookie = r.headers["set-cookie"]
    assert "autoslot_session=" in cookie and "HttpOnly" in cookie and "SameSite=lax" in cookie
    assert client.get("/api/auth/me").json()["email"] == "client@example.com"


def test_internal_verify_returns_identity_headers():
    login("boss@example.com")
    r = client.get("/internal/verify")
    assert r.status_code == 200
    assert r.headers["x-user-email"] == "boss@example.com"
    assert r.headers["x-user-role"] == "ADMIN"


def test_wrong_code_and_attempt_limit():
    email = "brute@example.com"
    code = client.post("/api/auth/request-code", json={"email": email}).json()["devCode"]
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(5):
        assert client.post("/api/auth/verify", json={"email": email, "code": wrong}).status_code == 400
    # шоста спроба: код анульовано, навіть правильний уже не підходить
    assert client.post("/api/auth/verify", json={"email": email, "code": code}).status_code == 429
    assert client.post("/api/auth/verify", json={"email": email, "code": code}).status_code == 400


def test_code_request_rate_limit():
    email = "spam@example.com"
    for _ in range(3):
        assert client.post("/api/auth/request-code", json={"email": email}).status_code == 200
    assert client.post("/api/auth/request-code", json={"email": email}).status_code == 429


def test_expired_code_rejected():
    email = "late@example.com"
    code = client.post("/api/auth/request-code", json={"email": email}).json()["devCode"]
    with auth.SessionLocal() as s:
        for c in s.query(auth.LoginCode).filter_by(email=email):
            c.expires_at = auth.now() - timedelta(seconds=1)
        s.commit()
    assert client.post("/api/auth/verify", json={"email": email, "code": code}).status_code == 400


def test_code_is_not_stored_in_plain_text():
    email = "hash@example.com"
    code = client.post("/api/auth/request-code", json={"email": email}).json()["devCode"]
    with auth.SessionLocal() as s:
        stored = s.query(auth.LoginCode).filter_by(email=email).one().code_hash
    assert code not in stored and len(stored) == 64


def test_invalid_or_forged_token_rejected():
    c = TestClient(auth.app)
    assert c.get("/internal/verify").status_code == 401
    import jwt
    forged = jwt.encode({"sub": "x", "email": "evil@example.com", "role": "ADMIN", "iss": "autoslot-auth",
                         "exp": auth.now() + timedelta(hours=1)}, "wrong-secret-0123456789", algorithm="HS256")
    c.cookies.set("autoslot_session", forged)
    assert c.get("/internal/verify").status_code == 401


def test_invalid_email_rejected():
    assert client.post("/api/auth/request-code", json={"email": "not-an-email"}).status_code == 422
