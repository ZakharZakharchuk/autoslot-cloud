"""Спільні утиліти БД. Кожен сервіс має власну базу даних (database per service)."""
import logging
import os
import time

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

log = logging.getLogger("db")


def normalize_url(url: str) -> str:
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg://", 1)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


def make_db(default_sqlite: str):
    url = normalize_url(os.getenv("DATABASE_URL", f"sqlite:///./{default_sqlite}"))
    args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    engine = create_engine(url, connect_args=args, pool_pre_ping=True)
    return engine, sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def wait_for_db(engine, attempts: int = 30, delay: float = 1.0) -> None:
    """Контейнер БД може стартувати пізніше — чекаємо, поки вона прийме з'єднання."""
    for i in range(1, attempts + 1):
        try:
            with engine.connect() as c:
                c.execute(text("SELECT 1"))
            return
        except Exception as exc:  # noqa: BLE001
            log.warning("БД недоступна (спроба %s/%s): %s", i, attempts, exc)
            time.sleep(delay)
    raise RuntimeError("Не вдалося під'єднатися до БД")


def init_schema(engine, metadata, seed=None, lock_name: str = "schema") -> None:
    """Створює таблиці й початкові дані так, щоб кілька реплік не заважали одна одній.

    Репліки одного сервісу стартують одночасно. Без захисту обидві спробували б
    створити таблиці й вставити ті самі початкові дані в порожню БД, і одна впала б
    на дублікаті ключа. Тому перша репліка бере advisory lock PostgreSQL, робить
    роботу й відпускає його; друга чекає на блокуванні й далі бачить готову схему.
    """
    if engine.dialect.name != "postgresql":  # SQLite (тести, локальний запуск) — одна репліка
        metadata.create_all(engine)
        if seed:
            seed()
        return
    # будь-яке ціле число, однакове для всіх реплік (hash() у Python між процесами різний)
    lock_id = sum(ord(c) * 31**i for i, c in enumerate(lock_name)) % 2**31
    with engine.connect() as conn:
        conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": lock_id})
        try:
            metadata.create_all(engine)
            if seed:
                seed()
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": lock_id})
            conn.commit()
