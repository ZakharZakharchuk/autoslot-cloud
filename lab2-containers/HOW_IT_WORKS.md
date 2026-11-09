# Як працює AutoSlot

Шпаргалка для команди: що де лежить і що відбувається від кліку в браузері до
листа клієнту. Після кожного розділу є питання, які може поставити викладач.

## 1. Із чого складається система

```
браузер ──► gateway (nginx :8080) ──┬─► auth          — вхід за кодом, JWT, ролі
                                    ├─► catalog       — послуги, ціни, майстри
                                    ├─► availability  — графіки, вільний час, утримання слоту
                                    ├─► booking       — записи клієнтів, адмін-API
                                    └─► notification  — листи й нагадування
        postgres: окрема база для кожного сервісу (auth, catalog, availability, booking, notification)
        redis:    шина подій (потік autoslot.events)
```

| Що | Де |
| --- | --- |
| сторінка запису | `gateway/html/index.html` + `app.js` |
| адмін-панель | `gateway/html/admin.html` + `admin.js` |
| вікно входу (спільне) | `gateway/html/auth.js` |
| маршрутизація, перевірка входу, обмеження частоти | `gateway/nginx.conf`, `gateway/snippets/*.conf` |
| код сервісів | `services/<сервіс>/main.py` |
| спільний код | `services/common/` (`db.py`, `events.py`, `identity.py`, `mailer.py`) |
| запуск усього | `docker-compose.yml`, налаштування в `.env` |
| тести | `services/tests/` (модульні), `scripts/smoke_test.py` (уся система) |

Кожен Python-сервіс — це FastAPI-застосунок: функції з декораторами `@app.get` /
`@app.post` і є обробниками запитів. Усі сервіси збираються одним
`services/Dockerfile`, сервіс обирається аргументом `SERVICE`.

**Питання:** Чому в кожного сервісу своя база? *(Сервіс володіє своїми даними;
інші можуть отримати їх лише через його API або події. Так сервіси можна змінювати
й розгортати незалежно.)* Чому назовні відкритий лише порт gateway? *(Сервіси
довіряють заголовкам X-User-\* від gateway; якби до них можна було звернутися
напряму, ці заголовки можна було б підробити.)*

## 2. Реєстрація і вхід (авторизація)

Пошта підтверджується один раз — кодом із листа, а далі користувач входить за паролем.

1. Користувач натискає «Увійти» → «Зареєструватися» (або «Забули пароль?») і вводить
   пошту → `auth.js` робить `POST /api/auth/request-code`.
2. `auth/main.py`, функція `request_code`:
   - перевіряє формат пошти й ліміт: не більше 3 кодів на адресу за 15 хв;
   - генерує 6 цифр через `secrets` (криптографічно стійкий генератор);
   - зберігає в таблиці `login_codes` **не код, а його HMAC-хеш** і час закінчення (10 хв);
   - надсилає лист через `common/mailer.py` (SMTP, наприклад Gmail; без SMTP — лише в журнал).
3. Користувач вводить код і пароль → `POST /api/auth/verify`, функція `verify`:
   - бере останній невикористаний код цієї адреси, рахує спроби (після 5 код анулюється);
   - порівнює хеші через `hmac.compare_digest`;
   - створює користувача в таблиці `users` (роль `ADMIN`, якщо пошта є в `ADMIN_EMAILS`);
   - зберігає **хеш пароля scrypt** з випадковою сіллю (сам пароль ніде не зберігається);
   - ставить cookie `autoslot_session` з **JWT** (підписаний `AUTH_SECRET`, діє 8 год).
4. Наступного разу — лише пошта й пароль → `POST /api/auth/login`, функція `login`:
   - перевіряє пароль за сталий час; для неіснуючої адреси теж рахує scrypt і відповідає
     тим самим «Невірна пошта або пароль», тож не видно, хто зареєстрований;
   - після 5 невдалих паролів поспіль вхід блокується на 15 хв (знімає відновлення пароля).
5. Далі браузер сам надсилає cookie з кожним запитом.

Щоб листи з кодом приходили на Gmail, у `.env` задаються `SMTP_HOST=smtp.gmail.com`,
`SMTP_USER` (адреса Gmail), `SMTP_PASSWORD` (пароль застосунку Google, не звичайний пароль
акаунта) і `AUTH_DEV_MODE=false`.

Cookie має прапорці `HttpOnly` (JavaScript її не прочитає, тож XSS не вкраде сесію),
`SameSite=Lax` (не йде з POST-запитами з чужих сайтів — захист від CSRF) і `Secure`
(лише HTTPS; вмикається `COOKIE_SECURE=true`).

**Питання:** Навіщо код із листа, якщо є пароль? *(Код доводить, що пошта ваша: без
нього можна було б зареєструватися на чужу адресу.)* Чому scrypt, а не SHA-256? *(scrypt
навмисно повільний і вимагає багато пам'яті, тож перебір паролів з викраденої бази дуже
дорогий; сіль не дає використати готові таблиці.)* Що таке JWT і що в ньому? *(Підписаний
JSON: id, email, роль, термін дії. Підробити без секрету неможливо.)* Що буде, якщо
перебирати коди чи паролі? *(5 спроб на код, 3 коди на 15 хв на адресу, блокування після
5 невірних паролів і ліміт запитів у nginx.)*

## 3. Як сервіси дізнаються, хто користувач

Перевірка JWT зроблена **в одному місці** — у gateway:

1. Запит `GET /api/bookings` приходить у nginx. Location має
   `include snippets/require_login.conf`, а в ньому `auth_request /_auth`.
2. nginx робить внутрішній підзапит до `auth` → `GET /internal/verify` (функція
   `internal_verify`). Той читає cookie і перевіряє JWT.
3. 401 → nginx одразу повертає 401 браузеру. 200 → nginx забирає з відповіді
   заголовки `X-User-Id`, `X-User-Email`, `X-User-Role` (`auth_request_set`).
4. nginx передає запит у booking із цими заголовками (`snippets/proxy.conf`).
   Якщо клієнт сам надіслав `X-User-Role: ADMIN`, nginx замінить його своїм значенням.
5. У booking функція `current_user` (`common/identity.py`) читає заголовки, а
   `admin_user` додатково вимагає роль `ADMIN` (інакше 403).

**Питання:** Чому сервіси не перевіряють JWT самі? *(Одне місце перевірки, сервісам не
потрібен секрет; це шаблон API gateway.)* Що завадить клієнту надіслати
`X-User-Role: ADMIN`? *(nginx перезаписує ці заголовки; smoke-тест це перевіряє.)*

## 4. Шлях одного запису

1. `app.js`: клієнт обирає послугу, день і час → `POST /api/bookings`.
2. nginx перевіряє вхід (розділ 3) і передає запит одній з реплік booking.
3. `booking/main.py`, `create_booking`:
   - синхронно кличе availability `POST /internal/holds`;
   - availability (`create_hold`) блокує рядок графіка майстра (`SELECT … FOR UPDATE`),
     перевіряє перетин з іншими записами й створює hold на 5 хв (або 409, якщо зайнято);
   - booking зберігає запис з `user_email` власника;
   - публікує подію `BookingCreated` у Redis (`common/events.py`, `publish` → `XADD`).
4. Подію читають дві групи (`_consume` → `XREADGROUP`):
   - **availability** (`handle_event`) переводить hold у CONFIRMED;
   - **notification** (`handle_event`) створює лист-підтвердження й нагадування на
     −24 год і −2 год. Нагадування надсилає фонова функція `send_due_reminders`.
5. Клієнт бачить запис у «Мої записи» (`GET /api/bookings` — лише свої).

Скасування: `POST /api/bookings/{id}/cancel` — власник або адмін. Чужий запис для
клієнта виглядає як неіснуючий (404). Скасування адміном пишеться в `admin_actions`.

**Питання:** Чому двоє не запишуться на один час, навіть через різні репліки?
*(Блокування в базі availability, а не в пам'яті процесу.)* Навіщо hold?
*(Між перевіркою й записом є мережевий виклик; hold резервує час і сам зникає через
5 хв, якщо booking впаде.)* Що буде, якщо notification лежить? *(Запис працює,
подія чекає в Redis і обробиться після запуску.)*

## 5. Адмін-панель

`/admin.html` + `admin.js` показують усі записи з фільтрами (дати, майстер, статус,
пошук), завантаженість майстрів і журнал дій. Дані беруться з
`/api/admin/bookings`, `/api/admin/stats`, `/api/admin/audit` (booking-service).

Сторінка сама по собі публічна, але **захист на сервері**: без ролі ADMIN API
поверне 403, і сторінка нічого не покаже. Ховати кнопки в браузері — не захист.

## 6. Безпека — коротко

| Загроза | Захист | Де |
| --- | --- | --- |
| хтось бачить чужі записи | записи прив'язані до email із сесії | `booking/main.py` |
| підробка ролі | роль лише з JWT, заголовки перезаписує nginx | `snippets/proxy.conf` |
| перебір кодів і паролів, спам листами | ліміти в nginx, блокування акаунта в auth-service | `nginx.conf`, `auth/main.py` |
| крадіжка сесії через XSS | HttpOnly-cookie, CSP без inline-скриптів, текст через `textContent` | `auth/main.py`, `security_headers.conf`, `*.js` |
| CSRF | `SameSite=Lax`, API приймає JSON | `auth/main.py` |
| витік бази | паролі лише як хеш scrypt із сіллю, коди лише як HMAC-хеш | `auth/main.py` |
| clickjacking | `X-Frame-Options: DENY` | `security_headers.conf` |
| секрети в git | лише в `.env`, який не комітиться | `.env.example` |

## 7. Репліки й балансування

`deploy.replicas` у compose запускає кожен сервіс у `REPLICAS` копіях. nginx
(`upstream … resolve`) бере адреси всіх копій з DNS Docker і розподіляє запити по
колу; заголовок `X-Upstream` показує, хто відповів. Схему БД створює одна репліка під
advisory lock (`init_schema` у `common/db.py`). Подію з Redis обробляє одна репліка
групи, а нагадування — одна завдяки `FOR UPDATE SKIP LOCKED`.

## 8. Моніторинг (ЛР4)

Кожен сервіс віддає `/metrics` (`common/metrics.py`): кількість і тривалість запитів,
пам'ять, бізнес-події. Prometheus збирає їх з усіх реплік (`monitoring/prometheus.yml`),
перевіряє правила сповіщень (`monitoring/alerts.yml`), а Grafana показує дашборд.
Запуск: `docker compose --profile monitoring up -d`, Grafana на порту 3000.

## 9. Запуск і перевірка

```bash
cd lab2-containers
cp .env.example .env            # задати паролі й секрет; ADMIN_EMAILS — ваша пошта
docker compose up -d --build
# http://localhost:8080 — запис, http://localhost:8080/admin.html — адмінка
python scripts/smoke_test.py    # 20 перевірок, потрібен AUTH_DEV_MODE=true
cd services && pip install -r requirements.txt pytest && python -m pytest tests -q
```

Як подивитися, що відбувається:
`docker compose logs -f auth` (коди входу в режимі розробки),
`docker compose logs -f notification` (листи),
`docker compose exec postgres psql -U autoslot -d booking -c "select * from bookings"`.
