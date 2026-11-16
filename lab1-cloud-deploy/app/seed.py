"""Початкові дані: каталог послуг і майстри. Записуються, лише якщо таблиці порожні."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Master, ServiceItem

SERVICES = [
    ("oil-change", "Заміна масла й фільтра", "ТО", 40, 650),
    ("tire-fitting", "Шиномонтаж, 4 колеса", "Шини", 60, 900),
    ("wheel-balance", "Балансування коліс", "Шини", 30, 400),
    ("diagnostics", "Комп'ютерна діагностика", "Діагностика", 50, 700),
    ("brake-pads", "Заміна гальмівних колодок", "Ходова", 90, 1200),
    ("ac-service", "Заправка кондиціонера", "Клімат", 60, 1100),
]

MASTERS = [
    ("master-1", "Олег Ковальчук", 9, 18, "0,1,2,3,4", ["oil-change", "brake-pads", "diagnostics"]),
    ("master-2", "Андрій Мельник", 8, 17, "0,1,2,3,4,5", ["tire-fitting", "wheel-balance", "oil-change"]),
    ("master-3", "Ірина Савчук", 10, 19, "1,2,3,4,5", ["diagnostics", "ac-service"]),
]


def seed(session: Session) -> None:
    if session.scalar(select(ServiceItem.id).limit(1)):
        return
    services = {sid: ServiceItem(id=sid, name=n, category=c, duration_min=d, price_uah=p)
                for sid, n, c, d, p in SERVICES}
    session.add_all(services.values())
    for mid, name, start, end, days, skills in MASTERS:
        session.add(Master(id=mid, name=name, work_start_hour=start, work_end_hour=end,
                           work_days=days, skills=[services[s] for s in skills]))
    session.commit()
