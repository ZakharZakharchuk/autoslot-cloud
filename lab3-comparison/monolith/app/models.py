import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Table
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base

master_skill = Table(
    "master_skill",
    Base.metadata,
    Column("master_id", ForeignKey("masters.id"), primary_key=True),
    Column("service_id", ForeignKey("services.id"), primary_key=True),
)


class ServiceItem(Base):
    """Послуга автосервісу (каталог)."""

    __tablename__ = "services"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    category: Mapped[str] = mapped_column(String(60))
    duration_min: Mapped[int] = mapped_column(Integer)
    price_uah: Mapped[int] = mapped_column(Integer)

    masters: Mapped[list["Master"]] = relationship(secondary=master_skill, back_populates="skills")


class Master(Base):
    """Майстер і його графік роботи (спрощено: однакові години в усі робочі дні)."""

    __tablename__ = "masters"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    work_start_hour: Mapped[int] = mapped_column(Integer, default=9)
    work_end_hour: Mapped[int] = mapped_column(Integer, default=18)
    # робочі дні тижня, 0 = понеділок
    work_days: Mapped[str] = mapped_column(String(20), default="0,1,2,3,4,5")

    skills: Mapped[list[ServiceItem]] = relationship(secondary=master_skill, back_populates="masters")

    def works_on(self, weekday: int) -> bool:
        return str(weekday) in self.work_days.split(",")


class Booking(Base):
    __tablename__ = "bookings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    client_name: Mapped[str] = mapped_column(String(120))
    phone: Mapped[str] = mapped_column(String(32), index=True)
    service_id: Mapped[str] = mapped_column(ForeignKey("services.id"))
    master_id: Mapped[str] = mapped_column(ForeignKey("masters.id"), index=True)
    # Локальний час автосервісу (Europe/Kyiv) без часової зони.
    start: Mapped[datetime] = mapped_column(DateTime, index=True)
    end: Mapped[datetime] = mapped_column(DateTime)
    price_uah: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="CONFIRMED")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))

    service: Mapped[ServiceItem] = relationship()
    master: Mapped[Master] = relationship()


class Notification(Base):
    """Сповіщення клієнту. У моноліті створюється в тій самій транзакції, що й запис."""

    __tablename__ = "notifications"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    booking_id: Mapped[str] = mapped_column(ForeignKey("bookings.id"), index=True)
    phone: Mapped[str] = mapped_column(String(32), index=True)
    kind: Mapped[str] = mapped_column(String(20))  # CONFIRMATION | REMINDER_24H | REMINDER_2H | CANCELLATION
    channel: Mapped[str] = mapped_column(String(10), default="sms")
    text: Mapped[str] = mapped_column(String(400))
    status: Mapped[str] = mapped_column(String(12))  # SENT | SCHEDULED | CANCELLED
    send_at: Mapped[datetime] = mapped_column(DateTime)
