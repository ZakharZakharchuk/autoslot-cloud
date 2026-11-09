// Вхід і реєстрація. Спільний для index.html і admin.html.
//
// Реєстрація: пошта -> код із листа + пароль. Код підтверджує, що пошта ваша.
// Далі вхід лише за поштою й паролем, без листів. «Забули пароль?» — той самий
// шлях, що й реєстрація: новий код на пошту і новий пароль.
//
// Сесія зберігається в HttpOnly-cookie, яку ставить auth-service. JavaScript її
// не бачить і не читає: браузер сам додає cookie до кожного запиту на цей сайт.
// Тому тут ми лише питаємо /api/auth/me, хто зараз увійшов.

const LOGIN_DIALOG = `
  <div class="stripe"></div>
  <form id="login-pass-form" method="dialog">
    <h2 id="login-title">Вхід</h2>
    <p id="login-reason"></p>
    <label for="login-email">Пошта</label>
    <input id="login-email" type="email" autocomplete="username" required placeholder="name@gmail.com">
    <label for="login-password">Пароль</label>
    <input id="login-password" type="password" autocomplete="current-password" required>
    <button class="btn" type="submit">Увійти</button>
    <div class="row-actions">
      <button type="button" class="link-btn" id="login-to-register">Зареєструватися</button>
      <button type="button" class="link-btn" id="login-to-reset">Забули пароль?</button>
    </div>
  </form>
  <form id="login-email-form" method="dialog" hidden>
    <h2 id="login-email-title">Реєстрація</h2>
    <p>Надішлемо на пошту код підтвердження, а потім ви задасте пароль.</p>
    <label for="login-reg-email">Пошта</label>
    <input id="login-reg-email" type="email" autocomplete="email" required placeholder="name@gmail.com">
    <button class="btn" type="submit">Надіслати код</button>
    <div class="row-actions"><button type="button" class="link-btn" data-to-login>Вже маю пароль</button></div>
  </form>
  <form id="login-code-form" method="dialog" hidden>
    <h2>Код із листа</h2>
    <p>Надіслали 6 цифр на <b id="login-sent-to"></b>. Код дійсний 10 хвилин. Якщо листа немає, перевірте «Спам».</p>
    <div class="dev-hint" id="login-dev" hidden></div>
    <label for="login-code">Код</label>
    <input id="login-code" class="code-input" inputmode="numeric" autocomplete="one-time-code" maxlength="6" pattern="[0-9]{6}" required>
    <label for="login-new-password">Пароль (щонайменше 8 символів)</label>
    <input id="login-new-password" type="password" autocomplete="new-password" minlength="8" maxlength="128" required>
    <label for="login-new-password2">Повторіть пароль</label>
    <input id="login-new-password2" type="password" autocomplete="new-password" minlength="8" maxlength="128" required>
    <button class="btn" type="submit">Підтвердити й увійти</button>
    <div class="row-actions"><button type="button" class="link-btn" id="login-back">Інша пошта</button></div>
  </form>
  <p class="msg err" id="login-msg" role="alert" style="padding: 0 20px 16px"></p>
  <div class="row-actions" style="padding: 0 20px 16px; margin: 0"><button type="button" class="link-btn" id="login-cancel">Скасувати</button></div>
`;

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

  dialog() {
    let dlg = document.getElementById("login");
    if (!dlg) {
      // розмітка статична, без даних користувача, тому innerHTML тут безпечний
      dlg = el("dialog", { className: "login", id: "login" });
      dlg.setAttribute("aria-labelledby", "login-title");
      dlg.innerHTML = LOGIN_DIALOG;
      document.body.append(dlg);
    }
    return dlg;
  },

  // Відкриває вікно входу. Повертає Promise, який виконується після успішного входу.
  open(reason) {
    const dlg = this.dialog();
    const $ = (id) => document.getElementById(id);
    const forms = { login: $("login-pass-form"), email: $("login-email-form"), code: $("login-code-form") };
    const msg = $("login-msg");
    const show = (name) => {
      for (const [k, f] of Object.entries(forms)) f.hidden = k !== name;
      msg.textContent = "";
      const first = forms[name].querySelector("input");
      if (first) first.focus();
    };
    const toEmail = (title) => {
      $("login-email-title").textContent = title;
      $("login-reg-email").value = $("login-email").value;
      show("email");
    };
    $("login-reason").textContent = reason || "Увійдіть за поштою й паролем.";
    $("login-password").value = "";
    show("login");
    dlg.showModal();

    return new Promise((resolve) => {
      const done = (user) => {
        this.user = user;
        dlg.close();
        this.render(); this.onChange();
        resolve(user);
      };
      forms.login.onsubmit = async (ev) => {
        ev.preventDefault();
        msg.textContent = "";
        const res = await postJson("/api/auth/login", { email: $("login-email").value.trim(), password: $("login-password").value });
        if (!res.ok) { msg.textContent = res.error; return; }
        done(res.data);
      };
      forms.email.onsubmit = async (ev) => {
        ev.preventDefault();
        msg.textContent = "";
        const email = $("login-reg-email").value.trim();
        const res = await postJson("/api/auth/request-code", { email });
        if (!res.ok) { msg.textContent = res.error; return; }
        $("login-sent-to").textContent = email;
        const hint = $("login-dev");
        // devCode приходить лише в режимі розробки, коли пошта не налаштована
        hint.hidden = !res.data.devCode;
        hint.textContent = res.data.devCode ? `Режим розробки: ваш код ${res.data.devCode}` : "";
        for (const id of ["login-code", "login-new-password", "login-new-password2"]) $(id).value = "";
        show("code");
      };
      forms.code.onsubmit = async (ev) => {
        ev.preventDefault();
        msg.textContent = "";
        const password = $("login-new-password").value;
        if (password !== $("login-new-password2").value) { msg.textContent = "Паролі не збігаються"; return; }
        const email = $("login-reg-email").value.trim();
        const res = await postJson("/api/auth/verify", { email, code: $("login-code").value.trim(), password });
        if (!res.ok) { msg.textContent = res.error; return; }
        $("login-email").value = email;
        done(res.data);
      };
      $("login-to-register").onclick = () => toEmail("Реєстрація");
      $("login-to-reset").onclick = () => toEmail("Новий пароль");
      dlg.querySelector("[data-to-login]").onclick = () => show("login");
      $("login-back").onclick = () => show("email");
      $("login-cancel").onclick = () => dlg.close();
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
