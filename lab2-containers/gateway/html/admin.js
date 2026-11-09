// Адмін-панель: вкладки «Записи», «Майстри», «Активність» (#bookings, #masters, #activity).
// Сторінка сама по собі публічна, але всі дані йдуть з /api/admin/*,
// а там booking-service перевіряє роль ADMIN (заголовок X-User-Role від gateway).
// Тобто захист — на сервері; приховування блоків тут лише для зручності.

const $ = (id) => document.getElementById(id);
const dt = new Intl.DateTimeFormat("uk-UA", { day: "2-digit", month: "2-digit", weekday: "short", hour: "2-digit", minute: "2-digit" });
const isoDate = (d) => d.toISOString().slice(0, 10);
const utc = (s) => new Date(s.endsWith("Z") ? s : s + "Z");  // час подій сервер зберігає в UTC без позначки
// 1 запис, 2 записи, 5 записів
const plural = (n) => (n % 10 === 1 && n % 100 !== 11) ? "запис"
  : (n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 10 || n % 100 >= 20)) ? "записи" : "записів";
const TABS = ["bookings", "masters", "activity"];
const ref = { masters: [], services: [], schedules: [] };  // довідники, завантажуються один раз

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

// 401/403 від адмін-API: сесія скінчилась або прав немає
function denied(res) {
  if (res.status === 401) { showGate("Сесія завершилась. Увійдіть ще раз.", true); return true; }
  if (res.status === 403) { showGate("У вашого облікового запису немає прав адміністратора.", false); return true; }
  return false;
}

function stat(label, value, note) {
  return el("div", { className: "stat" }, [el("small", { textContent: label }), el("b", { textContent: value }),
    ...(note ? [el("small", { textContent: note })] : [])]);
}

// ---------- вкладки ----------

function currentTab() {
  const t = location.hash.slice(1);
  return TABS.includes(t) ? t : "bookings";
}

function showTab() {
  const tab = currentTab();
  for (const t of TABS) {
    $(`tab-${t}`).hidden = t !== tab;
    $(`tab-btn-${t}`).setAttribute("aria-selected", String(t === tab));
  }
  ({ bookings: loadBookings, masters: loadMasters, activity: loadActivity })[tab]();
}

// ---------- Записи ----------

function query() {
  const p = new URLSearchParams();
  if ($("f-from").value) p.set("dateFrom", $("f-from").value);
  if ($("f-to").value) p.set("dateTo", $("f-to").value);
  if ($("f-master").value) p.set("masterId", $("f-master").value);
  if ($("f-status").value) p.set("status", $("f-status").value);
  if ($("f-q").value.trim()) p.set("q", $("f-q").value.trim());
  return p.toString();
}

async function loadBookings() {
  const bookings = await getJson(`/api/admin/bookings?${query()}`);
  if (denied(bookings)) return;
  const rows = bookings.data;
  const confirmed = rows.filter((b) => b.status === "CONFIRMED");
  $("summary").replaceChildren(
    stat("Підтверджено", `${confirmed.length} ${plural(confirmed.length)}`),
    stat("Скасовано", String(rows.length - confirmed.length)),
    stat("Сума підтверджених", `${confirmed.reduce((s, b) => s + b.priceUah, 0)} грн`),
  );
  $("count").textContent = `(${rows.length})`;
  $("rows").replaceChildren(...rows.map((b) => {
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
        loadBookings();
      };
      td.append(c);
    }
    tr.append(td);
    return tr;
  }));
  if (!rows.length) $("rows").append(el("tr", {}, [el("td", { colSpan: 9, className: "muted", textContent: "За обраними умовами записів немає." })]));
}

// ---------- Майстри ----------

async function loadMasters() {
  const p = new URLSearchParams({ dateFrom: $("m-from").value, dateTo: $("m-to").value });
  const stats = await getJson(`/api/admin/stats?${p}`);
  if (denied(stats)) return;
  const byMaster = new Map(stats.data.map((m) => [m.masterId, m]));
  const services = new Map(ref.services.map((s) => [s.id, s]));
  $("master-cards").replaceChildren(...ref.masters.map((m) => {
    const st = byMaster.get(m.id) || { confirmed: 0, cancelled: 0, revenueUah: 0 };
    const sch = ref.schedules.find((x) => x.masterId === m.id);
    const show = el("button", { type: "button", className: "btn ghost", textContent: "Записи майстра" });
    show.onclick = () => {
      $("f-master").value = m.id; $("f-from").value = $("m-from").value; $("f-to").value = $("m-to").value;
      location.hash = "#bookings";
    };
    return el("article", { className: "card" }, [
      el("h3", { textContent: m.name }),
      el("p", { className: "muted", textContent: sch ? `${sch.daysText}, ${sch.startHour}:00–${sch.endHour}:00` : "графік не задано" }),
      el("div", { className: "stats" }, [
        stat("Записи", String(st.confirmed)), stat("Скасовано", String(st.cancelled)), stat("Сума", `${st.revenueUah} грн`),
      ]),
      el("small", { className: "muted", textContent: "Виконує:" }),
      el("ul", { className: "skills" }, m.serviceIds.map((id) => el("li", { textContent: services.get(id)?.name || id }))),
      show,
    ]);
  }));
}

// ---------- Активність ----------

const ACTIVITY = {
  CREATED: ["new", "новий запис"],
  CANCELLED: ["cancel", "клієнт скасував"],
  CANCELLED_BY_ADMIN: ["admin", "адмін скасував"],
};

async function loadActivity() {
  const res = await getJson("/api/admin/activity?limit=200");
  if (denied(res)) return;
  const type = $("a-type").value;
  const items = res.data.filter((a) => !type || a.type === type);
  $("activity").replaceChildren(...items.map((a) => {
    const [cls, label] = ACTIVITY[a.type];
    const b = a.booking;
    return el("li", { className: `ev ${cls}` }, [
      el("time", { textContent: dt.format(utc(a.at)) }),
      el("span", { className: "tag", textContent: label }),
      el("div", {}, [
        el("b", { textContent: a.actor }),
        el("div", { className: "muted", textContent: `${b.serviceName} · ${b.masterName} · ${dt.format(new Date(b.start))} · ${b.clientName}, ${b.priceUah} грн` }),
      ]),
    ]);
  }));
  if (!items.length) $("activity").append(el("li", { className: "muted", textContent: "Подій поки немає." }));
}

// ---------- старт ----------

async function init() {
  const user = await Auth.load();
  if (!user) { showGate("Ця сторінка лише для адміністратора.", true); return; }
  if (user.role !== "ADMIN") { showGate("У вашого облікового запису немає прав адміністратора.", false); return; }
  $("gate").hidden = true; $("panel").hidden = false;
  if (!ref.masters.length) {
    const [masters, services, schedules] = await Promise.all([
      getJson("/api/masters"), getJson("/api/services"), getJson("/api/availability/schedules")]);
    ref.masters = masters.data || []; ref.services = services.data || []; ref.schedules = schedules.data || [];
    for (const m of ref.masters) $("f-master").append(el("option", { value: m.id, textContent: m.name }));
  }
  showTab();
}

const today = new Date();
for (const [from, to] of [["f-from", "f-to"], ["m-from", "m-to"]]) {
  $(from).value = isoDate(today);
  $(to).value = isoDate(new Date(today.getTime() + 30 * 864e5));
}
$("filters").onsubmit = (ev) => { ev.preventDefault(); loadBookings(); };
$("m-filters").onsubmit = (ev) => { ev.preventDefault(); loadMasters(); };
$("a-type").onchange = loadActivity;
window.addEventListener("hashchange", () => { if (!$("panel").hidden) showTab(); });
Auth.onChange = init;
init();
