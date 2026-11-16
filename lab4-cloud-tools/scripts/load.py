"""Навантаження для демонстрації моніторингу: кілька клієнтів переглядають каталог,
шукають вільний час, записуються й частину записів скасовують.

    python load.py http://<host>:8080 --minutes 5 --users 3

Вхід — за кодом із відповіді, тому на сервері потрібен AUTH_DEV_MODE=true без SMTP.
Окремо генеруються «погані» запити (без входу, невірний код), щоб на дашборді були
видні відмови 401 і невдалі спроби входу.
"""
import argparse
import http.cookiejar
import json
import random
import threading
import time
import urllib.error
import urllib.request
from datetime import date, timedelta


class Client:
    def __init__(self, base):
        self.base = base
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def call(self, method, path, body=None):
        req = urllib.request.Request(self.base + path, method=method, headers={"Content-Type": "application/json"},
                                     data=json.dumps(body).encode() if body is not None else None)
        try:
            with self.opener.open(req, timeout=10) as r:
                return r.status, json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            return e.code, None
        except OSError:
            return 0, None

    def login(self, email):
        st, data = self.call("POST", "/api/auth/request-code", {"email": email})
        if st == 200 and data and "devCode" in data:
            return self.call("POST", "/api/auth/verify", {"email": email, "code": data["devCode"]})[0] == 200
        return False


def user_loop(base, n, stop, stats):
    c = Client(base)
    if not c.login(f"load{n}-{int(time.time()) % 10000}@example.com"):
        print(f"користувач {n}: не вдалося увійти (ліміт або не dev-режим)")
        return
    services = [s["id"] for s in c.call("GET", "/api/services")[1]]
    while not stop.is_set():
        day = date.today() + timedelta(days=random.randint(1, 13))
        sid = random.choice(services)
        st, slots = c.call("GET", f"/api/availability?serviceId={sid}&date={day}")
        stats["requests"] += 2
        if st == 200 and slots and random.random() < 0.3:
            s = random.choice(slots)
            st, b = c.call("POST", "/api/bookings", {"serviceId": sid, "masterId": s["masterId"], "start": s["start"],
                                                     "clientName": "Навантаження", "phone": "+380500000000"})
            stats["requests"] += 1
            if st == 201:
                stats["bookings"] += 1
                if random.random() < 0.3:
                    c.call("POST", f"/api/bookings/{b['id']}/cancel")
                    stats["cancels"] += 1
        if random.random() < 0.2:
            c.call("GET", "/api/bookings")
        time.sleep(random.uniform(0.05, 0.3))


def bad_traffic(base, stop):
    anon = Client(base)
    while not stop.is_set():
        anon.call("POST", "/api/bookings", {"serviceId": "x", "masterId": "x", "start": "2030-01-01T10:00:00",
                                            "clientName": "xx", "phone": "+380500000000"})  # 401
        anon.call("POST", "/api/auth/verify", {"email": "nobody@example.com", "code": "000000"})  # невдалий вхід
        time.sleep(2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("base")
    ap.add_argument("--minutes", type=float, default=5)
    ap.add_argument("--users", type=int, default=3)
    a = ap.parse_args()
    stop, stats = threading.Event(), {"requests": 0, "bookings": 0, "cancels": 0}
    threads = [threading.Thread(target=user_loop, args=(a.base.rstrip("/"), i, stop, stats)) for i in range(a.users)]
    threads.append(threading.Thread(target=bad_traffic, args=(a.base.rstrip("/"), stop)))
    for t in threads:
        t.start()
    end = time.time() + a.minutes * 60
    while time.time() < end:
        time.sleep(10)
        print(f"{time.strftime('%H:%M:%S')} {stats}")
    stop.set()
    for t in threads:
        t.join()
    print("готово:", stats)


if __name__ == "__main__":
    main()
