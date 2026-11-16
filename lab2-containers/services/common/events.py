"""Шина подій на Redis Streams.

Продюсер додає подію в потік XADD. Кожен сервіс-споживач має власну
consumer group: подію отримують усі групи, а в межах групи — лише один
екземпляр сервісу. Подія підтверджується (XACK) тільки після успішної
обробки, тож при падінні сервісу вона залишається в pending і буде
оброблена повторно (доставка at-least-once => обробники ідемпотентні).
"""
import json
import logging
import os
import socket
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Callable

import redis

STREAM = os.getenv("EVENT_STREAM", "autoslot.events")
log = logging.getLogger("events")


class EventBus:
    def __init__(self, url: str | None = None):
        self.redis = redis.Redis.from_url(url or os.getenv("REDIS_URL", "redis://localhost:6379/0"),
                                          decode_responses=True)

    def publish(self, event_type: str, payload: dict) -> str:
        event_id = str(uuid.uuid4())
        self.redis.xadd(STREAM, {
            "id": event_id,
            "type": event_type,
            "occurredAt": datetime.now(timezone.utc).isoformat(),
            "payload": json.dumps(payload, ensure_ascii=False, default=str),
        }, maxlen=10_000, approximate=True)
        log.info("published %s %s", event_type, event_id)
        return event_id

    def start_consumer(self, group: str, handler: Callable[[dict], None]) -> threading.Event:
        stop = threading.Event()
        threading.Thread(target=self._consume, args=(group, handler, stop), daemon=True,
                         name=f"consumer-{group}").start()
        return stop

    def _ensure_group(self, group: str) -> None:
        try:
            self.redis.xgroup_create(STREAM, group, id="0", mkstream=True)
        except redis.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    def _consume(self, group: str, handler, stop: threading.Event) -> None:
        consumer = socket.gethostname()
        cursor = "0"  # спершу дочитуємо власні pending-повідомлення, потім нові (">")
        while not stop.is_set():
            try:
                self._ensure_group(group)
                resp = self.redis.xreadgroup(group, consumer, {STREAM: cursor}, count=20, block=2000)
                messages = resp[0][1] if resp else []
                if cursor == "0" and not messages:
                    cursor = ">"
                    continue
                for msg_id, fields in messages:
                    event = {**fields, "payload": json.loads(fields.get("payload", "{}"))}
                    try:
                        handler(event)
                        self.redis.xack(STREAM, group, msg_id)
                    except Exception:  # noqa: BLE001
                        log.exception("помилка обробки %s (%s), подія лишається в pending",
                                      event.get("type"), msg_id)
                if cursor == "0":
                    # pending-повідомлення без ack не повторюємо по колу — переходимо до нових
                    cursor = ">"
            except redis.ConnectionError as exc:
                log.warning("Redis недоступний: %s", exc)
                time.sleep(2)
