"""AutoSlot — монолітна версія (лабораторна робота 3).

Функціонально еквівалентна мікросервісній версії з лаб. 2: каталог, розклад,
бронювання та сповіщення в одному процесі й одній БД. API та веб-клієнт ті самі.
Запуск локально:  uvicorn app.main:app --reload
"""
import logging
import os
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import Base, SessionLocal, engine, get_session
from . import notifications
from .models import Booking, Master, ServiceItem
from .seed import seed
from .slots import candidate_starts, free_slots, now_local, overlaps

STATIC_DIR = Path(__file__).parent / "static"
APP_VERSION = os.getenv("APP_VERSION", "3.0.0")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s [%(name)s] %(message)s")


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(engine)
    with SessionLocal() as s:
        seed(s)
    yield


app = FastAPI(title="AutoSlot API", version=APP_VERSION, lifespan=lifespan)


# ---------- схеми ----------

class ServiceOut(BaseModel):
    id: str
    name: str
    category: str
    durationMin: int
    priceUah: int


class MasterOut(BaseModel):
    id: str
    name: str
    serviceIds: list[str]


class SlotOut(BaseModel):
    masterId: str
    masterName: str
    start: datetime
    end: datetime


class BookingIn(BaseModel):
    serviceId: str
    masterId: str
    start: datetime
    clientName: str = Field(min_length=2, max_length=120)
    phone: str = Field(pattern=r"^\+?[0-9 ()-]{10,20}$")


class BookingOut(BaseModel):
    id: str
    serviceId: str
    serviceName: str
    masterId: str
    masterName: str
    start: datetime
    end: datetime
    priceUah: int
    status: str
    clientName: str
    phone: str


def _booking_out(b: Booking) -> BookingOut:
    return BookingOut(id=b.id, serviceId=b.service_id, serviceName=b.service.name,
                      masterId=b.master_id, masterName=b.master.name, start=b.start, end=b.end,
                      priceUah=b.price_uah, status=b.status, clientName=b.client_name, phone=b.phone)


def _normalize_phone(phone: str) -> str:
    return "".join(ch for ch in phone if ch.isdigit() or ch == "+")


def _busy_intervals(s: Session, master_id: str, day: date):
    day_start = datetime.combine(day, datetime.min.time())
    rows = s.execute(
        select(Booking.start, Booking.end).where(
            Booking.master_id == master_id,
            Booking.status == "CONFIRMED",
            Booking.start >= day_start,
            Booking.start < day_start + timedelta(days=1),
        )
    ).all()
    return [(r.start, r.end) for r in rows]


# ---------- API ----------

@app.get("/health", include_in_schema=False)
@app.get("/api/health")
def health(s: Session = Depends(get_session)):
    s.execute(select(1))
    return {"status": "ok", "version": APP_VERSION, "db": engine.dialect.name}


@app.get("/api/services", response_model=list[ServiceOut])
def list_services(s: Session = Depends(get_session)):
    items = s.scalars(select(ServiceItem).order_by(ServiceItem.category, ServiceItem.name))
    return [ServiceOut(id=i.id, name=i.name, category=i.category,
                       durationMin=i.duration_min, priceUah=i.price_uah) for i in items]


@app.get("/api/masters", response_model=list[MasterOut])
def list_masters(s: Session = Depends(get_session)):
    return [MasterOut(id=m.id, name=m.name, serviceIds=[x.id for x in m.skills])
            for m in s.scalars(select(Master).order_by(Master.name))]


@app.get("/api/availability", response_model=list[SlotOut])
def availability(serviceId: str, date: date = Query(..., alias="date"), s: Session = Depends(get_session)):
    service = s.get(ServiceItem, serviceId)
    if not service:
        raise HTTPException(404, "Послугу не знайдено")
    duration = timedelta(minutes=service.duration_min)
    result: list[SlotOut] = []
    for master in service.masters:
        busy = _busy_intervals(s, master.id, date)
        for start, end in free_slots(date, master, duration, busy, not_before=now_local()):
            result.append(SlotOut(masterId=master.id, masterName=master.name, start=start, end=end))
    result.sort(key=lambda x: (x.start, x.masterName))
    return result


@app.post("/api/bookings", response_model=BookingOut, status_code=201)
def create_booking(body: BookingIn, s: Session = Depends(get_session)):
    start = body.start.replace(tzinfo=None, second=0, microsecond=0)
    service = s.get(ServiceItem, body.serviceId)
    if not service:
        raise HTTPException(404, "Послугу не знайдено")
    # Блокуємо рядок майстра на час перевірки (SELECT ... FOR UPDATE у PostgreSQL),
    # щоб два паралельні запити не забронювали той самий час.
    master = s.scalar(select(Master).where(Master.id == body.masterId).with_for_update())
    if not master or service not in master.skills:
        raise HTTPException(422, "Цей майстер не виконує обрану послугу")
    duration = timedelta(minutes=service.duration_min)
    end = start + duration
    valid_starts = set(candidate_starts(start.date(), master.work_start_hour, master.work_end_hour, duration))
    if not master.works_on(start.weekday()) or start not in valid_starts:
        raise HTTPException(422, "Обраний час не входить у робочий графік майстра")
    if start < now_local():
        raise HTTPException(422, "Не можна забронювати час у минулому")
    if any(overlaps(start, end, b0, b1) for b0, b1 in _busy_intervals(s, master.id, start.date())):
        raise HTTPException(409, "Цей час уже зайнятий — оберіть інший слот")
    booking = Booking(client_name=body.clientName.strip(), phone=_normalize_phone(body.phone),
                      service_id=service.id, master_id=master.id, start=start, end=end,
                      price_uah=service.price_uah)
    s.add(booking)
    s.flush()
    notifications.on_booking_created(s, booking)  # та сама транзакція
    s.commit()
    return _booking_out(booking)


@app.get("/api/bookings", response_model=list[BookingOut])
def list_bookings(phone: str, s: Session = Depends(get_session)):
    rows = s.scalars(select(Booking).where(Booking.phone == _normalize_phone(phone))
                     .order_by(Booking.start.desc()))
    return [_booking_out(b) for b in rows]


@app.post("/api/bookings/{booking_id}/cancel", response_model=BookingOut)
def cancel_booking(booking_id: str, s: Session = Depends(get_session)):
    booking = s.get(Booking, booking_id)
    if not booking:
        raise HTTPException(404, "Бронювання не знайдено")
    if booking.status == "CANCELLED":
        return _booking_out(booking)  # ідемпотентно
    booking.status = "CANCELLED"
    notifications.on_booking_cancelled(s, booking)
    s.commit()
    return _booking_out(booking)


@app.get("/api/notifications")
def list_notifications(phone: str, s: Session = Depends(get_session)):
    return [{"kind": n.kind, "channel": n.channel, "status": n.status, "sendAt": n.send_at, "text": n.text}
            for n in notifications.list_for_phone(s, _normalize_phone(phone))]


# ---------- веб-клієнт ----------

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC_DIR / "index.html")
