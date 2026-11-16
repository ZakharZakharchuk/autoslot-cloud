"""Метрики Prometheus для всіх сервісів (лабораторна робота 4).

Кожна репліка віддає GET /metrics у текстовому форматі Prometheus. Prometheus
раз на 15 с забирає ці дані з усіх реплік і зберігає як часові ряди, а Grafana
малює з них графіки.

Що збирається:
  http_requests_total            — скільки запитів оброблено (сервіс, метод, шлях, код відповіді);
  http_request_duration_seconds  — скільки тривала обробка (гістограма -> p50/p95/p99);
  process_*                      — пам'ять і процесорний час процесу (додає prometheus_client сам);
  бізнес-метрики сервісів        — записи, входи, листи (оголошені у відповідних main.py).
"""
import time

from fastapi import FastAPI, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

from .web import SERVICE_NAME

REQUESTS = Counter("http_requests_total", "Оброблені HTTP-запити",
                   ["service", "method", "route", "status"])
LATENCY = Histogram("http_request_duration_seconds", "Тривалість обробки HTTP-запиту",
                    ["service", "method", "route"],
                    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5))


def _service() -> str:
    # SERVICE_NAME у контейнері = назва сервісу (catalog, booking...), без номера репліки:
    # репліку Prometheus сам додає як мітку instance.
    return SERVICE_NAME.split("-")[0]


def setup(app: FastAPI) -> None:
    """Додає до застосунку /metrics і проміжний обробник, що міряє кожен запит."""

    @app.middleware("http")
    async def measure(request: Request, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        # Шаблон маршруту (/api/bookings/{booking_id}/cancel), а не фактичний шлях —
        # інакше кожен id створив би окремий часовий ряд.
        route = request.scope.get("route")
        path = route.path if route else "unmatched"
        if path not in ("/metrics", "/health"):
            REQUESTS.labels(_service(), request.method, path, str(response.status_code)).inc()
            LATENCY.labels(_service(), request.method, path).observe(time.perf_counter() - start)
        return response

    @app.get("/metrics", include_in_schema=False)
    def metrics():
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
