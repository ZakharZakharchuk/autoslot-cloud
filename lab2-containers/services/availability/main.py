"""availability-service: графіки майстрів, вільні слоти, резервування (SlotHold).

Власник даних про зайнятість майстрів (ADR-001 з архітектури AutoSlot).
Утримання слоту з TTL та ідемпотентним ключем (ADR-003).
Споживає події BookingCreated (hold -> CONFIRMED) і BookingCancelled (-> RELEASED).
Кілька послуг за один візит (serviceIds=a,b) виконує один майстер підряд, тож для
них шукається одне вікно сумарної тривалості в майстра, який уміє всі ці послуги.
"""
import logging
import os
import threading
import uuid
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta

import httpx
from prometheus_client import Counter
from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import DateTime, Integer, String, select, update
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from common.db import init_schema, make_db, wait_for_db
from common.events import EventBus
from common.timeutil import candidate_starts, now_local, overlaps
from common import metrics
from common.web import SERVICE_NAME, VERSION

log = logging.getLogger("availability")
CATALOG_URL = os.getenv("CATALOG_URL", "http://localhost:8101")
HOLD_TTL = timedelta(seconds=int(os.getenv("HOLD_TTL_SECONDS", "300")))
engine, SessionLocal = make_db("availability.db")
HOLDS = Counter("slot_holds_total", "Спроби утримати слот", ["result"])
# Один клієнт на весь процес: пул з'єднань (keep-alive) замість нового TCP-з'єднання на кожен запит.
catalog_http = httpx.Client(base_url=CATALOG_URL, timeout=3,
                            limits=httpx.Limits(max_connections=20, max_keepalive_connections=20))


class Base(DeclarativeBase):
    pass


class WorkSchedule(Base):
    __tablename__ = "work_schedules"
    master_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    start_hour: Mapped[int] = mapped_column(Integer)
    end_hour: Mapped[int] = mapped_column(Integer)
    days: Mapped[str] = mapped_column(String(20))  # 0 = понеділок

    def works_on(self, d: date) -> bool:
        return str(d.weekday()) in self.days.split(",")


class Reservation(Base):
    """SlotHold, що після події BookingCreated стає підтвердженим слотом."""
    __tablename__ = "reservations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(80), unique=True)
    master_id: Mapped[str] = mapped_column(String(40), index=True)
    service_id: Mapped[str] = mapped_column(String(200))  # одна послуга або кілька через кому
    start: Mapped[datetime] = mapped_column(DateTime, index=True)
    end: Mapped[datetime] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(12))  # HELD | CONFIRMED | RELEASED | EXPIRED
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    booking_id: Mapped[str | None] = mapped_column(String(36), nullable=True)


MIGRATIONS = ("ALTER TABLE reservations ALTER COLUMN service_id TYPE VARCHAR(200)",)
SCHEDULES = [("master-1", 9, 18, "0,1,2,3,4"), ("master-2", 8, 17, "0,1,2,3,4,5"), ("master-3", 10, 19, "1,2,3,4,5")]


def seed() -> None:
    with SessionLocal() as s:
        if s.scalar(select(WorkSchedule.master_id).limit(1)):
            return
        s.add_all(WorkSchedule(master_id=m, start_hour=a, end_hour=b, days=d) for m, a, b, d in SCHEDULES)
        s.commit()


def handle_event(event: dict) -> None:
    p = event["payload"]
    with SessionLocal() as s:
        if event["type"] == "BookingCreated":
            s.execute(update(Reservation).where(Reservation.id == p["holdId"], Reservation.status != "RELEASED")
                      .values(status="CONFIRMED", booking_id=p["bookingId"]))
        elif event["type"] == "BookingCancelled":
            s.execute(update(Reservation).where(Reservation.id == p["holdId"]).values(status="RELEASED"))
        s.commit()


def expire_holds_forever(stop: threading.Event) -> None:
    """Фонове очищення протермінованих hold (наслідок ADR-003)."""
    while not stop.wait(30):
        try:
            with SessionLocal() as s:
                n = s.execute(update(Reservation).where(Reservation.status == "HELD",
                                                        Reservation.expires_at < now_local())
                              .values(status="EXPIRED")).rowcount
                s.commit()
            if n:
                log.info("expired holds: %s", n)
        except Exception:  # noqa: BLE001
            log.exception("expire job failed")


@asynccontextmanager
async def lifespan(_: FastAPI):
    wait_for_db(engine)
    init_schema(engine, Base.metadata, seed, "availability", MIGRATIONS)
    stop = EventBus().start_consumer("availability", handle_event)
    threading.Thread(target=expire_holds_forever, args=(stop,), daemon=True).start()
    yield
    stop.set()


app = FastAPI(title="AutoSlot availability-service", version=VERSION, lifespan=lifespan)
metrics.setup(app)


def session():
    with SessionLocal() as s:
        yield s


def catalog_service(service_ids: str) -> dict:
    """Послуга або кілька послуг через кому: сумарні тривалість і ціна, спільні майстри."""
    try:
        r = catalog_http.get("/internal/bundle", params={"ids": service_ids})
    except httpx.HTTPError as exc:
        raise HTTPException(503, "Каталог тимчасово недоступний") from exc
    if r.status_code in (404, 422):
        raise HTTPException(r.status_code, r.json().get("detail", "Послугу не знайдено"))
    r.raise_for_status()
    return r.json()


def busy_intervals(s, master_id: str, day: date):
    d0 = datetime.combine(day, datetime.min.time())
    rows = s.execute(select(Reservation.start, Reservation.end).where(
        Reservation.master_id == master_id, Reservation.start >= d0, Reservation.start < d0 + timedelta(days=1),
        (Reservation.status == "CONFIRMED") | ((Reservation.status == "HELD") & (Reservation.expires_at > now_local())),
    )).all()
    return [(r.start, r.end) for r in rows]


@app.get("/health")
def health(s=Depends(session)):
    s.execute(select(1))
    return {"service": SERVICE_NAME, "status": "ok", "version": VERSION}


DAY_NAMES = ["пн", "вт", "ср", "чт", "пт", "сб", "нд"]


@app.get("/api/availability/schedules")
def schedules(s=Depends(session)):
    """Робочі графіки майстрів (для адмін-панелі)."""
    return [{"masterId": w.master_id, "startHour": w.start_hour, "endHour": w.end_hour,
             "days": [int(d) for d in w.days.split(",")],
             "daysText": ", ".join(DAY_NAMES[int(d)] for d in w.days.split(","))}
            for w in s.scalars(select(WorkSchedule).order_by(WorkSchedule.master_id))]


@app.get("/api/availability")
def availability(date: date, serviceId: str | None = None, serviceIds: str | None = None, s=Depends(session)):
    if not (serviceIds or serviceId):
        raise HTTPException(422, "Оберіть послугу")
    svc = catalog_service(serviceIds or serviceId)
    duration = timedelta(minutes=svc["durationMin"])
    now = now_local()
    result = []
    for m in svc["masters"]:
        sch = s.get(WorkSchedule, m["id"])
        if not sch or not sch.works_on(date):
            continue
        busy = busy_intervals(s, m["id"], date)
        for start in candidate_starts(date, sch.start_hour, sch.end_hour, duration):
            end = start + duration
            if start >= now and not any(overlaps(start, end, a, b) for a, b in busy):
                result.append({"masterId": m["id"], "masterName": m["name"], "start": start, "end": end})
    result.sort(key=lambda x: (x["start"], x["masterName"]))
    return result


class HoldIn(BaseModel):
    serviceId: str  # одна послуга або кілька через кому
    masterId: str
    start: datetime
    idempotencyKey: str


def hold_out(r: Reservation, svc: dict, master_name: str) -> dict:
    return {"holdId": r.id, "masterId": r.master_id, "masterName": master_name, "serviceId": svc["id"],
            "serviceName": svc["name"], "priceUah": svc["priceUah"], "start": r.start, "end": r.end,
            "status": r.status, "expiresAt": r.expires_at}


@app.post("/internal/holds", status_code=201)
def create_hold(body: HoldIn, s=Depends(session)):
    svc = catalog_service(body.serviceId)
    master = next((m for m in svc["masters"] if m["id"] == body.masterId), None)
    if not master:
        raise HTTPException(422, "Цей майстер не виконує обрані послуги")

    existing = s.scalar(select(Reservation).where(Reservation.idempotency_key == body.idempotencyKey))
    if existing:  # повторний запит з тим самим ключем — повертаємо той самий hold
        return hold_out(existing, svc, master["name"])

    start = body.start.replace(tzinfo=None, second=0, microsecond=0)
    end = start + timedelta(minutes=svc["durationMin"])
    # блокування рядка графіка майстра серіалізує паралельні спроби забронювати його час
    sch = s.scalar(select(WorkSchedule).where(WorkSchedule.master_id == body.masterId).with_for_update())
    valid = sch and sch.works_on(start.date()) and start in set(
        candidate_starts(start.date(), sch.start_hour, sch.end_hour, end - start))
    if not valid:
        raise HTTPException(422, "Обраний час не входить у робочий графік майстра")
    if start < now_local():
        raise HTTPException(422, "Не можна забронювати час у минулому")
    if any(overlaps(start, end, a, b) for a, b in busy_intervals(s, body.masterId, start.date())):
        HOLDS.labels("conflict").inc()
        raise HTTPException(409, "Цей час уже зайнятий — оберіть інший слот")

    r = Reservation(id=str(uuid.uuid4()), idempotency_key=body.idempotencyKey, master_id=body.masterId,
                    service_id=svc["id"], start=start, end=end, status="HELD",
                    expires_at=now_local() + HOLD_TTL)
    s.add(r)
    s.commit()
    HOLDS.labels("created").inc()
    return hold_out(r, svc, master["name"])
