"""Наскрізна перевірка системи через gateway (лише стандартна бібліотека Python).

    python scripts/smoke_test.py                       # http://localhost:8080
    python scripts/smoke_test.py http://<host>:8080

Вхід виконується за кодом із відповіді, тому на сервері має бути AUTH_DEV_MODE=true
і не налаштований SMTP. Адмін-перевірки потребують ADMIN_EMAILS з адресою
boss@example.com (або задайте свою через змінну SMOKE_ADMIN_EMAIL).
"""
import http.cookiejar
import json
import os
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8080").rstrip("/")
ADMIN_EMAIL = os.getenv("SMOKE_ADMIN_EMAIL", "boss@example.com")


class User:
    """Окремий «браузер» зі своїми cookie."""

    def __init__(self):
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def call(self, method, path, body=None, headers=None):
        req = urllib.request.Request(BASE + path, method=method,
                                     headers={"Content-Type": "application/json", **(headers or {})},
                                     data=json.dumps(body).encode() if body is not None else None)
        try:
            with self.opener.open(req, timeout=10) as r:
                return r.status, _json(r.read()), r.headers
        except urllib.error.HTTPError as e:
            return e.code, _json(e.read()), e.headers

    def login(self, email):
        st, data, _ = self.call("POST", "/api/auth/request-code", {"email": email})
        if st == 429:
            sys.exit(f"FAIL вхід {email}: ліміт кодів (3 на адресу за 15 хв або 5 на хвилину з IP), зачекайте")
        if st != 200 or "devCode" not in (data or {}):
            sys.exit("FAIL вхід: потрібен AUTH_DEV_MODE=true без SMTP на сервері")
        st, _, _ = self.call("POST", "/api/auth/verify", {"email": email, "code": data["devCode"]})
        return st


def _json(raw):
    try:
        return json.loads(raw or b"null")
    except ValueError:
        return None


def step(name, ok):
    print(("OK   " if ok else "FAIL ") + name)
    if not ok:
        sys.exit(1)


stamp = int(time.time()) % 100000
anon, anna, bob, admin = User(), User(), User(), User()
day = date.today() + timedelta(days=1)
while day.weekday() != 2:  # середа — працюють усі майстри
    day += timedelta(days=1)

# --- публічна частина ---
st, services, _ = anon.call("GET", "/api/services")
step(f"каталог без входу ({len(services)} послуг)", st == 200 and len(services) >= 5)
upstreams = {anon.call("GET", "/api/services")[2].get("X-Upstream") for _ in range(6)}
step(f"gateway балансує між репліками: {sorted(u for u in upstreams if u)}", len(upstreams - {None}) >= 2)
st, _, h = anon.call("GET", "/")
step("заголовки безпеки (CSP, X-Frame-Options)", "script-src 'self'" in (h.get("Content-Security-Policy") or "")
     and h.get("X-Frame-Options") == "DENY")
st, slots, _ = anon.call("GET", f"/api/availability?serviceId=diagnostics&date={day}")
step(f"вільні слоти на {day} ({len(slots)})", st == 200 and len(slots) > 0)
slot = slots[-1]
body = {"serviceId": "diagnostics", "masterId": slot["masterId"], "start": slot["start"],
        "clientName": "Анна", "phone": "+380671234567"}

# --- захист ---
step("запис без входу -> 401", anon.call("POST", "/api/bookings", body)[0] == 401)
fake = {"X-User-Email": ADMIN_EMAIL, "X-User-Role": "ADMIN", "X-User-Id": "x"}
step("підроблені X-User-* без входу -> 401", anon.call("GET", "/api/admin/bookings", headers=fake)[0] == 401)
step("вхід клієнта за кодом", anna.login(f"anna{stamp}@example.com") == 200)
step("клієнт з підробленим X-User-Role: ADMIN -> 403",
     anna.call("GET", "/api/admin/bookings", headers=fake)[0] == 403)
step("внутрішнє API закрите на gateway", anon.call("GET", "/internal/verify")[0] == 404)

# --- запис ---
with ThreadPoolExecutor(8) as ex:
    results = list(ex.map(lambda _: anna.call("POST", "/api/bookings", body), range(8)))
codes = sorted(r[0] for r in results)
step(f"8 паралельних запитів на один слот: {codes}", codes.count(201) == 1 and codes.count(409) == 7)
booking = next(r[1] for r in results if r[0] == 201)
time.sleep(1.5)  # події обробляються асинхронно
st, slots2, _ = anon.call("GET", f"/api/availability?serviceId=diagnostics&date={day}")
step("слот зник із вільних", not any(s["start"] == slot["start"] and s["masterId"] == slot["masterId"] for s in slots2))
st, notes, _ = anna.call("GET", "/api/notifications")
step("клієнт отримав лист-підтвердження", st == 200 and "CONFIRMATION" in [n["kind"] for n in notes])

# --- ізоляція клієнтів ---
bob.login(f"bob{stamp}@example.com")
step("інший клієнт не бачить чужий запис", bob.call("GET", "/api/bookings")[1] == [])
step("інший клієнт не може скасувати чужий запис",
     bob.call("POST", f"/api/bookings/{booking['id']}/cancel")[0] == 404)

# --- адміністратор ---
admin.login(ADMIN_EMAIL)
st, rows, _ = admin.call("GET", f"/api/admin/bookings?dateFrom={day}&dateTo={day}")
step("адмін бачить записи всіх клієнтів", st == 200 and booking["id"] in [r["id"] for r in rows])
st, cancelled, _ = admin.call("POST", f"/api/bookings/{booking['id']}/cancel")
step("адмін скасував запис клієнта", st == 200 and cancelled["status"] == "CANCELLED")
st, audit, _ = admin.call("GET", "/api/admin/audit")
step("дія адміна записана в журнал", bool(audit) and audit[0]["bookingId"] == booking["id"])
time.sleep(1.5)
st, slots3, _ = anon.call("GET", f"/api/availability?serviceId=diagnostics&date={day}")
step("слот повернувся після скасування", any(s["start"] == slot["start"] and s["masterId"] == slot["masterId"] for s in slots3))
st, notes2, _ = anna.call("GET", "/api/notifications")
step("клієнт отримав лист про скасування", "CANCELLATION" in [n["kind"] for n in notes2])
step("вихід завершує сесію", anna.call("POST", "/api/auth/logout")[0] == 200
     and anna.call("GET", "/api/bookings")[0] == 401)
print("Усі перевірки пройдено.")
