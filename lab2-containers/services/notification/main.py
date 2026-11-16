"""notification-service: підтвердження та нагадування клієнтам.

Споживач подій BookingCreated і BookingCancelled. Листи надсилаються на email
власника запису через common/mailer.py (SMTP, або лише журнал, якщо SMTP не задано).
Нагадування за 24 і за 2 години зберігаються як SCHEDULED, а фоновий планувальник
раз на хвилину надсилає ті, чий час настав.
"""
import logging
import threading
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from prometheus_client import Counter
from fastapi import Depends, FastAPI
from sqlalchemy import DateTime, String, UniqueConstraint, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from common import mailer
from common.db import init_schema, make_db, wait_for_db
from common.events import EventBus
from common.identity import Identity, current_user
from common.timeutil import now_local
from common import metrics
from common.web import SERVICE_NAME, VERSION

log = logging.getLogger("notification")
engine, SessionLocal = make_db("notification.db")


class Base(DeclarativeBase):
    pass


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (UniqueConstraint("event_id", "kind"),)  # ідемпотентність при повторній доставці
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(36))
    kind: Mapped[str] = mapped_column(String(20))  # CONFIRMATION | REMINDER_24H | REMINDER_2H | CANCELLATION
    booking_id: Mapped[str] = mapped_column(String(36), index=True)
    user_email: Mapped[str] = mapped_column(String(254), index=True)
    channel: Mapped[str] = mapped_column(String(10), default="email")
    text: Mapped[str] = mapped_column(String(400))
    status: Mapped[str] = mapped_column(String(12))  # SENDING | SENT | SCHEDULED | CANCELLED | FAILED
    send_at: Mapped[datetime] = mapped_column(DateTime)


def _fmt(iso: str) -> str:
    return datetime.fromisoformat(iso).strftime("%d.%m о %H:%M")


def handle_event(event: dict) -> None:
    p, eid = event["payload"], event["id"]
    now = now_local()  # увесь час у сервісі — локальний час автосервісу
    items: list[Notification] = []
    if event["type"] == "BookingCreated":
        start = datetime.fromisoformat(p["start"])
        items.append(Notification(kind="CONFIRMATION", status="SENDING", send_at=now,
                                  text=f"{p['clientName']}, вас записано: {p['serviceName']}, {_fmt(p['start'])}, "
                                       f"майстер {p['masterName']}. Вартість {p['priceUah']} грн."))
        for kind, delta in (("REMINDER_24H", timedelta(hours=24)), ("REMINDER_2H", timedelta(hours=2))):
            if start - delta <= now:
                continue  # запис зроблено пізніше, ніж мало б піти нагадування
            items.append(Notification(kind=kind, status="SCHEDULED", send_at=start - delta,
                                      text=f"Нагадування: {p['serviceName']} {_fmt(p['start'])}."))
    elif event["type"] == "BookingCancelled":
        with SessionLocal() as s:  # скасовуємо заплановані нагадування
            for n in s.scalars(select(Notification).where(Notification.booking_id == p["bookingId"],
                                                          Notification.status == "SCHEDULED")):
                n.status = "CANCELLED"
            s.commit()
        items.append(Notification(kind="CANCELLATION", status="SENDING", send_at=now,
                                  text=f"Запис на {_fmt(p['start'])} ({p['serviceName']}) скасовано."))
    for n in items:
        n.id, n.event_id, n.booking_id, n.user_email = str(uuid.uuid4()), eid, p["bookingId"], p["userEmail"]
        with SessionLocal() as s:
            try:
                s.add(n)
                s.commit()
            except IntegrityError:  # подію вже оброблено раніше — лист не дублюємо
                s.rollback()
                continue
        if n.status == "SENDING":
            deliver(n.id)


EMAILS = Counter("emails_total", "Листи клієнтам", ["kind", "status"])

SUBJECTS = {"CONFIRMATION": "Запис підтверджено", "REMINDER_24H": "Нагадування про запис",
            "REMINDER_2H": "Нагадування про запис", "CANCELLATION": "Запис скасовано"}


def deliver(notification_id: str) -> None:
    """Надсилає лист і фіксує результат. Помилка пошти не ламає обробку події."""
    with SessionLocal() as s:
        n = s.get(Notification, notification_id)
        try:
            mailer.send(n.user_email, f"AutoSlot: {SUBJECTS[n.kind]}", n.text)
            n.status = "SENT"
        except Exception:  # noqa: BLE001
            log.exception("не вдалося надіслати лист %s", n.id)
            n.status = "FAILED"
        s.commit()
        EMAILS.labels(n.kind, n.status).inc()


def send_due_reminders(stop: threading.Event) -> None:
    """Раз на хвилину надсилає нагадування, час яких настав.

    FOR UPDATE SKIP LOCKED: якщо працюють дві репліки notification, кожна бере лише
    ті рядки, які не заблокувала інша, тож нагадування не надсилається двічі.
    """
    while not stop.wait(60):
        try:
            with SessionLocal() as s:
                q = select(Notification.id).where(Notification.status == "SCHEDULED",
                                                  Notification.send_at <= now_local()).limit(50)
                if engine.dialect.name == "postgresql":
                    q = q.with_for_update(skip_locked=True)
                due = list(s.scalars(q))
                for nid in due:
                    s.get(Notification, nid).status = "SENDING"
                s.commit()
            for nid in due:
                deliver(nid)
        except Exception:  # noqa: BLE001
            log.exception("помилка планувальника нагадувань")


@asynccontextmanager
async def lifespan(_: FastAPI):
    wait_for_db(engine)
    init_schema(engine, Base.metadata, lock_name="notification")
    stop = EventBus().start_consumer("notification", handle_event)
    threading.Thread(target=send_due_reminders, args=(stop,), daemon=True).start()
    yield
    stop.set()


app = FastAPI(title="AutoSlot notification-service", version=VERSION, lifespan=lifespan)
metrics.setup(app)


def session():
    with SessionLocal() as s:
        yield s


@app.get("/health")
def health(s=Depends(session)):
    s.execute(select(1))
    return {"service": SERVICE_NAME, "status": "ok", "version": VERSION}


@app.get("/api/notifications")
def list_notifications(s=Depends(session), user: Identity = Depends(current_user)):
    rows = s.scalars(select(Notification).where(Notification.user_email == user.email,
                                                Notification.status != "CANCELLED")
                     .order_by(Notification.send_at).limit(20))
    return [{"kind": n.kind, "channel": n.channel, "status": n.status, "sendAt": n.send_at, "text": n.text}
            for n in rows]
