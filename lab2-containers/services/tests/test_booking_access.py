"""booking-service: кожен бачить лише свої записи, адмін — усі; дії адміна в журналі."""
import os
from datetime import datetime, timedelta

if os.path.exists("test_booking.db"):
    os.remove("test_booking.db")
os.environ["DATABASE_URL"] = "sqlite:///./test_booking.db"

from fastapi.testclient import TestClient  # noqa: E402

from booking import main as booking  # noqa: E402

published = []


class FakeResponse:
    def __init__(self, data):
        self.status_code, self._data = 201, data

    def json(self):
        return self._data


def fake_hold(url, json):
    start = datetime.fromisoformat(json["start"])
    return FakeResponse({"holdId": json["idempotencyKey"], "serviceId": json["serviceId"], "serviceName": "Діагностика",
                         "masterId": json["masterId"], "masterName": "Олег", "priceUah": 700,
                         "start": start.isoformat(), "end": (start + timedelta(minutes=50)).isoformat()})


booking.availability_http.post = fake_hold          # без справжнього availability-service
booking.bus.publish = lambda t, p: published.append((t, p))  # без Redis

client = TestClient(booking.app)
client.__enter__()

ANNA = {"X-User-Email": "anna@example.com", "X-User-Role": "CLIENT", "X-User-Id": "1"}
BOB = {"X-User-Email": "bob@example.com", "X-User-Role": "CLIENT", "X-User-Id": "2"}
ADMIN = {"X-User-Email": "boss@example.com", "X-User-Role": "ADMIN", "X-User-Id": "3"}


def book(headers, hour):
    start = (datetime.now() + timedelta(days=3)).replace(hour=hour, minute=0, second=0, microsecond=0)
    return client.post("/api/bookings", headers=headers, json={
        "serviceId": "diagnostics", "masterId": "master-1", "start": start.isoformat(),
        "clientName": "Тест", "phone": "+380671234567"})


def test_anonymous_cannot_book_or_list():
    assert book({}, 10).status_code == 401
    assert client.get("/api/bookings").status_code == 401


def test_each_user_sees_only_own_bookings():
    a = book(ANNA, 10).json()
    assert a["userEmail"] == "anna@example.com"
    assert published[-1][0] == "BookingCreated" and published[-1][1]["userEmail"] == "anna@example.com"
    assert [b["id"] for b in client.get("/api/bookings", headers=ANNA).json()] == [a["id"]]
    assert client.get("/api/bookings", headers=BOB).json() == []
    # чужий запис скасувати не можна, і він виглядає як неіснуючий
    assert client.post(f"/api/bookings/{a['id']}/cancel", headers=BOB).status_code == 404


def test_admin_endpoints_forbidden_for_client():
    for path in ("/api/admin/bookings", "/api/admin/stats", "/api/admin/audit"):
        assert client.get(path, headers=ANNA).status_code == 403
        assert client.get(path).status_code == 401


def test_admin_sees_all_and_cancel_is_audited():
    b = book(BOB, 12).json()
    all_rows = client.get("/api/admin/bookings", headers=ADMIN).json()
    assert {"anna@example.com", "bob@example.com"} <= {r["userEmail"] for r in all_rows}
    assert client.get("/api/admin/bookings", headers=ADMIN, params={"q": "bob"}).json()[0]["id"] == b["id"]

    r = client.post(f"/api/bookings/{b['id']}/cancel", headers=ADMIN)
    assert r.status_code == 200 and r.json()["status"] == "CANCELLED"
    audit = client.get("/api/admin/audit", headers=ADMIN).json()
    assert audit[0]["adminEmail"] == "boss@example.com" and audit[0]["bookingId"] == b["id"]

    stats = client.get("/api/admin/stats", headers=ADMIN).json()
    assert stats[0]["confirmed"] >= 1 and stats[0]["cancelled"] >= 1
