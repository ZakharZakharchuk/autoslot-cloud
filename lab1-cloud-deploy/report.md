# Звіт до лабораторної роботи 1

**Дисципліна:** Хмарні та GRID-технології
**Викладач:** Жереб К. А.
**Завдання:** Лабораторна робота 1. Розробка та розгортання хмарного застосунку
**Варіант:** тема обрана бригадою
**Виконали:** Захарчук Захар Сергійович, Кондрасюк Ярослав Володимирович, група ІТ-51мн
**Код:** [https://github.com/yaroslavkondrasiuk/autoslot-cloud/tree/cloud-lab1/lab1-cloud-deploy](https://github.com/yaroslavkondrasiuk/autoslot-cloud/tree/cloud-lab1/lab1-cloud-deploy) (тег `cloud-lab1`)

## 1. Розподіл обов'язків

Запланований розподіл; фактичний внесок видно в історії комітів.

| Учасник | Роль | Компоненти та розділи звіту |
| --- | --- | --- |
| Захарчук Захар Сергійович | backend-розробник, DevOps | моделі даних, розрахунок слотів, REST API, `render.yaml`; розділи 2, 3, 5, 6 |
| Кондрасюк Ярослав Володимирович | frontend-розробник, тестувальник | веб-клієнт, автотести, перевірка конкурентного запису; розділи 1, 4, 7, 8, 9 |

## 2. Що реалізовано

Застосунок **AutoSlot** — онлайн-запис на СТО. Предметна область та ж, що й у
проєкті з дисципліни про мікросервіси: клієнт обирає послугу, бачить вільний
час майстрів з урахуванням тривалості робіт і зайнятості, записується і може
скасувати запис. У цій роботі система зроблена одним сервісом, щоб зосередитись
на розгортанні в хмарі; розбиття на контейнери — у лабораторній роботі 2.

Серверна частина — REST API на FastAPI з базою PostgreSQL, розгорнута на
платформі Render. Клієнтська частина — односторінковий веб-клієнт, який
віддає той самий сервіс, тому API й клієнт мають одну адресу і CORS не потрібен.

| Метод і шлях | Призначення |
| --- | --- |
| `GET /api/health` | стан сервісу та тип БД (для health check платформи) |
| `GET /api/services`, `GET /api/masters` | каталог послуг і майстрів |
| `GET /api/availability?serviceId&date` | вільні слоти на дату |
| `POST /api/bookings` | створити запис (409, якщо час уже зайнятий) |
| `GET /api/bookings?phone` | записи клієнта |
| `POST /api/bookings/{id}/cancel` | скасувати запис (ідемпотентно) |
| `GET /docs` | автоматично згенерована документація OpenAPI (Swagger UI) |

```mermaid
flowchart LR
    user([Браузер клієнта]) -- HTTPS --> web
    subgraph Render["Render, регіон Frankfurt"]
        web["Web Service autoslot-api<br/>uvicorn + FastAPI<br/>API + index.html"]
        db[("Render Postgres<br/>autoslot-db")]
        web -- DATABASE_URL --> db
    end
    gh[(GitHub)] -- push = autoDeploy --> web
```

## 3. Використані технології та їхні можливості

**FastAPI** ([документація](https://fastapi.tiangolo.com/tutorial/body/)). Маршрути
оголошуються декораторами `@app.get` / `@app.post`; тіло запиту описується
Pydantic-моделлю, і FastAPI сам перевіряє типи й обмеження (наприклад, формат
телефону через `Field(pattern=...)`) та повертає 422 з описом помилки. Сесія
БД передається в обробники через [залежності](https://fastapi.tiangolo.com/tutorial/dependencies/)
`Depends(get_session)`. Створення таблиць і початкові дані виконуються у
функції [`lifespan`](https://fastapi.tiangolo.com/advanced/events/) при старті.
Веб-клієнт підключено через [`StaticFiles`](https://fastapi.tiangolo.com/tutorial/static-files/).
Документація `/docs` генерується автоматично з анотацій.

**SQLAlchemy 2.0** ([ORM quick start](https://docs.sqlalchemy.org/en/20/orm/quickstart.html)) і
драйвер **psycopg 3**. Моделі описані через `Mapped[...]` / `mapped_column`.
Адреса БД береться зі змінної оточення `DATABASE_URL`: локально без неї
використовується SQLite, у хмарі — PostgreSQL. Render віддає адресу у форматі
`postgresql://...`, тому в `app/db.py` вона приводиться до
`postgresql+psycopg://...`.

Захист від подвійного запису: перед перевіркою перетину інтервалів рядок майстра
блокується через [`with_for_update()`](https://docs.sqlalchemy.org/en/20/core/selectable.html#sqlalchemy.sql.expression.Select.with_for_update),
що в PostgreSQL перетворюється на [`SELECT ... FOR UPDATE`](https://www.postgresql.org/docs/16/sql-select.html#SQL-FOR-UPDATE-SHARE).
Паралельні запити на того самого майстра виконуються по черзі, і другий бачить
уже створений запис.

```python
master = s.scalar(select(Master).where(Master.id == body.masterId).with_for_update())
...
if any(overlaps(start, end, b0, b1) for b0, b1 in _busy_intervals(s, master.id, start.date())):
    raise HTTPException(409, "Цей час уже зайнятий — оберіть інший слот")
```

**Uvicorn** ([deployment](https://www.uvicorn.org/deployment/)) — ASGI-сервер. У хмарі
запускається командою `uvicorn app.main:app --host 0.0.0.0 --port $PORT`:
слухати треба всі інтерфейси та порт, який платформа передає в `PORT`.

**Render** — PaaS, на якому розгорнуто застосунок. Уся інфраструктура описана
файлом [`render.yaml`](../render.yaml) у корені репозиторію
([Blueprint spec](https://render.com/docs/blueprint-spec)), тобто як код.
Використані можливості:

- `type: web`, `runtime: python`, `buildCommand` / `startCommand` — збірка і запуск
  без Dockerfile ([deploy FastAPI](https://render.com/docs/deploy-fastapi));
- `rootDir: lab1-cloud-deploy` — сервіс збирається з підкаталогу репозиторію
  ([monorepo support](https://render.com/docs/monorepo-support));
- блок `databases` створює керований PostgreSQL, а `fromDatabase.property:
  connectionString` автоматично записує його адресу в `DATABASE_URL`
  ([Postgres](https://render.com/docs/postgresql-creating-connecting));
- `healthCheckPath: /api/health` — нова версія отримує трафік лише після
  успішної перевірки ([health checks](https://render.com/docs/health-checks));
- `autoDeploy: true` — кожен push у гілку запускає нове розгортання.

## 4. Порівняння з альтернативами

| Варіант | Переваги | Недоліки | Рішення |
| --- | --- | --- | --- |
| **Render** | безкоштовний тариф без картки, інфраструктура як код (`render.yaml`), керований Postgres, автодеплой з GitHub | безкоштовний сервіс засинає після 15 хв без запитів, безкоштовна БД діє 30 днів | обрано |
| Heroku | класичний PaaS, `Procfile` | безкоштовного тарифу немає | ні |
| Azure App Service | інтеграція з Azure for Students, більше налаштувань | складніше налаштування БД і мережі, вимагає облікового запису Azure | можлива альтернатива |
| AWS Elastic Beanstalk + RDS | гнучкість, промисловий стандарт | значно більше ручного налаштування (VPC, групи безпеки), ризик платних ресурсів | надмірно для задачі |

FastAPI обрано замість Flask через вбудовану валідацію й автоматичну
OpenAPI-документацію, замість Django — через менший обсяг для невеликого API.
PostgreSQL обрано замість SQLite у хмарі, бо файлова система Render-сервісу не
зберігається між розгортаннями, а `SELECT ... FOR UPDATE` в SQLite недоступний.

Обмеження безкоштовного тарифу описані в [документації Render](https://render.com/docs/free):
веб-сервіс зупиняється після 15 хвилин без вхідних запитів, і перший запит після
паузи може тривати близько хвилини; безкоштовна база PostgreSQL діє 30 днів від
створення, після чого є 14 днів, щоб перейти на платний тариф.

## 5. Розгортання в хмарі

1. Запушити репозиторій на GitHub.
2. У Render Dashboard: **New → Blueprint**, обрати репозиторій. Render знайде
   `render.yaml` і покаже два ресурси: `autoslot-db` та `autoslot-api`. Натиснути **Apply**.
3. Дочекатися завершення збірки й відкрити видану платформою адресу сервісу.
4. Перевірити `GET /api/health`: у відповіді має бути `"db": "postgresql"`.

## 6. Локальний запуск

```bash
cd lab1-cloud-deploy
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload                         # http://127.0.0.1:8000
```

Без змінної `DATABASE_URL` використовується SQLite (`autoslot.db`). Щоб працювати
з PostgreSQL, як у хмарі: `export DATABASE_URL=postgresql://user:pass@localhost:5432/autoslot`.

## 7. Тестування

Автотести (`tests/test_api.py`, [FastAPI TestClient](https://fastapi.tiangolo.com/tutorial/testing/))
перевіряють повний сценарій: каталог, вільні слоти, запис, конфлікт 409,
зникнення й повернення слоту після скасування, перевірку майстра і вихідних.

```bash
pip install pytest httpx && pytest -q        # 2 passed
```

Окремо перевірено конкурентність на PostgreSQL: 10 одночасних запитів на той
самий слот дали один `201 Created` і дев'ять `409 Conflict`.

## 8. Скріншоти

![Рис. 1](../docs/screenshots/lab1-booking-desktop.png)

Рис. 1 — вибір послуги, дня й часу, підтвердження запису та список записів клієнта (локальний запуск з PostgreSQL)

![Рис. 2](../docs/screenshots/lab1-mobile.png)

Рис. 2 — мобільна версія

## 9. Висновки

Розроблено застосунок для запису на СТО з REST API та веб-клієнтом і підготовлено
його розгортання на Render як коду (`render.yaml`): веб-сервіс і керована база
PostgreSQL створюються однією дією, адреса БД передається через змінну
оточення, а оновлення відбувається автоматично після push. Конфігурація через
змінні оточення дає змогу запускати той самий код локально на SQLite і в хмарі
на PostgreSQL. Головні обмеження — засинання безкоштовного сервісу і термін дії
безкоштовної БД. Для подальших робіт застосунок буде розділено на кілька
сервісів у контейнерах.
