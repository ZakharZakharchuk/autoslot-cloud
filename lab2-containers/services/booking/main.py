"""booking-service: життєвий цикл бронювання.

Синхронно: POST availability /internal/holds (утримати слот).
Асинхронно: публікує BookingCreated і BookingCancelled у шину подій.
Оплата не реалізується — бронювання одразу CONFIRMED.

Права доступу:
  клієнт  — створює записи, бачить і скасовує лише свої (за email із сесії);
  адмін   — бачить усі записи (/api/admin/*), може скасувати будь-який;
            кожна дія адміна записується в журнал admin_actions.
"""
import os
import uuid
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone

import httpx
from prometheus_client import Counter
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import DateTime, Integer, String, func, or_, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from common.db import init_schema, make_db, wait_for_db
from common.events import EventBus
from common.identity import Identity, admin_user, current_user
from common import metrics
from common.web import SERVICE_NAME, VERSION

AVAILABILITY_URL = os.getenv("AVAILABILITY_URL", "http://localhost:8102")
engine, SessionLocal = make_db("booking.db")
# Один клієнт на весь процес: пул з'єднань (keep-alive) замість нового TCP-з'єднання на кожен запит.
availability_http = httpx.Client(base_url=AVAILABILITY_URL, timeout=5,
                                 limits=httpx.Limits(max_connections=20, max_keepalive_connections=20))
bus = EventBus()
BOOKINGS_CREATED = Counter("bookings_created_total", "Створені записи", ["service_id"])
BOOKINGS_CANCELLED = Counter("bookings_cancelled_total", "Скасовані записи", ["by"])
BOOKING_REJECTED = Counter("bookings_rejected_total", "Відхилені спроби запису", ["status"])


class Base(DeclarativeBase):
    pass


class Booking(Base):
    __tablename__ = "bookings"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    hold_id: Mapped[str] = mapped_column(String(36))
    client_name: Mapped[str] = mapped_column(String(120))
    user_email: Mapped[str] = mapped_column(String(254), index=True)  # власник запису
    phone: Mapped[str] = mapped_column(String(32))
    # Назви послуги/майстра зберігаються як знімок на момент бронювання,
    # щоб читати історію без синхронних викликів до catalog-service.
    service_id: Mapped[str] = mapped_column(String(40))
    service_name: Mapped[str] = mapped_column(String(120))
    master_id: Mapped[str] = mapped_column(String(40))
    master_name: Mapped[str] = mapped_column(String(120))
    start: Mapped[datetime] = mapped_column(DateTime)
    end: Mapped[datetime] = mapped_column(DateTime)
    price_uah: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(12), default="CONFIRMED")
    created_at: Mapped[datetime] = mapped_column(DateTime)


class AdminAction(Base):
    """Журнал дій адміністраторів: хто, що і коли зробив."""
    __tablename__ = "admin_actions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    admin_email: Mapped[str] = mapped_column(String(254))
    action: Mapped[str] = mapped_column(String(40))
    booking_id: Mapped[str] = mapped_column(String(36))
    at: Mapped[datetime] = mapped_column(DateTime)


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


@asynccontextmanager
async def lifespan(_: FastAPI):
    wait_for_db(engine)
    init_schema(engine, Base.metadata, lock_name="booking")
    yield


app = FastAPI(title="AutoSlot booking-service", version=VERSION, lifespan=lifespan)
metrics.setup(app)


def session():
    with SessionLocal() as s:
        yield s


def norm_phone(p: str) -> str:
    return "".join(ch for ch in p if ch.isdigit() or ch == "+")


def out(b: Booking) -> dict:
    return {"id": b.id, "serviceId": b.service_id, "serviceName": b.service_name, "masterId": b.master_id,
            "masterName": b.master_name, "start": b.start, "end": b.end, "priceUah": b.price_uah,
            "status": b.status, "clientName": b.client_name, "phone": b.phone, "userEmail": b.user_email,
            "createdAt": b.created_at}


def event_payload(b: Booking) -> dict:
    return {"bookingId": b.id, "holdId": b.hold_id, "userEmail": b.user_email, "phone": b.phone,
            "clientName": b.client_name,
            "serviceName": b.service_name, "masterName": b.master_name,
            "start": b.start.isoformat(), "end": b.end.isoformat(), "priceUah": b.price_uah}


class BookingIn(BaseModel):
    serviceId: str
    masterId: str
    start: datetime
    clientName: str = Field(min_length=2, max_length=120)
    phone: str = Field(pattern=r"^\+?[0-9 ()-]{10,20}$")


@app.get("/health")
def health(s=Depends(session)):
    s.execute(select(1))
    return {"service": SERVICE_NAME, "status": "ok", "version": VERSION}


@app.post("/api/bookings", status_code=201)
def create_booking(body: BookingIn, s=Depends(session), user: Identity = Depends(current_user),
                   idempotency_key: str | None = Header(None)):
    key = idempotency_key or str(uuid.uuid4())
    try:
        r = availability_http.post("/internal/holds", json={
            "serviceId": body.serviceId, "masterId": body.masterId,
            "start": body.start.isoformat(), "idempotencyKey": key})
    except httpx.HTTPError as exc:
        raise HTTPException(503, "Сервіс розкладу тимчасово недоступний") from exc
    if r.status_code >= 400:
        BOOKING_REJECTED.labels(str(r.status_code)).inc()
        raise HTTPException(r.status_code, r.json().get("detail", "Не вдалося утримати слот"))
    hold = r.json()

    existing = s.scalar(select(Booking).where(Booking.hold_id == hold["holdId"]))
    if existing:  # повтор того самого запиту (той самий Idempotency-Key)
        return out(existing)

    b = Booking(id=str(uuid.uuid4()), hold_id=hold["holdId"], user_email=user.email,
                client_name=body.clientName.strip(),
                phone=norm_phone(body.phone), service_id=hold["serviceId"], service_name=hold["serviceName"],
                master_id=hold["masterId"], master_name=hold["masterName"],
                start=datetime.fromisoformat(hold["start"]), end=datetime.fromisoformat(hold["end"]),
                price_uah=hold["priceUah"], status="CONFIRMED",
                created_at=utcnow())
    s.add(b)
    s.commit()
    bus.publish("BookingCreated", event_payload(b))
    BOOKINGS_CREATED.labels(b.service_id).inc()
    return out(b)


@app.get("/api/bookings")
def list_my_bookings(s=Depends(session), user: Identity = Depends(current_user)):
    rows = s.scalars(select(Booking).where(Booking.user_email == user.email).order_by(Booking.start.desc()))
    return [out(b) for b in rows]


@app.post("/api/bookings/{booking_id}/cancel")
def cancel(booking_id: str, s=Depends(session), user: Identity = Depends(current_user)):
    b = s.get(Booking, booking_id)
    # Чужий запис для клієнта виглядає як неіснуючий: так не можна перебором
    # дізнатися, які id існують.
    if not b or (b.user_email != user.email and not user.is_admin):
        raise HTTPException(404, "Бронювання не знайдено")
    if b.status != "CANCELLED":
        b.status = "CANCELLED"
        if user.is_admin and b.user_email != user.email:
            s.add(AdminAction(id=str(uuid.uuid4()), admin_email=user.email, action="CANCEL_BOOKING",
                              booking_id=b.id, at=utcnow()))
        s.commit()
        bus.publish("BookingCancelled", event_payload(b))
        BOOKINGS_CANCELLED.labels("admin" if user.is_admin and b.user_email != user.email else "client").inc()
    return out(b)


# ---------- адміністрування ----------

def _period(date_from: date | None, date_to: date | None):
    d0 = datetime.combine(date_from or date.today() - timedelta(days=30), datetime.min.time())
    d1 = datetime.combine(date_to or date.today() + timedelta(days=60), datetime.min.time()) + timedelta(days=1)
    return d0, d1


@app.get("/api/admin/bookings")
def admin_bookings(dateFrom: date | None = None, dateTo: date | None = None, masterId: str | None = None,
                   status: str | None = None, q: str | None = None,
                   s=Depends(session), admin: Identity = Depends(admin_user)):
    d0, d1 = _period(dateFrom, dateTo)
    query = select(Booking).where(Booking.start >= d0, Booking.start < d1)
    if masterId:
        query = query.where(Booking.master_id == masterId)
    if status:
        query = query.where(Booking.status == status)
    if q:  # пошук за ім'ям, email або телефоном
        like = f"%{q.strip().lower()}%"
        query = query.where(or_(func.lower(Booking.client_name).like(like),
                                func.lower(Booking.user_email).like(like), Booking.phone.like(like)))
    return [out(b) for b in s.scalars(query.order_by(Booking.start).limit(500))]


@app.get("/api/admin/stats")
def admin_stats(dateFrom: date | None = None, dateTo: date | None = None,
                s=Depends(session), admin: Identity = Depends(admin_user)):
    """Завантаженість майстрів за період: кількість записів і сума."""
    d0, d1 = _period(dateFrom, dateTo)
    rows = s.execute(select(Booking.master_id, Booking.master_name, Booking.status, func.count(),
                            func.coalesce(func.sum(Booking.price_uah), 0))
                     .where(Booking.start >= d0, Booking.start < d1)
                     .group_by(Booking.master_id, Booking.master_name, Booking.status)).all()
    masters: dict[str, dict] = {}
    for mid, name, st, cnt, total in rows:
        m = masters.setdefault(mid, {"masterId": mid, "masterName": name, "confirmed": 0, "cancelled": 0,
                                     "revenueUah": 0})
        if st == "CONFIRMED":
            m["confirmed"] += cnt
            m["revenueUah"] += int(total)
        else:
            m["cancelled"] += cnt
    return sorted(masters.values(), key=lambda m: m["masterName"])


@app.get("/api/admin/audit")
def admin_audit(s=Depends(session), admin: Identity = Depends(admin_user)):
    rows = s.scalars(select(AdminAction).order_by(AdminAction.at.desc()).limit(100))
    return [{"adminEmail": a.admin_email, "action": a.action, "bookingId": a.booking_id, "at": a.at} for a in rows]
