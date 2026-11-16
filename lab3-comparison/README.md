# Лабораторна 3 — моноліт проти мікросервісів

- `monolith/` — монолітна версія AutoSlot (функціонально еквівалентна `../lab2-containers`), Docker Compose на порту 8081.
- `../lab2-containers/` — мікросервісна версія (7 контейнерів), порт 8080.
- `benchmark/` — інструменти вимірювань і результати (`results/`).
- `report.md` — звіт.

```bash
# обидві версії
(cd monolith && cp .env.example .env && docker compose up -d --build)
(cd ../lab2-containers && cp .env.example .env && docker compose up -d --build)

# вимірювання
pip install httpx psutil matplotlib
cd benchmark
python bench.py --base http://localhost:8081 --label monolith-r1
python bench.py --base http://localhost:8080 --label microservices-r1
# ... r2, r3 на чистих БД (docker compose down -v && up), потім:
python summarize.py
docker stats --no-stream        # пам'ять і CPU контейнерів
```
