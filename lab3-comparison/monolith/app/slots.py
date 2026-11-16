"""Розрахунок вільних слотів з урахуванням тривалості послуги й зайнятості майстра."""
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

SHOP_TZ = ZoneInfo("Europe/Kyiv")
STEP = timedelta(minutes=30)


def now_local() -> datetime:
    return datetime.now(SHOP_TZ).replace(tzinfo=None)


def overlaps(a_start, a_end, b_start, b_end) -> bool:
    return a_start < b_end and b_start < a_end


def candidate_starts(day: date, start_hour: int, end_hour: int, duration: timedelta):
    """Початки слотів з кроком 30 хв так, щоб робота завершилась до кінця зміни."""
    t = datetime.combine(day, time(start_hour))
    close = datetime.combine(day, time(end_hour))
    while t + duration <= close:
        yield t
        t += STEP


def free_slots(day: date, master, duration: timedelta, busy, not_before: datetime | None = None):
    """master — будь-який об'єкт з works_on(), work_start_hour, work_end_hour."""
    if not master.works_on(day.weekday()):
        return []
    result = []
    for start in candidate_starts(day, master.work_start_hour, master.work_end_hour, duration):
        end = start + duration
        if not_before and start < not_before:
            continue
        if any(overlaps(start, end, b0, b1) for b0, b1 in busy):
            continue
        result.append((start, end))
    return result
