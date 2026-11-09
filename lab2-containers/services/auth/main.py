"""auth-service: реєстрація з підтвердженням пошти, вхід за паролем, сесія в JWT,
ролі CLIENT і ADMIN.

Реєстрація (і відновлення пароля — те саме):
  1. POST /api/auth/request-code {email}  -> генеруємо 6-значний код, зберігаємо лише
     його хеш (HMAC) з терміном дії 10 хв і надсилаємо код листом.
  2. POST /api/auth/verify {email, code, password}  -> перевіряємо код (так ми знаємо,
     що пошта справді належить користувачу), створюємо користувача, зберігаємо хеш
     пароля і видаємо JWT у cookie autoslot_session (HttpOnly).
Подальші входи:
  POST /api/auth/login {email, password}  -> лист більше не потрібен.
Після входу браузер сам надсилає cookie з кожним запитом. Gateway (nginx) перед
захищеними запитами звертається до GET /internal/verify, і цей сервіс каже, хто
користувач: заголовки X-User-Id, X-User-Email, X-User-Role.

Пароль зберігається лише як хеш scrypt з випадковою сіллю: навіть з копією бази
відновити пароль можна тільки перебором, а scrypt робить перебір дорогим.
"""
import base64
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
PASSWORD_MIN, PASSWORD_MAX = 8, 128
MAX_PASSWORD_FAILS = 5                # невдалих паролів поспіль, після чого вхід блокується ...
LOCK_TIME = timedelta(minutes=15)     # ... на цей час (захист від перебору пароля)
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
    password_hash: Mapped[str | None] = mapped_column(String(200), nullable=True)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


# Бази, створені до появи паролів, отримують нові колонки при старті сервісу.
MIGRATIONS = (
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash VARCHAR(200)",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS failed_logins INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS locked_until TIMESTAMP",
)


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


# scrypt: n=2^14, r=8 — близько 16 МБ пам'яті й десятки мс на одну перевірку
SCRYPT = {"n": 2**14, "r": 8, "p": 1, "dklen": 32}


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, **SCRYPT)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()


def check_password(password: str, stored: str | None) -> bool:
    try:
        _, salt, digest = (stored or "").split("$")
        actual = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), **SCRYPT)
        return hmac.compare_digest(actual, base64.b64decode(digest))
    except ValueError:
        return False


# Для неіснуючої адреси все одно рахуємо scrypt, щоб за часом відповіді не можна
# було дізнатися, хто зареєстрований.
DUMMY_HASH = hash_password(secrets.token_hex(8))


def check_new_password(password: str) -> str:
    if not PASSWORD_MIN <= len(password) <= PASSWORD_MAX:
        raise HTTPException(422, f"Пароль має містити від {PASSWORD_MIN} до {PASSWORD_MAX} символів")
    return password


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
    init_schema(engine, Base.metadata, lock_name="auth", migrations=MIGRATIONS)
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
    password: str | None = None  # задається при реєстрації та відновленні пароля


class LoginRequest(BaseModel):
    email: str
    password: str


def start_session(response: Response, user: User, s) -> dict:
    user.last_login_at, user.failed_logins, user.locked_until = now(), 0, None
    s.commit()
    log.info("login %s (%s)", user.email, user.role)
    LOGINS.labels(user.role).inc()
    response.set_cookie(COOKIE_NAME, make_token(user), max_age=int(SESSION_TTL.total_seconds()),
                        httponly=True,      # JavaScript сторінки не бачить токен -> XSS не вкраде його
                        secure=COOKIE_SECURE,  # лише по HTTPS
                        samesite="lax",     # не надсилається з POST-запитами з чужих сайтів (CSRF)
                        path="/")
    return {"email": user.email, "role": user.role}


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
    mailer.send(email, "Код підтвердження AutoSlot",
                f"Ваш код підтвердження: {code}\nКод дійсний {int(CODE_TTL.total_seconds() // 60)} хвилин. "
                "Введіть його на сайті й задайте пароль — далі входитимете за паролем.\n"
                "Якщо ви не реєструвались в AutoSlot, просто проігноруйте цей лист.")
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
    password = check_new_password(body.password) if body.password is not None else None
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
        user = User(id=str(uuid.uuid4()), email=email, role=role, created_at=now(), failed_logins=0)
        s.add(user)
    user.role = role
    if password is not None:  # пошту підтверджено кодом — можна задати (або змінити) пароль
        user.password_hash = hash_password(password)
    return start_session(response, user, s)


@app.post("/api/auth/login")
def login(body: LoginRequest, response: Response, s=Depends(session)):
    email = normalize_email(body.email)
    user = s.scalar(select(User).where(User.email == email))
    if user and user.locked_until and user.locked_until > now():
        LOGIN_FAILURES.labels("locked").inc()
        raise HTTPException(429, "Забагато невдалих спроб. Спробуйте через 15 хвилин або відновіть пароль")
    ok = check_password(body.password, user.password_hash if user and user.password_hash else DUMMY_HASH)
    if not user or not user.password_hash or not ok:
        if user:
            user.failed_logins = (user.failed_logins or 0) + 1
            if user.failed_logins >= MAX_PASSWORD_FAILS:
                user.locked_until, user.failed_logins = now() + LOCK_TIME, 0
            s.commit()
        LOGIN_FAILURES.labels("wrong_password").inc()
        # однакова відповідь для неіснуючої адреси й невірного пароля
        raise HTTPException(400, "Невірна пошта або пароль")
    user.role = "ADMIN" if email in ADMIN_EMAILS else "CLIENT"
    return start_session(response, user, s)


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
