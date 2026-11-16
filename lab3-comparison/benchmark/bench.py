"""Навантажувальне тестування AutoSlot: однаковий набір сценаріїв для обох версій.

Обидві версії мають однакове публічне API, тому скрипт працює з будь-якою з них:

    python bench.py --base http://localhost:8081 --label monolith
    python bench.py --base http://localhost:8080 --label microservices

Сценарії:
  services      GET /api/services                       (просте читання, один компонент)
  availability  GET /api/availability                   (розрахунок слотів; у мікросервісах + виклик catalog)
  booking       POST /api/bookings на унікальні слоти   (запис; у мікросервісах ланцюжок booking -> availability -> catalog + подія)
  notify-delay  час від POST /api/bookings до появи підтвердження в GET /api/notifications

Результат: results/<label>.json і зведення в консолі.
"""
import argparse
import asyncio
import json
import statistics
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx

LEVELS = [1, 10, 50]


def pct(values, q):
    if not values:
        return None
    s = sorted(values)
    return s[min(len(s) - 1, int(round(q / 100 * (len(s) - 1))))]


def summarize(lat_ms, errors, wall_s):
    return {
        "requests": len(lat_ms) + errors,
        "errors": errors,
        "rps": round((len(lat_ms) + errors) / wall_s, 1),
        "mean_ms": round(statistics.mean(lat_ms), 2) if lat_ms else None,
        "p50_ms": round(pct(lat_ms, 50), 2) if lat_ms else None,
        "p95_ms": round(pct(lat_ms, 95), 2) if lat_ms else None,
        "p99_ms": round(pct(lat_ms, 99), 2) if lat_ms else None,
    }


async def run_load(client, make_request, total, concurrency, ok_status=(200,)):
    """Виконує total запитів, не більше concurrency одночасно."""
    lat, errors = [], 0
    queue = asyncio.Queue()
    for i in range(total):
        queue.put_nowait(i)

    async def worker():
        nonlocal errors
        while True:
            try:
                i = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            t0 = time.perf_counter()
            try:
                r = await make_request(client, i)
                if r.status_code in ok_status:
                    lat.append((time.perf_counter() - t0) * 1000)
                else:
                    errors += 1
            except httpx.HTTPError:
                errors += 1

    t0 = time.perf_counter()
    await asyncio.gather(*(worker() for _ in range(concurrency)))
    return summarize(lat, errors, time.perf_counter() - t0)


def next_weekday(wd, start):
    d = start
    while d.weekday() != wd:
        d += timedelta(days=1)
    return d


async def collect_booking_slots(client, days_from, days, need):
    """Неперетинні слоти (по одному на годину на майстра) для послуги diagnostics (50 хв)."""
    slots = []
    d0 = date.today() + timedelta(days=days_from)
    for k in range(days):
        day = d0 + timedelta(days=k)
        r = await client.get("/api/availability", params={"serviceId": "diagnostics", "date": day.isoformat()})
        for s in r.json():
            if s["start"][14:16] == "00":  # лише повні години -> слоти по 50 хв не перетинаються
                slots.append(s)
        if len(slots) >= need:
            break
    return slots[:need]


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--label", required=True)
    ap.add_argument("--read-requests", type=int, default=600)
    ap.add_argument("--bookings", type=int, default=240)
    ap.add_argument("--booking-days-from", type=int, default=7,
                    help="з якого дня від сьогодні брати слоти для запису (щоб не перетинатися з іншими тестами)")
    args = ap.parse_args()

    limits = httpx.Limits(max_connections=100, max_keepalive_connections=100)
    async with httpx.AsyncClient(base_url=args.base, timeout=30, limits=limits) as client:
        result = {"label": args.label, "base": args.base, "started": datetime.now().isoformat(timespec="seconds"),
                  "scenarios": {}}
        day = next_weekday(2, date.today() + timedelta(days=1)).isoformat()

        async def services(c, i):
            return await c.get("/api/services")

        async def availability(c, i):
            return await c.get("/api/availability", params={"serviceId": "diagnostics", "date": day})

        # прогрів
        await run_load(client, services, 50, 5)
        await run_load(client, availability, 50, 5)

        for name, fn in (("services", services), ("availability", availability)):
            result["scenarios"][name] = {}
            for c in LEVELS:
                res = await run_load(client, fn, args.read_requests, c)
                result["scenarios"][name][str(c)] = res
                print(f"{args.label:14} {name:13} c={c:<3} {res}")

        # запис: кожен запит — окремий вільний слот
        slots = await collect_booking_slots(client, args.booking_days_from, 120, args.bookings * 2)
        result["scenarios"]["booking"] = {}
        offset = 0
        for c in (1, 10):
            n = args.bookings // 2
            batch = slots[offset:offset + n]
            offset += n

            async def booking(cl, i, batch=batch, c=c):
                s = batch[i]
                return await cl.post("/api/bookings", json={
                    "serviceId": "diagnostics", "masterId": s["masterId"], "start": s["start"],
                    "clientName": "Bench", "phone": f"+38050{c:02d}{i:05d}"})

            res = await run_load(client, booking, len(batch), c, ok_status=(201,))
            result["scenarios"]["booking"][str(c)] = res
            print(f"{args.label:14} {'booking':13} c={c:<3} {res}")

        # затримка появи сповіщення (узгодженість)
        delays = []
        for i, s in enumerate(slots[offset:offset + 20]):
            phone = f"+38063{i:07d}"
            t0 = time.perf_counter()
            r = await client.post("/api/bookings", json={"serviceId": "diagnostics", "masterId": s["masterId"],
                                                         "start": s["start"], "clientName": "Bench", "phone": phone})
            if r.status_code != 201:
                continue
            while time.perf_counter() - t0 < 10:
                n = await client.get("/api/notifications", params={"phone": phone})
                if n.status_code == 200 and any(x["kind"] == "CONFIRMATION" for x in n.json()):
                    delays.append((time.perf_counter() - t0) * 1000)
                    break
                await asyncio.sleep(0.005)
        result["scenarios"]["notify-delay"] = {
            "samples": len(delays),
            "p50_ms": round(pct(delays, 50), 1) if delays else None,
            "max_ms": round(max(delays), 1) if delays else None,
        }
        print(f"{args.label:14} notify-delay  {result['scenarios']['notify-delay']}")

    out = Path(__file__).parent / "results" / f"{args.label}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print("saved", out)


if __name__ == "__main__":
    asyncio.run(main())
