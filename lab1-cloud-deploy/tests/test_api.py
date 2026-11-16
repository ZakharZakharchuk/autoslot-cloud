import os
from datetime import date, timedelta

os.environ["DATABASE_URL"] = "sqlite:///./test_autoslot.db"
if os.path.exists("test_autoslot.db"):
    os.remove("test_autoslot.db")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


def next_weekday(wd: int) -> date:
    d = date.today() + timedelta(days=1)
    while d.weekday() != wd:
        d += timedelta(days=1)
    return d


def test_full_booking_flow():
    with TestClient(app) as c:
        assert c.get("/api/health").json()["status"] == "ok"
        services = c.get("/api/services").json()
        assert any(s["id"] == "oil-change" for s in services)

        day = next_weekday(0)  # понеділок
        slots = c.get("/api/availability", params={"serviceId": "oil-change", "date": day.isoformat()}).json()
        assert slots, "у понеділок мають бути вільні слоти"
        slot = slots[0]

        body = {"serviceId": "oil-change", "masterId": slot["masterId"], "start": slot["start"],
                "clientName": "Тест Клієнт", "phone": "+380 67 123 45 67"}
        r = c.post("/api/bookings", json=body)
        assert r.status_code == 201, r.text
        booking = r.json()

        # той самий час повторно — конфлікт
        assert c.post("/api/bookings", json=body).status_code == 409

        # слот зник із вільних
        slots2 = c.get("/api/availability", params={"serviceId": "oil-change", "date": day.isoformat()}).json()
        assert not any(x["start"] == slot["start"] and x["masterId"] == slot["masterId"] for x in slots2)

        mine = c.get("/api/bookings", params={"phone": "+380671234567"}).json()
        assert [b["id"] for b in mine] == [booking["id"]]

        assert c.post(f"/api/bookings/{booking['id']}/cancel").json()["status"] == "CANCELLED"
        slots3 = c.get("/api/availability", params={"serviceId": "oil-change", "date": day.isoformat()}).json()
        assert any(x["start"] == slot["start"] and x["masterId"] == slot["masterId"] for x in slots3)


def test_validation():
    with TestClient(app) as c:
        day = next_weekday(6)  # неділя — ніхто не працює
        assert c.get("/api/availability", params={"serviceId": "oil-change", "date": day.isoformat()}).json() == []
        r = c.post("/api/bookings", json={"serviceId": "ac-service", "masterId": "master-1",
                                          "start": f"{next_weekday(1)}T10:00:00",
                                          "clientName": "Тест", "phone": "+380671234567"})
        assert r.status_code == 422  # майстер не виконує послугу
