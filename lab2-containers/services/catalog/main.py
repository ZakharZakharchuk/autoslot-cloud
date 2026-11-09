"""catalog-service: послуги, ціни, тривалість, які майстри що виконують.

Власник даних: ServiceItem, Master, MasterSkill.
Публічне API (через gateway): GET /api/services, GET /api/masters
Внутрішнє API (лише в мережі контейнерів): GET /internal/services/{id},
GET /internal/bundle?ids=a,b (кілька послуг за один візит)
"""
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy import Column, ForeignKey, Integer, String, Table, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from common.db import init_schema, make_db, wait_for_db
from common import metrics
from common.web import SERVICE_NAME, VERSION

engine, SessionLocal = make_db("catalog.db")


class Base(DeclarativeBase):
    pass


master_skill = Table("master_skill", Base.metadata,
                     Column("master_id", ForeignKey("masters.id"), primary_key=True),
                     Column("service_id", ForeignKey("services.id"), primary_key=True))


class ServiceItem(Base):
    __tablename__ = "services"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    category: Mapped[str] = mapped_column(String(60))
    duration_min: Mapped[int] = mapped_column(Integer)
    price_uah: Mapped[int] = mapped_column(Integer)
    masters: Mapped[list["Master"]] = relationship(secondary=master_skill, back_populates="skills")


class Master(Base):
    __tablename__ = "masters"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    skills: Mapped[list[ServiceItem]] = relationship(secondary=master_skill, back_populates="masters")


SERVICES = [
    ("oil-change", "Заміна масла й фільтра", "ТО", 40, 650),
    ("tire-fitting", "Шиномонтаж, 4 колеса", "Шини", 60, 900),
    ("wheel-balance", "Балансування коліс", "Шини", 30, 400),
    ("diagnostics", "Комп'ютерна діагностика", "Діагностика", 50, 700),
    ("brake-pads", "Заміна гальмівних колодок", "Ходова", 90, 1200),
    ("ac-service", "Заправка кондиціонера", "Клімат", 60, 1100),
]
MASTERS = [
    ("master-1", "Олег Ковальчук", ["oil-change", "brake-pads", "diagnostics"]),
    ("master-2", "Андрій Мельник", ["tire-fitting", "wheel-balance", "oil-change"]),
    ("master-3", "Ірина Савчук", ["diagnostics", "ac-service"]),
]


def seed() -> None:
    with SessionLocal() as s:
        if s.scalar(select(ServiceItem.id).limit(1)):
            return
        items = {i[0]: ServiceItem(id=i[0], name=i[1], category=i[2], duration_min=i[3], price_uah=i[4])
                 for i in SERVICES}
        s.add_all(items.values())
        s.add_all(Master(id=m, name=n, skills=[items[k] for k in sk]) for m, n, sk in MASTERS)
        s.commit()


@asynccontextmanager
async def lifespan(_: FastAPI):
    wait_for_db(engine)
    init_schema(engine, Base.metadata, seed, "catalog")
    yield


app = FastAPI(title="AutoSlot catalog-service", version=VERSION, lifespan=lifespan)
metrics.setup(app)


def session():
    with SessionLocal() as s:
        yield s


@app.get("/health")
def health(s=Depends(session)):
    s.execute(select(1))
    return {"service": SERVICE_NAME, "status": "ok", "version": VERSION}


@app.get("/api/services")
def list_services(s=Depends(session)):
    rows = s.scalars(select(ServiceItem).order_by(ServiceItem.category, ServiceItem.name))
    return [{"id": r.id, "name": r.name, "category": r.category,
             "durationMin": r.duration_min, "priceUah": r.price_uah} for r in rows]


@app.get("/api/masters")
def list_masters(s=Depends(session)):
    return [{"id": m.id, "name": m.name, "serviceIds": [x.id for x in m.skills]}
            for m in s.scalars(select(Master).order_by(Master.name))]


@app.get("/internal/services/{service_id}")
def service_details(service_id: str, s=Depends(session)):
    item = s.get(ServiceItem, service_id)
    if not item:
        raise HTTPException(404, "Послугу не знайдено")
    return {"id": item.id, "name": item.name, "durationMin": item.duration_min, "priceUah": item.price_uah,
            "masters": [{"id": m.id, "name": m.name} for m in item.masters]}


MAX_BUNDLE = 6  # скільки послуг можна обрати за один візит


@app.get("/internal/bundle")
def bundle_details(ids: str, s=Depends(session)):
    """Кілька послуг за один візит: виконуються одна за одною в одного майстра.

    Тривалість і ціна сумуються, а майстри — лише ті, хто вміє всі обрані послуги.
    Для однієї послуги відповідь така сама, як /internal/services/{id}.
    """
    keys = list(dict.fromkeys(k.strip() for k in ids.split(",") if k.strip()))  # без дублікатів, у порядку вибору
    if not 1 <= len(keys) <= MAX_BUNDLE:
        raise HTTPException(422, f"Оберіть від 1 до {MAX_BUNDLE} послуг")
    items = [s.get(ServiceItem, k) for k in keys]
    if not all(items):
        raise HTTPException(404, "Послугу не знайдено")
    common = set.intersection(*({m.id for m in i.masters} for i in items))
    masters = [{"id": m.id, "name": m.name} for m in items[0].masters if m.id in common]
    return {"id": ",".join(keys), "name": " + ".join(i.name for i in items),
            "durationMin": sum(i.duration_min for i in items), "priceUah": sum(i.price_uah for i in items),
            "serviceIds": keys, "masters": masters}
