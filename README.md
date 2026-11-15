# AutoSlot — лабораторні з хмарних технологій

Онлайн-запис на СТО: клієнт обирає послугу, бачить вільний час майстрів з
урахуванням тривалості робіт і їхньої зайнятості, записується й отримує
нагадування. Предметна область і архітектура (baseline v1.0, ADR-001…003)
взяті з проєкту AutoSlot з дисципліни про мікросервіси.

Бригада: Захарчук Захар Сергійович, Кондрасюк Ярослав Володимирович (ІТ-51мн).

| Каталог | Лабораторна | Звіт |
| --- | --- | --- |
| `lab1-cloud-deploy/` | 1. Розробка та розгортання хмарного застосунку (FastAPI + PostgreSQL на Render) | [report.md](lab1-cloud-deploy/report.md) |
| `lab2-containers/` | 2. Застосунок у контейнерах: 5 сервісів з репліками за nginx, реєстрація з підтвердженням пошти і вхід за паролем (JWT), запис на кілька послуг, адмін-панель; Docker Compose, Redis Streams, PostgreSQL | [report.md](lab2-containers/report.md), [як це працює](lab2-containers/HOW_IT_WORKS.md) |
| `lab3-comparison/` | 3. Порівняння монолітної та мікросервісної архітектури (моноліт, вимірювання, результати) | [report.md](lab3-comparison/report.md) |
| `project/` | Навчальний проєкт: презентація і пояснювальна записка | [presentation.html](project/presentation.html) |
| `lab4-cloud-tools/` | 4. Terraform, GitHub Actions (CI/CD), Prometheus і Grafana | [report.md](lab4-cloud-tools/report.md) |
| `render.yaml` | Blueprint Render для лабораторної 1 (має лежати в корені) | — |
| `docs/screenshots/` | скріншоти для звітів | — |

## Швидкий старт

```bash
# Лабораторна 1 — локально
cd lab1-cloud-deploy && pip install -r requirements.txt && uvicorn app.main:app --reload
# http://127.0.0.1:8000, документація API: /docs, тести: pip install pytest httpx && pytest -q

# Лабораторна 2 — контейнери
cd lab2-containers && cp .env.example .env   # задати POSTGRES_PASSWORD, AUTH_SECRET, ADMIN_EMAILS
docker compose up -d --build                  # з моніторингом (ЛР4): --profile monitoring
# http://localhost:8080 (адмін-панель: /admin.html), Grafana: http://localhost:3000
# перевірка: python scripts/smoke_test.py (потрібен AUTH_DEV_MODE=true)

# Лабораторна 3 — моноліт (порт 8081) і вимірювання
cd lab3-comparison/monolith && cp .env.example .env && docker compose up -d --build
# інструкції з вимірювань: lab3-comparison/README.md
```

## Git-теги

- `cloud-lab1` — здача лабораторної 1
- `cloud-lab2` — здача лабораторної 2
- `cloud-lab3` — здача лабораторної 3
- `cloud-lab4` — здача лабораторної 4
