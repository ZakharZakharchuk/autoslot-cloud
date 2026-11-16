// Сторінка запису. Каталог і вільний час доступні всім, а запис,
// «Мої записи» й сповіщення — лише після входу (див. auth.js).

const state = { services: [], service: null, day: null, slot: null };
const $ = (id) => document.getElementById(id);
const dayFmt = new Intl.DateTimeFormat("uk-UA", { weekday: "short" });
const longDay = new Intl.DateTimeFormat("uk-UA", { weekday: "long", day: "numeric", month: "long" });
const hm = (iso) => iso.slice(11, 16);
const isoDate = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

async function getJson(url) {
  const res = await fetch(url);
  if (res.status === 401) return { ok: false, status: 401 };
  const data = await res.json().catch(() => ({}));
  return res.ok ? { ok: true, data } : { ok: false, status: res.status, error: data.detail || `Помилка ${res.status}` };
}

function press(container, btn) {
  container.querySelectorAll("[aria-pressed]").forEach((b) => b.setAttribute("aria-pressed", "false"));
  if (btn) btn.setAttribute("aria-pressed", "true");
}

function renderTicket() {
  const s = state.service, sl = state.slot;
  $("t-svc").textContent = s ? s.name : "—";
  $("t-day").textContent = state.day ? longDay.format(new Date(state.day + "T12:00")) : "—";
  $("t-time").textContent = sl ? `${hm(sl.start)}–${hm(sl.end)}` : "—";
  $("t-master").textContent = sl ? sl.masterName : "—";
  $("t-price").textContent = s ? `${s.priceUah} грн` : "—";
  $("book-btn").disabled = !sl;
  $("book-btn").textContent = Auth.user ? "Записатися" : "Увійти й записатися";
}

async function loadServices() {
  const res = await getJson("/api/services");
  if (!res.ok) { $("services").replaceChildren(el("p", { className: "msg err", textContent: "Сервер недоступний" })); return; }
  state.services = res.data;
  const box = $("services");
  box.replaceChildren();
  for (const s of state.services) {
    const b = el("button", { type: "button", className: "svc" }, [
      el("span", {}, [s.name, el("small", { textContent: s.category })]),
      el("span", { className: "dur", textContent: `${s.durationMin} хв` }),
      el("span", { className: "price", textContent: `${s.priceUah} грн` }),
    ]);
    b.setAttribute("aria-pressed", "false");
    b.onclick = () => { state.service = s; state.slot = null; press(box, b); renderTicket(); loadSlots(); };
    box.append(b);
  }
}

function renderDays() {
  const box = $("days");
  const today = new Date();
  for (let i = 0; i < 14; i++) {
    const d = new Date(today); d.setDate(today.getDate() + i);
    const b = el("button", { type: "button", className: "day" }, [
      el("small", { textContent: i === 0 ? "сьогодні" : dayFmt.format(d) }),
      el("b", { textContent: String(d.getDate()) }),
    ]);
    b.setAttribute("aria-pressed", "false");
    b.setAttribute("aria-label", longDay.format(d));
    b.onclick = () => { state.day = isoDate(d); state.slot = null; press(box, b); renderTicket(); loadSlots(); };
    box.append(b);
  }
}

async function loadSlots() {
  const box = $("slots");
  if (!state.service || !state.day) return;
  box.replaceChildren(el("p", { className: "hint", textContent: "Шукаємо вільний час…" }));
  const res = await getJson(`/api/availability?serviceId=${encodeURIComponent(state.service.id)}&date=${state.day}`);
  if (!res.ok) { box.replaceChildren(el("p", { className: "msg err", textContent: res.error })); return; }
  if (!res.data.length) {
    box.replaceChildren(el("p", { className: "hint", textContent: "На цей день вільних вікон немає. Спробуйте інший день." }));
    return;
  }
  const byMaster = new Map();
  for (const sl of res.data) {
    if (!byMaster.has(sl.masterId)) byMaster.set(sl.masterId, []);
    byMaster.get(sl.masterId).push(sl);
  }
  box.replaceChildren();
  for (const list of byMaster.values()) {
    const times = el("div", { className: "times" });
    for (const sl of list) {
      const b = el("button", { type: "button", className: "time", textContent: hm(sl.start) });
      b.setAttribute("aria-pressed", "false");
      b.onclick = () => { state.slot = sl; press(box, b); renderTicket(); };
      times.append(b);
    }
    box.append(el("div", { className: "master" }, [el("h3", { textContent: list[0].masterName }), times]));
  }
}

$("book").onsubmit = async (ev) => {
  ev.preventDefault();
  const msg = $("msg"); msg.className = "msg"; msg.textContent = "";
  if (!Auth.user) {
    await Auth.open("Щоб записатися, увійдіть — надішлемо код на пошту.");
  }
  const res = await postJson("/api/bookings", {
    serviceId: state.service.id, masterId: state.slot.masterId, start: state.slot.start,
    clientName: $("name").value, phone: $("phone").value,
  });
  if (res.status === 401) { await Auth.open("Сесія завершилась, увійдіть ще раз."); return; }
  if (!res.ok) { msg.className = "msg err"; msg.textContent = res.error; loadSlots(); return; }
  const b = res.data;
  msg.className = "msg ok";
  msg.textContent = `Записали: ${longDay.format(new Date(b.start))}, ${hm(b.start)}. Майстер — ${b.masterName}. Підтвердження надійде на ${b.userEmail}.`;
  $("ticket").classList.add("flash"); setTimeout(() => $("ticket").classList.remove("flash"), 1300);
  state.slot = null; renderTicket(); loadSlots(); loadMine();
};

async function loadMine() {
  const box = $("my-bookings");
  $("mine-login").hidden = !!Auth.user;
  if (!Auth.user) { box.replaceChildren(); $("notifications").hidden = true; return; }
  const res = await getJson("/api/bookings");
  if (!res.ok) { box.replaceChildren(el("p", { className: "msg err", textContent: res.error || "Не вдалося завантажити" })); return; }
  box.replaceChildren();
  if (!res.data.length) box.append(el("p", { className: "hint", textContent: "Записів поки немає." }));
  for (const b of res.data) {
    const row = el("div", { className: "row" + (b.status === "CANCELLED" ? " cancelled" : "") }, [
      el("div", { className: "when" }, [hm(b.start), el("small", { textContent: longDay.format(new Date(b.start)) })]),
      el("div", { className: "what", textContent: `${b.serviceName}, ${b.masterName}, ${b.priceUah} грн` }),
    ]);
    if (b.status !== "CANCELLED") {
      const c = el("button", { type: "button", className: "btn ghost", textContent: "Скасувати запис" });
      c.onclick = async () => { await postJson(`/api/bookings/${b.id}/cancel`); loadMine(); loadSlots(); };
      row.append(c);
    }
    box.append(row);
  }
  // Сповіщення формуються асинхронно (через подію), тому підвантажуємо їх трохи пізніше
  setTimeout(loadNotifications, 600);
}

async function loadNotifications() {
  const box = $("notifications");
  const res = await getJson("/api/notifications");
  if (!res.ok || !res.data.length) { box.hidden = true; return; }
  box.hidden = false;
  box.replaceChildren(...res.data.map((n) => {
    const when = n.status === "SCHEDULED" ? `заплановано на ${hm(n.sendAt)}, ${longDay.format(new Date(n.sendAt))}`
               : n.status === "FAILED" ? "не вдалося надіслати" : "надіслано";
    return el("li", { textContent: `Лист, ${when}: ${n.text}` });
  }));
}

Auth.onChange = () => { renderTicket(); loadMine(); };
$("mine-login-btn").onclick = () => Auth.open();

renderDays();
renderTicket();
loadServices();
Auth.load().then(() => { renderTicket(); loadMine(); });
fetch("/api/health").then((r) => r.json()).then((h) => { $("ver").textContent = `версія ${h.version}`; }).catch(() => {});
