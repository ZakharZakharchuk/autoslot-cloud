"""auth-service: вхід за одноразовим кодом з пошти, сесія в JWT, ролі CLIENT і ADMIN.

Як відбувається вхід:
  1. POST /api/auth/request-code {email}  -> генеруємо 6-значний код, зберігаємо лише
     його хеш (HMAC) з терміном дії 10 хв і надсилаємо код листом.
  2. POST /api/auth/verify {email, code}  -> перевіряємо код, створюємо користувача
     (якщо новий) і видаємо JWT у cookie autoslot_session (HttpOnly).
  3. Далі браузер сам надсилає cookie з кожним запитом. Gateway (nginx) перед
     захищеними запитами звертається до GET /internal/verify, і цей сервіс каже,
     хто користувач: заголовки X-User-Id, X-User-Email, X-User-Role.

Паролів немає взагалі, тож немає що вкрасти з бази чи підібрати.
"""
import hashlib
import hmac
import logging
import os
import re
import secrets
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import jwt
from prometheus_client import Counter
from fastapi import Cookie, Depends, FastAPI, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import DateTime, Integer, String, func, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from common import mailer
from common.db import init_schema, make_db, wait_for_db
from common import metrics
from common.web import SERVICE_NAME, VERSION

log = logging.getLogger("auth")

engine, SessionLocal = make_db("auth.db")
IS_SQLITE = engine.dialect.name == "sqlite"

# Секрет для підпису JWT і хешування кодів. Однаковий для всіх реплік (з .env),
# інакше токен, виданий однією реплікою, не пройшов би перевірку в іншій.
AUTH_SECRET = os.getenv("AUTH_SECRET") or ("dev-only-secret" if IS_SQLITE else "")
if len(AUTH_SECRET) < 16:
    raise RuntimeError("AUTH_SECRET не задано або він коротший за 16 символів")

ADMIN_EMAILS = {e.strip().lower() for e in os.getenv("ADMIN_EMAILS", "").split(",") if e.strip()}
CODE_TTL = timedelta(minutes=int(os.getenv("CODE_TTL_MINUTES", "10")))
MAX_ATTEMPTS = 5                 # спроб ввести код, після чого код анулюється
MAX_CODES = 3                    # кодів на одну адресу ...
CODES_WINDOW = timedelta(minutes=15)  # ... за цей проміжок часу
SESSION_TTL = timedelta(hours=int(os.getenv("SESSION_HOURS", "8")))
COOKIE_NAME = "autoslot_session"
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() == "true"  # true, коли сайт працює по HTTPS
# Режим розробки: якщо пошта не налаштована, код повертається у відповіді.
# Лише для локального запуску й автотестів, у хмарі має бути false.
DEV_MODE = os.getenv("AUTH_DEV_MODE", "false").lower() == "true"
LOGIN_CODES = Counter("auth_login_codes_total", "Надіслані коди входу")
LOGINS = Counter("auth_logins_total", "Успішні входи", ["role"])
LOGIN_FAILURES = Counter("auth_login_failures_total", "Невдалі спроби входу", ["reason"])
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True)
    role: Mapped[str] = mapped_column(String(10))  # CLIENT | ADMIN
    created_at: Mapped[datetime] = mapped_column(DateTime)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class LoginCode(Base):
    __tablename__ = "login_codes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    email: Mapped[str] = mapped_column(String(254), index=True)
    code_hash: Mapped[str] = mapped_column(String(64))  # сам код не зберігається
    created_at: Mapped[datetime] = mapped_column(DateTime)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    used: Mapped[int] = mapped_column(Integer, default=0)


def now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def hash_code(email: str, code: str) -> str:
    return hmac.new(AUTH_SECRET.encode(), f"{email}:{code}".encode(), hashlib.sha256).hexdigest()


def normalize_email(email: str) -> str:
    email = email.strip().lower()
    if len(email) > 254 or not EMAIL_RE.match(email):
        raise HTTPException(422, "Некоректна адреса пошти")
    return email


def make_token(user: User) -> str:
    claims = {"sub": user.id, "email": user.email, "role": user.role, "iss": "autoslot-auth",
              "iat": now(), "exp": now() + SESSION_TTL}
    return jwt.encode(claims, AUTH_SECRET, algorithm="HS256")


def read_token(token: str | None) -> dict | None:
    if not token:
        return None
    try:
        return jwt.decode(token, AUTH_SECRET, algorithms=["HS256"], issuer="autoslot-auth")
    except jwt.PyJWTError:  # підпис не збігся, термін минув, інший видавець
        return None


@asynccontextmanager
async def lifespan(_: FastAPI):
    wait_for_db(engine)
    init_schema(engine, Base.metadata, lock_name="auth")
    yield


app = FastAPI(title="AutoSlot auth-service", version=VERSION, lifespan=lifespan)
metrics.setup(app)


def session():
    with SessionLocal() as s:
        yield s


class CodeRequest(BaseModel):
    email: str


class VerifyRequest(BaseModel):
    email: str
    code: str


@app.get("/health")
def health(s=Depends(session)):
    s.execute(select(1))
    return {"service": SERVICE_NAME, "status": "ok", "version": VERSION}


@app.post("/api/auth/request-code")
def request_code(body: CodeRequest, s=Depends(session)):
    email = normalize_email(body.email)
    recent = s.scalar(select(func.count()).select_from(LoginCode)
                      .where(LoginCode.email == email, LoginCode.created_at > now() - CODES_WINDOW))
    if recent >= MAX_CODES:
        raise HTTPException(429, "Забагато запитів коду. Спробуйте через кілька хвилин")
    code = f"{secrets.randbelow(1_000_000):06d}"  # криптографічно стійкий генератор
    s.add(LoginCode(id=str(uuid.uuid4()), email=email, code_hash=hash_code(email, code),
                    created_at=now(), expires_at=now() + CODE_TTL, attempts=0, used=0))
    s.commit()
    mailer.send(email, "Код входу AutoSlot",
                f"Ваш код входу: {code}\nКод дійсний {int(CODE_TTL.total_seconds() // 60)} хвилин. "
                "Якщо ви не намагались увійти, просто проігноруйте цей лист.")
    LOGIN_CODES.inc()
    # Відповідь однакова для нових і наявних адрес, щоб не можна було дізнатися,
    # хто зареєстрований.
    result = {"sent": True}
    if DEV_MODE and not mailer.enabled():
        result["devCode"] = code
    return result


@app.post("/api/auth/verify")
def verify(body: VerifyRequest, response: Response, s=Depends(session)):
    email = normalize_email(body.email)
    entry = s.scalar(select(LoginCode).where(LoginCode.email == email, LoginCode.used == 0,
                                             LoginCode.expires_at > now())
                     .order_by(LoginCode.created_at.desc()).limit(1))
    if not entry:
        LOGIN_FAILURES.labels("no_valid_code").inc()
        raise HTTPException(400, "Код невірний або його термін минув. Запросіть новий")
    entry.attempts += 1
    if entry.attempts > MAX_ATTEMPTS:
        LOGIN_FAILURES.labels("too_many_attempts").inc()
        entry.used = 1  # анулюємо код: перебрати 1 000 000 варіантів за 5 спроб неможливо
        s.commit()
        raise HTTPException(429, "Забагато спроб. Запросіть новий код")
    # compare_digest порівнює за сталий час, щоб за часом відповіді не можна було вгадувати код
    if not hmac.compare_digest(entry.code_hash, hash_code(email, body.code.strip())):
        s.commit()
        LOGIN_FAILURES.labels("wrong_code").inc()
        raise HTTPException(400, "Код невірний або його термін минув. Запросіть новий")
    entry.used = 1

    role = "ADMIN" if email in ADMIN_EMAILS else "CLIENT"
    user = s.scalar(select(User).where(User.email == email))
    if not user:
        user = User(id=str(uuid.uuid4()), email=email, role=role, created_at=now())
        s.add(user)
    user.role, user.last_login_at = role, now()
    s.commit()
    log.info("login %s (%s)", email, role)
    LOGINS.labels(role).inc()

    response.set_cookie(COOKIE_NAME, make_token(user), max_age=int(SESSION_TTL.total_seconds()),
                        httponly=True,      # JavaScript сторінки не бачить токен -> XSS не вкраде його
                        secure=COOKIE_SECURE,  # лише по HTTPS
                        samesite="lax",     # не надсилається з POST-запитами з чужих сайтів (CSRF)
                        path="/")
    return {"email": user.email, "role": user.role}


@app.post("/api/auth/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@app.get("/api/auth/me")
def me(autoslot_session: str | None = Cookie(None)):
    claims = read_token(autoslot_session)
    if not claims:
        raise HTTPException(401, "Потрібно увійти")
    return {"email": claims["email"], "role": claims["role"]}


@app.get("/internal/verify")
def internal_verify(response: Response, autoslot_session: str | None = Cookie(None)):
    """Викликається nginx (auth_request) перед кожним захищеним запитом."""
    claims = read_token(autoslot_session)
    if not claims:
        raise HTTPException(401, "Потрібно увійти")
    response.headers["X-User-Id"] = claims["sub"]
    response.headers["X-User-Email"] = claims["email"]
    response.headers["X-User-Role"] = claims["role"]
    return {"ok": True}
