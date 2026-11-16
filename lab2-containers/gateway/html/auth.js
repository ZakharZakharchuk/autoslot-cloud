// Вхід за кодом з пошти. Спільний для index.html і admin.html.
//
// Сесія зберігається в HttpOnly-cookie, яку ставить auth-service. JavaScript її
// не бачить і не читає: браузер сам додає cookie до кожного запиту на цей сайт.
// Тому тут ми лише питаємо /api/auth/me, хто зараз увійшов.

const Auth = {
  user: null,          // { email, role } або null
  onChange: () => {},  // сторінка підписується, щоб оновитися після входу/виходу

  async load() {
    try {
      const res = await fetch("/api/auth/me");
      this.user = res.ok ? await res.json() : null;
    } catch { this.user = null; }
    this.render();
    return this.user;
  },

  // Шапка: «Увійти» або email, роль і «Вийти»
  render() {
    const box = document.getElementById("account");
    if (!box) return;
    box.replaceChildren();
    if (!this.user) {
      const b = el("button", { className: "link-btn", textContent: "Увійти" });
      b.onclick = () => this.open();
      box.append(b);
      return;
    }
    box.append(el("span", { className: "who", textContent: this.user.email }));
    if (this.user.role === "ADMIN") {
      box.append(el("span", { className: "badge", textContent: "адмін" }));
    }
    const out = el("button", { className: "link-btn", textContent: "Вийти" });
    out.onclick = async () => {
      await fetch("/api/auth/logout", { method: "POST" });
      this.user = null; this.render(); this.onChange();
    };
    box.append(out);
  },

  // Відкриває вікно входу. Повертає Promise, який виконується після успішного входу.
  open(reason) {
    const dlg = document.getElementById("login");
    const emailForm = document.getElementById("login-email-form");
    const codeForm = document.getElementById("login-code-form");
    const msg = document.getElementById("login-msg");
    document.getElementById("login-reason").textContent = reason || "Введіть пошту — надішлемо код входу.";
    emailForm.hidden = false; codeForm.hidden = true; msg.textContent = "";
    dlg.showModal();
    document.getElementById("login-email").focus();

    return new Promise((resolve) => {
      emailForm.onsubmit = async (ev) => {
        ev.preventDefault();
        msg.textContent = "";
        const email = document.getElementById("login-email").value.trim();
        const res = await postJson("/api/auth/request-code", { email });
        if (!res.ok) { msg.textContent = res.error; return; }
        document.getElementById("login-sent-to").textContent = email;
        const hint = document.getElementById("login-dev");
        // devCode приходить лише в режимі розробки, коли пошта не налаштована
        hint.hidden = !res.data.devCode;
        hint.textContent = res.data.devCode ? `Режим розробки: ваш код ${res.data.devCode}` : "";
        emailForm.hidden = true; codeForm.hidden = false;
        document.getElementById("login-code").value = "";
        document.getElementById("login-code").focus();
      };
      codeForm.onsubmit = async (ev) => {
        ev.preventDefault();
        msg.textContent = "";
        const email = document.getElementById("login-email").value.trim();
        const code = document.getElementById("login-code").value.trim();
        const res = await postJson("/api/auth/verify", { email, code });
        if (!res.ok) { msg.textContent = res.error; return; }
        this.user = res.data;
        dlg.close();
        this.render(); this.onChange();
        resolve(this.user);
      };
      document.getElementById("login-back").onclick = () => { emailForm.hidden = false; codeForm.hidden = true; msg.textContent = ""; };
      document.getElementById("login-cancel").onclick = () => dlg.close();
    });
  },
};

// Створює елемент і задає властивості. Текст завжди через textContent, а не innerHTML:
// дані, які ввів користувач (ім'я, email), не виконаються як HTML/JS (захист від XSS).
function el(tag, props = {}, children = []) {
  const node = Object.assign(document.createElement(tag), props);
  node.append(...children);
  return node;
}

async function postJson(url, body) {
  const res = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" },
                                 body: body === undefined ? undefined : JSON.stringify(body) });
  const data = await res.json().catch(() => ({}));
  if (res.ok) return { ok: true, data };
  let error = Array.isArray(data.detail) ? "Перевірте введені дані" : data.detail;
  if (res.status === 429 && !error) error = "Забагато спроб. Зачекайте хвилину";
  return { ok: false, status: res.status, error: error || `Помилка ${res.status}` };
}
