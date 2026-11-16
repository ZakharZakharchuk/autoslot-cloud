// Адмін-панель. Сторінка сама по собі публічна, але всі дані йдуть з /api/admin/*,
// а там booking-service перевіряє роль ADMIN (заголовок X-User-Role від gateway).
// Тобто захист — на сервері; приховування блоків тут лише для зручності.

const $ = (id) => document.getElementById(id);
const dt = new Intl.DateTimeFormat("uk-UA", { day: "2-digit", month: "2-digit", weekday: "short", hour: "2-digit", minute: "2-digit" });
const isoDate = (d) => d.toISOString().slice(0, 10);
// 1 запис, 2 записи, 5 записів
const plural = (n) => (n % 10 === 1 && n % 100 !== 11) ? "запис"
  : (n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 10 || n % 100 >= 20)) ? "записи" : "записів";

function showGate(text, withLogin) {
  $("panel").hidden = true;
  const gate = $("gate");
  gate.hidden = false;
  gate.replaceChildren(el("p", { textContent: text, style: "margin:0 0 10px" }));
  if (withLogin) {
    const b = el("button", { className: "btn", textContent: "Увійти", style: "max-width:220px" });
    b.onclick = () => Auth.open("Вхід для адміністратора");
    gate.append(b);
  }
}

async function getJson(url) {
  const res = await fetch(url);
  const data = await res.json().catch(() => null);
  return { ok: res.ok, status: res.status, data };
}

function query() {
  const p = new URLSearchParams();
  if ($("f-from").value) p.set("dateFrom", $("f-from").value);
  if ($("f-to").value) p.set("dateTo", $("f-to").value);
  if ($("f-master").value) p.set("masterId", $("f-master").value);
  if ($("f-status").value) p.set("status", $("f-status").value);
  if ($("f-q").value.trim()) p.set("q", $("f-q").value.trim());
  return p.toString();
}

async function loadAll() {
  const qs = query();
  const [bookings, stats, audit] = await Promise.all([
    getJson(`/api/admin/bookings?${qs}`), getJson(`/api/admin/stats?${qs}`), getJson("/api/admin/audit"),
  ]);
  if (bookings.status === 401) { showGate("Сесія завершилась. Увійдіть ще раз.", true); return; }
  if (bookings.status === 403) { showGate("У вашого облікового запису немає прав адміністратора.", false); return; }
  $("gate").hidden = true; $("panel").hidden = false;

  $("stats").replaceChildren(...stats.data.map((m) => el("div", { className: "stat" }, [
    el("small", { textContent: m.masterName }),
    el("b", { textContent: `${m.confirmed} ${plural(m.confirmed)}` }),
    el("small", { textContent: `${m.revenueUah} грн, скасовано: ${m.cancelled}` }),
  ])));
  if (!stats.data.length) $("stats").append(el("p", { className: "muted", textContent: "За період записів немає." }));

  $("count").textContent = `(${bookings.data.length})`;
  $("rows").replaceChildren(...bookings.data.map((b) => {
    const tr = el("tr", { className: b.status === "CANCELLED" ? "cancelled" : "" }, [
      el("td", { textContent: dt.format(new Date(b.start)) }),
      el("td", { textContent: b.serviceName }),
      el("td", { textContent: b.masterName }),
      el("td", { textContent: b.clientName }),
      el("td", { textContent: b.userEmail }),
      el("td", { textContent: b.phone }),
      el("td", { textContent: `${b.priceUah} грн` }),
      el("td", { textContent: b.status === "CANCELLED" ? "скасовано" : "підтверджено" }),
    ]);
    const td = el("td");
    if (b.status !== "CANCELLED") {
      const c = el("button", { className: "link-btn", textContent: "Скасувати" });
      c.onclick = async () => {
        if (!confirm(`Скасувати запис ${b.clientName} на ${dt.format(new Date(b.start))}? Клієнт отримає лист.`)) return;
        await postJson(`/api/bookings/${b.id}/cancel`);
        loadAll();
      };
      td.append(c);
    }
    tr.append(td);
    return tr;
  }));

  $("audit").replaceChildren(...(audit.data || []).map((a) =>
    el("li", { textContent: `${dt.format(new Date(a.at + "Z"))}: ${a.adminEmail} скасував запис ${a.bookingId.slice(0, 8)}` })));
  if (!audit.data || !audit.data.length) $("audit").append(el("li", { textContent: "Дій ще не було." }));
}

async function init() {
  const user = await Auth.load();
  if (!user) { showGate("Ця сторінка лише для адміністратора.", true); return; }
  if (user.role !== "ADMIN") { showGate("У вашого облікового запису немає прав адміністратора.", false); return; }
  const masters = await getJson("/api/masters");
  for (const m of masters.data || []) $("f-master").append(el("option", { value: m.id, textContent: m.name }));
  loadAll();
}

const today = new Date();
$("f-from").value = isoDate(today);
$("f-to").value = isoDate(new Date(today.getTime() + 30 * 864e5));
$("filters").onsubmit = (ev) => { ev.preventDefault(); loadAll(); };
Auth.onChange = init;
init();
