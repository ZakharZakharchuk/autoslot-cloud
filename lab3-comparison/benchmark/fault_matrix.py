"""Які функції лишаються доступними, коли один компонент зупинено.

Зупиніть компонент (у Docker: docker compose stop <сервіс>) і запустіть:
    python fault_matrix.py --base http://localhost:8080 --label "без notification"
"""
import argparse
import json
import urllib.error
import urllib.request
from datetime import date, timedelta
from urllib.parse import quote


def call(base, method, path, body=None):
    req = urllib.request.Request(base + path, method=method, headers={"Content-Type": "application/json"},
                                 data=json.dumps(body).encode() if body else None)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, None
    except (urllib.error.URLError, ConnectionError, TimeoutError):
        return "немає з'єднання", None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--label", required=True)
    ap.add_argument("--slot", help="masterId,start для спроби запису (береться з --slots-from, якщо не задано)")
    a = ap.parse_args()
    day = date.today() + timedelta(days=30)
    while day.weekday() != 2:
        day += timedelta(days=1)
    phone = "+380960000000"
    res = {}
    res["каталог"] = call(a.base, "GET", "/api/services")[0]
    st, slots = call(a.base, "GET", f"/api/availability?serviceId=diagnostics&date={day}")
    res["вільні слоти"] = st
    if a.slot:
        m, s = a.slot.split(",")
    elif slots:
        m, s = slots[0]["masterId"], slots[0]["start"]
    else:
        m, s = "master-1", f"{day}T11:00:00"
    res["запис"] = call(a.base, "POST", "/api/bookings", {"serviceId": "diagnostics", "masterId": m, "start": s,
                                                          "clientName": "Fault", "phone": phone})[0]
    res["мої записи"] = call(a.base, "GET", f"/api/bookings?phone={quote(phone)}")[0]
    res["сповіщення"] = call(a.base, "GET", f"/api/notifications?phone={quote(phone)}")[0]
    print(json.dumps({"label": a.label, "result": res}, ensure_ascii=False))


if __name__ == "__main__":
    main()
