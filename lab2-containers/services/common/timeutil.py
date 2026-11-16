from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

SHOP_TZ = ZoneInfo("Europe/Kyiv")
STEP = timedelta(minutes=30)


def now_local() -> datetime:
    return datetime.now(SHOP_TZ).replace(tzinfo=None)


def overlaps(a0, a1, b0, b1) -> bool:
    return a0 < b1 and b0 < a1


def candidate_starts(day: date, start_hour: int, end_hour: int, duration: timedelta):
    t = datetime.combine(day, time(start_hour))
    close = datetime.combine(day, time(end_hour))
    while t + duration <= close:
        yield t
        t += STEP
