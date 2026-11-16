"""Модуль сповіщень моноліту.

На відміну від мікросервісної версії (лаб. 2), де notification-service отримує
події з Redis Streams асинхронно, тут сповіщення створюються прямим викликом
функції в тій самій транзакції БД, що й запис. Відправка SMS імітується журналом.
"""
import logging
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .models import Booking, Notification
from .slots import now_local

log = logging.getLogger("notifications")


def _fmt(dt) -> str:
    return dt.strftime("%d.%m о %H:%M")


def on_booking_created(s: Session, b: Booking) -> None:
    now = now_local()
    s.add(Notification(booking_id=b.id, phone=b.phone, kind="CONFIRMATION", status="SENT", send_at=now,
                       text=f"{b.client_name}, вас записано: {b.service.name}, {_fmt(b.start)}, "
                            f"майстер {b.master.name}. Вартість {b.price_uah} грн."))
    for kind, delta in (("REMINDER_24H", timedelta(hours=24)), ("REMINDER_2H", timedelta(hours=2))):
        if b.start - delta > now:
            s.add(Notification(booking_id=b.id, phone=b.phone, kind=kind, status="SCHEDULED",
                               send_at=b.start - delta, text=f"Нагадування: {b.service.name} {_fmt(b.start)}."))
    log.info("[SMS -> %s] підтвердження запису %s", b.phone, b.id)


def on_booking_cancelled(s: Session, b: Booking) -> None:
    s.execute(update(Notification).where(Notification.booking_id == b.id, Notification.status == "SCHEDULED")
              .values(status="CANCELLED"))
    s.add(Notification(booking_id=b.id, phone=b.phone, kind="CANCELLATION", status="SENT", send_at=now_local(),
                       text=f"Запис на {_fmt(b.start)} ({b.service.name}) скасовано."))
    log.info("[SMS -> %s] скасування запису %s", b.phone, b.id)


def list_for_phone(s: Session, phone: str) -> list[Notification]:
    return list(s.scalars(select(Notification).where(Notification.phone == phone, Notification.status != "CANCELLED")
                          .order_by(Notification.send_at).limit(20)))
