"use strict";
/* TradingApp - frontend bez frameworkow. Routing po #hash, dane z /api. */

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const view = $("#view");
let SCHEMA = null;
let DEMO = false;
const DEMO_NOTES = {
  accounts: "W wersji demonstracyjnej nie da się dodać prawdziwego konta ani kluczy — możesz za to obejrzeć, "
    + "z jakimi brokerami i giełdami łączy się pełna wersja.",
  signals: "Dane o insiderach i funduszach są w wersji demonstracyjnej przykładowe (w pełnej wersji pochodzą z SEC).",
  radar: "Radar w wersji demonstracyjnej liczy ranking na danych symulowanych.",
  chart: "Wykresy w wersji demonstracyjnej pokazują ceny symulowane — wpisz dowolny symbol, np. AAPL, PKO.WSE albo BTC/USD.",
};
let timers = [];
let charts = [];

// ------------------------------------------------------------------ narzedzia
function esc(v) {
  return String(v ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
const nf2 = new Intl.NumberFormat("pl-PL", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
function usd(v, sign = false) {
  if (v == null || isNaN(v)) return "—";
  const s = nf2.format(Math.abs(v)) + " $";
  if (!sign) return (v < 0 ? "−" : "") + s;
  return (v > 0 ? "+" : v < 0 ? "−" : "") + s;
}
function pct(v, digits = 2, sign = true) {
  if (v == null || isNaN(v)) return "—";
  const s = Math.abs(v).toFixed(digits).replace(".", ",") + "%";
  return (sign && v > 0 ? "+" : v < 0 ? "−" : "") + s;
}
function money(v, cur, sign = false) {
  if (!cur || cur === "USD") return usd(v, sign);
  if (v == null || isNaN(v)) return "—";
  const s = nf2.format(Math.abs(v)) + " " + cur;
  if (!sign) return (v < 0 ? "−" : "") + s;
  return (v > 0 ? "+" : v < 0 ? "−" : "") + s;
}
function accTag(a) {
  const cur = a.currency && a.currency !== "USD" ? " · " + a.currency : "";
  if (a.type === "kraken") return "Kraken · " + (a.paper ? "na niby" : "na żywo") + cur;
  if (a.type === "gielda") return (a.exchange || "giełda") + " · " + (a.paper ? "na niby" : a.sandbox ? "testnet" : "na żywo") + cur;
  if (a.type === "sim") return "symulacja";
  if (a.type === "ibkr") return "IBKR · " + (a.paper ? "papier" : "na żywo") + cur;
  return (a.paper ? "papier" : "na żywo") + cur;
}
function accDesc(a) {
  if (a.type === "kraken" || a.type === "gielda") return (a.type === "kraken" ? "Kraken" : (a.exchange || "giełda")) + ", " +
    (a.paper ? "na niby" : a.sandbox ? "testnet" : "PRAWDZIWE PIENIĄDZE") + ", " + a.currency;
  if (a.type === "sim") return "symulacja";
  return a.paper ? "paper" : "LIVE";
}
function num(v, d = 2) {
  if (v == null || isNaN(v)) return "—";
  return new Intl.NumberFormat("pl-PL", { maximumFractionDigits: d }).format(v);
}
function price(v) {
  if (v == null) return "—";
  return Math.abs(v) >= 1 ? nf2.format(v) : Number(v).toFixed(6);
}
const tone = v => (v > 0 ? "up" : v < 0 ? "down" : "");
function when(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString("pl-PL", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}
function statusPill(s) {
  const label = { running: "działa", stopped: "zatrzymany", error: "błąd" }[s] || s;
  return `<span class="pill ${esc(s)}">${esc(label)}</span>`;
}
const MARKET = { stocks: "Akcje", crypto: "Krypto" };

function toast(msg, bad = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast" + (bad ? " bad" : "");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.add("hidden"), bad ? 7000 : 3500);
}

async function api(method, url, body) {
  const res = await fetch(url, {
    method, headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined, credentials: "same-origin",
  });
  if (res.status === 401 && !url.endsWith("/login")) { showLogin(); throw new Error("Zaloguj się"); }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Błąd serwera");
  return data;
}

function every(ms, fn) { timers.push(setInterval(fn, ms)); }
function cleanup() {
  timers.forEach(clearInterval); timers = [];
  charts.forEach(c => c.destroy()); charts = [];
}

// ------------------------------------------------------------------ wykresy
function css(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
const SERIES = ["#f2b544", "#6fa8ff", "#5ed3d0", "#b493ff", "#ff8fb1", "#9fd36a", "#c9d2e0", "#ff9f43"];
if (window.Chart) {
  Chart.defaults.font.family = "'Plex Sans', system-ui, sans-serif";
  Chart.defaults.font.size = 12;
  Chart.defaults.plugins.tooltip.backgroundColor = "#0c1422";
  Chart.defaults.plugins.tooltip.borderColor = "#2d3d5c";
  Chart.defaults.plugins.tooltip.borderWidth = 1;
  Chart.defaults.plugins.tooltip.padding = 10;
  Chart.defaults.plugins.tooltip.bodyFont = { family: "'Plex Mono', monospace", size: 12 };
}
function spark(points, w = 200, h = 38) {
  if (!points || points.length < 2) return "";
  const ys = points.map(p => p[1]), lo = Math.min(...ys), hi = Math.max(...ys), span = hi - lo || 1;
  const xy = ys.map((y, i) => [(i / (ys.length - 1)) * w, h - 3 - ((y - lo) / span) * (h - 6)]);
  const line = xy.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const tone = ys[ys.length - 1] >= ys[0] ? "var(--up)" : "var(--down)";
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-hidden="true">
    <polygon points="0,${h} ${line} ${w},${h}" fill="${tone}" opacity=".10"/>
    <polyline points="${line}" fill="none" stroke="${tone}" stroke-width="1.6" vector-effect="non-scaling-stroke"/></svg>`;
}

function lineChart(canvas, datasets, { money = true, zero = false } = {}) {
  const grid = css("--line"), muted = css("--muted");
  const xs = datasets.flatMap(d => d.points.map(([t]) => new Date(t).getTime()));
  const span = xs.length ? Math.max(...xs) - Math.min(...xs) : 0;
  const ys = datasets.flatMap(d => d.points.map(([, v]) => v));
  const yspan = ys.length ? Math.max(...ys) - Math.min(...ys) : 0;
  const xfmt = v => span < 2 * 864e5
    ? new Date(v).toLocaleTimeString("pl-PL", { hour: "2-digit", minute: "2-digit" })
    : new Date(v).toLocaleDateString("pl-PL", { day: "2-digit", month: "2-digit" });
  const ch = new Chart(canvas, {
    type: "line",
    data: { datasets: datasets.map((d, i) => ({
      label: d.label, data: d.points.map(([t, v]) => ({ x: new Date(t).getTime(), y: v })),
      borderColor: d.color || SERIES[i % SERIES.length], backgroundColor: "transparent",
      borderWidth: d.dashed ? 1.5 : 2, borderDash: d.dashed ? [5, 4] : [], pointRadius: 0, tension: .15,
    })) },
    options: {
      responsive: true, maintainAspectRatio: false, animation: false, parsing: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { display: datasets.length > 1, labels: { color: muted, boxWidth: 12, boxHeight: 2 } },
        tooltip: { callbacks: {
          title: items => new Date(items[0].parsed.x).toLocaleString("pl-PL"),
          label: it => ` ${it.dataset.label}: ${money ? usd(it.parsed.y) : num(it.parsed.y)}`,
        } },
      },
      scales: {
        x: { type: "linear", min: xs.length ? Math.min(...xs) : undefined, max: xs.length ? Math.max(...xs) : undefined,
             grid: { color: grid }, ticks: { color: muted, maxTicksLimit: 7,
          callback: xfmt } },
        y: { grid: { color: grid }, ticks: { color: muted, callback: v => num(v, yspan < 20 ? 2 : 0) },
             beginAtZero: zero },
      },
    },
  });
  charts.push(ch);
  return ch;
}

function barChart(canvas, labels, values, { money = true, horizontal = false } = {}) {
  const grid = css("--line"), muted = css("--muted"), up = css("--up"), down = css("--down");
  const ch = new Chart(canvas, {
    type: "bar",
    data: { labels, datasets: [{ data: values, backgroundColor: values.map(v => v >= 0 ? up : down), borderRadius: 3, maxBarThickness: 36 }] },
    options: {
      indexAxis: horizontal ? "y" : "x", responsive: true, maintainAspectRatio: false, animation: false,
      plugins: { legend: { display: false }, tooltip: { callbacks: { label: it => " " + (money ? usd(it.raw, true) : num(it.raw)) } } },
      scales: {
        x: { grid: { color: grid }, ticks: horizontal ? { color: muted, callback: v => num(v, 0) } : { color: muted } },
        y: { grid: { color: grid }, ticks: horizontal ? { color: muted } : { color: muted, callback: v => num(v, 0) } },
      },
    },
  });
  charts.push(ch);
  return ch;
}

function groupBarChart(canvas, labels, series, { suffix = "", horizontal = false } = {}) {
  const grid = css("--line"), muted = css("--muted");
  const ch = new Chart(canvas, {
    type: "bar",
    data: { labels, datasets: series.map((d, i) => ({ label: d.label, data: d.values,
      backgroundColor: d.color || SERIES[i % SERIES.length], borderRadius: 3, maxBarThickness: 28 })) },
    options: {
      indexAxis: horizontal ? "y" : "x", responsive: true, maintainAspectRatio: false, animation: false,
      plugins: { legend: { display: series.length > 1, labels: { color: muted, boxWidth: 12 } },
        tooltip: { callbacks: { label: it => ` ${it.dataset.label || ""}: ${num(it.raw, 1)}${suffix}` } } },
      scales: {
        x: { grid: { color: grid }, ticks: horizontal ? { color: muted, callback: v => num(v, 0) + suffix } : { color: muted } },
        y: { grid: { color: grid }, ticks: horizontal ? { color: muted } : { color: muted, callback: v => num(v, 0) + suffix } },
      },
    },
  });
  charts.push(ch);
  return ch;
}

function emptyChart(box, text) {
  box.innerHTML = `<div class="empty">${esc(text)}</div>`;
}

// ------------------------------------------------------------------ logowanie
let SETUP = false, INSTALLED = false;
function showLogin() {
  cleanup();
  $("#app").classList.add("hidden");
  $("#login").classList.remove("hidden");
  loginStep(1);
  api("GET", "/api/session").then(s => {
    SETUP = !!s.setup;
    $("#login-pass2-f").classList.toggle("hidden", !SETUP);
    $("#login-pass").autocomplete = SETUP ? "new-password" : "current-password";
    $("#login-btn").textContent = SETUP ? "Utwórz i wejdź" : "Zaloguj";
    $("#login-hint").innerHTML = SETUP ? "<b>Witaj!</b> To pierwsze uruchomienie. Ustaw login i hasło do panelu (hasło min. 10 znaków). "
        + "Zapisz je w menedżerze haseł — nie da się go odzyskać mailem."
      : s.custom_login ? "Zaloguj się do panelu botów."
      : "Zaloguj się hasłem z pliku .env (login możesz zostawić pusty). Potem ustaw własny login w zakładce Logowanie.";
  }).catch(() => {});
  ($("#login-user").value ? $("#login-pass") : $("#login-user")).focus();
}
function loginStep(n) {
  $("#login-step1").classList.toggle("hidden", n !== 1);
  $("#login-step2").classList.toggle("hidden", n !== 2);
  $("#login-back").classList.toggle("hidden", n !== 2);
  $("#login-btn").textContent = n === 2 ? "Potwierdź" : "Zaloguj";
  $("#login-pass").required = n === 1;
  if (n === 2) { $("#login-code").value = ""; $("#login-code").focus(); }
}
$("#login-back").onclick = () => { $("#login-err").textContent = ""; loginStep(1); $("#login-pass").focus(); };
$("#login-form").addEventListener("submit", async e => {
  e.preventDefault();
  $("#login-err").textContent = "";
  const btn = $("#login-btn"); btn.disabled = true;
  if (SETUP) {
    try {
      if ($("#login-pass").value !== $("#login-pass2").value) throw new Error("Hasła się różnią.");
      await api("POST", "/api/setup", { username: $("#login-user").value, password: $("#login-pass").value });
      $("#login-pass").value = $("#login-pass2").value = ""; SETUP = false; $("#login-pass2-f").classList.add("hidden");
      sessionStorage.setItem("first_run", "1");
      boot();
    } catch (err) { $("#login-err").textContent = err.message; }
    finally { btn.disabled = false; }
    return;
  }
  try {
    const r = await api("POST", "/api/login", { username: $("#login-user").value, password: $("#login-pass").value,
      code: $("#login-step2").classList.contains("hidden") ? "" : $("#login-code").value });
    if (r.need_code) { loginStep(2); return; }
    $("#login-pass").value = ""; $("#login-code").value = "";
    boot();
  } catch (err) {
    $("#login-err").textContent = err.message;
    if (!$("#login-step2").classList.contains("hidden") && /login lub hasło/.test(err.message)) loginStep(1);
  } finally { btn.disabled = false; }
});
$("#logout").addEventListener("click", async () => { await api("POST", "/api/logout"); showLogin(); });

// ------------------------------------------------------------------ router
const routes = [
  [/^#?\/?$/, "dash", renderDash],
  [/^#\/bots$/, "bots", renderBots],
  [/^#\/bot\/(\d+)$/, "bots", renderBot],
  [/^#\/backtests$/, "backtests", renderBacktests],
  [/^#\/backtest\/(\d+)$/, "backtests", renderBacktest],
  [/^#\/trades$/, "trades", renderTrades],
  [/^#\/signals$/, "signals", renderSignals],
  [/^#\/reports(?:\?bot=(\d+))?$/, "reports", renderReports],
  [/^#\/report\/(\d+)$/, "reports", renderReport],
  [/^#\/system$/, "system", renderSystem],
  [/^#\/accounts$/, "accounts", renderAccounts],
  [/^#\/security$/, "security", renderSecurity],
  [/^#\/gateway$/, "gateway", renderGateway],
  [/^#\/chart(?:\/(.+))?$/, "chart", renderChart],
  [/^#\/ml$/, "ml", renderMl],
  [/^#\/radar$/, "radar", renderRadarOverview],
  [/^#\/radar\/crypto$/, "radar", renderRadar],
  [/^#\/radar\/(gpw|usa)$/, "radar", renderRadarStocks],
  [/^#\/lab$/, "lab", renderLab],
  [/^#\/calendar$/, "calendar", renderCalendar],
  [/^#\/risk$/, "risk", renderRisk],
  [/^#\/gallery$/, "gallery", renderGallery],
  [/^#\/ml\/(\d+)$/, "ml", renderMlModel],
];
async function route() {
  cleanup();
  const h = location.hash || "#/";
  for (const [re, nav, fn] of routes) {
    const m = h.match(re);
    if (m) {
      $$("#nav a").forEach(a => a.classList.toggle("active", a.dataset.route === nav));
      view.innerHTML = `<div class="empty">Ładowanie…</div>`;
      try {
        await fn(...m.slice(1));
        const note = DEMO && DEMO_NOTES[nav];
        if (note) {
          const head = view.querySelector(".page-head");
          (head || view).insertAdjacentHTML(head ? "afterend" : "afterbegin", `<div class="card demo-note">${note}</div>`);
        }
      } catch (err) {
        if (err.message !== "Zaloguj się") view.innerHTML = `<div class="card err">${esc(err.message)}</div>`;
      }
      return;
    }
  }
  location.hash = "#/";
}
window.addEventListener("hashchange", () => { closeModal(); $("#side").classList.remove("open"); $("#menu-btn").setAttribute("aria-expanded", "false"); route(); });
$("#menu-btn").addEventListener("click", () => {
  const open = $("#side").classList.toggle("open");
  $("#menu-btn").setAttribute("aria-expanded", String(open));
});

async function boot() {
  const s = await api("GET", "/api/session");
  if (!s.logged_in) return showLogin();
  DEMO = !!s.demo;
  INSTALLED = !!s.installed;
  document.body.classList.toggle("demo", DEMO);
  if (DEMO) demoBar();
  $("#login").classList.add("hidden");
  $("#app").classList.remove("hidden");
  SCHEMA = await api("GET", "/api/schema");
  route();
  mlBadge();
  if (!mlBadge._t) mlBadge._t = setInterval(mlBadge, 60000);
}
// ------------------------------------------------------------------ wersja demonstracyjna
async function demoBar() {
  const bar = $("#demo-bar");
  let d;
  try { d = await api("GET", "/api/demo"); } catch (e) { return; }
  bar.classList.remove("hidden");
  if (d.state === "running") {
    bar.innerHTML = `<b>Wersja demonstracyjna</b> <span>Przygotowuję przykładowe dane: ${esc(d.step || "")}
      (${Math.round((d.progress || 0) * 100)}%). To potrwa 1–3 minuty.</span>
      <span class="demo-prog"><i style="width:${Math.round((d.progress || 0) * 100)}%"></i></span>`;
    demoBar.wasRunning = true;
    clearTimeout(demoBar._t);
    demoBar._t = setTimeout(demoBar, 2500);
    return;
  }
  bar.innerHTML = `<b>Wersja demonstracyjna</b>
    <span>Konto, ceny i transakcje są symulowane — nic tu nie dzieje się naprawdę i nie ma żadnych prawdziwych pieniędzy.
    Boty działają dalej na danych symulowanych, możesz je zmieniać, dodawać i testować.</span>
    ${d.state === "error" ? `<span class="err">Nie udało się przygotować danych: ${esc(d.error || "")}</span>` : ""}
    <a href="#/system" class="btn ghost small">Przywróć dane demo</a>`;
  if (demoBar.wasRunning) { demoBar.wasRunning = false; route(); }
}
async function renderDemoSystem() {
  const d = await api("GET", "/api/demo");
  view.innerHTML = `
    <div class="page-head"><h1>System</h1></div>
    <div class="card"><h2>Wersja demonstracyjna</h2>
      <p>Ta kopia aplikacji działa tylko na tym komputerze, na koncie symulowanym ze 100 000 USD na start.
      Ceny są generowane (nazwy spółek i kryptowalut to tylko etykiety, a nie prawdziwe notowania), więc wyniki botów
      niczego nie przewidują.</p>
      <p>W pełnej wersji aplikacja łączy się z prawdziwymi brokerami (Alpaca, Interactive Brokers, Kraken i inne giełdy
      krypto), wysyła powiadomienia na telefon, ma logowanie z kodem 2FA i sama się aktualizuje — te zakładki są tu
      wyłączone.</p>
      <p class="muted small">Stan danych: ${d.state === "running" ? "przygotowuję…" : d.state === "error" ? "błąd" : "gotowe"}.</p>
      <button class="btn primary" id="demo-reset" ${d.state === "running" ? "disabled" : ""}>Przywróć dane demo</button>
      <div class="help">Zatrzymuje boty, usuwa wszystkie zmiany (boty, backtesty, transakcje) i tworzy przykłady od nowa.</div>
    </div>`;
  $("#demo-reset").onclick = async () => {
    if (!confirm("Usunąć wszystkie zmiany i przywrócić przykładowe dane?")) return;
    try { await api("POST", "/api/demo/reset"); toast("Przywracam dane demo…"); setTimeout(demoBar, 500); location.hash = "#/"; }
    catch (e) { toast(e.message, true); }
  };
}
async function mlBadge() {
  try {
    const s = await api("GET", "/api/ml/summary");
    const b = $("#ml-badge");
    b.textContent = s.pending || (s.training ? "…" : "");
    b.title = s.pending ? `${s.pending} model(e) czeka na akceptację` : s.training ? "model się uczy" : "";
    b.classList.toggle("hidden", !s.pending && !s.training);
    const lb = $("#lab-badge");
    lb.textContent = s.lab_pending || "";
    lb.title = s.lab_pending ? "zwycięzca laboratorium czeka na zatwierdzenie" : "";
    lb.classList.toggle("hidden", !s.lab_pending);
    try { const r = await api("GET", "/api/risk"); const rb = $("#risk-badge");
      const bad = r.halt ? "wyłącznik włączony" : r.accounts.some(a => a.tripped) ? "dzienny limit straty zadziałał" : "";
      rb.textContent = bad ? "!" : ""; rb.title = bad; rb.classList.toggle("hidden", !bad); } catch (e) { /* bez znaczenia */ }
    const g = await api("GET", "/api/gateway");
    const bad = gwProblem(g);
    const gb = $("#gw-badge"); gb.textContent = bad ? "!" : ""; gb.title = bad || ""; gb.classList.toggle("hidden", !bad);
  } catch (e) { /* bez znaczenia */ }
}
setInterval(() => { $("#clock").textContent = new Date().toLocaleTimeString("pl-PL", { hour: "2-digit", minute: "2-digit" }); }, 1000);

// ------------------------------------------------------------------ PULPIT
let dashRange = 7;
async function renderDash() {
  view.innerHTML = `
    <div class="page-head"><h1>Pulpit</h1>
      <div class="actions"><a class="btn" href="#/backtests">Nowy backtest</a>
      <button class="btn primary" id="new-bot">Nowy bot</button></div></div>
    <div id="first-steps"></div>
    <div class="grid kpis" id="kpis"></div>
    <div class="grid two">
      <div class="card"><div class="card-head"><h2>Kapitał kont</h2>${rangeSeg()}</div>
        <div class="chart-box" id="acc-chart"><canvas></canvas></div></div>
      <div class="card"><div class="card-head"><h2>Wynik botów (zrealizowany + otwarty)</h2></div>
        <div class="chart-box" id="bot-chart"><canvas></canvas></div></div>
    </div>
    <div class="card" style="margin-top:14px"><div class="card-head"><h2>Boty</h2>
      <span class="muted small" id="upd"></span></div><div id="bots-table"></div></div>`;
  $("#new-bot").onclick = () => botForm();
  $$(".seg button").forEach(b => b.onclick = () => { dashRange = +b.dataset.d; loadDash(true); });
  await loadDash(true);
  every(30000, () => loadDash(false));
  const ov = await api("GET", "/api/overview");
  if (!ov.bots.length && !DEMO) {
    const real = SCHEMA.accounts.some(a => a.type !== "sim");
    $("#first-steps").innerHTML = `<div class="card" style="margin-bottom:14px"><h2>Pierwsze kroki</h2>
      <ol class="small" style="margin:8px 0 0 18px;line-height:1.9">
        <li>${real ? "✓ Konto podłączone." : `<a href="#/accounts">Konta</a> → <b>+ Podłącz platformę</b>: darmowe konto <b>Alpaca Paper</b> (akcje USA na niby) albo <b>Kraken</b> w trybie na niby (krypto).
          Na start wystarczy też wbudowane konto <b>demo</b> (symulacja).`}</li>
        <li><a href="#/gallery">Galeria strategii</a> → wybierz strategię → <b>Backtest na moich danych</b>, żeby zobaczyć, jak by sobie radziła.</li>
        <li>Spodobała się? <b>Utwórz bota</b> na koncie papierowym i kliknij <b>Start</b>. Bot działa, dopóki działa ten komputer.</li>
        <li><a href="#/system">System</a> → <b>Powiadomienia</b>: transakcje i problemy na telefon (ntfy albo Telegram).</li>
        <li><a href="#/security">Logowanie</a> → włącz <b>kod z aplikacji (2FA)</b>.</li>
      </ol>
      <p class="muted small" style="margin-top:8px">Prawdziwe pieniądze dopiero po kilku tygodniach na papierze. Klucze API giełd zawsze <b>bez prawa wypłaty</b>.</p></div>`;
  }
}
function rangeSeg() {
  return `<div class="seg">${[[1, "1D"], [7, "7D"], [30, "30D"], [90, "90D"]].map(([d, l]) =>
    `<button data-d="${d}" class="${d === dashRange ? "on" : ""}">${l}</button>`).join("")}</div>`;
}
async function loadDash(withCharts) {
  const [ov, eq] = await Promise.all([api("GET", "/api/overview"), withCharts ? api("GET", `/api/equity?days=${dashRange}`) : null]);
  if (eq) loadDash.eq = eq;
  const eqs = (loadDash.eq || {}).accounts || {};
  $("#kpis").innerHTML = ov.accounts.map(a => {
    const prev = a.equity - a.day_change, pct = a.ok && prev ? a.day_change / prev * 100 : 0;
    const arrow = a.day_change > 0 ? "▲" : a.day_change < 0 ? "▼" : "■";
    return `<div class="card kpi acc-kpi ${a.ok ? "" : "off"}">
      <div class="label"><span class="acc-k">${esc(a.name)}</span><span class="tag">${esc(accTag(a))}</span></div>
      ${a.ok ? `<div class="value">${money(a.equity, a.currency)}</div>
        <div class="kpi-day ${tone(a.day_change)}"><span>${arrow} ${money(a.day_change, a.currency, true)}</span>
          <span class="mono">${pct >= 0 ? "+" : "−"}${Math.abs(pct).toFixed(2).replace(".", ",")}%</span><span class="muted">dziś</span></div>
        <div class="sub">${a.positions ? `${a.positions} poz. · w rynku ${money(a.exposure, a.currency)}` : "bez otwartych pozycji"}</div>
        ${spark(eqs[a.name])}`
      : `<div class="value down" style="font-size:16px;font-family:var(--sans)">brak połączenia</div><div class="sub">${esc(a.error)}</div>`}
    </div>`;
  }).join("") || `<div class="card empty">Brak kont — dodaj je w zakładce Konta.</div>`;
  $("#bots-table").innerHTML = botsTable(ov.bots);
  bindBotRows();
  $("#upd").textContent = "odświeżono " + new Date().toLocaleTimeString("pl-PL");
  if (withCharts) {
    $$(".seg button").forEach(b => b.classList.toggle("on", +b.dataset.d === dashRange));
    charts.forEach(c => c.destroy()); charts = [];
    const acc = Object.entries(eq.accounts).filter(([, p]) => p.length > 1);
    const accBox = $("#acc-chart");
    if (acc.length) { accBox.innerHTML = "<canvas></canvas>"; lineChart($("canvas", accBox), acc.map(([n, p]) => ({ label: n, points: p }))); }
    else emptyChart(accBox, "Wykres pojawi się po kilku migawkach kapitału (co 15 min).");
    const bots = Object.values(eq.bots).filter(b => b.points.length > 1);
    const botBox = $("#bot-chart");
    if (bots.length) { botBox.innerHTML = "<canvas></canvas>"; lineChart($("canvas", botBox), bots.map(b => ({ label: b.name, points: b.points }))); }
    else emptyChart(botBox, "Uruchom boty - tu porównasz ich wyniki.");
  }
}

function botsTable(bots) {
  if (!bots.length) return `<div class="empty">Nie masz jeszcze botów. Kliknij „Nowy bot” i wybierz szablon.</div>`;
  return `<div class="table-wrap"><table><thead><tr>
    <th>Bot</th><th>Status</th><th>Rynek</th><th>Konto</th><th>Strategia</th>
    <th class="num">Wynik</th><th class="num">Zrealiz.</th><th class="num">Otwarte</th>
    <th class="num">Transakcje</th><th class="num">Skuteczność</th><th class="num">Pozycje</th><th></th></tr></thead><tbody>
    ${bots.map(b => `<tr class="link" data-id="${b.id}">
      <td><b>${esc(b.name)}</b><div class="muted small">${esc(b.timeframe)} · ${b.symbols.length} symb. · budżet ${pct(b.allocation_pct * 100, 0, false)}</div></td>
      <td>${statusPill(b.status)}</td><td>${MARKET[b.market]}</td><td>${esc(b.account)}</td>
      <td class="small">${esc(b.strategy_name)}${levTag(b.lev)}</td>
      <td class="num ${tone(b.total_pnl)}">${usd(b.total_pnl, true)}</td>
      <td class="num ${tone(b.realized)}">${usd(b.realized, true)}</td>
      <td class="num ${tone(b.unrealized)}">${usd(b.unrealized, true)}</td>
      <td class="num">${b.closed_trades}</td><td class="num">${b.win_rate == null ? "—" : pct(b.win_rate, 0, false)}</td>
      <td class="num">${b.open_positions}</td>
      <td class="num">${b.status === "running"
        ? `<button class="btn small" data-act="stop" data-id="${b.id}">Stop</button>`
        : `<button class="btn small primary" data-act="start" data-id="${b.id}">Start</button>`}</td>
    </tr>`).join("")}</tbody></table></div>`;
}
function bindBotRows() {
  $$("tr.link[data-id]").forEach(tr => tr.onclick = e => {
    if (e.target.closest("button")) return;
    location.hash = `#/bot/${tr.dataset.id}`;
  });
  $$("button[data-act]").forEach(b => b.onclick = async e => {
    e.stopPropagation();
    b.disabled = true;
    try {
      await api("POST", `/api/bots/${b.dataset.id}/${b.dataset.act}`);
      toast(b.dataset.act === "start" ? "Bot uruchomiony" : "Bot zatrzymany");
    } catch (err) { toast(err.message, true); }
    route();
  });
}

// ------------------------------------------------------------------ LISTA BOTOW
async function renderBots() {
  const ov = await api("GET", "/api/overview");
  view.innerHTML = `
    <div class="page-head"><h1>Boty</h1><div class="actions">
      <button class="btn primary" id="new-bot">Nowy bot</button></div></div>
    <div class="card">${botsTable(ov.bots)}</div>
    <p class="note" style="margin-top:14px">Każdy bot handluje jednym rynkiem (akcje albo krypto) na wybranym koncie i ma
    własny budżet (% kapitału konta). Dwa działające boty na tym samym koncie nie mogą mieć wspólnych symboli, a suma
    ich budżetów nie może przekroczyć 100%.</p>`;
  $("#new-bot").onclick = () => botForm();
  bindBotRows();
  every(30000, async () => { const o = await api("GET", "/api/overview"); $(".card").innerHTML = botsTable(o.bots); bindBotRows(); });
}

// ------------------------------------------------------------------ FORMULARZ PARAMETROW
function paramSpecs(market, strategy) {
  const strat = SCHEMA.strategies.find(s => s.key === strategy);
  const common = SCHEMA.common.filter(s => (!s.market || s.market === market)
    && !(s.not_strategy || []).includes(strategy) && (!s.only_strategy || s.only_strategy.includes(strategy)));
  return { common, strat: strat ? strat.params : [], stratInfo: strat };
}
function defaultsFor(market, strategy) {
  const strat = SCHEMA.strategies.find(s => s.key === strategy);
  return { symbols: [], ...SCHEMA.market_defaults[market], ...(strat ? strat.defaults : {}) };
}
function fieldHtml(spec, value) {
  const id = "p_" + spec.key;
  const help = spec.help ? `<div class="help">${esc(spec.help)}</div>` : "";
  if (spec.type === "rules")
    return `<div class="field" style="grid-column:1/-1"><label>${esc(spec.label)}</label>
      <div class="rules" id="${id}" data-key="${spec.key}" data-type="rules" data-side="${spec.side}"
        data-value="${esc(JSON.stringify(value || []))}"></div>${help}</div>`;
  if (spec.type === "bool")
    return `<div class="field check"><input type="checkbox" id="${id}" data-key="${spec.key}" data-type="bool" ${value ? "checked" : ""}>
      <label for="${id}" style="font-size:13px;color:var(--text)">${esc(spec.label)}</label></div>`;
  let input;
  if (spec.type === "choice")
    input = `<select id="${id}" data-key="${spec.key}" data-type="choice">${spec.options.map(o =>
      `<option value="${esc(o)}" ${o === value ? "selected" : ""}>${esc(spec.labels?.[o] || o)}</option>`).join("")}</select>`;
  else if (spec.type === "list")
    input = `<input id="${id}" data-key="${spec.key}" data-type="list" value="${esc((value || []).join(", "))}">`;
  else if (spec.type === "pct")
    input = `<div class="suffix"><input type="number" step="any" id="${id}" data-key="${spec.key}" data-type="pct"
      value="${value == null ? "" : +(value * 100).toFixed(4)}"><span>%</span></div>`;
  else if (spec.type === "str")
    input = `<input id="${id}" data-key="${spec.key}" data-type="str" value="${esc(value ?? "")}">`;
  else
    input = `<input type="number" step="${spec.type === "int" ? 1 : "any"}" id="${id}" data-key="${spec.key}"
      data-type="${spec.type}" value="${value ?? ""}">`;
  const wide = spec.type === "list" || spec.key === "copy_managers" ? ` style="grid-column:1/-1"` : "";
  return `<div class="field"${wide}><label for="${id}">${esc(spec.label)}</label>${input}${help}</div>`;
}
function paramsHtml(market, strategy, values) {
  const { common, strat, stratInfo } = paramSpecs(market, strategy);
  const by = k => common.find(s => s.key === k);
  const basic = ["symbols", "timeframe", "allocation_pct"].map(by).filter(Boolean);
  const risk = ["risk_per_trade_pct", "stop_loss_pct", "take_profit_pct", "max_positions"].map(by).filter(Boolean);
  const extp = common.filter(s => s.group === "ext");
  const mlp = common.filter(s => s.group === "ml");
  const exitp = common.filter(s => s.group === "exits");
  const radp = common.filter(s => s.group === "radar");
  const labp = common.filter(s => s.group === "lab");
  const sigp = common.filter(s => s.group === "signals");
  const levp = common.filter(s => s.group === "lev");
  const adv = common.filter(s => !basic.includes(s) && !risk.includes(s) && !["ext", "ml", "exits", "radar", "lab", "signals", "lev"].includes(s.group));
  const f = specs => specs.map(s => fieldHtml(s, values[s.key])).join("");
  const special = ["grid", "dca"].includes(strategy), noMl = special || strategy === "tv_alerts";
  if (special) return `
    <fieldset><legend>Symbole i interwał</legend><div class="form-grid">${f(basic)}</div>
      <p class="muted small" style="margin:10px 0 0">Budżet bota dzielony jest po równo między symbole. Interwał służy tylko backtestowi — na żywo bot sprawdza cenę co cykl.</p></fieldset>
    <fieldset><legend>Strategia: ${esc(stratInfo?.name || "")}</legend>
      <p class="muted small" style="margin:0 0 10px">${esc(stratInfo?.description || "")}</p>
      <div class="form-grid">${f(strat)}</div>
      <div class="note warn" style="margin-top:10px">Bez stop-lossa na serwerze brokera — wyjścia pilnuje bot w każdym cyklu. Dźwignia i ML są tu wyłączone.</div></fieldset>
    <fieldset><legend>Okazje na telefon</legend><div class="form-grid">${f(sigp)}</div></fieldset>`;
  return `
    <fieldset><legend>Symbole i interwał</legend><div class="form-grid">${f(basic)}</div></fieldset>
    <fieldset><legend>Strategia: ${esc(stratInfo?.name || "")}</legend>
      <p class="muted small" style="margin:0 0 10px">${esc(stratInfo?.description || "")}</p>
      <div class="form-grid">${f(strat)}</div></fieldset>
    <fieldset><legend>Ryzyko</legend><div class="form-grid">${f(risk)}</div>
      <div class="note" id="risk-note" style="margin-top:12px"></div></fieldset>
    <fieldset class="lev-fs"><legend>Dźwignia i gra na spadki — dla bardziej ryzykownych</legend>
      <div class="note warn" style="margin:0 0 10px">Dźwignia zwiększa zyski i straty tak samo. Gra na spadek (krótka sprzedaż) przy marginesie
      może przynieść stratę większą niż wkład, gdy cena gwałtownie rośnie (stop-loss przy luce cenowej wykona się gorzej).
      ETF-y lewarowane tracą wartość przy długim trzymaniu w rynku bez trendu. Na koncie z prawdziwymi pieniędzmi bot wystartuje
      dopiero po Twojej zgodzie w zakładce <a href="#/risk">Ryzyko</a>; tam jest też wyłącznik i dzienny limit straty.</div>
      <div class="form-grid">${f(levp)}</div><div class="note" id="lev-note" style="margin-top:10px"></div></fieldset>
    <fieldset><legend>Wyjścia z pozycji — obok stop-lossa i take-profitu</legend>
      <p class="muted small" style="margin:0 0 10px">Pilnuje ich bot w każdym cyklu; twardy stop-loss zostaje na serwerze brokera jako zabezpieczenie.</p>
      <div class="form-grid">${f(exitp)}</div></fieldset>
    ${extp.length && strategy !== "copy_funds" ? `<fieldset><legend>Sygnały zewnętrzne — drugie zdanie dla bota</legend>
      <p class="muted small" style="margin:0 0 10px">Insiderzy (SEC Form 4) i fundusze (SEC 13F) jako weto albo potwierdzenie
      sygnału technicznego. Działa dla akcji pojedynczych spółek z USA — ETF-y nie mają insiderów. Podgląd danych: zakładka „Sygnały”.</p>
      <div class="form-grid">${f(extp)}</div></fieldset>` : ""}
    ${noMl ? "" : `<fieldset><legend>Uczenie maszynowe (ML)</legend>
      <p class="muted small" style="margin:0 0 10px">${strategy === "ml_model"
        ? "Ta strategia to sam model — ustaw, jak pewny musi być, żeby wejść, i na jakim okresie ma się uczyć."
        : "Model uczy się na historii, które wejścia z tym stop-lossem i take-profitem kończyły się zyskiem, i ocenia każdy sygnał strategii."}
      W backteście uczy się krocząco (tylko na przeszłości). Dla bota na żywo nowy model powstaje co tydzień i czeka na Twoją akceptację w zakładce „ML”.</p>
      <div class="form-grid">${f(mlp)}</div></fieldset>`}
    ${radp.length ? `<fieldset><legend>Radar altcoinów</legend>
      <p class="muted small" style="margin:0 0 10px">Codziennie ranking monet z giełdy konta (siła względem BTC, momentum, rosnący obrót, trend).
      Bot handluje N najlepszymi i zawsze pilnuje monet, które już ma. Stablecoiny są pomijane. Podgląd: zakładka „Radar”.</p>
      <div class="form-grid">${f(radp)}</div></fieldset>` : ""}
    ${noMl ? "" : `<fieldset><legend>Laboratorium wariantów</legend>
      <p class="muted small" style="margin:0 0 10px">Obok bota grają „na niby” jego warianty z jedną zmianą (a dla botów ML także model douczany co noc).
      Zwycięzca przejmuje bota automatycznie na koncie papierowym, a na koncie z prawdziwymi pieniędzmi — po Twoim zatwierdzeniu. Wyniki: zakładka „Laboratorium”.</p>
      <div class="form-grid">${f(labp)}</div></fieldset>`}
    <fieldset><legend>Okazje na telefon — do ręcznego kopiowania</legend>
      <p class="muted small" style="margin:0 0 10px">Gdy bot kupi albo sprzeda, dostaniesz powiadomienie z ceną, stop-lossem i kwotą dla Ciebie.
      Telefon podłączysz w zakładce System → Powiadomienia.</p>
      <div class="form-grid">${f(sigp)}</div></fieldset>
    <fieldset><legend>Zaawansowane</legend><div class="form-grid">${f(adv)}</div></fieldset>`;
}
function readParams(root) {
  const out = {};
  $$("[data-key]", root).forEach(el => {
    const t = el.dataset.type, k = el.dataset.key;
    if (t === "bool") out[k] = el.checked;
    else if (t === "list") out[k] = el.value.split(/[,\s]+/).map(s => s.trim().toUpperCase()).filter(Boolean);
    else if (t === "pct") out[k] = el.value === "" ? null : +el.value / 100;
    else if (t === "int") out[k] = el.value === "" ? null : parseInt(el.value, 10);
    else if (t === "float") out[k] = el.value === "" ? null : +el.value;
    else if (t === "rules") out[k] = JSON.parse(el.dataset.value || "[]");
    else out[k] = el.value;
  });
  Object.keys(out).forEach(k => out[k] == null && delete out[k]);
  return out;
}
const SIDE_LABEL = { BUY: "Kupno", SELL: "Sprzedaż", SHORT: "Sprzedaż krótka ↓", COVER: "Odkupienie" };
function levTag(l) {
  if (!l) return "";
  const dir = { long: "↑", short: "↓", both: "↑↓" }[l.direction] || "";
  return l.mode === "etf" ? ` <span class="f-tag warn" title="ETF-y lewarowane / odwrotne">ETF ${dir}</span>`
    : ` <span class="f-tag down" title="margin u brokera">${num(l.leverage, 1)}× ${dir}</span>`;
}
function levNote(root) {
  const p = readParams(root), el = $("#lev-note", root);
  if (!el) return;
  const mode = p.leverage_mode || "off";
  if (mode === "off") { el.className = "note"; el.textContent = "Wyłączone — bot tylko kupuje za własne pieniądze (jak dotąd)."; return; }
  const lev = mode === "margin" ? (p.leverage || 1) : 1, sl = (p.stop_loss_pct || 0);
  const dir = { long: "tylko na wzrost", short: "tylko na spadek", both: "na wzrost i na spadek" }[p.direction || "long"];
  el.className = "note warn";
  el.innerHTML = mode === "etf"
    ? `Bot gra ${dir}: sygnał liczy na spółce, a kupuje ETF 2× (wzrost) albo odwrotny (spadek). Stop-loss i take-profit
       przeliczane na ETF (przy 2× ruch spółki o ${num(sl * 100, 1)}% to ok. ${num(sl * 200, 1)}% ETF-u). Spółki bez ETF-u na liście bot pomija.`
    : `Bot gra ${dir} z dźwignią <b>${num(lev, 1)}×</b>: pozycje do ${num(lev * 100, 0)}% budżetu łącznie.
       Jedna strata na stopie to ok. <b>${num(sl * lev * 100, 1)}%</b> budżetu pozycji (plus poślizg).
       Koszty: odsetki od pożyczki (akcje ok. 7% rocznie od pożyczonej części, Kraken 0,02% co 4 h).`;
}
function riskNote(root) {
  const p = readParams(root);
  const note = $("#risk-note", root);
  if (!note || !p.stop_loss_pct) return;
  const ratio = p.risk_per_trade_pct / p.stop_loss_pct;
  const perPos = Math.min(ratio, 1 / (p.max_positions || 1)) * 100;
  note.className = "note" + (ratio > 1 ? " warn" : "");
  note.innerHTML = `Ryzyko / stop = <b class="mono">${ratio.toFixed(2)}</b>. Jedna pozycja to maks. ok.
    <b class="mono">${perPos.toFixed(0)}%</b> budżetu bota (limit: budżet / max pozycji).
    ${ratio > 1 ? "Ryzyko jest większe niż stop — pozycje będą przycinane, realne ryzyko wyjdzie niższe niż ustawione." :
      "Bez ukrytej dźwigni."} Stosunek TP/SL: <b class="mono">${(p.take_profit_pct / p.stop_loss_pct).toFixed(2)}</b>.`;
}

function strategyOptions(sel) {
  return SCHEMA.strategies.map(s => `<option value="${s.key}" ${s.key === sel ? "selected" : ""}>${esc(s.name)}</option>`).join("");
}

// wspolny edytor: bot albo backtest
function setupEditor(root, init) {
  let state = { market: init.market, strategy: init.strategy, params: { ...defaultsFor(init.market, init.strategy), ...init.params } };
  const box = $("#params-box", root);
  const draw = () => {
    box.innerHTML = paramsHtml(state.market, state.strategy, state.params);
    $$(".rules", box).forEach(drawRules);
    riskNote(root);
    levNote(root);
    $$("input,select", box).forEach(el => el.addEventListener("input", () => { riskNote(root); levNote(root); }));
    $$("select", box).forEach(el => el.addEventListener("change", () => levNote(root)));
  };
  $("#f-market", root).onchange = e => {
    state = { market: e.target.value, strategy: state.strategy, params: defaultsFor(e.target.value, state.strategy) };
    draw();
  };
  $("#f-strategy", root).onchange = e => {
    const keep = readParams(box);
    state = { market: state.market, strategy: e.target.value, params: { ...defaultsFor(state.market, e.target.value), ...keep } };
    const strat = SCHEMA.strategies.find(s => s.key === e.target.value);
    Object.assign(state.params, strat.defaults);
    draw();
  };
  const tpl = $("#f-template", root);
  if (tpl) tpl.onchange = e => {
    const t = SCHEMA.templates.find(x => x.id === e.target.value);
    if (!t) return;
    state = { market: t.market, strategy: t.strategy, params: { ...defaultsFor(t.market, t.strategy), ...t.params } };
    $("#f-market", root).value = t.market;
    $("#f-strategy", root).value = t.strategy;
    const name = $("#f-name", root);
    if (name && !name.dataset.touched) name.value = t.name;
    draw();
  };
  const name = $("#f-name", root);
  if (name) name.addEventListener("input", () => name.dataset.touched = "1");
  draw();
  return () => ({ market: state.market, strategy: $("#f-strategy", root).value, params: readParams(box) });
}

function openModal(html) {
  $("#modal-card").innerHTML = html;
  $("#modal").classList.remove("hidden");
  $("#modal").onclick = e => { if (e.target.id === "modal") closeModal(); };
}
function closeModal() { $("#modal").classList.add("hidden"); $("#modal-card").innerHTML = ""; }

function botForm(bot = null, preset = null) {
  const init = bot || preset || { name: "", account: SCHEMA.accounts[0]?.name, market: "stocks", strategy: "sma_cross", params: {} };
  openModal(`
    <div class="card-head"><h2>${bot ? "Edytuj bota" : "Nowy bot"}</h2>
      <button class="btn ghost small" id="m-close">Zamknij</button></div>
    <form class="form" id="bot-form">
      <div class="form-grid">
        ${bot ? "" : `<div class="field"><label>Szablon</label><select id="f-template"><option value="">— bez szablonu —</option>
          ${SCHEMA.templates.map(t => `<option value="${t.id}">${esc(t.name)}</option>`).join("")}</select></div>`}
        <div class="field"><label>Nazwa</label><input id="f-name" required value="${esc(init.name)}"></div>
        <div class="field"><label>Konto</label><select id="f-account">${SCHEMA.accounts.map(a =>
          `<option value="${esc(a.name)}" ${a.name === init.account ? "selected" : ""}>${esc(a.name)} (${esc(accDesc(a))})</option>`).join("")}</select></div>
        <div class="field"><label>Rynek</label><select id="f-market">
          <option value="stocks" ${init.market === "stocks" ? "selected" : ""}>Akcje / ETF</option>
          <option value="crypto" ${init.market === "crypto" ? "selected" : ""}>Krypto</option></select></div>
        <div class="field"><label>Strategia</label><select id="f-strategy">${strategyOptions(init.strategy)}</select></div>
      </div>
      <div id="params-box" class="stack"></div>
      ${bot && bot.status === "running" ? `<p class="note">Bot działa — po zapisaniu zostanie automatycznie zrestartowany z nowymi ustawieniami.</p>` : ""}
      <p class="err" id="f-err"></p>
      <div class="modal-foot"><button type="button" class="btn" id="m-cancel">Anuluj</button>
        <button class="btn primary" type="submit">${bot ? "Zapisz" : "Utwórz bota"}</button></div>
    </form>`);
  const root = $("#bot-form");
  const read = setupEditor(root, init);
  if (preset && !bot) $("#f-name").dataset.touched = "1";
  $("#params-box").insertAdjacentHTML("beforebegin", `<div class="note hidden" id="acc-note" style="margin-bottom:12px"></div>`);
  ACC_EQ = null;                                   // świeży kapitał kont przy każdym otwarciu formularza
  const adapt = () => adaptToAccount(root, !bot);
  $("#f-account").addEventListener("change", adapt);
  $("#f-market").addEventListener("change", () => setTimeout(adapt, 0));
  root.addEventListener("input", e => { if (e.target.dataset?.key === "allocation_pct" || e.target.dataset?.key === "max_positions") accountNote(root); });
  adapt();
  $("#m-close").onclick = $("#m-cancel").onclick = closeModal;
  root.onsubmit = async e => {
    e.preventDefault();
    const body = { name: $("#f-name").value.trim(), account: $("#f-account").value, ...read() };
    try {
      const r = bot ? await api("PUT", `/api/bots/${bot.id}`, body) : await api("POST", "/api/bots", body);
      closeModal();
      toast(bot ? (r.restarted ? "Zapisano i zrestartowano bota" : "Zapisano") : "Bot utworzony — uruchom go przyciskiem Start");
      location.hash = `#/bot/${bot ? bot.id : r.id}`;
      route();
    } catch (err) { $("#f-err").textContent = err.message; }
  };
}

// ------------------------------------------------------------------ SZCZEGOLY BOTA
async function renderBot(id) {
  const b = await api("GET", `/api/bots/${id}`);
  const p = b.params;
  const running = b.status === "running";
  view.innerHTML = `
    <div class="page-head"><div><a href="#/bots" class="muted small" style="text-decoration:none">← Boty</a>
      <h1 style="display:flex;gap:10px;align-items:center;margin-top:4px">${esc(b.name)} ${statusPill(b.status)}</h1>
      <div class="muted small" style="margin-top:4px">${MARKET[b.market]} · konto <b>${esc(b.account)}</b> ·
        ${esc(SCHEMA.strategies.find(s => s.key === b.strategy)?.name)} · ${esc(p.timeframe)} · budżet ${pct(p.allocation_pct * 100, 0, false)}
        ${["grid", "dca"].includes(b.strategy) ? "" : `· ryzyko ${pct(p.risk_per_trade_pct * 100, 1, false)} · SL ${pct(p.stop_loss_pct * 100, 1, false)} · TP ${pct(p.take_profit_pct * 100, 1, false)}`}
        ${p.leverage_mode && p.leverage_mode !== "off" ? levTag({ mode: p.leverage_mode, direction: p.direction, leverage: p.leverage }) : ""}</div></div>
      <div class="actions">
        ${running ? `<button class="btn" id="b-stop">Stop</button><button class="btn danger" id="b-stopclose">Stop i zamknij pozycje</button>`
                  : `<button class="btn primary" id="b-start">Start</button>`}
        <button class="btn" id="b-edit">Edytuj</button>
        <button class="btn" id="b-bt">Backtest tych ustawień</button>
        <button class="btn" id="b-rep">Raporty</button>
        ${running ? "" : `<button class="btn ghost danger" id="b-del">Usuń</button>`}
      </div></div>
    ${b.last_error ? `<p class="note warn">${b.status === "error" ? "Ostatni błąd" : "Problem (bot ponawia próby automatycznie)"}: ${esc(b.last_error)}</p>` : ""}
    ${b.warning ? `<p class="note warn">${esc(b.warning)}</p>` : ""}
    <div class="grid metrics" style="margin-bottom:14px">
      ${metric("Zrealizowany wynik", usd(b.stats.realized, true), tone(b.stats.realized))}
      ${metric("Otwarte pozycje (P/L)", usd(b.positions.reduce((a, x) => a + x.unrealized, 0), true), tone(b.positions.reduce((a, x) => a + x.unrealized, 0)))}
      ${metric("Zamknięte transakcje", b.stats.closed_trades)}
      ${metric("Skuteczność", b.stats.win_rate == null ? "—" : pct(b.stats.win_rate, 0, false))}
      ${metric("W rynku", usd(b.positions.reduce((a, x) => a + x.market_value, 0)))}
      ${metric("Symbole", p.symbols.length)}
    </div>
    ${b.ml?.uses_ml ? mlBotCard(b) : ""}
    ${b.special ? specialCard(b) : ""}
    ${b.strategy === "tv_alerts" ? `<div class="card" id="tv-box" style="margin-bottom:14px"><h2>Alerty z TradingView</h2><div class="empty">Ładowanie…</div></div>` : ""}
    <div class="grid two">
      <div class="card"><h2>Otwarte pozycje</h2>${positionsTable(b.positions, b.positions_error)}</div>
      <div class="card"><h2>Wynik w czasie</h2><div class="chart-box small" id="pnl-chart"><canvas></canvas></div></div>
    </div>
    <div class="grid two" style="margin-top:14px">
      <div class="card"><div class="card-head"><h2>Dziennik bota</h2><span class="muted small">na żywo</span></div>
        <div class="logbox" id="logs"></div></div>
      <div class="card"><h2>Transakcje</h2><div id="trades" class="table-wrap" style="max-height:340px;overflow:auto"></div></div>
    </div>
    ${b.strategy === "rules" ? `<p class="note" style="margin-top:12px">${esc(rulesSummary(p))}</p>` : ""}
    <p class="muted small" style="margin-top:12px">Symbole: ${esc(p.symbols.join(", "))}</p>`;

  const act = async (fn, msg) => { try { await fn(); toast(msg); route(); } catch (e) { toast(e.message, true); } };
  $("#b-start") && ($("#b-start").onclick = () => {
    const a = SCHEMA.accounts.find(x => x.name === b.account);
    if (a && isReal(a) && !confirmBox(`Ten bot będzie handlował PRAWDZIWYMI PIENIĘDZMI na koncie „${a.name}”` +
        `${a.exchange ? ` (${a.exchange})` : ""}. Uruchomić?`)) return;
    act(() => api("POST", `/api/bots/${id}/start`), "Bot uruchomiony");
  });
  $("#b-stop") && ($("#b-stop").onclick = () => act(() => api("POST", `/api/bots/${id}/stop`), "Bot zatrzymany — pozycje zostają ze stopami na serwerze"));
  $("#b-stopclose") && ($("#b-stopclose").onclick = () => {
    if (confirmBox("Zatrzymać bota i zamknąć wszystkie jego pozycje po cenie rynkowej?"))
      act(() => api("POST", `/api/bots/${id}/stop?close_positions=true`), "Bot zatrzymany, pozycje zamknięte");
  });
  $("#b-del") && ($("#b-del").onclick = () => {
    if (confirmBox("Usunąć bota razem z jego historią transakcji i logami?"))
      act(async () => { await api("DELETE", `/api/bots/${id}`); location.hash = "#/bots"; }, "Bot usunięty");
  });
  $("#b-edit").onclick = () => botForm(b);
  $("#b-bt").onclick = () => { sessionStorage.setItem("bt_preset", JSON.stringify({ market: b.market, strategy: b.strategy, params: b.params, name: b.name })); location.hash = "#/backtests"; };
  $("#b-rep").onclick = () => { location.hash = `#/reports?bot=${id}`; };
  $("#b-mltrain") && ($("#b-mltrain").onclick = () => act(() => api("POST", `/api/bots/${id}/ml/train`),
    "Uczenie modelu rozpoczęte — wynik pojawi się w zakładce ML"));

  if (b.strategy === "tv_alerts") await drawTv(id);
  const eq = await api("GET", "/api/equity?days=90");
  const pts = eq.bots[id]?.points || [];
  if (pts.length > 1) lineChart($("#pnl-chart canvas"), [{ label: "Wynik", points: pts }]);
  else emptyChart($("#pnl-chart"), "Wykres pojawi się po kilku migawkach (co 15 min działania bota).");

  let lastLog = 0;
  const box = $("#logs");
  const loadLogs = async () => {
    const rows = await api("GET", `/api/bots/${id}/logs?after=${lastLog}`);
    if (!rows.length) { if (!lastLog) box.innerHTML = `<span class="muted">Brak wpisów.</span>`; return; }
    if (!lastLog) box.innerHTML = "";
    const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
    box.insertAdjacentHTML("beforeend", rows.map(r =>
      `<div class="${esc(r.level)}"><span class="ts">${when(r.ts)}</span> ${esc(r.msg)}</div>`).join(""));
    lastLog = rows[rows.length - 1].id;
    if (atBottom || rows.length > 50) box.scrollTop = box.scrollHeight;
  };
  const loadTrades = async () => { $("#trades").innerHTML = tradesTable(await api("GET", `/api/bots/${id}/trades`)); };
  await Promise.all([loadLogs(), loadTrades()]);
  box.scrollTop = box.scrollHeight;
  every(5000, loadLogs);
  every(30000, loadTrades);
}
function confirmBox(msg) { return window.confirm(msg); }
function metric(label, value, cls = "") {
  return `<div class="metric"><div class="label">${esc(label)}</div><div class="value ${cls}">${value}</div></div>`;
}
function positionsTable(rows, error) {
  if (error) return `<p class="err">${esc(error)}</p>`;
  if (!rows.length) return `<div class="empty">Brak otwartych pozycji.</div>`;
  return `<div class="table-wrap"><table><thead><tr><th>Symbol</th><th class="num">Ilość</th><th class="num">Wejście</th>
    <th class="num">Cena</th><th class="num">P/L</th><th class="num">SL</th><th class="num">TP</th></tr></thead><tbody>
    ${rows.map(r => `<tr><td><b>${esc(r.symbol)}</b></td><td class="num">${num(r.qty, 6)}</td>
      <td class="num">${price(r.avg_entry)}</td><td class="num">${price(r.price)}</td>
      <td class="num ${tone(r.unrealized)}">${usd(r.unrealized, true)}<div class="small">${pct((r.price / r.avg_entry - 1) * 100)}</div></td>
      <td class="num">${price(r.sl)}</td><td class="num">${price(r.tp)}</td></tr>`).join("")}</tbody></table></div>`;
}
function tradesTable(rows, withBot = false) {
  if (!rows.length) return `<div class="empty">Brak transakcji.</div>`;
  return `<table><thead><tr><th>Czas</th>${withBot ? "<th>Bot</th>" : ""}<th>Symbol</th><th>Strona</th>
    <th class="num">Ilość</th><th class="num">Cena</th><th class="num">Wartość</th><th class="num">P/L</th><th>Powód</th></tr></thead><tbody>
    ${rows.map(t => `<tr><td class="small">${when(t.ts)}</td>${withBot ? `<td><a href="#/bot/${t.bot_id}">${esc(t.bot_name)}</a></td>` : ""}
      <td><a class="sym" href="#/chart/${encodeURIComponent(t.symbol)}" title="Pokaż wykres"><b>${esc(t.symbol)}</b></a></td><td class="${t.side === "BUY" ? "" : t.side === "SHORT" ? "down" : "muted"}">${SIDE_LABEL[t.side] || t.side}</td>
      <td class="num">${num(t.qty, 6)}</td><td class="num">${price(t.price)}</td><td class="num">${usd(t.value)}</td>
      <td class="num ${tone(t.pnl)}">${t.pnl == null ? "" : usd(t.pnl, true) + `<div class="small">${pct(t.pnl_pct)}</div>`}</td>
      <td class="small muted">${esc(t.reason || "")}</td></tr>`).join("")}</tbody></table>`;
}

// ------------------------------------------------------------------ BACKTESTY
function iso(d) { return d.toISOString().slice(0, 10); }
async function renderBacktests() {
  const preset = JSON.parse(sessionStorage.getItem("bt_preset") || "null");
  sessionStorage.removeItem("bt_preset");
  const init = preset || { market: "stocks", strategy: "mean_reversion", params: SCHEMA.templates.find(t => t.id === "hybrid_etf").params };
  const today = new Date(); const yearAgo = new Date(Date.now() - 365 * 864e5);
  const bots = (await api("GET", "/api/overview")).bots;
  view.innerHTML = `
    <div class="page-head"><h1>Backtesty</h1></div>
    <div class="card"><form class="form" id="bt-form">
      <div class="form-grid">
        <div class="field"><label>Wczytaj ustawienia</label><select id="f-template"><option value="">— wybierz —</option>
          <optgroup label="Szablony">${SCHEMA.templates.map(t => `<option value="${t.id}">${esc(t.name)}</option>`).join("")}</optgroup>
          ${bots.length ? `<optgroup label="Twoje boty">${bots.map(b => `<option value="bot:${b.id}">${esc(b.name)}</option>`).join("")}</optgroup>` : ""}
        </select></div>
        <div class="field"><label>Rynek</label><select id="f-market">
          <option value="stocks" ${init.market === "stocks" ? "selected" : ""}>Akcje / ETF</option>
          <option value="crypto" ${init.market === "crypto" ? "selected" : ""}>Krypto</option></select></div>
        <div class="field"><label>Strategia</label><select id="f-strategy">${strategyOptions(init.strategy)}</select></div>
        <div class="field"><label>Od</label><input type="date" id="f-start" value="${esc(preset?.start || iso(yearAgo))}"></div>
        <div class="field"><label>Do</label><input type="date" id="f-end" value="${esc(preset?.end && preset.end < iso(today) ? preset.end : iso(today))}" max="${iso(today)}"></div>
        <div class="field"><label>Kapitał startowy</label><div class="suffix"><input type="number" id="f-cap" value="100000" min="1000"><span>$</span></div></div>
        <div class="field"><label>Koszt transakcji (na stronę)</label><div class="suffix"><input type="number" step="any" id="f-cost" placeholder="auto"><span>%</span></div>
          <div class="help">Puste = 0,05% akcje, 0,25% krypto (prowizja + poślizg)</div></div>
        <div class="field"><label>Dane</label><select id="f-src">
          <option value="auto">${SCHEMA.data_source === "alpaca" ? "Prawdziwe (Alpaca)" : "Symulowane (brak konta Alpaca)"}</option>
          <option value="kraken">Kraken (krypto)</option>
          <option value="ibkr">IBKR (GPW, waluty, USA)</option>
          ${[...new Set(SCHEMA.accounts.filter(a => a.type === "gielda").map(a => a.exchange))].map(x =>
            `<option value="ccxt:${esc(x)}">${esc(x)} (krypto)</option>`).join("")}
          <option value="sim">Symulowane</option></select>
          <div class="help">Kraken daje 720 ostatnich świec na interwał (1 h ≈ 30 dni, 4 h ≈ 120 dni, 1 dzień ≈ 2 lata) plus to, co aplikacja już zebrała. Koszt domyślny 0,40%.</div></div>
      </div>
      <div id="params-box" class="stack"></div>
      <p class="err" id="f-err"></p>
      <div style="display:flex;gap:10px;justify-content:flex-end"><button class="btn primary" type="submit">Uruchom backtest</button></div>
    </form></div>
    <div class="card" style="margin-top:14px"><h2>Historia testów</h2><div id="bt-list"></div></div>`;
  const root = $("#bt-form");
  const read = setupEditor(root, init);
  if (preset?.data_source && $(`#f-src option[value="${preset.data_source}"]`)) $("#f-src").value = preset.data_source;
  else if (preset?.data_source === "alpaca") $("#f-src").value = "auto";
  $("#f-template").addEventListener("change", async e => {
    if (!e.target.value.startsWith("bot:")) return;
    const b = await api("GET", `/api/bots/${e.target.value.slice(4)}`);
    sessionStorage.setItem("bt_preset", JSON.stringify({ market: b.market, strategy: b.strategy, params: b.params }));
    renderBacktests();
  });
  root.onsubmit = async e => {
    e.preventDefault();
    $("#f-err").textContent = "";
    const cost = $("#f-cost").value;
    const body = { ...read(), start: $("#f-start").value, end: $("#f-end").value,
      initial_capital: +$("#f-cap").value, cost_pct: cost === "" ? null : +cost / 100, data_source: $("#f-src").value,
      name: preset?.name || "" };
    try { const r = await api("POST", "/api/backtests", body); toast("Backtest uruchomiony"); location.hash = `#/backtest/${r.id}`; }
    catch (err) { $("#f-err").textContent = err.message; }
  };
  const loadList = async () => {
    const rows = await api("GET", "/api/backtests");
    $("#bt-list").innerHTML = !rows.length ? `<div class="empty">Brak testów.</div>` : `<div class="table-wrap"><table><thead><tr>
      <th>#</th><th>Data</th><th>Strategia</th><th>Rynek</th><th>Interwał</th><th>Okres</th><th>Status</th>
      <th class="num">Zwrot</th><th class="num">Kup i trzymaj</th><th class="num">Max obsunięcie</th><th class="num">Transakcje</th><th class="num">Skuteczność</th><th></th></tr></thead><tbody>
      ${rows.map(r => { const m = r.metrics || {}; const c = r.config;
        return `<tr class="link" data-bt="${r.id}"><td>${r.id}</td><td class="small">${when(r.created_at)}</td>
        <td>${esc(SCHEMA.strategies.find(s => s.key === c.strategy)?.name)}<div class="muted small">${c.params.symbols.length} symb.${c.data_source === "sim" ? " · symulacja" : ""}</div></td>
        <td>${MARKET[c.market]}</td><td>${esc(c.params.timeframe)}</td><td class="small">${esc(c.start)} → ${esc(c.end)}</td>
        <td>${r.status === "running" || r.status === "queued" ? `<div class="progress"><div style="width:${(r.progress || 0) * 100}%"></div></div>`
             : r.status === "error" ? `<span class="down small" title="${esc(r.error)}">błąd</span>` : `<span class="muted small">gotowy${r.opt_status === "done" ? " · z propozycjami" : r.opt_status === "running" ? " · analiza…" : ""}</span>`}</td>
        <td class="num ${tone(m.total_return_pct)}">${pct(m.total_return_pct)}</td>
        <td class="num ${tone(m.benchmark_return_pct)}">${pct(m.benchmark_return_pct)}</td>
        <td class="num down">${m.max_drawdown_pct == null ? "—" : pct(m.max_drawdown_pct)}</td>
        <td class="num">${m.trades ?? "—"}</td><td class="num">${m.win_rate_pct == null ? "—" : pct(m.win_rate_pct, 0, false)}</td>
        <td><button class="btn ghost small" data-del="${r.id}">Usuń</button></td></tr>`; }).join("")}</tbody></table></div>`;
    $$("tr[data-bt]").forEach(tr => tr.onclick = e => { if (!e.target.closest("button")) location.hash = `#/backtest/${tr.dataset.bt}`; });
    $$("button[data-del]").forEach(b => b.onclick = async () => { await api("DELETE", `/api/backtests/${b.dataset.del}`); loadList(); });
    return rows.some(r => r.status === "running" || r.status === "queued");
  };
  await loadList();
  every(4000, loadList);
}

async function renderBacktest(id) {
  const load = () => api("GET", `/api/backtests/${id}`);
  let bt = await load();
  if (bt.status === "running" || bt.status === "queued") {
    view.innerHTML = `<div class="card"><h2>Backtest #${id} w toku…</h2><div class="progress" style="width:100%"><div id="bt-prog" style="width:${bt.progress * 100}%"></div></div>
      <p class="muted small">Pierwsze uruchomienie pobiera dane z Alpaca — kolejne testy na tych samych danych są szybsze.</p></div>`;
    every(1500, async () => {
      bt = await load();
      if (bt.status === "done" || bt.status === "error") route(); else $("#bt-prog").style.width = bt.progress * 100 + "%";
    });
    return;
  }
  if (bt.status === "error") {
    view.innerHTML = `<div class="card"><h2>Backtest #${id} nieudany</h2><p class="err">${esc(bt.error)}</p><a class="btn" href="#/backtests">Wróć</a></div>`;
    return;
  }
  const c = bt.config, r = bt.result, m = r.metrics;
  const sname = SCHEMA.strategies.find(s => s.key === c.strategy)?.name;
  view.innerHTML = `
    <div class="page-head"><div><a href="#/backtests" class="muted small no-print" style="text-decoration:none">← Backtesty</a>
      <h1 style="margin-top:4px">Backtest #${id}: ${esc(sname)}</h1>
      <div class="muted small" style="margin-top:4px">${MARKET[c.market]} · ${esc(c.params.timeframe)} · ${esc(c.start)} → ${esc(c.end)} ·
        kapitał ${usd(c.initial_capital)} · ${esc(m.fees_note)} · dane: ${({ sim: "symulowane", kraken: "Kraken", ibkr: "IBKR" })[c.data_source] || "Alpaca"} ·
        ${c.params.symbols.length} symb.: ${esc(c.params.symbols.join(", "))}</div></div>
      <div class="actions no-print"><button class="btn" id="bt-print">Drukuj / PDF</button>
        <button class="btn" id="bt-again">Zmień i uruchom ponownie</button>
        <button class="btn primary" id="bt-bot">Utwórz bota z tych ustawień</button></div></div>
    ${c.data_source === "sim" ? `<p class="note warn">To test na danych symulowanych — sprawdza działanie mechaniki, nie opłacalność strategii.</p>` : ""}
    <div class="tabs no-print" id="bt-tabs">
      <button data-tab="res">Wyniki</button><button data-tab="rep">Raport w czasie</button><button data-tab="opt">Propozycje poprawek</button>
    </div>
    <div id="bt-tab"></div>`;
  $("#bt-bot").onclick = () => botForm(null, { name: `${sname} (z testu #${id})`,
    account: SCHEMA.accounts[0]?.name, market: c.market, strategy: c.strategy, params: c.params });
  $("#bt-again").onclick = () => { sessionStorage.setItem("bt_preset", JSON.stringify({ market: c.market, strategy: c.strategy, params: c.params })); location.hash = "#/backtests"; };
  $("#bt-print").onclick = () => window.print();
  const show = tab => {
    sessionStorage.setItem("bt_tab", tab);
    $$("#bt-tabs button").forEach(b => b.classList.toggle("on", b.dataset.tab === tab));
    charts.forEach(ch => ch.destroy()); charts = []; timers.forEach(clearInterval); timers = [];
    ({ res: tabResults, rep: tabReport, opt: tabOptimize })[tab](bt, id, show);
  };
  $$("#bt-tabs button").forEach(b => b.onclick = () => show(b.dataset.tab));
  show(r.report ? (sessionStorage.getItem("bt_tab") || "rep") : "res");
}

function tabResults(bt) {
  const r = bt.result, m = r.metrics;
  const beat = m.total_return_pct - m.benchmark_return_pct;
  $("#bt-tab").innerHTML = `
    <div class="grid metrics" style="margin-bottom:14px">
      ${metric("Zwrot", pct(m.total_return_pct), tone(m.total_return_pct))}
      ${metric("Kup i trzymaj (te symbole)", pct(m.benchmark_return_pct), tone(m.benchmark_return_pct))}
      ${metric("Różnica vs kup i trzymaj", pct(beat), tone(beat))}
      ${metric("Max obsunięcie", pct(m.max_drawdown_pct), "down")}
      ${metric("Sharpe", m.sharpe == null ? "—" : num(m.sharpe))}
      ${metric("CAGR", m.cagr_pct == null ? "—" : pct(m.cagr_pct))}
      ${metric("Transakcje", m.trades)}
      ${metric("Skuteczność", m.win_rate_pct == null ? "—" : pct(m.win_rate_pct, 0, false))}
      ${metric("Śr. zysk / strata", `${pct(m.avg_win_pct, 1)} / ${pct(m.avg_loss_pct, 1)}`)}
      ${metric("Profit factor", m.profit_factor == null ? "—" : num(m.profit_factor))}
      ${metric("Czas w rynku", pct(m.exposure_pct, 0, false))}
      ${metric("Śr. czas pozycji", m.avg_hours == null ? "—" : m.avg_hours < 48 ? num(m.avg_hours, 1) + " h" : num(m.avg_hours / 24, 1) + " dni")}
    </div>
    <div class="card"><div class="card-head"><h2>Kapitał: strategia vs kup i trzymaj</h2></div>
      <div class="chart-box"><canvas id="bt-chart"></canvas></div></div>
    ${r.ml ? mlQualityCard(r.ml, "Model ML w tym teście (uczony krocząco)") : ""}
    <div class="grid two" style="margin-top:14px">
      <div class="card"><h2>Wyniki per symbol</h2><div class="table-wrap" style="max-height:380px;overflow:auto">${perSymbolTable(r.per_symbol)}</div></div>
      <div class="card"><h2>Transakcje (${r.trades.length})</h2><div class="table-wrap" style="max-height:380px;overflow:auto">${btTrades(r.trades)}</div></div>
    </div>`;
  lineChart($("#bt-chart"), [
    { label: "Strategia", points: r.equity },
    { label: "Kup i trzymaj", points: r.benchmark, color: css("--muted"), dashed: true },
  ]);
  if (r.ml) mlQualityCharts(r.ml);
}

// ------------------------------------------------------------------ RAPORT W CZASIE
const SEV = { bad: ["Problem", "down"], warn: ["Uwaga", "warn"], info: ["Obserwacja", "info"], good: ["Mocna strona", "up"] };
function heat(v, scale = 5) {
  if (v == null) return "";
  const a = Math.min(Math.abs(v) / scale, 1) * 0.55 + 0.08;
  return `background:color-mix(in srgb, var(${v >= 0 ? "--up" : "--down"}) ${Math.round(a * 100)}%, transparent)`;
}
function statTable(rows, labelHead) {
  if (!rows.length) return `<div class="empty">Brak danych.</div>`;
  return `<table><thead><tr><th>${labelHead}</th><th class="num">Transakcje</th><th class="num">Skuteczność</th>
    <th class="num">Śr. wynik</th><th class="num">Wynik</th></tr></thead><tbody>
    ${rows.map(x => `<tr><td>${esc(x.label || x.reason)}</td><td class="num">${x.trades}</td>
      <td class="num">${x.win_rate == null ? "—" : pct(x.win_rate, 0, false)}</td>
      <td class="num ${tone(x.avg_pct)}">${pct(x.avg_pct)}</td><td class="num ${tone(x.pnl)}">${usd(x.pnl, true)}</td></tr>`).join("")}
    </tbody></table>`;
}
function tabReport(bt, id, show) {
  const rep = bt.result.report;
  if (!rep) { $("#bt-tab").innerHTML = `<div class="card empty">Ten test powstał w starszej wersji — uruchom go ponownie, żeby dostać raport.</div>`; return; }
  const c = rep.consistency, h = rep.halves, cost = rep.costs;
  const years = {};
  rep.months.forEach(mo => { const [y, mm] = mo.key.split("-"); (years[y] ||= {})[+mm] = mo; });
  const yearRet = y => { const vals = Object.values(years[y]).map(x => x.ret).filter(v => v != null); return (vals.reduce((a, v) => a * (1 + v / 100), 1) - 1) * 100; };
  const scale = Math.max(2, ...rep.months.map(x => Math.abs(x.ret || 0)));
  $("#bt-tab").innerHTML = `
    <div class="card"><div class="card-head"><h2>Wnioski i co poprawić</h2>
      <span class="muted small">hipotezy z danych — sprawdzane w czasie w zakładce „Propozycje poprawek”</span></div>
      <div class="findings">${rep.findings.map(f => `
        <div class="finding ${f.severity}"><div class="f-head"><span class="f-tag ${SEV[f.severity][1]}">${SEV[f.severity][0]}</span>
          <b>${esc(f.title)}</b></div>
          <div class="f-body">${esc(f.detail)}</div>
          ${f.suggestion ? `<div class="f-sug">→ ${esc(f.suggestion)}${f.variant ? ` <a href="javascript:void 0" class="f-link no-print" data-go="opt">Sprawdź w propozycjach</a>` : ""}</div>` : ""}
        </div>`).join("")}</div></div>

    <div class="grid metrics" style="margin:14px 0">
      ${metric(`Zyskowne okresy (${c.windows})`, `${c.windows_positive} z ${c.windows}`, c.windows_positive / c.windows >= .6 ? "up" : "down")}
      ${metric("Okresy lepsze niż kup i trzymaj", `${c.windows_beat_bench} z ${c.windows}`)}
      ${metric("Zyskowne miesiące (z transakcjami)", c.active_months ? `${c.positive_months} z ${c.active_months}` : "—")}
      ${metric("Najlepszy miesiąc", c.best ? `${pct(c.best.ret, 1)} <span class="small muted">${esc(c.best.label)}</span>` : "—", "up")}
      ${metric("Najgorszy miesiąc", c.worst ? `${pct(c.worst.ret, 1)} <span class="small muted">${esc(c.worst.label)}</span>` : "—", "down")}
      ${metric("Najdłuższa seria strat", `${c.longest_losing_streak} mies.`)}
      ${metric(`1. połowa (do ${h.mid})`, pct(h.first, 1), tone(h.first))}
      ${metric("2. połowa", pct(h.second, 1), tone(h.second))}
    </div>

    <div class="card"><div class="card-head"><h2>Wynik w kolejnych okresach</h2>
      <span class="muted small">zwrot strategii vs kup i trzymaj w każdym okresie osobno</span></div>
      <div class="table-wrap"><table><thead><tr><th>Okres</th><th class="num">Strategia</th><th class="num">Kup i trzymaj</th>
        <th class="num">Różnica</th><th class="num">Transakcje</th><th class="num">Skuteczność</th><th class="num">Wynik $</th></tr></thead><tbody>
        ${rep.windows.map(w => `<tr><td>${esc(w.start)} → ${esc(w.end)}</td>
          <td class="num ${tone(w.ret)}">${pct(w.ret)}</td><td class="num ${tone(w.bench)}">${pct(w.bench)}</td>
          <td class="num ${tone((w.ret ?? 0) - (w.bench ?? 0))}">${pct((w.ret ?? 0) - (w.bench ?? 0))}</td>
          <td class="num">${w.trades}</td><td class="num">${w.win_rate == null ? "—" : pct(w.win_rate, 0, false)}</td>
          <td class="num ${tone(w.pnl)}">${usd(w.pnl, true)}</td></tr>`).join("")}</tbody></table></div></div>

    <div class="card" style="margin-top:14px"><div class="card-head"><h2>Zwroty miesięczne</h2>
      <span class="muted small">kolor = wynik strategii, w dymku: kup i trzymaj i liczba transakcji</span></div>
      <div class="table-wrap"><table class="heat"><thead><tr><th>Rok</th>${["sty","lut","mar","kwi","maj","cze","lip","sie","wrz","paź","lis","gru"].map(x => `<th class="num">${x}</th>`).join("")}<th class="num">Rok</th></tr></thead><tbody>
        ${Object.keys(years).sort().map(y => `<tr><td><b>${y}</b></td>${Array.from({ length: 12 }, (_, i) => {
          const mo = years[y][i + 1];
          return mo ? `<td class="num" style="${heat(mo.ret, scale)}" title="kup i trzymaj ${pct(mo.bench)} · ${mo.trades} transakcji">${pct(mo.ret, 1)}</td>` : `<td></td>`;
        }).join("")}<td class="num ${tone(yearRet(y))}"><b>${pct(yearRet(y), 1)}</b></td></tr>`).join("")}
      </tbody></table></div></div>

    <div class="grid two" style="margin-top:14px">
      <div class="card"><div class="card-head"><h2>Obsunięcie kapitału</h2><span class="muted small">maks. ${pct(rep.drawdown.max_pct, 1)} (${esc(rep.drawdown.max_date)}) · najdłużej pod wodą ${rep.drawdown.longest_days} dni</span></div>
        <div class="chart-box small"><canvas id="dd-chart"></canvas></div></div>
      <div class="card"><h2>Koszty</h2>
        <div class="grid metrics">
          ${metric("Wynik przed kosztami", usd(cost.gross, true), tone(cost.gross))}
          ${metric("Koszty transakcji", usd(-cost.fees, true), "down")}
          ${metric("Wynik netto", usd(cost.net, true), tone(cost.net))}
          ${metric("Koszty / zysk brutto", cost.fees_share_of_gross_profit == null ? "—" : pct(cost.fees_share_of_gross_profit, 0, false),
                   (cost.fees_share_of_gross_profit || 0) > 30 ? "down" : "")}
        </div></div>
    </div>

    <div class="grid two" style="margin-top:14px">
      <div class="card"><h2>Jak zamykane są pozycje</h2><div class="table-wrap">${statTable(rep.exits, "Wyjście")}</div></div>
      <div class="card"><h2>Czas trzymania pozycji</h2><div class="table-wrap">${statTable(rep.holding, "Czas")}</div></div>
      <div class="card"><h2>Dzień tygodnia wejścia</h2><div class="table-wrap">${statTable(rep.weekdays, "Dzień")}</div></div>
      ${rep.hours.length ? `<div class="card"><h2>Godzina wejścia (czas PL)</h2><div class="table-wrap" style="max-height:320px;overflow:auto">${statTable(rep.hours, "Godzina")}</div></div>` : ""}
      ${rep.external?.active ? `<div class="card"><h2>Sygnały zewnętrzne</h2>
        <p class="small muted" style="margin:0 0 8px">Insiderzy: ${esc(rep.external.insider_mode)} · fundusze: ${esc(rep.external.funds_mode)} ·
          zablokowane ${rep.external.blocked} z ${rep.external.raw} sygnałów wejścia</p>
        <div class="table-wrap"><table><thead><tr><th>Symbol</th><th class="num">Sygnały</th><th class="num">Zablokowane</th>
          <th class="num">Transakcje insiderów</th></tr></thead><tbody>
          ${Object.entries(rep.external.per_symbol || {}).map(([sym, v]) => `<tr><td><b>${esc(sym)}</b></td><td class="num">${v.raw}</td>
            <td class="num">${v.blocked}</td><td class="num">${v.insider_events ?? "—"}</td></tr>`).join("")}
        </tbody></table></div></div>` : ""}
      <div class="card"><h2>Symbole w obu połowach testu</h2><div class="table-wrap" style="max-height:320px;overflow:auto">
        <table><thead><tr><th>Symbol</th><th class="num">Transakcje</th><th class="num">1. połowa</th><th class="num">2. połowa</th><th></th></tr></thead><tbody>
        ${rep.symbols.map(s => `<tr><td><b>${esc(s.symbol)}</b></td><td class="num">${s.trades}</td>
          <td class="num ${tone(s.pnl_first)}">${usd(s.pnl_first, true)}</td><td class="num ${tone(s.pnl_second)}">${usd(s.pnl_second, true)}</td>
          <td>${s.consistent_loser ? `<span class="f-tag down">stratny w obu</span>` : ""}</td></tr>`).join("")}
        </tbody></table></div></div>
    </div>`;
  $$("[data-go]").forEach(a => a.onclick = () => show(a.dataset.go));
  lineChart($("#dd-chart"), [{ label: "Obsunięcie %", points: rep.drawdown.series, color: css("--down") }], { money: false });
}

// ------------------------------------------------------------------ PROPOZYCJE POPRAWEK
const OPT_STATUS = { confirmed: ["potwierdzona", "up"], uncertain: ["niepewna", "warn"], rejected: ["odrzucona", "muted"], error: ["błąd", "down"] };
async function tabOptimize(bt, id, show) {
  const box = $("#bt-tab");
  if (bt.opt_status === "running") {
    box.innerHTML = `<div class="card"><h2>Sprawdzam warianty w kolejnych okresach…</h2>
      <div class="progress" style="width:100%"><div id="opt-prog" style="width:${(bt.opt_progress || 0) * 100}%"></div></div>
      <p class="muted small">Każdy wariant to pełny backtest. Zwykle trwa to od kilku sekund do kilku minut.</p></div>`;
    every(1500, async () => {
      const nb = await api("GET", `/api/backtests/${id}`);
      if (nb.opt_status !== "running") { Object.assign(bt, nb); show("opt"); }
      else $("#opt-prog").style.width = (nb.opt_progress || 0) * 100 + "%";
    });
    return;
  }
  const intro = `<p class="muted" style="margin:0 0 12px">Każda propozycja zmienia <b>jedną</b> rzecz i jest sprawdzana w czasie:
    wybieramy ją na pierwszych 70% okresu (<b>próba</b>), a potem sprawdzamy na ostatnich 30%, których nie użyto do wyboru
    (<b>poza próbą</b>), oraz osobno w każdym z kolejnych okresów. <b>Potwierdzona</b> = lepsza w próbie, lepsza poza próbą,
    lepsza w co najmniej 60% okresów, bez większego obsunięcia i z min. 10 transakcjami.</p>`;
  if (bt.opt_status !== "done") {
    box.innerHTML = `<div class="card"><h2>Propozycje poprawek</h2>${intro}
      ${bt.opt_status === "error" ? `<p class="err">Poprzednia analiza nie powiodła się: ${esc(bt.opt_error)}</p>` : ""}
      <button class="btn primary" id="opt-run">Uruchom analizę poprawek</button></div>`;
    $("#opt-run").onclick = async () => {
      try { await api("POST", `/api/backtests/${id}/optimize`); bt.opt_status = "running"; bt.opt_progress = 0; show("opt"); }
      catch (e) { toast(e.message, true); }
    };
    return;
  }
  const o = bt.opt_result, base = o.base, c = bt.config;
  const all = [...(o.combined ? [o.combined] : []), ...o.variants];
  const squares = flags => `<span class="wsq">${(flags || []).map((f, i) => `<i class="${f ? "on" : ""}" title="okres ${i + 1}"></i>`).join("")}</span>`;
  const best = o.best;
  const applyParams = v => ({ ...o.base_params, ...v.changes });
  box.innerHTML = `
    ${best ? `<div class="card best"><div class="card-head"><h2>Najlepsza potwierdzona zmiana</h2>
        <span class="f-tag up">potwierdzona w czasie</span></div>
      <p style="margin:0 0 12px;font-size:15px"><b>${esc(best.label)}</b></p>
      <div class="grid metrics">
        ${metric("Wynik całość", `${pct(base.total, 1)} → ${pct(best.total, 1)}`, tone(best.delta_total))}
        ${metric("Poza próbą", `${pct(base.oos_ret, 1)} → ${pct(best.oos_ret, 1)}`, tone(best.delta_oos))}
        ${metric("Maks. obsunięcie", `${pct(base.dd, 1)} → ${pct(best.dd, 1)}`)}
        ${metric("Lepsza w okresach", `${best.windows_better} z ${o.windows.length}`)}
        ${metric("Transakcje", `${base.trades} → ${best.trades}`)}
      </div>
      ${o.best_still_negative ? `<p class="note warn" style="margin-top:12px">Ta zmiana poprawia wynik, ale strategia <b>nadal traci</b>.
        To znak, że problem jest w samej strategii dla tych symboli, a nie w dostrojeniu parametrów.</p>` : ""}
      <div style="display:flex;gap:8px;margin-top:14px;flex-wrap:wrap" class="no-print">
        <button class="btn" data-apply="bt" data-key="${esc(best.key)}">Pełny backtest z tą zmianą</button>
        <button class="btn primary" data-apply="bot" data-key="${esc(best.key)}">Utwórz bota z tą zmianą</button></div></div>`
    : `<div class="card"><h2>Brak potwierdzonej poprawy</h2><p class="muted" style="margin:0">Żadna ze sprawdzonych zmian nie poprawiła wyniku
        jednocześnie w próbie, poza próbą i w większości okresów. Obecne ustawienia są w tym sensie „najlepsze z sąsiednich” —
        albo strategia wymaga innego pomysłu, nie strojenia.</p></div>`}
    <div class="card" style="margin-top:14px"><div class="card-head"><h2>Wszystkie sprawdzone warianty (${o.tested})</h2>
      <button class="btn small no-print" id="opt-rerun">Przelicz ponownie</button></div>
      ${intro}
      <p class="small muted" style="margin:0 0 10px">Próba: ${esc(c.start)} → ${esc(o.in_sample_end)} · Poza próbą: ${esc(o.oos_start)} → ${esc(c.end)} ·
        Okresy: ${o.windows.map((w, i) => `${i + 1}) ${w.start} → ${w.end}`).join(", ")}</p>
      <div class="table-wrap"><table class="opt"><thead><tr><th>Zmiana</th><th>Status</th><th class="num">W próbie</th>
        <th class="num">Poza próbą</th><th class="num">Całość</th><th class="num">Maks. obs.</th><th class="num">Transakcje</th>
        <th>Okresy lepsze</th><th class="no-print"></th></tr></thead><tbody>
        <tr class="base-row"><td><b>Obecne ustawienia</b></td><td></td><td class="num ${tone(base.is_ret)}">${pct(base.is_ret, 1)}</td>
          <td class="num ${tone(base.oos_ret)}">${pct(base.oos_ret, 1)}</td><td class="num ${tone(base.total)}">${pct(base.total, 1)}</td>
          <td class="num">${pct(base.dd, 1)}</td><td class="num">${base.trades}</td><td></td><td class="no-print"></td></tr>
        ${all.map((v, i) => v.status === "error" ? `<tr><td>${esc(v.label)}</td><td><span class="f-tag down">błąd</span></td><td colspan="7" class="small muted">${esc(v.reasons.join(", "))}</td></tr>` : `
          <tr class="link" data-row="${i}"><td>${v.key === "combined" ? "<b>" + esc(v.label) + "</b>" : esc(v.label)}
            ${v.reasons.length ? `<div class="small muted">${esc(v.reasons.join(" · "))}</div>` : ""}</td>
            <td><span class="f-tag ${OPT_STATUS[v.status][1]}">${OPT_STATUS[v.status][0]}</span></td>
            <td class="num ${tone(v.is_ret - base.is_ret)}">${pct(v.is_ret, 1)}</td>
            <td class="num ${tone(v.delta_oos)}">${pct(v.oos_ret, 1)}</td>
            <td class="num ${tone(v.delta_total)}">${pct(v.total, 1)}</td>
            <td class="num">${pct(v.dd, 1)}</td><td class="num">${v.trades}</td>
            <td>${squares(v.windows_better_flags)} <span class="small muted">${v.windows_better}/${o.windows.length}</span></td>
            <td class="no-print"><button class="btn small" data-apply="bt" data-key="${esc(v.key)}">Test</button></td></tr>
          <tr class="detail hidden" data-detail="${i}"><td colspan="9"><table><thead><tr><th>Okres</th><th class="num">Obecne</th>
            <th class="num">Wariant</th><th class="num">Różnica</th></tr></thead><tbody>
            ${o.windows.map((w, k) => `<tr><td>${esc(w.start)} → ${esc(w.end)}</td><td class="num ${tone(base.windows[k])}">${pct(base.windows[k], 2)}</td>
              <td class="num ${tone(v.windows[k])}">${pct(v.windows[k], 2)}</td>
              <td class="num ${tone((v.windows[k] ?? 0) - (base.windows[k] ?? 0))}">${pct((v.windows[k] ?? 0) - (base.windows[k] ?? 0), 2)}</td></tr>`).join("")}
          </tbody></table></td></tr>`).join("")}
      </tbody></table></div>
      <p class="small muted" style="margin:10px 0 0">Kolor liczby = lepiej / gorzej niż obecne ustawienia. Kliknij wiersz, żeby zobaczyć wynik w każdym okresie. Kwadraty: zielony = wariant lepszy w danym okresie.</p></div>`;
  $$("tr[data-row]").forEach(tr => tr.onclick = e => {
    if (e.target.closest("button")) return;
    $(`tr[data-detail="${tr.dataset.row}"]`).classList.toggle("hidden");
  });
  $("#opt-rerun").onclick = async () => { await api("POST", `/api/backtests/${id}/optimize`); bt.opt_status = "running"; show("opt"); };
  $$("[data-apply]").forEach(b => b.onclick = async () => {
    const v = all.find(x => x.key === b.dataset.key);
    const params = applyParams(v);
    const sname = SCHEMA.strategies.find(s => s.key === c.strategy)?.name;
    if (b.dataset.apply === "bot") {
      botForm(null, { name: `${sname} (poprawiony, test #${id})`, account: SCHEMA.accounts[0]?.name, market: c.market, strategy: c.strategy, params });
      return;
    }
    try {
      const r = await api("POST", "/api/backtests", { market: c.market, strategy: c.strategy, params, start: c.start, end: c.end,
        initial_capital: c.initial_capital, cost_pct: c.cost_pct, data_source: c.data_source, name: v.label });
      toast("Uruchomiono backtest z tą zmianą"); location.hash = `#/backtest/${r.id}`;
    } catch (e) { toast(e.message, true); }
  });
}
function perSymbolTable(rows) {
  if (!rows.length) return `<div class="empty">Brak transakcji.</div>`;
  return `<table><thead><tr><th>Symbol</th><th class="num">Transakcje</th><th class="num">Skuteczność</th><th class="num">Wynik</th></tr></thead><tbody>
    ${rows.map(s => `<tr><td><b>${esc(s.symbol)}</b></td><td class="num">${s.trades}</td><td class="num">${pct(s.win_rate, 0, false)}</td>
    <td class="num ${tone(s.pnl)}">${usd(s.pnl, true)}</td></tr>`).join("")}</tbody></table>`;
}
function btTrades(rows) {
  if (!rows.length) return `<div class="empty">Strategia nie zawarła żadnej transakcji w tym okresie.</div>`;
  return `<table><thead><tr><th>Symbol</th><th>Wejście</th><th>Wyjście</th><th class="num">Cena we/wy</th><th class="num">Wynik</th><th>Powód</th></tr></thead><tbody>
    ${rows.slice().reverse().slice(0, 300).map(t => `<tr><td><b>${esc(t.symbol)}</b></td><td class="small">${when(t.t_in)}</td><td class="small">${when(t.t_out)}</td>
      <td class="num small">${price(t.entry)} → ${price(t.exit)}</td>
      <td class="num ${tone(t.pnl)}">${usd(t.pnl, true)}<div class="small">${pct(t.pnl_pct)}</div></td>
      <td class="small muted">${esc(t.reason)}</td></tr>`).join("")}</tbody></table>`;
}

// ------------------------------------------------------------------ RAPORTY Z DZIALANIA BOTOW
const KIND = { daily: "Dzienny", weekly: "Tygodniowy", stop: "Podsumowanie", manual: "Na żądanie" };
function fmtPeriod(a, b) {
  const o = { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" };
  return `${new Date(a).toLocaleString("pl-PL", o)} → ${new Date(b).toLocaleString("pl-PL", o)}`;
}
async function renderReports(botId) {
  const [rows, ov] = await Promise.all([api("GET", `/api/reports${botId ? `?bot_id=${botId}` : ""}`), api("GET", "/api/overview")]);
  view.innerHTML = `
    <div class="page-head"><h1>Raporty z działania botów</h1>
      <div class="actions"><select id="r-bot" style="width:auto"><option value="">Wszystkie boty</option>
        ${ov.bots.map(b => `<option value="${b.id}" ${String(b.id) === botId ? "selected" : ""}>${esc(b.name)}</option>`).join("")}</select>
        <button class="btn primary" id="r-new">Generuj raport</button></div></div>
    <p class="note" style="margin-bottom:14px">Raporty powstają same: <b>dzienny</b> po zamknięciu sesji (22:30), <b>tygodniowy</b> w sobotę rano
      i <b>podsumowanie</b> przy każdym zatrzymaniu bota. Możesz też wygenerować raport za dowolny okres.</p>
    <div class="card table-wrap">${!rows.length ? `<div class="empty">Brak raportów. Pierwszy powstanie dziś po sesji albo po kliknięciu „Generuj raport”.</div>` : `
      <table><thead><tr><th>Utworzono</th><th>Bot</th><th>Rodzaj</th><th>Okres</th><th class="num">Wynik</th><th class="num">% budżetu</th>
        <th class="num">Kup i trzymaj</th><th class="num">Transakcje</th><th class="num">Skuteczność</th><th></th></tr></thead><tbody>
      ${rows.map(r => { const s = r.summary || {};
        return `<tr class="link" data-rep="${r.id}"><td class="small">${when(r.created_at)}</td><td><b>${esc(r.bot_name)}</b></td>
        <td><span class="tag">${KIND[r.kind] || r.kind}</span></td><td class="small muted">${fmtPeriod(r.period_from, r.period_to)}</td>
        <td class="num ${tone(s.pnl)}">${usd(s.pnl, true)}</td><td class="num ${tone(s.return_pct)}">${pct(s.return_pct)}</td>
        <td class="num ${tone(s.benchmark_pct)}">${pct(s.benchmark_pct)}</td><td class="num">${s.closed_trades ?? "—"}</td>
        <td class="num">${s.win_rate == null ? "—" : pct(s.win_rate, 0, false)}</td>
        <td>${s.errors ? `<span class="f-tag down">${s.errors} błędów</span>` : ""}</td></tr>`; }).join("")}</tbody></table>`}</div>`;
  $("#r-bot").onchange = e => { location.hash = e.target.value ? `#/reports?bot=${e.target.value}` : "#/reports"; };
  $$("tr[data-rep]").forEach(tr => tr.onclick = () => { location.hash = `#/report/${tr.dataset.rep}`; });
  $("#r-new").onclick = () => reportForm(ov.bots, botId);
}
function reportForm(bots, botId) {
  if (!bots.length) { toast("Najpierw utwórz bota.", true); return; }
  const today = new Date().toISOString().slice(0, 10);
  openModal(`<div class="card-head"><h2>Generuj raport</h2><button class="btn ghost small" id="m-close">Zamknij</button></div>
    <form class="form" id="rep-form"><div class="form-grid">
      <div class="field"><label>Bot</label><select id="rp-bot">${bots.map(b => `<option value="${b.id}" ${String(b.id) === botId ? "selected" : ""}>${esc(b.name)}</option>`).join("")}</select></div>
      <div class="field"><label>Okres</label><select id="rp-period"><option value="day">Ostatnie 24 h</option><option value="week" selected>Ostatnie 7 dni</option>
        <option value="month">Ostatnie 30 dni</option><option value="run">Od uruchomienia bota</option><option value="custom">Wybrane daty</option></select></div>
      <div class="field rp-custom hidden"><label>Od</label><input type="date" id="rp-start" max="${today}"></div>
      <div class="field rp-custom hidden"><label>Do</label><input type="date" id="rp-end" value="${today}" max="${today}"></div>
    </div><p class="err" id="rp-err"></p>
    <div class="modal-foot"><button type="button" class="btn" id="m-cancel">Anuluj</button><button class="btn primary" type="submit">Generuj</button></div></form>`);
  $("#m-close").onclick = $("#m-cancel").onclick = closeModal;
  $("#rp-period").onchange = e => $$(".rp-custom").forEach(x => x.classList.toggle("hidden", e.target.value !== "custom"));
  $("#rep-form").onsubmit = async e => {
    e.preventDefault();
    const btn = $("#rep-form button[type=submit]"); btn.disabled = true; btn.textContent = "Generuję…";
    try {
      const r = await api("POST", "/api/reports", { bot_id: +$("#rp-bot").value, period: $("#rp-period").value,
        start: $("#rp-start").value || null, end: $("#rp-end").value || null });
      closeModal(); location.hash = `#/report/${r.id}`;
    } catch (err) { $("#rp-err").textContent = err.message; btn.disabled = false; btn.textContent = "Generuj"; }
  };
}
async function renderReport(id) {
  const r = await api("GET", `/api/reports/${id}`);
  const d = r.data, m = d.metrics, b = d.bot, ev = d.events, bt = d.backtest;
  const diff = m.return_pct != null && m.benchmark_pct != null ? m.return_pct - m.benchmark_pct : null;
  view.innerHTML = `
    <div class="page-head"><div><a href="#/reports?bot=${b.id}" class="muted small no-print" style="text-decoration:none">← Raporty</a>
      <h1 style="margin-top:4px">${esc(d.kind_label)}: ${esc(b.name)}</h1>
      <div class="muted small" style="margin-top:4px">${fmtPeriod(d.from, d.to)} · ${MARKET[b.market]} · konto <b>${esc(b.account)}</b>
        ${b.account_type === "sim" ? "(symulacja)" : ""} · ${esc(b.strategy)} · ${esc(b.timeframe)} · budżet ${pct(b.allocation_pct * 100, 0, false)}
        (${usd(m.budget)}) · ryzyko ${pct(b.risk_pct * 100, 1, false)} · SL ${pct(b.sl * 100, 1, false)} · TP ${pct(b.tp * 100, 1, false)}</div></div>
      <div class="actions no-print"><button class="btn" id="rp-print">Drukuj / PDF</button>
        <a class="btn" href="#/bot/${b.id}">Otwórz bota</a><button class="btn ghost danger" id="rp-del">Usuń raport</button></div></div>

    <div class="grid metrics" style="margin-bottom:14px">
      ${metric("Wynik w okresie", usd(m.period_pnl, true), tone(m.period_pnl))}
      ${metric("% budżetu bota", pct(m.return_pct), tone(m.return_pct))}
      ${metric("Kup i trzymaj (te symbole)", pct(m.benchmark_pct), tone(m.benchmark_pct))}
      ${metric("Różnica vs rynek", diff == null ? "—" : pct(diff), tone(diff))}
      ${metric("Zamknięte transakcje", m.closed_trades)}
      ${metric("Otwarte w okresie", m.opened)}
      ${metric("Skuteczność", m.win_rate == null ? "—" : pct(m.win_rate, 0, false))}
      ${metric("Śr. zysk / strata", m.avg_win_pct == null && m.avg_loss_pct == null ? "—" : `${pct(m.avg_win_pct, 1)} / ${pct(m.avg_loss_pct, 1)}`)}
      ${metric("Profit factor", m.profit_factor == null ? "—" : num(m.profit_factor))}
      ${metric("Maks. obsunięcie", m.max_dd_usd ? `${usd(m.max_dd_usd, true)} <span class="small muted">${pct(m.max_dd_pct, 1)}</span>` : "—", m.max_dd_usd < 0 ? "down" : "")}
      ${metric("Czas w rynku", m.exposure_pct == null ? "—" : pct(m.exposure_pct, 0, false))}
      ${metric("Śr. czas pozycji", m.avg_hours == null ? "—" : m.avg_hours < 48 ? num(m.avg_hours, 1) + " h" : num(m.avg_hours / 24, 1) + " dni")}
    </div>

    <div class="card"><div class="card-head"><h2>Podsumowanie i wnioski</h2></div>
      <div class="findings">${d.findings.length ? d.findings.map(f => `
        <div class="finding ${f.severity}"><div class="f-head"><span class="f-tag ${SEV[f.severity][1]}">${SEV[f.severity][0]}</span><b>${esc(f.title)}</b></div>
        <div class="f-body">${esc(f.detail)}</div></div>`).join("") : `<div class="empty">Brak uwag.</div>`}</div></div>

    <div class="grid two" style="margin-top:14px">
      <div class="card"><div class="card-head"><h2>Wynik bota w okresie</h2><span class="muted small">zrealizowany + otwarte pozycje, $</span></div>
        <div class="chart-box" id="rc-curve"><canvas></canvas></div></div>
      <div class="card"><div class="card-head"><h2>Rynek: kup i trzymaj</h2><span class="muted small">symbole bota po równo, %</span></div>
        <div class="chart-box" id="rc-bench"><canvas></canvas></div></div>
      <div class="card"><div class="card-head"><h2>Wynik dzienny (zamknięte transakcje)</h2></div>
        <div class="chart-box small" id="rc-daily"><canvas></canvas></div></div>
      <div class="card"><div class="card-head"><h2>Wynik per symbol</h2></div>
        <div class="chart-box small" id="rc-sym"><canvas></canvas></div></div>
      <div class="card"><div class="card-head"><h2>Zaangażowanie w rynku</h2><span class="muted small">wartość otwartych pozycji, $</span></div>
        <div class="chart-box small" id="rc-exp"><canvas></canvas></div></div>
      <div class="card"><h2>Zgodność z backtestem</h2>
        ${bt && !bt.error ? `<div class="grid metrics">
            ${metric("Transakcje na żywo", m.closed_trades)}${metric("Transakcje w backteście", bt.trades)}
            ${metric("Skuteczność na żywo", m.win_rate == null ? "—" : pct(m.win_rate, 0, false))}
            ${metric("Skuteczność w backteście", bt.win_rate == null ? "—" : pct(bt.win_rate, 0, false))}</div>
            <p class="small muted" style="margin:10px 0 0">Ten sam okres i ustawienia przepuszczone przez backtest (${esc(bt.note)}). Duża różnica w liczbie
            transakcji oznacza, że bot na żywo zachowuje się inaczej niż w symulacji.</p>`
          : `<div class="empty">${bt?.error ? "Nie udało się: " + esc(bt.error) : "Porównanie powstaje w raportach tygodniowych, podsumowaniach i raportach na żądanie (okres ≥ 1 dzień)."}</div>`}</div>
    </div>

    <div class="grid two" style="margin-top:14px">
      <div class="card"><h2>Jak zamykane były pozycje</h2><div class="table-wrap">${statTable(d.exits, "Wyjście")}</div></div>
      <div class="card"><h2>Symbole</h2><div class="table-wrap" style="max-height:320px;overflow:auto">${statTable(d.per_symbol.map(x => ({ ...x, label: x.symbol })), "Symbol")}</div></div>
      <div class="card"><h2>Otwarte pozycje na koniec okresu</h2>${positionsTable(d.positions, d.positions_error)}</div>
      <div class="card"><h2>Zdarzenia</h2><div class="grid metrics">
        ${metric("Błędy", ev.errors, ev.errors ? "down" : "")}${metric("Ostrzeżenia", ev.warnings)}
        ${metric("Zablokowane sygnały", ev.blocked)}${metric("Uruchomienia bota", ev.starts)}</div>
        ${ev.last_errors.length ? `<div class="logbox" style="height:auto;max-height:160px;margin-top:10px">${ev.last_errors.map(e =>
          `<div class="ERROR"><span class="ts">${when(e.ts)}</span> ${esc(e.msg)}</div>`).join("")}</div>` : ""}</div>
    </div>

    <div class="card" style="margin-top:14px"><h2>Zamknięte transakcje (${d.trades.length})</h2>
      <div class="table-wrap" style="max-height:420px;overflow:auto">${d.trades.length ? `<table><thead><tr><th>Symbol</th><th>Wejście</th><th>Wyjście</th>
        <th class="num">Cena we/wy</th><th class="num">Ilość</th><th class="num">Wynik</th><th class="num">Czas</th><th>Powód</th></tr></thead><tbody>
        ${d.trades.slice().reverse().map(t => `<tr><td><b>${esc(t.symbol)}</b></td><td class="small">${when(t.t_in)}</td><td class="small">${when(t.t_out)}</td>
          <td class="num small">${price(t.entry)} → ${price(t.exit)}</td><td class="num">${num(t.qty, 6)}</td>
          <td class="num ${tone(t.pnl)}">${usd(t.pnl, true)}<div class="small">${pct(t.pnl_pct)}</div></td>
          <td class="num small">${t.hours == null ? "—" : t.hours < 48 ? num(t.hours, 1) + " h" : num(t.hours / 24, 1) + " d"}</td>
          <td class="small muted">${esc(t.reason || "")}</td></tr>`).join("")}</tbody></table>` : `<div class="empty">Brak zamkniętych transakcji w tym okresie.</div>`}</div></div>`;

  $("#rp-print").onclick = () => window.print();
  $("#rp-del").onclick = async () => { if (confirmBox("Usunąć ten raport?")) { await api("DELETE", `/api/reports/${id}`); location.hash = `#/reports?bot=${b.id}`; } };
  const put = (boxId, ok, draw, msg) => { const box = $(boxId); if (ok) { box.innerHTML = "<canvas></canvas>"; draw($("canvas", box)); } else emptyChart(box, msg); };
  put("#rc-curve", d.curve.length > 1, c => lineChart(c, [{ label: "Wynik bota", points: d.curve }]), "Za mało migawek wyniku w tym okresie (zapisywane co 15 min działania bota).");
  put("#rc-bench", d.benchmark_curve.length > 1, c => lineChart(c, [{ label: "Kup i trzymaj", points: d.benchmark_curve, color: css("--info") }], { money: false }), "Brak danych rynkowych dla tego okresu.");
  put("#rc-daily", d.daily_pnl.length > 0, c => barChart(c, d.daily_pnl.map(x => x[0].slice(5).split("-").reverse().join(".")), d.daily_pnl.map(x => x[1])), "Brak zamkniętych transakcji.");
  put("#rc-sym", d.per_symbol.length > 0, c => barChart(c, d.per_symbol.map(x => x.symbol), d.per_symbol.map(x => +x.pnl.toFixed(2)), { horizontal: true }), "Brak zamkniętych transakcji.");
  put("#rc-exp", d.exposure.length > 1, c => lineChart(c, [{ label: "W rynku", points: d.exposure, color: css("--warn") }], { zero: true }), "Brak danych o pozycjach.");
}

// ------------------------------------------------------------------ SYGNALY ZEWNETRZNE
async function renderSignals() {
  view.innerHTML = `
    <div class="page-head"><h1>Sygnały zewnętrzne</h1></div>
    <div class="card"><form class="form" id="sig-form">
      <p class="muted" style="margin:0">Transakcje insiderów (zarząd i dyrektorzy kupujący lub sprzedający akcje własnej spółki, SEC Form 4)
        oraz ruchy wybranych funduszy z raportów 13F. Liczy się <b>data złożenia</b> w SEC — tak samo widzi to bot i backtest.
        Zakupy insiderów są rzadkie i znaczące; sprzedaże są częste (plany sprzedaży, podatki, opcje), więc same w sobie mniej mówią.</p>
      <div class="form-grid">
        <div class="field" style="grid-column:1/-1"><label>Symbole (puste = symbole Twoich botów akcyjnych)</label>
          <input id="sig-syms" placeholder="np. NVDA, TSLA, AMD"></div>
        <div class="field"><label>Okres</label><select id="sig-days"><option value="30">30 dni</option><option value="90" selected>90 dni</option><option value="180">180 dni</option></select></div>
        <div class="field"><label>Źródło</label><select id="sig-src"><option value="auto">SEC (prawdziwe dane)</option><option value="sim">Demo (dane syntetyczne)</option></select></div>
      </div>
      <div><button class="btn primary" type="submit">Sprawdź</button>
        <span class="muted small" style="margin-left:8px">Pierwsze sprawdzenie spółki pobiera jej zgłoszenia z SEC (do minuty), kolejne są natychmiastowe.</span></div>
    </form></div>
    <div id="sig-out" class="stack" style="margin-top:14px"></div>`;
  $("#sig-form").onsubmit = async e => {
    e.preventDefault();
    const out = $("#sig-out");
    out.innerHTML = `<div class="card empty">Pobieram dane…</div>`;
    try {
      const q = new URLSearchParams({ symbols: $("#sig-syms").value, days: $("#sig-days").value, source: $("#sig-src").value });
      const d = await api("GET", `/api/signals?${q}`);
      if (!d.symbols.length) { out.innerHTML = `<div class="card empty">Podaj symbole albo utwórz bota akcyjnego.</div>`; return; }
      out.innerHTML = `
        ${d.source === "sim" ? `<p class="note warn">Dane syntetyczne (demo). Prawdziwe dane: ustaw SEC_USER_AGENT w pliku .env.</p>` : ""}
        ${d.fund_error ? `<p class="note warn">Fundusze 13F niedostępne: ${esc(d.fund_error)}</p>` : ""}
        ${d.symbols.map(s => signalCard(s, d)).join("")}`;
    } catch (err) { out.innerHTML = `<div class="card err">${esc(err.message)}</div>`; }
  };
  $("#sig-form").requestSubmit();
}
function signalCard(s, d) {
  if (s.error) return `<div class="card"><h2>${esc(s.symbol)}</h2><p class="err">${esc(s.error)}</p></div>`;
  const i = s.insider;
  const fundTag = v => v > 0 ? `<span class="f-tag up">dokupił / otworzył</span>` : v < 0 ? `<span class="f-tag down">sprzedał / zamknął</span>` : `<span class="f-tag muted">bez zmian / brak</span>`;
  return `<div class="card"><div class="card-head"><h2>${esc(s.symbol)}</h2>
      <span class="muted small">${i.count} transakcji insiderów w ${d.days} dni</span></div>
    <div class="grid metrics" style="margin-bottom:12px">
      ${metric("Kupujący insiderzy (30 dni)", i.buyers_30d, i.buyers_30d ? "up" : "")}
      ${metric("Sprzedający insiderzy (30 dni)", i.sellers_30d, i.sellers_30d >= 3 ? "down" : "")}
      ${metric("Wartość zakupów (30 dni)", usd(i.buy_value_30d))}
      ${metric("Wartość sprzedaży (30 dni)", usd(i.sell_value_30d))}
    </div>
    ${s.funds.length ? `<div class="small" style="display:flex;gap:14px;flex-wrap:wrap;margin-bottom:12px">
      ${s.funds.map(f => `<span><b>${esc(f.manager)}</b> ${fundTag(f.change)} <span class="muted">13F za ${esc(f.period || "—")}, złożony ${esc(f.filed)}</span></span>`).join("")}</div>` : ""}
    ${i.events.length ? `<div class="table-wrap" style="max-height:300px;overflow:auto"><table><thead><tr><th>Złożono</th><th>Transakcja</th>
      <th>Osoba</th><th>Rola</th><th>Typ</th><th class="num">Akcje</th><th class="num">Cena</th><th class="num">Wartość</th></tr></thead><tbody>
      ${i.events.map(e => `<tr><td class="small">${esc(e.filed)}</td><td class="small muted">${esc(e.date || "")}</td><td>${esc(e.owner)}</td>
        <td class="small muted">${esc(e.role || "")}</td><td class="${e.code === "P" ? "up" : "down"}">${e.code === "P" ? "Kupno" : "Sprzedaż"}</td>
        <td class="num">${num(e.shares, 0)}</td><td class="num">${price(e.price)}</td><td class="num">${usd(e.value)}</td></tr>`).join("")}
      </tbody></table></div>` : `<div class="empty">Brak transakcji insiderów w tym okresie${/^X|ETF/.test(s.symbol) ? " (to może być ETF — nie ma insiderów)" : ""}.</div>`}
  </div>`;
}

// ------------------------------------------------------------------ SYSTEM I AKTUALIZACJE
const UPD_STATE = { idle: ["brak oczekujących", "muted"], waiting: ["czeka na zamknięcie rynku", "warn"],
  running: ["aktualizacja w toku", "info"], done: ["zaktualizowano", "up"], rolled_back: ["przywrócono poprzednią", "warn"],
  failed: ["błąd", "down"] };
async function renderSystem() {
  if (DEMO) return renderDemoSystem();
  // Formularz budujemy RAZ - odswiezamy tylko status, liste i dziennik (wczesniej odswiezanie
  // co 10 s podmienialo formularz w trakcie wybierania pliku i wybor przepadal).
  view.innerHTML = `<div class="page-head"><h1>System</h1></div>
    <div class="stack">
      <div id="sys-top"></div>
      ${INSTALLED ? `<div class="card"><h2>Aktualizacje</h2><p class="muted" style="margin:0">Ta wersja jest zainstalowana instalatorem.
        Nową wersję pobierasz z GitHuba (<a href="https://github.com/xboff57/tradingapp-demo/releases/latest" target="_blank" rel="noopener">Releases → TradingApp-Setup.exe</a>)
        i uruchamiasz instalator — boty, konta, hasło i historia zostają.</p></div>` : ""}
      <div class="card${INSTALLED ? " hidden" : ""}"><h2>Wgraj nową wersję</h2>
        <p class="muted" style="margin:0 0 12px">Wybierz <b>podpisaną</b> paczkę <b>tradingapp.zip</b> (oficjalne wydanie z folderu <i>wydania</i> w repozytorium). Paczki bez podpisu autora są odrzucane. Aktualizator zrobi kopię bazy i kodu,
          podmieni kod (bez ruszania .env i danych), sprawdzi, czy aplikacja wstała, a w razie problemu przywróci poprzednią wersję.
          Domyślnie czeka z tym do zamknięcia rynku w USA (22:00 czasu PL), żeby nie przerywać botom sesji.</p>
        <form id="upd-form" class="form">
          <input type="file" id="upd-file" accept=".zip">
          <label class="check" style="padding:0"><input type="checkbox" id="upd-force"> Aktualizuj od razu, nawet w trakcie sesji</label>
          <div><button class="btn primary" type="submit" id="upd-btn">Wgraj</button> <span class="small" id="upd-msg"></span></div>
          <div class="upbar hidden" id="upd-bar"><div></div><span></span></div>
        </form>
        <div id="sys-steps"></div>
        <div id="sys-pending"></div>
      </div>
      <div class="card" id="sys-notify"></div>
      <div class="card"><h2>Dziennik aktualizacji</h2><div class="logbox" id="sys-log" style="height:260px"></div></div>
    </div>`;
  const msg = (text, bad = false) => { const m = $("#upd-msg"); m.textContent = text; m.className = "small " + (bad ? "down" : "muted"); };
  let loadedVersion = null, offline = 0, active = false, tick = 0;
  const draw = async () => {
    let d;
    try { d = await api("GET", "/api/system"); }
    catch (err) {
      if (err.message === "Zaloguj się") throw err;
      offline++;
      $("#sys-steps").innerHTML = `<div class="note warn" style="margin-top:14px"><span class="spin"></span> Aplikacja się restartuje —
        czekam, aż panel wróci (${offline * 3} s)… Nie zamykaj tej strony.</div>` + ($("#sys-steps").dataset.last || "");
      active = true;
      return;
    }
    if (loadedVersion && d.version !== loadedVersion && offline) {
      toast(`Działa nowa wersja ${d.version} — przeładowuję stronę`);
      setTimeout(() => location.reload(), 1500);
    }
    loadedVersion = loadedVersion || d.version;
    offline = 0;
    const st = d.status;
    const steps = updateSteps(st);
    $("#sys-steps").innerHTML = steps;
    $("#sys-steps").dataset.last = steps;
    active = !!st && ["queued", "running"].includes(st.state);
    $("#sys-top").innerHTML = `
      <div class="grid metrics" style="margin-bottom:12px">
        ${metric("Wersja aplikacji", esc(d.version))}
        ${metric("Aktualizacje", st ? `<span class="f-tag ${UPD_STATE[st.state]?.[1] || "muted"}">${esc(UPD_STATE[st.state]?.[0] || st.state)}</span>` : "—")}
        ${metric("Ostatnia kopia zapasowa", d.backups[0] ? esc(d.backups[0]) : "—")}
        ${metric("Tryb", d.platform === "windows" ? "Windows" : "Docker / NAS")}
        ${metric("Podpis wydań", !d.signing ? "—" : !d.signing.required ? '<span class="down">wyłączony</span>'
          : d.signing.key ? `<span class="up">wymagany</span> <span class="muted small">klucz ${esc(d.signing.key.fingerprint)}</span>`
          : '<span class="warn">brak klucza</span>')}
      </div>
      ${st && st.step && st.state !== "idle" ? "" : st ? `<p class="note">${esc(st.message)}${st.file ? ` <span class="muted">(${esc(st.file)})</span>` : ""}</p>`
           : d.platform === "windows" ? `<p class="note">Po wgraniu paczki aplikacja sama uruchomi aktualizację (skrypt aktualizuj.ps1) po zamknięciu rynku
              albo od razu, jeśli zaznaczysz tę opcję. Okno start.bat się wtedy zamknie — aplikacja wróci w tle pod tym samym adresem.</p>`
           : `<p class="note warn">Nie wykryto automatycznego aktualizatora. Na NAS-ie uruchom <b>docker compose up -d</b> z nowym
              docker-compose.yml (dochodzi kontener „tradingapp-updater”).</p>`}`;
    $("#sys-pending").innerHTML = d.pending.length ? `<h2 style="margin-top:16px">Oczekujące</h2><table><tbody>${d.pending.map(f =>
      `<tr><td class="mono small">${esc(f)}</td><td class="num"><button class="btn small" data-cancel="${esc(f)}">Anuluj</button></td></tr>`).join("")}</tbody></table>
      ${d.force ? `<p class="small warn">Zaznaczono aktualizację od razu.</p>` : ""}` : "";
    $$("[data-cancel]").forEach(b => b.onclick = async () => { await api("DELETE", `/api/system/pending/${encodeURIComponent(b.dataset.cancel)}`); draw(); });
    const box = $("#sys-log");
    const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
    box.innerHTML = d.log.length ? d.log.map(l => `<div>${esc(l)}</div>`).join("") : `<span class="muted">Brak wpisów.</span>`;
    if (atBottom) box.scrollTop = box.scrollHeight;
  };
  $("#upd-form").onsubmit = async e => {
    e.preventDefault();
    const file = $("#upd-file").files[0];
    if (!file) { msg("Najpierw wybierz plik .zip.", true); return; }
    if (!/\.zip$/i.test(file.name)) { msg("To nie jest plik .zip.", true); return; }
    msg(`Wysyłam ${file.name} (${(file.size / 1024).toFixed(0)} KB)…`);
    $("#upd-btn").disabled = true;
    const bar = $("#upd-bar");
    const setBar = (frac, text) => { bar.classList.remove("hidden"); bar.firstElementChild.style.width = (frac * 100).toFixed(0) + "%";
      bar.lastElementChild.textContent = text; };
    setBar(0, "0%");
    try {
      const r = await new Promise((resolve, reject) => {   // XHR - fetch nie pokazuje postepu wysylania
        const x = new XMLHttpRequest();
        x.open("POST", `/api/system/upload?force=${$("#upd-force").checked}&filename=${encodeURIComponent(file.name)}`);
        x.setRequestHeader("Content-Type", "application/zip");
        x.upload.onprogress = ev => ev.lengthComputable && setBar(ev.loaded / ev.total,
          `${(ev.loaded / 1024).toFixed(0)} / ${(ev.total / 1024).toFixed(0)} KB (${(ev.loaded / ev.total * 100).toFixed(0)}%)`);
        x.upload.onload = () => setBar(1, "wysłano — serwer sprawdza paczkę…");
        x.onload = () => {
          let body = {}; try { body = JSON.parse(x.responseText); } catch (_) { /* pusto */ }
          if (x.status === 401) { showLogin(); reject(new Error("Sesja wygasła — zaloguj się ponownie.")); }
          else if (x.status >= 400) reject(new Error(body.detail || `Błąd serwera (${x.status})`));
          else resolve(body);
        };
        x.onerror = () => reject(new Error("Połączenie przerwane podczas wysyłania."));
        x.send(file);
      });
      setBar(1, "wysłano ✓");
      msg(`Wgrano wersję ${r.version} (obecna: ${r.current}). ${r.next || ""}`);
      toast(`Wgrano wersję ${r.version}`);
      $("#upd-file").value = "";
      draw();
    } catch (err) { msg(err.message, true); toast(err.message, true); bar.classList.add("hidden"); }
    finally { $("#upd-btn").disabled = false; }
  };
  await draw();
  drawNotify();
  every(3000, () => { tick++; if (active || tick % 4 === 0) draw().catch(() => {}); });
}

async function drawNotify() {
  const box = $("#sys-notify");
  if (!box) return;
  const n = await api("GET", "/api/notify");
  const tg = n.token && n.chat, nt = !!n.ntfy_topic, url = nt ? `${n.ntfy_server}/${n.ntfy_topic}` : "";
  box.innerHTML = `<h2>Powiadomienia na telefon ${tg || nt ? '<span class="f-tag up">włączone</span>' : '<span class="f-tag muted">wyłączone</span>'}</h2>
    <div class="grid two nt-grid">
      <div class="nt-ch">
        <h3>Aplikacja ntfy <span class="small muted">— najprościej, bez konta</span></h3>
        ${nt ? `<div class="nt-on">
            <div class="tf-qr nt-qr" id="nt-qr"></div>
            <ol class="small">
              <li>Zainstaluj <b>ntfy</b> na telefonie (App Store / Google Play).</li>
              <li>W aplikacji: <b>+</b> (subskrybuj temat) i wpisz temat — albo zeskanuj kod aparatem telefonu:<div class="mono tf-secret">${esc(n.ntfy_topic)}</div></li>
              <li>Kliknij poniżej „Wyślij test”.</li></ol></div>
            <p class="small muted" style="margin:8px 0 0">Temat działa jak hasło — kto go zna, widzi powiadomienia (bez dostępu do panelu).
              Serwer: ${esc(n.ntfy_server)}.</p>
            <div class="acc-actions" style="margin-top:10px"><button class="btn small" id="nt-off">Wyłącz ntfy</button>
              <button class="btn small" id="nt-new">Nowy temat</button></div>`
          : `<p class="small" style="margin:0 0 10px">Darmowa aplikacja ntfy (iPhone i Android). Panel tworzy prywatny temat, Ty go subskrybujesz.</p>
            <button class="btn primary small" id="nt-on">Włącz ntfy</button>`}
      </div>
      <div class="nt-ch">
        <h3>Telegram ${tg ? '<span class="f-tag up">połączony</span>' : ""}</h3>
        ${!n.token ? `<ol class="small" style="margin:0 0 10px;padding-left:18px">
            <li>W Telegramie napisz do <b>@BotFather</b>: <span class="mono">/newbot</span>, podaj nazwę — dostaniesz <b>token</b>.</li>
            <li>Dopisz do pliku <span class="mono">.env</span> linię <span class="mono">TELEGRAM_BOT_TOKEN=token</span> i zrestartuj aplikację.</li>
            <li>Napisz cokolwiek do swojego bota w Telegramie i kliknij tutaj „Połącz”.</li></ol>`
          : !n.chat ? `<p class="small" style="margin:0 0 10px">Token jest. Napisz cokolwiek do swojego bota w Telegramie i kliknij „Połącz”.</p>` : ""}
        ${n.token ? `<button class="btn small ${n.chat ? "" : "primary"}" id="nt-connect">${n.chat ? "Połącz ponownie" : "Połącz"}</button>` : ""}
      </div>
    </div>
    <div style="display:flex;gap:8px;flex-wrap:wrap;margin:14px 0 10px;align-items:center">
      ${tg || nt ? `<button class="btn small" id="nt-test">Wyślij test</button>` : ""}
      ${n.last_error ? `<span class="small down">Ostatni błąd wysyłki: ${esc(n.last_error)}</span>` : ""}
    </div>
    <div class="hb-box" id="hb-box"></div>
    <div class="small muted" style="margin-bottom:6px">Co wysyłać:</div>
    <div class="form-grid">${Object.entries(n.labels).map(([k, l]) => `<label class="check small" style="padding-top:0"><input type="checkbox" data-ev="${k}" ${n.events[k] ? "checked" : ""}> ${esc(l)}</label>`).join("")}</div>`;
  if (nt && window.qrcode) { const q = qrcode(0, "M"); q.addData(url); q.make(); $("#nt-qr").innerHTML = q.createSvgTag({ cellSize: 3, margin: 2, scalable: true }); }
  const ntfy = async en => { try { await api("POST", "/api/notify/ntfy", { enable: en }); drawNotify(); } catch (e) { toast(e.message, true); } };
  $("#nt-on") && ($("#nt-on").onclick = () => ntfy(true));
  $("#nt-off") && ($("#nt-off").onclick = () => confirm("Wyłączyć powiadomienia ntfy?") && ntfy(false));
  $("#nt-new") && ($("#nt-new").onclick = () => confirm("Utworzyć nowy temat? Stary przestanie dostawać powiadomienia — trzeba będzie zasubskrybować nowy.") && ntfy(true));
  $("#nt-connect") && ($("#nt-connect").onclick = async () => {
    try { const r = await api("POST", "/api/notify/connect"); toast(`Połączono z czatem: ${r.who}`); drawNotify(); } catch (e) { toast(e.message, true); } });
  $("#nt-test") && ($("#nt-test").onclick = async () => {
    try { await api("POST", "/api/notify/test"); toast("Wysłano — sprawdź telefon"); } catch (e) { toast(e.message, true); } });
  drawHeartbeat();
  $$("[data-ev]", box).forEach(c => c.onchange = async () => {
    const ev = {}; $$("[data-ev]", box).forEach(x => ev[x.dataset.ev] = x.checked);
    await api("PUT", "/api/notify/events", { events: ev }); toast("Zapisano");
  });
}

async function drawHeartbeat() {
  const box = $("#hb-box");
  if (!box) return;
  let h;
  try { h = await api("GET", "/api/notify/heartbeat"); } catch (e) { box.innerHTML = ""; return; }
  const st = h.last ? (h.ok ? (h.problems.length ? `<span class="warn">ostatni ping ${when(h.last)}: zgłoszony problem — ${esc(h.problems.join("; "))}</span>`
    : `<span class="up">ostatni ping ${when(h.last)}: OK</span>`) : `<span class="down">ostatni ping ${when(h.last)} nieudany: ${esc(h.error || "")}</span>`) : "";
  box.innerHTML = `<h3>Strażnik z zewnątrz ${h.enabled ? '<span class="f-tag up">włączony</span>' : '<span class="f-tag muted">wyłączony</span>'}</h3>
    <p class="small" style="margin:0 0 8px">Gdy NAS padnie albo straci prąd lub internet, nie wyśle żadnego powiadomienia — robi to wtedy serwis z zewnątrz.
      Panel co ${h.every_min} min daje znak życia; gdy znaki przestaną przychodzić (albo panel zgłosi problem), dostajesz maila, SMS-a albo powiadomienie.</p>
    ${h.enabled ? `<div class="small" style="margin-bottom:8px">Adres: <span class="mono">${esc(h.url_masked)}</span> · ${st}</div>
      <div class="acc-actions"><button class="btn small" id="hb-test">Wyślij ping teraz</button><button class="btn small" id="hb-off">Wyłącz</button></div>`
    : `<ol class="small" style="margin:0 0 10px;padding-left:18px">
        <li>Załóż darmowe konto na <a href="https://healthchecks.io" target="_blank" rel="noopener noreferrer">healthchecks.io</a> i kliknij „Add Check”.</li>
        <li>Ustaw <b>Period: 5 minutes</b> i <b>Grace: 10 minutes</b>; w „Integrations” dodaj e-mail (albo SMS / Telegram / ntfy).</li>
        <li>Skopiuj „Ping URL” (https://hc-ping.com/…) i wklej poniżej — panel od razu wyśle próbny ping.</li></ol>
      <form id="hb-form" style="display:flex;gap:8px;flex-wrap:wrap"><input id="hb-url" placeholder="https://hc-ping.com/…" style="flex:1;min-width:240px" autocomplete="off">
        <button class="btn primary small">Zapisz i sprawdź</button></form>`}
    <p class="small muted" style="margin:8px 0 0">Adres pingu działa jak hasło — nie udostępniaj go.</p>`;
  if ($("#hb-form")) $("#hb-form").onsubmit = async e => { e.preventDefault();
    try { await api("PUT", "/api/notify/heartbeat", { url: $("#hb-url").value }); toast("Zapisano — ping wysłany"); drawHeartbeat(); } catch (er) { toast(er.message, true); } };
  if ($("#hb-test")) $("#hb-test").onclick = async () => { try { await api("POST", "/api/notify/heartbeat/test"); toast("Ping wysłany"); drawHeartbeat(); } catch (er) { toast(er.message, true); } };
  if ($("#hb-off")) $("#hb-off").onclick = async () => { if (!confirm("Wyłączyć strażnika? Healthchecks.io po chwili zgłosi brak pingów — wstrzymaj tam też sprawdzanie.")) return;
    try { await api("PUT", "/api/notify/heartbeat", { url: "" }); drawHeartbeat(); } catch (er) { toast(er.message, true); } };
}

// etapy aktualizacji - rysowane ze statusu zapisywanego przez aktualizator
const UPD_STEPS = {
  windows: [["queued", "W kolejce"], ["check", "Sprawdzenie paczki"], ["backup", "Kopia zapasowa kodu i bazy"],
    ["copy", "Podmiana kodu"], ["deps", "Instalacja bibliotek"], ["start", "Uruchomienie nowej wersji"],
    ["health", "Sprawdzenie, czy panel działa"]],
  docker: [["queued", "W kolejce"], ["check", "Sprawdzenie paczki"], ["backup", "Kopia zapasowa kodu i bazy"],
    ["copy", "Podmiana kodu"], ["build", "Budowa i start kontenera (z bibliotekami)"], ["health", "Sprawdzenie, czy panel działa"]],
};
function updateSteps(st) {
  if (!st || !st.step || st.state === "idle") return "";
  const list = UPD_STEPS[st.platform] || UPD_STEPS.docker;
  const keys = list.map(x => x[0]);
  const failed = ["failed", "rolled_back"].includes(st.state);
  const rb = st.step === "rollback";
  let cur = st.state === "done" ? keys.length : keys.indexOf(st.step);
  if (rb) cur = keys.length - 1;
  const since = st.started ? Math.max(0, Math.round((new Date(st.updated) - new Date(st.started)) / 1000)) : null;
  const rows = list.map(([k, label], i) => {
    let icon, cls;
    if (i < cur || st.state === "done") { icon = "✓"; cls = "up"; }
    else if (i === cur && failed && !rb) { icon = "✗"; cls = "down"; }
    else if (i === cur && (st.state === "running" || st.state === "queued" || st.state === "waiting")) {
      icon = st.state === "waiting" ? "⏸" : `<span class="spin"></span>`; cls = st.state === "waiting" ? "warn" : "info"; }
    else if (i === cur && rb) { icon = "✗"; cls = "down"; }
    else { icon = "○"; cls = "muted"; }
    return `<li class="${cls}"><span class="st-ic">${icon}</span>${esc(label)}</li>`;
  }).join("");
  const extra = rb ? `<li class="${st.state === "running" ? "info" : st.state === "rolled_back" ? "warn" : "down"}"><span class="st-ic">${
    st.state === "running" ? '<span class="spin"></span>' : "↺"}</span>Przywracanie poprzedniej wersji</li>` : "";
  const head = { done: ["Aktualizacja zakończona", "up"], rolled_back: ["Nowa wersja nie wstała — przywrócono poprzednią", "warn"],
    failed: ["Aktualizacja nieudana", "down"], running: ["Aktualizacja w toku", "info"], queued: ["Paczka w kolejce", "info"],
    waiting: ["Czeka na zamknięcie rynku", "warn"] }[st.state] || [st.state, "muted"];
  return `<div class="steps-box"><div class="card-head"><h2 class="${head[1]}" style="margin:0">${esc(head[0])}</h2>
      <span class="muted small">${esc(st.file || "")}${since != null && st.state !== "queued" ? ` · ${since < 60 ? since + " s" : Math.floor(since / 60) + " min " + (since % 60) + " s"}` : ""}</span></div>
    <ol class="steps">${rows}${extra}</ol><div class="muted small">${esc(st.message || "")}</div></div>`;
}

// ------------------------------------------------------------------ TRANSAKCJE
async function renderTrades() {
  const [rows, ov] = await Promise.all([api("GET", "/api/trades?limit=1000"), api("GET", "/api/overview")]);
  view.innerHTML = `
    <div class="page-head"><h1>Transakcje</h1><div class="actions">
      <select id="t-bot" style="width:auto"><option value="">Wszystkie boty</option>
        ${ov.bots.map(b => `<option value="${b.id}">${esc(b.name)}</option>`).join("")}</select>
      <button class="btn" id="t-csv">Eksport CSV</button>
      <button class="btn primary" id="t-manual">Zlecenie ręczne</button></div></div>
    <div class="card" id="t-manual-list" style="margin-bottom:14px"></div>
    <div class="card table-wrap" id="t-table"></div>`;
  $("#t-manual").onclick = () => orderTicket("");
  drawManualOrders();
  const draw = () => {
    const f = $("#t-bot").value;
    const list = f ? rows.filter(r => String(r.bot_id) === f) : rows;
    $("#t-table").innerHTML = tradesTable(list, true);
    return list;
  };
  $("#t-bot").onchange = draw;
  $("#t-csv").onclick = () => {
    const list = draw();
    const head = ["czas_utc", "bot", "symbol", "strona", "ilosc", "cena", "wartosc", "pnl", "pnl_pct", "powod"];
    const lines = [head.join(";")].concat(list.map(t => [t.ts, t.bot_name, t.symbol, t.side, t.qty, t.price, t.value.toFixed(2),
      t.pnl == null ? "" : t.pnl.toFixed(2), t.pnl_pct == null ? "" : t.pnl_pct.toFixed(2), t.reason || ""]
      .map(v => `"${String(v).replace(/"/g, '""')}"`).join(";")));
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob(["﻿" + lines.join("\n")], { type: "text/csv" }));
    a.download = "transakcje.csv"; a.click();
  };
  draw();
}

boot();

// ------------------------------------------------------------------ UCZENIE MASZYNOWE
const MLST = { lab: ["w laboratorium", "info"], training: ["uczy się", "info"], candidate: ["czeka na akceptację", "warn"], active: ["aktywny", "up"],
  rejected: ["odrzucony", "muted"], superseded: ["zastąpiony nowszym", "muted"], retired: ["wycofany", "muted"],
  error: ["błąd", "down"] };
const MLKIND = { lab: "nocny (laboratorium)", first: "pierwszy model", weekly: "douczanie tygodniowe", manual: "ręcznie", retry: "ponowna próba" };
const mlTag = s => `<span class="f-tag ${MLST[s]?.[1] || "muted"}">${esc(MLST[s]?.[0] || s)}</span>`;
const aucTone = a => a == null ? "down" : a >= 0.55 ? "up" : a >= 0.52 ? "warn" : "down";

function mlBotCard(b) {
  const m = b.ml;
  const state = m.active && !m.active_stale
    ? `<span class="f-tag up">model #${m.active.id} aktywny</span> <span class="muted small">od ${when(m.active.created_at)}</span>`
    : m.active_stale ? `<span class="f-tag warn">model #${m.active.id} nieaktualny (zmieniono SL/TP/interwał)</span>`
    : `<span class="f-tag warn">brak zatwierdzonego modelu</span>`;
  const effect = m.needs_model && (!m.active || m.active_stale)
    ? `<p class="note warn" style="margin:10px 0 0">Bez zatwierdzonego modelu bot nie otwiera nowych pozycji. Pierwszy model uczy się automatycznie
       (kilka minut po zapisaniu bota) — zatwierdzisz go w zakładce ML.</p>` : "";
  return `<div class="card" style="margin-bottom:14px"><div class="card-head"><h2>Uczenie maszynowe</h2>
      <div class="actions"><a class="btn small" href="#/ml">Zakładka ML</a><button class="btn small" id="b-mltrain">Naucz teraz</button></div></div>
    <div>${state}</div>
    <p class="muted small" style="margin:8px 0 0">Wejście, gdy model ocenia szansę trafienia TP przed SL na co najmniej
      <b>${num(m.threshold, 0)}%</b> (próg opłacalności ${num(m.breakeven, 0)}% + margines).
      Douczanie: co niedzielę od 7:00, nowa wersja czeka na Twoją akceptację.</p>${effect}</div>`;
}

function mlQualityCard(q, title, prefix = "mlq") {
  const lift = q.hit_rate != null && q.base_rate != null ? q.hit_rate - q.base_rate : null;
  return `<div class="card" style="margin-top:14px"><div class="card-head"><h2>${esc(title)}</h2></div>
    <div class="grid metrics" style="margin-bottom:12px">
      ${metric("AUC poza próbą", q.auc == null ? "—" : num(q.auc, 2), aucTone(q.auc))}
      ${metric("Trafność powyżej progu", q.hit_rate == null ? "—" : pct(q.hit_rate, 0, false))}
      ${metric("Średnio (bez selekcji)", q.base_rate == null ? "—" : pct(q.base_rate, 0, false))}
      ${metric("Przewaga", lift == null ? "—" : num(lift, 1) + " pp", tone(lift))}
      ${metric("Próg wejścia", pct(q.threshold, 0, false))}
      ${metric("Świec powyżej progu", q.coverage == null ? "—" : pct(q.coverage, 0, false))}
      ${q.blocked != null ? metric("Odrzucone sygnały", q.blocked) : ""}
      ${q.refits_ok != null ? metric("Uczenie krocząco", `${q.refits_ok}×`) : ""}
    </div>
    <p class="muted small" style="margin:0 0 12px">AUC 0,50 = rzut monetą; od ok. 0,55 model realnie odróżnia lepsze wejścia od gorszych.
      „Trafność” to średni wynik wejść powyżej progu w skali 0–100% (TP = 100%, SL = 0%, wyjście po czasie — proporcjonalnie).
      Wszystkie liczby pochodzą z okresów, których model nie widział podczas nauki.</p>
    <div class="grid two">
      <div><h2 class="small muted">Czy pewność modelu się sprawdza</h2><div class="chart-box small" id="${prefix}-cal"><canvas></canvas></div></div>
      <div><h2 class="small muted">Na co model patrzy najbardziej</h2><div class="chart-box small" id="${prefix}-imp"><canvas></canvas></div></div>
    </div></div>`;
}
function mlQualityCharts(q, prefix = "mlq") {
  const cal = q.calibration || [];
  if (cal.length) groupBarChart($(`#${prefix}-cal canvas`), cal.map(c => `${num(c.from * 100, 0)}–${num(c.to * 100, 0)}%`), [
    { label: "Przewidywana szansa", values: cal.map(c => c.predicted), color: css("--info") },
    { label: "Faktyczny wynik", values: cal.map(c => c.actual), color: css("--up") }], { suffix: "%" });
  else emptyChart($(`#${prefix}-cal`), "Za mało danych do porównania.");
  const imp = q.importance || [];
  if (imp.length) groupBarChart($(`#${prefix}-imp canvas`), imp.map(i => i.label),
    [{ label: "Wpływ", values: imp.map(i => i.value * 100), color: css("--accent") }], { horizontal: true });
  else emptyChart($(`#${prefix}-imp`), "Brak cech o wyraźnym wpływie — to też informacja: model niewiele wnosi.");
}

function mlCompareCell(v, digits = 1) { return v == null ? "—" : `<span class="${tone(v)}">${pct(v, digits)}</span>`; }

async function renderMl() {
  view.innerHTML = `<div class="page-head"><h1>Uczenie maszynowe</h1></div><div id="ml-body" class="stack"></div>`;
  const draw = async () => {
    const d = await api("GET", "/api/ml/models");
    const pending = d.models.filter(m => m.status === "candidate");
    const training = d.models.some(m => m.status === "training");
    const botRows = d.bots.map(b => {
      const busy = d.models.some(m => m.bot_id === b.id && m.status === "training");
      return `<tr><td><a href="#/bot/${b.id}">${esc(b.name)}</a></td><td>${esc(b.strategy_name)}${b.strategy !== "ml_model" ? " + ML" : ""}</td>
        <td>${statusPill(b.status)}</td>
        <td>${b.active ? `<a href="#/ml/${b.active.id}">#${b.active.id}</a>${b.active_stale ? ' <span class="f-tag warn">nieaktualny</span>' : ""}`
                       : `<span class="${b.needs_model ? "warn" : "muted"}">brak${b.needs_model ? " — wejścia wstrzymane" : ""}</span>`}</td>
        <td class="num">${num(b.threshold, 0)}%</td>
        <td class="num"><button class="btn small" data-train="${b.id}" ${busy ? "disabled" : ""}>${busy ? "uczy się…" : "Naucz teraz"}</button></td></tr>`;
    }).join("");
    const pendingHtml = pending.map(m => {
      const s = m.summary || {}, e = s.eval || {}, q = s.quality || {};
      const diff = e.base ? (e.ml?.total ?? 0) - (e.base?.total ?? 0) : null;
      return `<div class="finding ${s.passed ? "good" : "warn"}">
        <div style="display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap">
          <b>${esc(m.bot_name)} — model #${m.id}</b><span class="muted small">${when(m.created_at)} · ${esc(MLKIND[m.kind] || m.kind)}</span></div>
        <div class="small" style="margin:6px 0">Test w czasie (${esc(e.start)} → ${esc(e.end)}): z ML ${mlCompareCell(e.ml?.total)}
          ${e.base ? ` vs bez ML ${mlCompareCell(e.base.total)} (różnica ${mlCompareCell(diff)})` : ""} · poza próbą ${mlCompareCell(e.ml?.oos_ret)}
          · AUC <span class="${aucTone(q.auc)}">${q.auc == null ? "—" : num(q.auc, 2)}</span></div>
        <div class="actions" style="display:flex;gap:8px;flex-wrap:wrap">
          <a class="btn small" href="#/ml/${m.id}">Szczegóły</a>
          <button class="btn small primary" data-approve="${m.id}">Zatwierdź</button>
          <button class="btn small" data-reject="${m.id}">Odrzuć</button></div></div>`;
    }).join("");
    const hist = d.models.map(m => {
      const s = m.summary || {}, e = s.eval || {}, q = s.quality || {};
      return `<tr><td><a href="#/ml/${m.id}">#${m.id}</a></td><td>${esc(m.bot_name || "")}</td><td class="small">${when(m.created_at)}</td>
        <td class="small">${esc(MLKIND[m.kind] || m.kind || "")}</td>
        <td>${mlTag(m.status)}${m.status === "training" ? ` <span class="muted small">${Math.round((m.progress || 0) * 100)}%</span>` : ""}</td>
        <td class="num ${aucTone(q.auc)}">${q.auc == null ? "—" : num(q.auc, 2)}</td>
        <td class="num">${mlCompareCell(e.ml?.total)}</td><td class="num">${e.base ? mlCompareCell(e.base.total) : "—"}</td>
        <td class="small muted">${m.status === "error" ? esc(m.error || "") : s.passed === false ? esc((s.reasons || []).join("; ")) : ""}</td></tr>`;
    }).join("");
    $("#ml-body").innerHTML = `
      <div class="note">Model uczy się na historii, które wejścia z Twoim stop-lossem i take-profitem kończyły się zyskiem.
        Nowa wersja najpierw przechodzi <b>test w czasie</b> na ostatnich miesiącach (uczy się krocząco, tylko na przeszłości — tak jak w backteście)
        i dopiero wtedy trafia tu do akceptacji. Aktywny model zmienia się wyłącznie po Twoim kliknięciu „Zatwierdź”.</div>
      ${pending.length ? `<div class="card"><h2>Czekają na Twoją decyzję (${pending.length})</h2><div class="findings">${pendingHtml}</div></div>` : ""}
      <div class="card"><h2>Boty korzystające z ML</h2>${d.bots.length ? `<div class="table-wrap"><table>
        <thead><tr><th>Bot</th><th>Strategia</th><th>Status</th><th>Aktywny model</th><th class="num">Próg wejścia</th><th></th></tr></thead>
        <tbody>${botRows}</tbody></table></div>`
        : `<div class="empty">Żaden bot nie używa jeszcze ML. Włącz „Filtr ML” w ustawieniach bota (sekcja „Uczenie maszynowe”) albo utwórz bota
           z szablonu „Model ML – ETF sektorowe”. Najpierw sprawdź to w backteście — pokaże, czy model w ogóle coś wnosi.</div>`}</div>
      <div class="card"><h2>Historia modeli</h2>${d.models.length ? `<div class="table-wrap"><table>
        <thead><tr><th>#</th><th>Bot</th><th>Utworzony</th><th>Powód</th><th>Status</th><th class="num">AUC</th>
          <th class="num">Z ML</th><th class="num">Bez ML</th><th>Uwagi</th></tr></thead><tbody>${hist}</tbody></table></div>`
        : `<div class="empty">Brak modeli.</div>`}</div>`;
    $$("[data-train]").forEach(b => b.onclick = async () => {
      try { await api("POST", `/api/bots/${b.dataset.train}/ml/train`); toast("Uczenie rozpoczęte"); draw(); mlBadge(); }
      catch (e) { toast(e.message, true); } });
    $$("[data-approve]").forEach(b => b.onclick = () => mlDecide(b.dataset.approve, "approve", draw));
    $$("[data-reject]").forEach(b => b.onclick = () => mlDecide(b.dataset.reject, "reject", draw));
    return training;
  };
  await draw();
  every(10000, () => draw().catch(() => {}));
}

async function mlDecide(id, action, after, msg) {
  if (msg && !confirmBox(msg)) return;
  try {
    await api("POST", `/api/ml/models/${id}/${action}`);
    toast(action === "approve" ? "Model zatwierdzony — bot użyje go od następnego cyklu" : "Gotowe");
    mlBadge();
    after();
  } catch (e) { toast(e.message, true); }
}

async function renderMlModel(id) {
  const m = await api("GET", `/api/ml/models/${id}`);
  const s = m.summary || {}, e = s.eval || {}, q = s.quality || {}, t = s.train || {}, pr = s.params || {};
  const rows = [["Zwrot w całym teście", "total"], ["W próbie (pierwsze 70%)", "is_ret"], ["Poza próbą (ostatnie 30%)", "oos_ret"],
    ["Max obsunięcie", "dd"], ["Transakcje", "trades"], ["Skuteczność", "win_rate"], ["Sharpe", "sharpe"]];
  const cell = (k, v) => v == null ? "—" : k === "trades" ? v : k === "sharpe" ? num(v) : k === "win_rate" ? pct(v, 0, false) : mlCompareCell(v);
  const cmp = e.ml ? `<div class="table-wrap"><table><thead><tr><th></th><th class="num">Z modelem ML</th>
      ${e.base ? `<th class="num">Bez ML</th><th class="num">Różnica</th>` : ""}</tr></thead><tbody>
      ${rows.map(([l, k]) => `<tr><td>${l}</td><td class="num">${cell(k, e.ml[k])}</td>
        ${e.base ? `<td class="num">${cell(k, e.base[k])}</td><td class="num">${["trades", "win_rate", "sharpe"].includes(k) || e.ml[k] == null || e.base[k] == null
          ? "" : mlCompareCell(e.ml[k] - e.base[k])}</td>` : ""}</tr>`).join("")}</tbody></table></div>` : "";
  const wins = (e.windows || []).map((w, i) => `<tr><td class="small">${esc(w.start)} → ${esc(w.end)}</td>
      <td class="num">${mlCompareCell(e.ml?.windows?.[i])}</td>${e.base ? `<td class="num">${mlCompareCell(e.base.windows?.[i])}</td>` : ""}
      <td>${e.ml?.windows_better_flags?.[i] ? '<span class="up">✓</span>' : '<span class="muted">✗</span>'}</td></tr>`).join("");
  const canApprove = m.status !== "active" && m.status !== "training" && m.status !== "error";
  view.innerHTML = `
    <div class="page-head"><div><a href="#/ml" class="muted small" style="text-decoration:none">← ML</a>
      <h1 style="display:flex;gap:10px;align-items:center;margin-top:4px">Model #${m.id} ${mlTag(m.status)}</h1>
      <div class="muted small" style="margin-top:4px"><a href="#/bot/${m.bot_id}">${esc(m.bot_name || "")}</a> · ${when(m.created_at)} ·
        ${esc(MLKIND[m.kind] || m.kind || "")}${m.decided_at ? ` · decyzja ${when(m.decided_at)}` : ""}</div></div>
      <div class="actions">
        ${m.status === "candidate" ? `<button class="btn primary" id="ml-ok">Zatwierdź</button><button class="btn" id="ml-no">Odrzuć</button>` : ""}
        ${canApprove && m.status !== "candidate" ? `<button class="btn" id="ml-ok">${s.passed ? "Przywróć ten model" : "Zatwierdź mimo to"}</button>` : ""}
        ${m.status === "active" ? `<button class="btn danger" id="ml-no">Wyłącz model</button>` : ""}
      </div></div>
    ${m.status === "training" ? `<p class="note">Model się uczy (${Math.round((m.progress || 0) * 100)}%). Strona odświeży się sama.</p>` : ""}
    ${m.status === "error" ? `<p class="note warn">Błąd: ${esc(m.error || "")}</p>` : ""}
    ${m.summary ? `
      <div class="finding ${s.passed ? "good" : "bad"}" style="margin-bottom:14px"><b>${s.passed ? "Przeszedł test w czasie" : "Nie przeszedł testu w czasie"}</b>
        <div class="small" style="margin-top:4px">${s.passed
          ? (e.mode === "absolute" ? "Zyskowny w całym teście, poza próbą i w większości okresów." :
             "Lepszy niż bot bez ML w próbie, poza próbą i w większości okresów, bez większego obsunięcia; AUC powyżej 0,52.")
          : esc((s.reasons || []).join("; ")) + ". Możesz go zatwierdzić mimo to, ale test sugeruje, że nie poprawi wyników."}</div></div>
      <div class="grid two">
        <div class="card"><h2>Test w czasie: ${esc(e.start)} → ${esc(e.end)}</h2>${cmp}
          <p class="muted small" style="margin:10px 0 0">Poza próbą od ${esc(e.oos_start)}. W teście model uczył się krocząco ${s.refits}× — każdy
          odcinek oceniał model, który nie widział jego danych.</p></div>
        <div class="card"><h2>Okresy</h2><div class="table-wrap"><table><thead><tr><th>Okres</th><th class="num">Z ML</th>
          ${e.base ? `<th class="num">Bez ML</th>` : ""}<th>${e.base ? "Lepiej" : "Zysk"}</th></tr></thead><tbody>${wins}</tbody></table></div></div>
      </div>
      ${mlQualityCard({ ...q, importance: s.importance }, "Jakość modelu", "mlm")}
      <div class="card" style="margin-top:14px"><h2>Na czym się uczył</h2>
        <div class="grid metrics">
          ${metric("Okno uczenia", `${esc(t.from)} → ${esc(t.to)}`)}
          ${metric("Przykłady", t.rows)}
          ${metric("Symbole", (t.symbols || []).length)}
          ${metric("Interwał / horyzont", `${esc(pr.timeframe)} / ${pr.horizon} św.`)}
          ${metric("SL / TP", `${pct(pr.stop_loss_pct * 100, 1, false)} / ${pct(pr.take_profit_pct * 100, 1, false)}`)}
          ${metric("Próg opłacalności", pct(pr.breakeven, 0, false))}
          ${metric("Próg wejścia", pct(pr.threshold, 0, false))}
        </div>
        <p class="muted small" style="margin:10px 0 0">Symbole: ${esc((t.symbols || []).join(", "))}. Zmiana SL, TP, interwału albo horyzontu w bocie
          wymaga nowego modelu (uczy się sam w ciągu kilku minut).</p></div>` : ""}`;
  if (m.summary) mlQualityCharts({ ...q, importance: s.importance }, "mlm");
  const again = () => route();
  $("#ml-ok") && ($("#ml-ok").onclick = () => mlDecide(id, "approve", again,
    m.status === "candidate" || s.passed ? null : "Ten model nie przeszedł testu w czasie. Zatwierdzić go mimo to?"));
  $("#ml-no") && ($("#ml-no").onclick = () => mlDecide(id, "reject", again,
    m.status === "active" ? "Wyłączyć model? Bot z filtrem ML albo strategią ML wstrzyma nowe wejścia do czasu zatwierdzenia innego modelu." : null));
  if (m.status === "training") every(5000, async () => {
    const x = await api("GET", `/api/ml/models/${id}`);
    if (x.status !== "training") route();
  });
}


// ------------------------------------------------------------------ KONSTRUKTOR ZASAD
function ruleText(r) {
  const spec = SCHEMA.rules.find(x => x.id === r.id);
  if (!spec) return r.id;
  const vals = {};
  spec.params.forEach(p => { const v = r[p.key] ?? p.default; vals[p.key] = p.type === "pct" ? `${+(v * 100).toFixed(1)}%` : `${v}`; });
  return spec.fmt.replace(/\{(\w+)\}/g, (_, k) => vals[k] ?? "");
}
function rulesSummary(p) {
  if (!p.entry_rules) return "";
  const join = p.entry_mode === "any" ? " lub " : " i ";
  return `Kupno: ${p.entry_rules.map(ruleText).join(join) || "—"}. Sprzedaż: ${p.exit_rules.map(ruleText).join(" lub ") || "tylko SL / TP"}.`;
}
function drawRules(box) {
  const side = box.dataset.side;
  const cat = SCHEMA.rules.filter(r => r.side === side);
  const list = JSON.parse(box.dataset.value || "[]");
  const save = () => { box.dataset.value = JSON.stringify(list); box.dispatchEvent(new Event("input", { bubbles: true })); };
  const groups = SCHEMA.rule_groups;
  box.innerHTML = list.map((r, i) => {
    const spec = cat.find(c => c.id === r.id);
    if (!spec) return "";
    const inputs = spec.params.map(p => {
      const v = r[p.key] ?? p.default;
      return `<label class="rp"><span>${esc(p.label)}</span>${p.type === "pct"
        ? `<div class="suffix"><input type="number" step="any" data-i="${i}" data-rp="${p.key}" data-t="pct" value="${+(v * 100).toFixed(4)}"><span>%</span></div>`
        : `<input type="number" step="${p.type === "int" ? 1 : "any"}" data-i="${i}" data-rp="${p.key}" data-t="${p.type}" value="${v}">`}</label>`;
    }).join("");
    return `<div class="rule-row"><div class="rule-head"><span class="f-tag info">${esc(groups[spec.group] || spec.group)}</span>
        <b>${esc(spec.label)}</b><button type="button" class="btn ghost small" data-del="${i}" title="Usuń zasadę">✕</button></div>
      ${spec.help ? `<div class="help">${esc(spec.help)}</div>` : ""}
      ${inputs ? `<div class="rule-params">${inputs}</div>` : ""}</div>`;
  }).join("") + `<select class="rule-add"><option value="">+ dodaj zasadę ${side === "entry" ? "kupna" : "sprzedaży"}…</option>
      ${Object.entries(groups).map(([g, gl]) => {
        const items = cat.filter(c => c.group === g);
        return items.length ? `<optgroup label="${esc(gl)}">${items.map(c => `<option value="${c.id}">${esc(c.label)}</option>`).join("")}</optgroup>` : "";
      }).join("")}</select>
    ${!list.length ? `<div class="help">${side === "entry" ? "Dodaj co najmniej jedną zasadę kupna." : "Brak — pozycję zamknie SL, TP, stop kroczący albo limit czasu."}</div>` : ""}`;
  $$("[data-rp]", box).forEach(el => el.addEventListener("input", () => {
    const i = +el.dataset.i, v = el.value === "" ? null : +el.value;
    if (v == null || isNaN(v)) return;
    list[i][el.dataset.rp] = el.dataset.t === "pct" ? v / 100 : el.dataset.t === "int" ? Math.round(v) : v;
    save();
  }));
  $$("[data-del]", box).forEach(b => b.onclick = () => { list.splice(+b.dataset.del, 1); save(); drawRules(box); });
  $(".rule-add", box).onchange = e => {
    const spec = cat.find(c => c.id === e.target.value);
    if (!spec) return;
    const r = { id: spec.id };
    spec.params.forEach(p => r[p.key] = p.default);
    list.push(r); save(); drawRules(box);
  };
}


// ------------------------------------------------------------------ RADAR ALTCOINÓW
let radarSrc = null;
async function renderRadar() {
  view.innerHTML = `<div class="page-head"><h1>Radar</h1><div class="actions" id="rd-actions"></div></div>${radarTabs("crypto")}
    <div id="rd-body" class="stack"><div class="empty">Ładowanie…</div></div>`;
  const draw = async () => {
    const d = await api("GET", `/api/radar?provider=${radarSrc || "kraken"}`);
    radarSrc = d.provider;
    const running = d.job?.state === "running";
    $("#rd-actions").innerHTML = `<select id="rd-src">${d.providers.map(p =>
        `<option value="${p.name}" ${p.name === d.provider ? "selected" : ""}>${esc(p.label)} (/${esc(p.quote)})</option>`).join("")}</select>
      <button class="btn primary" id="rd-scan" ${running ? "disabled" : ""}>${running ? "Skanuję…" : "Skanuj teraz"}</button>`;
    $("#rd-src").onchange = e => { radarSrc = e.target.value; draw(); };
    $("#rd-scan").onclick = async () => { await api("POST", `/api/radar/scan?provider=${d.provider}`); toast("Skan rozpoczęty — potrwa 1–3 min"); draw(); };
    const sc = d.scan;
    const rows = sc ? sc.items.map((r, i) => `<tr class="click" data-coin="${esc(r.symbol)}"><td class="num muted">${i + 1}</td><td><b>${esc(r.symbol)}</b></td>
        <td class="num"><b>${num(r.score, 0)}</b></td>${trendCells(r.outlook)}
        <td class="num ${tone(r.rel30)}">${pct(r.rel30, 1)}</td><td class="num ${tone(r.ret30)}">${pct(r.ret30, 1)}</td>
        <td class="num ${tone(r.ret7)}">${pct(r.ret7, 1)}</td>
        <td class="num ${r.vol_ratio >= 1.2 ? "up" : ""}">${num(r.vol_ratio, 2)}×</td>
        <td>${r.above_sma50 == null ? "—" : r.above_sma50 ? '<span class="up">nad</span>' : '<span class="down">pod</span>'}</td>
        <td class="num">${num(r.volatility, 0)}%</td><td class="num muted">${num(r.volume24h / 1000, 0)} tys.</td></tr>`).join("") : "";
    $("#rd-body").innerHTML = `
      <div class="note">Ranking najpłynniejszych monet giełdy (bez stablecoinów). Wynik 0–100 = 40% siła względem BTC z 30 dni + 30% momentum z 7 dni
        + 20% wzrost obrotu + 10% cena nad średnią 50-dniową. Bot z włączonym radarem handluje N pierwszymi pozycjami z tej listy.
        To lista kandydatów, a nie rekomendacja: o wejściu nadal decydują zasady strategii i ryzyko.</div>
      ${d.job?.state === "error" ? `<p class="note warn">Skan nieudany: ${esc(d.job.error)}</p>` : ""}
      ${sc?.market ? marketCard(sc) : ""}
      <div id="rd-coin"></div>
      <div class="card"><div class="card-head"><h2>Ranking ${sc ? `<span class="muted small">— ${when(sc.ts)}, ${sc.candidates} z ${sc.universe} par /${esc(sc.quote)}</span>` : ""}</h2></div>
        ${sc ? `<div class="table-wrap"><table><thead><tr><th>#</th><th>Moneta</th><th class="num">Wynik</th><th class="num">vs BTC 30 d</th>
          <th>Trend</th><th class="num" title="Jak często i o ile moneta rosła w ciągu 30 dni w przeszłości, gdy była w takim samym trendzie jak dziś">Po 30 dniach (historycznie)</th>
          <th class="num">30 dni</th><th class="num">7 dni</th><th class="num">Obrót 7/30 d</th><th>SMA 50</th><th class="num">Zmienność r/r</th>
          <th class="num">Obrót 24 h</th></tr></thead><tbody>${rows}</tbody></table></div>
          ${sc.skipped?.length ? `<p class="muted small">Za krótka historia (poniżej 35 dni), pominięte: ${esc(sc.skipped.join(", "))}</p>` : ""}`
          : `<div class="empty">Brak skanu dla tego źródła. Kliknij „Skanuj teraz”.</div>`}</div>
      <div class="card"><h2>Nowe pary na giełdzie <span class="muted small">(ostatnie 60 dni, od pierwszego skanu)</span></h2>
        ${d.new.length ? `<table><tbody>${d.new.map(n => `<tr><td><b>${esc(n.symbol)}</b></td><td class="muted small">pierwszy raz widziana ${when(n.first_seen)}</td></tr>`).join("")}</tbody></table>`
          : `<div class="empty">Na razie brak. Pierwszy skan zapisuje stan wyjściowy; każda para, która pojawi się później, trafi tutaj.
             Nowe monety mają krótką historię i bywają bardzo ryzykowne — radar dopuści je do handlu dopiero po 35 dniach notowań.</div>`}</div>`;
    $$("[data-coin]").forEach(tr => tr.onclick = () => showCoin(d.provider, tr.dataset.coin));
    return running;
  };
  await draw();
  every(5000, async () => { if ($("#rd-scan")?.disabled) await draw().catch(() => {}); });
}

const TREND = { 1: ["↑ wzrostowy", "up"], 0: ["→ boczny", "muted"], "-1": ["↓ spadkowy", "down"] };
function trendCells(o) {
  if (!o) return `<td class="muted small">za krótka historia</td><td></td>`;
  const t = TREND[o.trend];
  const weak = o.n30 < 30;
  return `<td><span class="${t[1]}"><b>${t[0]}</b></span><div class="muted small">${o.days_in_trend} dni · ${pct(o.slope30, 0)}/30 d</div></td>
    <td class="num ${weak ? "muted" : ""}">${o.p_up30 == null ? "—" : `<span class="${o.p_up30 >= 55 ? "up" : o.p_up30 <= 45 ? "down" : ""}">${num(o.p_up30, 0)}% wzrostów</span>`}
      <div class="small ${tone(o.med30)}">mediana ${o.med30 == null ? "—" : pct(o.med30, 1)} · zakres ±${num(o.range30, 0)}%</div>
      ${weak ? `<div class="small muted">mało danych (${o.n30} dni)</div>` : ""}</td>`;
}
function marketCard(sc) {
  const m = sc.market, total = m.up + m.side + m.down || 1, b = m.btc;
  const mood = m.up / total >= 0.6 ? ["Rynek w trendzie wzrostowym", "up"] : m.down / total >= 0.6 ? ["Rynek w trendzie spadkowym", "down"]
    : ["Rynek mieszany", "warn"];
  return `<div class="card"><div class="card-head"><h2 class="${mood[1]}" style="margin:0">${mood[0]}</h2>
      <span class="muted small">z ${total} ${m.bench ? "spółek" : "monet"} rankingu</span></div>
    <div class="grid metrics" style="margin-top:10px">
      ${metric("Trend wzrostowy", `<span class="up">${m.up}</span>`)}${metric("Boczny", m.side)}${metric("Spadkowy", `<span class="down">${m.down}</span>`)}
      ${b ? metric(esc(m.bench || `BTC/${sc.quote}`), `<span class="${TREND[b.trend][1]}">${TREND[b.trend][0]}</span>`) : ""}
      ${b && b.p_up30 != null ? metric(`${esc(m.bench ? "Indeks" : "BTC")} po 30 dniach (hist.)`, `${num(b.p_up30, 0)}% wzrostów`) : ""}
      ${b && b.med30 != null ? metric(`${esc(m.bench ? "Indeks" : "BTC")} mediana 30 dni`, `<span class="${tone(b.med30)}">${pct(b.med30, 1)}</span>`) : ""}
    </div>
    <p class="muted small" style="margin:10px 0 0">„Po 30 dniach (historycznie)” to nie przepowiednia, tylko statystyka: jak często i o ile moneta rosła w ciągu
      kolejnych 30 dni w przeszłości, gdy była w takim samym trendzie jak dziś (z ok. 2 lat notowań). Porównaj z „bez względu na trend” w szczegółach —
      jeśli liczby są podobne, trend niewiele mówi o przyszłości. Kliknij monetę, żeby zobaczyć wykres z lejkiem możliwych cen.</p></div>`;
}
async function showCoin(provider, symbol) {
  const box = $("#rd-coin");
  box.innerHTML = `<div class="card"><div class="empty">Ładuję ${esc(symbol)}…</div></div>`;
  box.scrollIntoView({ behavior: "smooth", block: "start" });
  let d;
  try { d = await api("GET", `/api/radar/coin?provider=${provider}&symbol=${encodeURIComponent(symbol)}`); }
  catch (e) { box.innerHTML = `<div class="card err">${esc(e.message)}</div>`; return; }
  const o = d.outlook || {}, t = TREND[o.trend] || ["—", "muted"];
  const last = d.price[d.price.length - 1][1], c = d.cone[d.cone.length - 1];
  box.innerHTML = `<div class="card"><div class="card-head"><h2 style="margin:0">${esc(symbol)} <span class="${t[1]}">${t[0]}</span>
      <span class="muted small">od ${o.days_in_trend ?? "?"} dni, nachylenie ${pct(o.slope30, 1)} na 30 dni</span></h2>
      <button class="btn small ghost" id="rd-close">Zamknij</button></div>
    <div class="grid metrics" style="margin:10px 0">
      ${metric("Po 7 dniach: wzrosty", o.p_up7 == null ? "—" : `${num(o.p_up7, 0)}% <span class="muted small">(bez względu na trend ${num(o.base_up7, 0)}%)</span>`)}
      ${metric("Po 7 dniach: mediana", `<span class="${tone(o.med7)}">${pct(o.med7, 1)}</span> <span class="muted small">±${num(o.range7, 0)}%</span>`)}
      ${metric("Po 30 dniach: wzrosty", o.p_up30 == null ? "—" : `${num(o.p_up30, 0)}% <span class="muted small">(bez względu na trend ${num(o.base_up30, 0)}%)</span>`)}
      ${metric("Po 30 dniach: mediana", `<span class="${tone(o.med30)}">${pct(o.med30, 1)}</span> <span class="muted small">±${num(o.range30, 0)}%</span>`)}
      ${metric("Zakres za 30 dni (≈68%)", `${price(c[2])} – ${price(c[3])}`)}
      ${metric("Dni w historii", `${o.n30 ?? 0} <span class="muted small">z ${o.history_days ?? "?"}</span>`)}
    </div>
    <div class="chart-box"><canvas id="rd-chart"></canvas></div>
    <p class="muted small" style="margin:8px 0 0">Lejek: środek = mediana z historii w tym trendzie (gdy jest min. 30 dni danych, inaczej płasko),
      ciemniejsze pasmo = typowy ruch (1 odchylenie, ok. 2 na 3 przypadki), jaśniejsze = 2 odchylenia (ok. 19 na 20).
      Liczone ze zmienności z 30 dni. Rynek krypto potrafi wyjść poza oba pasma — to mapa ryzyka, nie prognoza ceny.</p></div>`;
  $("#rd-close").onclick = () => { box.innerHTML = ""; };
  coneChart($("#rd-chart"), d, last);
}
function coneChart(canvas, d, last) {
  const grid = css("--line"), muted = css("--muted"), up = css("--up"), info = css("--info");
  const pts = a => a.filter(x => x[1] != null).map(([t, v]) => ({ x: new Date(t).getTime(), y: v }));
  const cone = i => d.cone.map(r => ({ x: new Date(r[0]).getTime(), y: r[i] }));
  const fillCol = a => `color-mix(in srgb, ${info} ${a}%, transparent)`;
  const ch = new Chart(canvas, {
    type: "line",
    data: { datasets: [
      { label: "Cena", data: pts(d.price), borderColor: css("--text"), borderWidth: 2, pointRadius: 0 },
      { label: "SMA 20", data: pts(d.sma20), borderColor: up, borderWidth: 1.2, pointRadius: 0 },
      { label: "SMA 50", data: pts(d.sma50), borderColor: css("--warn"), borderWidth: 1.2, pointRadius: 0 },
      { label: "2 odchylenia (dół)", data: cone(4), borderColor: "transparent", pointRadius: 0 },
      { label: "2 odchylenia", data: cone(5), borderColor: "transparent", backgroundColor: fillCol(12), fill: "-1", pointRadius: 0 },
      { label: "1 odchylenie (dół)", data: cone(2), borderColor: "transparent", pointRadius: 0 },
      { label: "1 odchylenie", data: cone(3), borderColor: "transparent", backgroundColor: fillCol(28), fill: "-1", pointRadius: 0 },
      { label: "Środek (mediana historyczna)", data: cone(1), borderColor: info, borderDash: [5, 4], borderWidth: 1.5, pointRadius: 0 },
    ] },
    options: { responsive: true, maintainAspectRatio: false, animation: false, parsing: false,
      interaction: { mode: "index", intersect: false },
      plugins: { legend: { labels: { color: muted, boxWidth: 12, filter: it => !it.text.includes("(dół)") } },
        tooltip: { callbacks: { title: it => new Date(it[0].parsed.x).toLocaleDateString("pl-PL"), label: it => ` ${it.dataset.label}: ${price(it.parsed.y)}` } } },
      scales: { x: { type: "linear", grid: { color: grid }, ticks: { color: muted, maxTicksLimit: 8,
          callback: v => new Date(v).toLocaleDateString("pl-PL", { day: "2-digit", month: "2-digit" }) } },
        y: { grid: { color: grid }, ticks: { color: muted, callback: v => price(v) } } } },
  });
  charts.push(ch);
}

// ------------------------------------------------------------------ LABORATORIUM
async function renderLab() {
  view.innerHTML = `<div class="page-head"><h1>Laboratorium wariantów</h1></div>
    <div class="card" id="lev-study" style="margin-bottom:14px"></div><div id="lab-body" class="stack"></div>`;
  await drawLevStudy();
  every(15000, () => { if (drawLevStudy.running) drawLevStudy(); });
  const draw = async () => {
    const d = await api("GET", "/api/lab");
    if (!d.bots.length) {
      $("#lab-body").innerHTML = `<div class="card empty">Żaden bot nie ma włączonego laboratorium. Zaznacz „Laboratorium: testuj warianty na żywo”
        w ustawieniach bota (sekcja „Laboratorium wariantów”).</div>`;
      return;
    }
    $("#lab-body").innerHTML = `<div class="note">Każdy wariant to kopia bota z jedną zmianą, grająca „na niby” na tych samych świecach na żywo.
      Pretendent wygrywa, gdy ma dość transakcji i dni testu, wynik lepszy od mistrza o ponad 1 pp i nie większe obsunięcie.
      Na koncie papierowym przejmuje bota sam, a na koncie z prawdziwymi pieniędzmi czeka na Twoje zatwierdzenie.</div>` +
      d.bots.map(b => {
        const pend = b.events.filter(e => e.kind === "proposal" && e.status === "pending");
        const champ = b.variants.find(v => v.kind === "champion");
        const rows = b.variants.map(v => `<tr class="${v.id === b.leader ? "lead" : ""}">
          <td>${v.kind === "champion" ? "👑 " : ""}${esc(v.label)}${v.id === b.leader ? ' <span class="f-tag up">wygrywa</span>' : ""}
            ${v.kind === "challenger_model" && !v.model_id ? '<div class="muted small">czeka na pierwszy nocny model (od 2:00)</div>' : ""}</td>
          <td class="num">${v.trades}${v.open ? `<span class="muted small"> +${v.open} otw.</span>` : ""}</td>
          <td class="num">${v.win_rate == null ? "—" : pct(v.win_rate, 0, false)}</td>
          <td class="num ${tone(v.return_pct)}">${pct(v.return_pct, 2)}</td>
          <td class="num ${champ && v !== champ ? tone(v.return_pct - champ.return_pct) : ""}">${champ && v !== champ ? pct(v.return_pct - champ.return_pct, 2) : "—"}</td>
          <td class="num down">${pct(v.max_dd_pct, 2)}</td><td class="num muted">${num(v.days, 1)}</td>
          <td class="small muted">${v.kind === "champion" ? "punkt odniesienia" : esc((v.why || []).join(", "))}</td></tr>`).join("");
        return `<div class="card"><div class="card-head"><h2><a href="#/bot/${b.id}">${esc(b.name)}</a> ${statusPill(b.status)}
            <span class="f-tag ${b.auto ? "info" : "warn"}">${b.auto ? "zwycięzca wdrażany automatycznie" : "wymaga Twojego zatwierdzenia"}</span></h2>
            <div class="actions"><button class="btn small" data-reset="${b.id}">Zacznij od nowa</button></div></div>
          ${pend.map(e => `<div class="finding good" style="margin-bottom:10px">${esc(e.text)}
            <div style="margin-top:8px;display:flex;gap:8px"><button class="btn small primary" data-ok="${e.id}">Zatwierdź</button>
            <button class="btn small" data-no="${e.id}">Odrzuć</button></div></div>`).join("")}
          ${b.status !== "running" ? `<p class="note warn">Bot nie działa — laboratorium stoi razem z nim.</p>` : ""}
          <div class="table-wrap"><table><thead><tr><th>Wariant</th><th class="num">Transakcje</th><th class="num">Skuteczność</th>
            <th class="num">Wynik</th><th class="num">vs mistrz</th><th class="num">Max obsun.</th><th class="num">Dni</th><th>Do wygranej brakuje</th></tr></thead>
            <tbody>${rows}</tbody></table></div>
          <p class="muted small">Próg wygranej: min. ${b.min_trades} transakcji i ${b.min_days} dni. Wynik = wirtualny kapitał (każda transakcja
            to 1/max pozycji kapitału), po kosztach.</p>
          <div class="chart-box small" id="lab-ch-${b.id}"><canvas></canvas></div>
          ${b.events.length ? `<details style="margin-top:10px"><summary class="muted small">Historia laboratorium (${b.events.length})</summary>
            <table><tbody>${b.events.map(e => `<tr><td class="small muted">${when(e.ts)}</td><td class="small">${esc(e.text)}</td>
            <td class="small muted">${esc({ pending: "czeka", applied: "wdrożono", rejected: "odrzucono", info: "" }[e.status] ?? e.status)}</td></tr>`).join("")}</tbody></table></details>` : ""}
        </div>`;
      }).join("");
    d.bots.forEach(b => {
      const sets = b.variants.filter(v => v.curve.length).map(v => ({ label: v.label, points: v.curve,
        dashed: v.kind !== "champion", color: v.kind === "champion" ? css("--text") : undefined }));
      if (sets.length) lineChart($(`#lab-ch-${b.id} canvas`), sets, { money: false });
      else emptyChart($(`#lab-ch-${b.id}`), "Wykres wyników pojawi się po pierwszych zamkniętych transakcjach wariantów.");
    });
    $$("[data-ok]").forEach(x => x.onclick = async () => {
      if (!confirmBox("Zwycięzca przejmie bota (zmiana ustawień = krótki restart bota). Kontynuować?")) return;
      try { const r = await api("POST", `/api/lab/events/${x.dataset.ok}/approve`); toast(r.text || "Wdrożono"); mlBadge(); draw(); }
      catch (e) { toast(e.message, true); } });
    $$("[data-no]").forEach(x => x.onclick = async () => { await api("POST", `/api/lab/events/${x.dataset.no}/reject`); mlBadge(); draw(); });
    $$("[data-reset]").forEach(x => x.onclick = async () => {
      if (!confirmBox("Wyzerować wyniki wariantów i zacząć porównanie od nowa?")) return;
      await api("POST", `/api/lab/${x.dataset.reset}/reset`); toast("Laboratorium zacznie od nowa w następnym cyklu bota"); draw(); });
  };
  await draw();
  every(60000, () => { cleanupCharts(); draw().catch(() => {}); });
}
function cleanupCharts() { charts.forEach(c => c.destroy()); charts = []; }

// ------------------------------------------------------------------ KONTA I PLATFORMY
async function renderAccounts() {
  const d = await api("GET", "/api/accounts");
  const modeTag = a => a.mode === "live" ? '<span class="f-tag warn">prawdziwe pieniądze</span>'
    : a.mode === "testnet" ? '<span class="f-tag info">sieć testowa</span>' : '<span class="f-tag info">na niby</span>';
  const cards = d.accounts.map(a => `
    <div class="card acc-card">
      <div class="acc-top">
        <div><div class="acc-name">${esc(a.name)}</div><div class="muted small">${esc(a.platform)} · ${esc(a.currency)}</div></div>
        ${modeTag(a)}
      </div>
      <dl class="acc-meta">
        <dt>Klucz API</dt><dd class="mono">${a.key ? esc(a.key) : "—"}</dd>
        <dt>Zapisane w</dt><dd>${a.source === "panel" ? "panelu (zaszyfrowane)" : "pliku .env"}</dd>
        <dt>Boty</dt><dd>${a.bots.length ? esc(a.bots.join(", ")) : '<span class="muted">brak</span>'}</dd>
      </dl>
      <div class="acc-actions">
        <button class="btn small" data-hold="${esc(a.name)}">Portfel</button>
        <button class="btn small" data-test="${esc(a.name)}">Sprawdź połączenie</button>
        ${a.source === "panel" ? `<button class="btn small" data-edit="${esc(a.name)}">Zmień</button>
          <button class="btn small danger" data-del="${esc(a.name)}" ${a.bots.length ? "disabled title='Najpierw usuń boty tego konta'" : ""}>Usuń</button>` : ""}
      </div>
      <div class="acc-result small" data-res="${esc(a.name)}"></div>
    </div>`).join("");
  view.innerHTML = `
    <div class="page-head"><div><h1>Konta i platformy</h1>
      <div class="muted small" style="margin-top:4px">Konta brokerów i giełd, z których korzystają boty.</div></div>
      <div class="actions"><button class="btn primary" id="acc-add">+ Podłącz platformę</button></div></div>
    <div class="acc-grid">${cards || '<div class="card empty">Brak kont — podłącz pierwszą platformę.</div>'}</div>
    <div id="acc-form"></div>
    <p class="note" style="margin-top:16px">Klucze API są w bazie zaszyfrowane (klucz szyfrujący: ${esc(d.master_key)} — kopiuj go razem z bazą).
      Panel nigdy nie pokazuje kluczy z powrotem. Dodanie, zmiana i usunięcie konta wymagają hasła do panelu.
      Konta z pliku <span class="mono">.env</span> zmieniasz w pliku.</p>`;
  $("#acc-add").onclick = () => accountForm(d);
  $$("[data-hold]").forEach(b => b.onclick = () => holdingsModal(b.dataset.hold));
  $$("[data-test]").forEach(b => b.onclick = async () => {
    const out = $(`[data-res="${b.dataset.test}"]`);
    b.disabled = true; out.className = "acc-result small muted"; out.textContent = "Łączę…";
    try { const r = await api("POST", "/api/accounts/test", { name: b.dataset.test, values: {} });
      out.className = "acc-result small up";
      out.textContent = `✓ ${r.mode}${r.equity != null ? ` · kapitał ${num(r.equity)} ${r.currency}` : ""}`;
    } catch (e) { out.className = "acc-result small down"; out.textContent = e.message; }
    b.disabled = false;
  });
  $$("[data-edit]").forEach(b => b.onclick = () => accountForm(d, d.accounts.find(x => x.name === b.dataset.edit)));
  $$("[data-del]").forEach(b => b.onclick = async () => {
    const pw = prompt(`Usunąć konto „${b.dataset.del}”? Podaj hasło do panelu:`);
    if (!pw) return;
    const res = await fetch(`/api/accounts/${encodeURIComponent(b.dataset.del)}`, { method: "DELETE",
      headers: { "X-Confirm-Password": pw }, credentials: "same-origin" });
    const r = await res.json().catch(() => ({}));
    if (!res.ok) return toast(r.detail || "Błąd", true);
    toast("Usunięto konto"); SCHEMA = await api("GET", "/api/schema"); renderAccounts();
  });
}

function accountForm(d, existing = null) {
  const box = $("#acc-form");
  const platforms = d.platforms, modes = d.modes;
  let type = existing ? existing.type : null;
  const pick = () => {
    const ok = platforms.filter(p => p.available), no = platforms.filter(p => !p.available);
    box.innerHTML = `<div class="card acc-form">
      <div class="card-head"><h2>Podłącz platformę</h2><button class="btn ghost small" id="af-cancel">Zamknij</button></div>
      <div class="pl-grid">${ok.map(p => `<button class="pl-tile" data-pl="${p.type}">
        <span class="pl-name">${esc(p.label)}</span>
        <span class="pl-short">${esc(p.short)}</span>
        <span class="pl-tags">${p.tags.map(t => `<span class="chip">${esc(t)}</span>`).join("")}</span></button>`).join("")}</div>
      ${no.length ? `<p class="small muted" style="margin:12px 0 0">Niedostępne: ${no.map(p => `<b>${esc(p.label)}</b> — ${esc(p.short)}`).join("; ")}</p>` : ""}
    </div>`;
    $("#af-cancel").onclick = () => { box.innerHTML = ""; };
    $$("[data-pl]", box).forEach(b => b.onclick = () => { type = b.dataset.pl; form(); });
  };
  const form = () => {
    const p = platforms.find(x => x.type === type);
    const ex = existing ? existing.extra : {};
    const modeF = p.fields.find(f => f.type === "mode");
    let mode = existing ? existing.mode : (modeF ? modeF.default : "paper");
    const field = f => {
      const id = `af-${f.key}`, when = f.when ? ` data-when="${f.when.join(" ")}"` : "";
      let val = ex[f.key] ?? f.default ?? "";
      const hint = f.hint ? `<div class="help">${esc(f.hint)}</div>` : "";
      if (f.type === "choice") {
        const other = f.other && val && !f.options.includes(val);
        return `<div class="field"${when}><label for="${id}">${esc(f.label)}</label><select id="${id}" data-f="${f.key}">
          ${f.options.map(o => `<option value="${esc(o)}" ${o === val ? "selected" : ""}>${esc((f.labels || {})[o] || o)}</option>`).join("")}
          ${f.other ? `<option value="__other" ${other ? "selected" : ""}>Inna giełda…</option>` : ""}</select>${hint}</div>`;
      }
      if (f.advanced) {
        const show = p.fields.some(g => g.other === f.key && ex[g.key] && !g.options.includes(ex[g.key]));
        return `<div class="field${show ? "" : " hidden"}" data-adv="${f.key}"><label for="${id}">${esc(f.label)}</label>
          <input id="${id}" data-f="${f.key}" value="${esc(show ? ex.exchange : "")}" autocomplete="off" spellcheck="false">${hint}</div>`;
      }
      if (f.type === "secret") return `<div class="field"${when}><label for="${id}">${esc(f.label)}</label>
        <input id="${id}" data-f="${f.key}" type="password" autocomplete="off" spellcheck="false"
          placeholder="${existing ? "bez zmian" : ""}">${hint}</div>`;
      return `<div class="field"${when}><label for="${id}">${esc(f.label)}</label><input id="${id}" data-f="${f.key}"
        ${f.type === "int" || f.type === "float" ? `type="number" step="any" data-t="num"` : ""} value="${esc(val)}">${hint}</div>`;
    };
    const settings = p.fields.filter(f => f.type !== "mode" && f.type !== "secret");
    const secrets = p.fields.filter(f => f.type === "secret");
    const defName = existing ? existing.name : `${type === "gielda" ? "binance" : type}_${mode === "live" ? "konto" : "niby"}`;
    box.innerHTML = `<div class="card acc-form">
      <div class="card-head"><h2>${existing ? `Zmień konto „${esc(existing.name)}”` : esc(p.label)}</h2>
        ${existing ? "" : '<button class="btn ghost small" id="af-back">← Inna platforma</button>'}</div>
      ${modeF ? `<div class="af-section"><div class="af-title">Tryb konta</div>
        <div class="mode-grid">${modeF.options.map(m => `<label class="mode-opt ${m === "live" ? "live" : ""}">
          <input type="radio" name="af-mode" value="${m}" ${m === mode ? "checked" : ""}>
          <span><b>${esc(modes[m][0])}</b><span class="small muted">${esc(modes[m][1])}</span></span></label>`).join("")}</div></div>` : ""}
      <div class="af-section"><div class="af-title">Ustawienia</div><div class="form-grid">
        ${existing ? "" : `<div class="field"><label for="af-name">Nazwa konta</label><input id="af-name" value="${esc(defName)}" autocomplete="off">
          <div class="help">Małe litery, cyfry i podkreślnik _.</div></div>`}
        ${settings.map(field).join("")}</div></div>
      ${secrets.length ? `<div class="af-section" id="af-keys"><div class="af-title">Klucze API</div>
        ${p.warn ? `<div class="note warn" style="margin-bottom:12px">${esc(p.warn)}</div>` : ""}
        <div class="form-grid">${secrets.map(field).join("")}</div></div>` : ""}
      <details class="af-steps" ${secrets.length ? "" : "open"}><summary>${secrets.length ? "Jak utworzyć klucz API?" : "Jak to działa?"}</summary>
        <ol>${p.steps.map(h => `<li>${esc(h)}</li>`).join("")}</ol></details>
      <div class="af-foot">
        <div class="field" style="max-width:260px"><label for="af-pw">Hasło do panelu</label>
          <input id="af-pw" type="password" autocomplete="current-password"><div class="help">Potrzebne do zapisania.</div></div>
        <div class="af-buttons">
          <button class="btn" id="af-test">Sprawdź połączenie</button>
          <button class="btn primary" id="af-save">${existing ? "Zapisz zmiany" : "Zapisz konto"}</button>
          <button class="btn ghost" id="af-cancel">Anuluj</button></div>
      </div>
      <div class="acc-result small" id="af-msg"></div></div>`;
    const apply = () => {
      $$("[data-when]", box).forEach(el => el.classList.toggle("hidden", !el.dataset.when.split(" ").includes(mode)));
      const keys = $("#af-keys");
      if (keys) {
        const hide = $$("[data-when]", keys).length > 0 && !$$("[data-when]", keys).some(el => !el.classList.contains("hidden"));
        keys.classList.toggle("hidden", hide);
        $(".af-steps", box).classList.toggle("hidden", hide);
      }
      $$(".mode-opt", box).forEach(l => l.classList.toggle("on", l.querySelector("input").checked));
    };
    $$("input[name=af-mode]", box).forEach(r => r.onchange = () => {
      mode = r.value; apply();
      const n = $("#af-name");
      if (n && /_(niby|konto|test)$/.test(n.value)) n.value = n.value.replace(/_(niby|konto|test)$/, mode === "live" ? "_konto" : mode === "testnet" ? "_test" : "_niby");
    });
    const exSel = $("#af-exchange");
    if (exSel) exSel.onchange = () => {
      const adv = $("[data-adv]", box); adv.classList.toggle("hidden", exSel.value !== "__other");
      const n = $("#af-name");
      if (n && exSel.value !== "__other" && /^[a-z0-9]+_(niby|konto|test)$/.test(n.value)) n.value = n.value.replace(/^[a-z0-9]+/, exSel.value);
    };
    apply();
    const values = () => {
      const v = { mode };
      $$("[data-f]", box).forEach(el => {
        if (el.closest(".hidden")) return;
        v[el.dataset.f] = el.dataset.t === "num" ? (el.value === "" ? null : +el.value) : el.value;
      });
      if (v.exchange === "__other") v.exchange = "";
      return v;
    };
    const msg = (t, cls) => { const m = $("#af-msg"); m.textContent = t; m.className = "acc-result small " + (cls || ""); };
    $$("#af-cancel", box).forEach(b => b.onclick = () => { box.innerHTML = ""; });
    if ($("#af-back")) $("#af-back").onclick = pick;
    $("#af-test").onclick = async () => {
      msg("Łączę…", "muted");
      try { const r = await api("POST", "/api/accounts/test", { name: existing ? existing.name : "", type, values: values() });
        msg(`✓ ${r.mode}${r.equity != null ? ` · kapitał ${num(r.equity)} ${r.currency}` : ""}`, "up");
      } catch (e) { msg(e.message, "down"); }
    };
    $("#af-save").onclick = async () => {
      const pw = $("#af-pw").value;
      if (!pw) { $("#af-pw").focus(); return msg("Podaj hasło do panelu, żeby zapisać.", "down"); }
      try {
        if (existing) await api("PUT", `/api/accounts/${encodeURIComponent(existing.name)}`, { values: values(), password: pw });
        else await api("POST", "/api/accounts", { name: $("#af-name").value, type, values: values(), password: pw });
        toast("Zapisano konto"); SCHEMA = await api("GET", "/api/schema"); renderAccounts();
      } catch (e) { msg(e.message, "down"); }
    };
  };
  type ? form() : pick();
  box.scrollIntoView({ behavior: "smooth", block: "start" });
}

// ------------------------------------------------------------------ bezpieczeństwo: login, 2FA, klucze skryptów
function secShowCodes(box, codes, title) {
  $(box).innerHTML = `<div class="note warn sec-codes"><b>${title}</b>
    <p style="margin:6px 0 10px">Każdy kod działa raz. Zapisz je w menedżerze haseł albo wydrukuj — <b>więcej ich nie zobaczysz</b>.</p>
    <div class="codes mono">${codes.map(c => `<span>${c}</span>`).join("")}</div>
    <button class="btn small" id="codes-copy" style="margin-top:10px">Kopiuj kody</button></div>`;
  $("#codes-copy").onclick = () => navigator.clipboard.writeText(codes.join("\n")).then(() => toast("Skopiowano"));
}
async function renderSecurity() {
  const s = await api("GET", "/api/security");
  const codeField = (id) => s.totp ? `<div class="field"><label for="${id}">Kod 2FA z aplikacji</label>
    <input id="${id}" inputmode="numeric" autocomplete="one-time-code" placeholder="123 456"></div>` : "";
  const step = (ok, title, sub) => `<div class="sec-step ${ok ? "ok" : ""}"><span class="sec-dot">${ok ? "✓" : ""}</span>
    <div><b>${title}</b><div class="small muted">${sub}</div></div></div>`;
  const when = t => t ? new Date(t).toLocaleString("pl-PL", { dateStyle: "short", timeStyle: "short" }) : "—";
  view.innerHTML = `
    <div class="page-head"><div><h1>Logowanie i bezpieczeństwo</h1>
      <div class="muted small" style="margin-top:4px">Logowanie do panelu i dostęp dla skryptów.</div></div></div>
    <div class="card sec-steps">
      ${step(s.custom_login, "Własny login i hasło", s.custom_login ? `Login: <b>${esc(s.username)}</b> · zmienione ${when(s.changed_at)}` : "Teraz działa hasło z pliku .env — ustaw własne poniżej.")}
      ${step(s.totp, "Kod z telefonu (2FA)", s.totp ? `Włączone · kody awaryjne: zostało ${s.recovery_left} z 10` : "Po haśle panel poprosi o 6-cyfrowy kod z aplikacji.")}
    </div>
    <div class="grid two sec-grid" style="margin-top:14px">
      <div class="card stack">
        <h2>${s.custom_login ? "Zmień login lub hasło" : "Ustaw własny login i hasło"}</h2>
        <div class="form-grid one">
          <div class="field"><label for="sl-cur">${s.custom_login ? "Obecne hasło" : "Obecne hasło (z pliku .env)"}</label>
            <input id="sl-cur" type="password" autocomplete="current-password"></div>
          <div class="field"><label for="sl-user">Login</label>
            <input id="sl-user" value="${esc(s.username || "")}" autocomplete="username" spellcheck="false" autocapitalize="off"></div>
          <div class="field"><label for="sl-new">Nowe hasło</label>
            <input id="sl-new" type="password" autocomplete="new-password"><div class="help">Co najmniej 10 znaków. Najlepiej z menedżera haseł.</div></div>
          <div class="field"><label for="sl-new2">Powtórz nowe hasło</label><input id="sl-new2" type="password" autocomplete="new-password"></div>
          ${codeField("sl-code")}
        </div>
        <div><button class="btn primary" id="sl-save">Zapisz</button></div>
        <div class="acc-result small" id="sl-msg"></div>
        ${s.custom_login ? "" : '<p class="note">Po zapisaniu hasło z pliku .env przestanie otwierać panel. Inne zalogowane urządzenia zostaną wylogowane.</p>'}
      </div>
      <div class="card stack" id="tf-card">
        <h2>Kod z telefonu (2FA)</h2>
        ${!s.custom_login ? '<p class="muted small">Najpierw ustaw własny login i hasło.</p>' : s.totp ? `
          <p class="small">2FA jest <b class="up">włączone</b>. Przy logowaniu panel prosi o kod z aplikacji. Gdy zgubisz telefon,
            zaloguj się kodem awaryjnym.</p>
          <div class="form-grid one">
            <div class="field"><label for="tf-pw">Hasło</label><input id="tf-pw" type="password" autocomplete="current-password"></div>
            ${codeField("tf-code")}
          </div>
          <div class="acc-actions"><button class="btn" id="tf-rec">Nowe kody awaryjne</button>
            <button class="btn danger" id="tf-off">Wyłącz 2FA</button></div>` : `
          <p class="small">Potrzebujesz aplikacji z kodami na telefonie: Google Authenticator, Microsoft Authenticator, Authy
            albo menedżera haseł (1Password, Bitwarden, Hasła w iPhonie).</p>
          <div class="form-grid one"><div class="field"><label for="tf-pw">Hasło</label>
            <input id="tf-pw" type="password" autocomplete="current-password"></div></div>
          <div><button class="btn primary" id="tf-on">Włącz 2FA</button></div>`}
        <div class="acc-result small" id="tf-msg"></div>
        <div id="tf-box"></div>
      </div>
    </div>
    <div class="card stack" style="margin-top:14px">
      <div class="card-head"><h2>Klucze dla skryptów</h2></div>
      <p class="small muted" style="margin:0">Skrypty na laptopie (backtesty, wgrywanie wersji) łączą się kluczem zamiast hasła — przy włączonym 2FA
        to jedyny sposób. Klucz wpisz do pliku <span class="mono">.env</span> laptopa jako <span class="mono">PANEL_API_TOKEN=…</span>.
        Klucz widać tylko raz; w panelu zostaje jego skrót.</p>
      ${s.tokens.length ? `<div class="table-wrap"><table><thead><tr><th>Nazwa</th><th>Utworzony</th><th>Ostatnio użyty</th><th></th></tr></thead><tbody>
        ${s.tokens.map(t => `<tr><td>${esc(t.name)}</td><td>${when(t.created_at)}</td><td>${when(t.last_used)}</td>
          <td><button class="btn small danger" data-tdel="${t.id}">Usuń</button></td></tr>`).join("")}</tbody></table></div>` : ""}
      <div class="form-grid">
        <div class="field"><label for="tk-name">Nazwa</label><input id="tk-name" value="laptop" maxlength="40"></div>
        <div class="field"><label for="tk-pw">Hasło</label><input id="tk-pw" type="password" autocomplete="current-password"></div>
        ${codeField("tk-code")}
      </div>
      <div><button class="btn" id="tk-add">Utwórz klucz</button></div>
      <div class="acc-result small" id="tk-msg"></div>
      <div id="tk-box"></div>
    </div>
    <p class="note" style="margin-top:14px">Awaryjnie (zgubiony telefon i kody): na NAS-ie
      <span class="mono">sudo docker exec tradingapp python -m app.auth reset</span> usuwa własny login i 2FA — znowu działa hasło z .env.</p>`;

  const msg = (id, t, cls) => { const m = $(id); m.textContent = t; m.className = "acc-result small " + (cls || ""); };
  const val = id => ($(id) || {}).value || "";
  const showCodes = secShowCodes;


  $("#sl-save").onclick = async () => {
    if (val("#sl-new") !== val("#sl-new2")) return msg("#sl-msg", "Nowe hasła się różnią.", "down");
    try {
      await api("POST", "/api/security/login", { password: val("#sl-cur"), username: val("#sl-user"),
        new_password: val("#sl-new"), code: val("#sl-code") });
      toast("Zapisano login i hasło"); renderSecurity();
    } catch (e) { msg("#sl-msg", e.message, "down"); }
  };
  if ($("#tf-on")) $("#tf-on").onclick = async () => {
    try {
      const r = await api("POST", "/api/security/2fa/begin", { password: val("#tf-pw") });
      msg("#tf-msg", "");
      const qr = qrcode(0, "M"); qr.addData(r.uri); qr.make();
      $("#tf-box").innerHTML = `<div class="tf-setup">
        <div class="tf-qr">${qr.createSvgTag({ cellSize: 4, margin: 2, scalable: true })}</div>
        <ol class="small">
          <li>W aplikacji na telefonie wybierz <b>Dodaj konto → Zeskanuj kod QR</b>.</li>
          <li>Nie możesz skanować? Wpisz ręcznie klucz:<div class="mono tf-secret">${esc(r.secret)}</div></li>
          <li>Wpisz 6-cyfrowy kod, który pokaże aplikacja:</li></ol>
        <div class="tf-confirm"><input id="tf-new" inputmode="numeric" autocomplete="one-time-code" placeholder="123 456" maxlength="7">
          <button class="btn primary" id="tf-ok">Potwierdź i włącz</button></div></div>`;
      $("#tf-new").focus();
      $("#tf-ok").onclick = async () => {
        try {
          const c = await api("POST", "/api/security/2fa/confirm", { code: val("#tf-new") });
          await renderSecurity();
          msg("#tf-msg", "✓ 2FA włączone. Od następnego logowania panel poprosi o kod.", "up");
          secShowCodes("#tf-box", c.recovery, "Kody awaryjne");
        } catch (e) { msg("#tf-msg", e.message, "down"); }
      };
    } catch (e) { msg("#tf-msg", e.message, "down"); }
  };
  if ($("#tf-rec")) $("#tf-rec").onclick = async () => {
    try { const r = await api("POST", "/api/security/recovery", { password: val("#tf-pw"), code: val("#tf-code") });
      msg("#tf-msg", "Stare kody awaryjne przestały działać.", "up"); showCodes("#tf-box", r.recovery, "Nowe kody awaryjne");
    } catch (e) { msg("#tf-msg", e.message, "down"); }
  };
  if ($("#tf-off")) $("#tf-off").onclick = async () => {
    if (!confirm("Wyłączyć 2FA? Do panelu wystarczy wtedy samo hasło.")) return;
    try { await api("POST", "/api/security/2fa/disable", { password: val("#tf-pw"), code: val("#tf-code") });
      toast("2FA wyłączone"); renderSecurity();
    } catch (e) { msg("#tf-msg", e.message, "down"); }
  };
  $("#tk-add").onclick = async () => {
    try {
      const r = await api("POST", "/api/security/tokens", { name: val("#tk-name"), password: val("#tk-pw"), code: val("#tk-code") });
      msg("#tk-msg", "");
      $("#tk-box").innerHTML = `<div class="note warn"><b>Nowy klucz — skopiuj go teraz, więcej go nie zobaczysz:</b>
        <div class="mono tf-secret" style="margin:8px 0">${esc(r.token)}</div>
        <button class="btn small" id="tk-copy">Kopiuj</button></div>`;
      $("#tk-copy").onclick = () => navigator.clipboard.writeText(r.token).then(() => toast("Skopiowano"));
      $("#tk-pw").value = ""; if ($("#tk-code")) $("#tk-code").value = "";
    } catch (e) { msg("#tk-msg", e.message, "down"); }
  };
  $$("[data-tdel]").forEach(b => b.onclick = async () => {
    const pw = prompt("Usunąć klucz? Skrypty, które go używają, przestaną działać. Podaj hasło:");
    if (!pw) return;
    const res = await fetch(`/api/security/tokens/${b.dataset.tdel}`, { method: "DELETE",
      headers: { "X-Confirm-Password": pw }, credentials: "same-origin" });
    const r = await res.json().catch(() => ({}));
    if (!res.ok) return toast(r.detail || "Błąd", true);
    toast("Usunięto klucz"); renderSecurity();
  });
}

// ------------------------------------------------------------------ bramka IB Gateway
function gwProblem(g) {
  if (!g.connections.length || g.weekend) return "";
  if (g.container.exists && g.container.status && g.container.status !== "running") return "Kontener bramki nie działa";
  const c = g.connections.find(x => !x.api || !x.server_ok);
  if (!c) return "";
  return !c.api ? "Aplikacja nie łączy się z bramką" : "Bramka nie ma połączenia z serwerami IBKR";
}
function ago(sec) {
  sec = Math.max(0, Math.round(sec));
  if (sec < 90) return `${sec} s`;
  if (sec < 5400) return `${Math.round(sec / 60)} min`;
  if (sec < 172800) return `${(sec / 3600).toFixed(1).replace(".0", "")} h`;
  return `${Math.round(sec / 86400)} dni`;
}
async function renderGateway() {
  const draw = async () => {
    const g = await api("GET", "/api/gateway");
    const now = g.now, c = g.container || {}, conn = g.connections[0];
    const tile = (tone, title, value, sub) => `<div class="card gw-tile ${tone}"><div class="gw-label"><span class="gw-dot"></span>${title}</div>
      <div class="gw-value">${value}</div><div class="small muted">${sub}</div></div>`;
    const cont = !g.updater_ok ? tile("muted", "Kontener bramki", "brak danych", "Updater na NAS-ie nie odpowiada — sterowanie niedostępne.")
      : !c.exists ? tile("bad", "Kontener bramki", "nie znaleziono", `Brak kontenera „${esc(c.name || "ib-gateway")}”.`)
      : c.status === "running" ? tile("good", "Kontener bramki", "działa", `od ${ago((now * 1000 - Date.parse(c.started)) / 1000)} · restartów: ${c.restarts}`)
      : tile("bad", "Kontener bramki", esc(c.status), "Bramka jest wyłączona — boty IBKR nie handlują.");
    const api_ = !conn ? tile("muted", "Aplikacja ↔ bramka", "—", "Brak konta IBKR.")
      : conn.api ? tile("good", "Aplikacja ↔ bramka", "połączona", `${esc(conn.host)}:${conn.port}`)
      : tile("bad", "Aplikacja ↔ bramka", "brak połączenia", conn.api_since ? `od ${ago(now - conn.api_since)}` : "");
    const srv = !conn ? tile("muted", "Bramka ↔ serwery IBKR", "—", "")
      : !conn.api ? tile("muted", "Bramka ↔ serwery IBKR", "nieznane", "Najpierw połączenie aplikacji z bramką.")
      : conn.server_ok ? tile("good", "Bramka ↔ serwery IBKR", "połączona", `od ${ago(now - conn.server_since)}`)
      : tile("bad", "Bramka ↔ serwery IBKR", "utracone", `od ${ago(now - conn.server_since)} · zwykle wraca samo w kilka minut`);
    const pend = g.pending ? `<div class="note">Polecenie „${esc(g.pending.action)}” czeka na wykonanie (do 15 s)…</div>` : "";
    const res = g.result ? `<div class="small ${g.result.ok ? "up" : "down"}">Ostatnie polecenie: ${esc(g.result.action)} —
      ${g.result.ok ? "wykonane" : "błąd: " + esc(g.result.message)} · ${ago(now - g.result.t)} temu</div>` : "";
    const ev = g.events.length ? g.events.map(e => `<li><span class="mono small muted">${new Date(e.ts).toLocaleString("pl-PL", { dateStyle: "short", timeStyle: "short" })}</span>
      <span class="f-tag ${({ alert: "warn", ok: "up", auto: "info", manual: "muted" })[e.kind] || "muted"}">${({ alert: "problem", ok: "OK", auto: "automat", manual: "ręcznie" })[e.kind] || e.kind}</span>
      ${esc(e.text)}</li>`).join("") : '<li class="muted">Brak zdarzeń.</li>';
    const st = g.settings;
    const open = $("#gw-logs") && $("#gw-logs").open;
    view.innerHTML = `
      <div class="page-head"><div><h1>Bramka IBKR</h1>
        <div class="muted small" style="margin-top:4px">IB Gateway łączy aplikację z Interactive Brokers. Odświeża się co 10 s.</div></div></div>
      ${gwProblem(g) ? `<div class="note warn" style="margin-bottom:14px"><b>${esc(gwProblem(g))}.</b> Boty na koncie IBKR nie mogą teraz handlować —
        zlecenia stop-loss i take-profit leżą na serwerze IBKR i działają dalej.</div>` : ""}
      <div class="gw-tiles">${cont}${api_}${srv}</div>
      <div class="grid two" style="margin-top:14px">
        <div class="card stack" style="align-content:start">
          <h2>Sterowanie</h2>
          <div class="acc-actions">
            <button class="btn primary" id="gw-restart" ${g.updater_ok ? "" : "disabled"}>Restartuj bramkę</button>
            ${c.status === "running" ? `<button class="btn danger" id="gw-stop" ${g.updater_ok ? "" : "disabled"}>Zatrzymaj</button>`
              : `<button class="btn" id="gw-start" ${g.updater_ok ? "" : "disabled"}>Uruchom</button>`}
          </div>
          <p class="small muted" style="margin:0">Restart trwa 1–3 minuty (bramka loguje się od nowa). Zatrzymanie wyłącza handel
            botów na IBKR — wymaga hasła.</p>
          ${pend}${res}
        </div>
        <div class="card stack" style="align-content:start">
          <h2>Automatyczny restart</h2>
          <label class="check" style="padding-top:0"><input type="checkbox" id="gw-auto" ${st.auto_restart ? "checked" : ""}>
            Restartuj bramkę, gdy nie ma połączenia z IBKR dłużej niż</label>
          <div class="seg" id="gw-th">${[5, 10, 15, 30, 60].map(m => `<button data-m="${m}" class="${m === st.threshold_min ? "on" : ""}">${m} min</button>`).join("")}</div>
          <label class="check" style="padding-top:0"><input type="checkbox" id="gw-weekend" ${st.weekend_pause !== false ? "checked" : ""}>
            Wstrzymaj w weekend (pt 23:00 – pn 06:00): giełdy są zamknięte, bez restartów i powiadomień</label>
          ${g.weekend ? `<p class="small" style="margin:0;color:var(--info)">Teraz weekend — brak połączenia jest normalny (prace IBKR). Restart, jeśli trzeba, w poniedziałek o 6:00.</p>` : ""}
          <p class="small muted" style="margin:0">Najwyżej raz na 30 minut i 4 razy na 6 godzin; nie w nocy (23:45–00:15 bramka
            restartuje się sama). Każdy problem i powrót połączenia trafia do historii i do powiadomień.</p>
        </div>
      </div>
      <div class="card" style="margin-top:14px"><h2>Historia</h2><ul class="gw-events">${ev}</ul></div>
      <details class="card" id="gw-logs" style="margin-top:14px" ${open ? "open" : ""}><summary><b>Dziennik bramki</b>
        <span class="small muted">(ostatnie linie, numery kont zamaskowane)</span></summary>
        <pre class="gw-log">${esc(g.logs || "Brak dziennika — pojawi się, gdy updater na NAS-ie zapisze go po raz pierwszy.")}</pre></details>`;
    const act = async (action, extra = {}) => {
      try { await api("POST", "/api/gateway/action", { action, ...extra }); toast("Polecenie wysłane"); draw(); }
      catch (e) { toast(e.message, true); }
    };
    $("#gw-restart").onclick = () => confirm("Zrestartować bramkę IBKR? Przez 1–3 minuty boty IBKR nie będą handlować.") && act("restart");
    if ($("#gw-stop")) $("#gw-stop").onclick = () => {
      const pw = prompt("Zatrzymać bramkę? Boty na IBKR przestaną handlować. Podaj hasło do panelu:");
      if (!pw) return;
      const code = gwProblem.totp ? prompt("Kod 2FA z aplikacji:") : "";
      act("stop", { password: pw, code: code || "" });
    };
    if ($("#gw-start")) $("#gw-start").onclick = () => {
      const pw = prompt("Uruchomić bramkę? Podaj hasło do panelu:");
      if (!pw) return;
      const code = gwProblem.totp ? prompt("Kod 2FA z aplikacji:") : "";
      act("start", { password: pw, code: code || "" });
    };
    const save = async ch => { try { await api("PUT", "/api/gateway/settings", ch); toast("Zapisano"); draw(); } catch (e) { toast(e.message, true); } };
    $("#gw-auto").onchange = e => save({ auto_restart: e.target.checked });
    $$("#gw-th button").forEach(b => b.onclick = () => save({ threshold_min: +b.dataset.m }));
    $("#gw-weekend").onchange = e => save({ weekend_pause: e.target.checked });
  };
  try { gwProblem.totp = (await api("GET", "/api/security")).totp; } catch (e) { gwProblem.totp = false; }
  await draw();
  every(10000, () => draw().catch(() => {}));
}

// ------------------------------------------------------------------ WYKRESY
function loadScript(src) {
  return new Promise((ok, bad) => {
    if (document.querySelector(`script[data-src="${src}"]`)) return ok();
    const el = document.createElement("script"); el.src = src; el.dataset.src = src;
    el.onload = ok; el.onerror = () => bad(new Error("Nie udało się wczytać biblioteki wykresów."));
    document.head.appendChild(el);
  });
}
function smaSeries(bars, n) {
  const out = []; let sum = 0;
  bars.forEach((b, i) => { sum += b[4]; if (i >= n) sum -= bars[i - n][4]; if (i >= n - 1) out.push({ time: b[0], value: sum / n }); });
  return out;
}
let chartState = { tf: "1Day", source: "auto", sma: { 20: true, 50: true, 200: false }, plan: "default", pat: true, off: [], fund: true };
const fundCache = {};
try { Object.assign(chartState, JSON.parse(localStorage.getItem("chartState") || "{}")); } catch (e) { /* bez znaczenia */ }
async function renderChart(symParam) {
  const sym = symParam ? decodeURIComponent(symParam).toUpperCase() : "";
  let curChart = null;
  const meta = await api("GET", "/api/chart/symbols");
  const tfs = [["15Min", "15 min"], ["1Hour", "1 h"], ["4Hour", "4 h"], ["1Day", "1 dzień"]];
  view.innerHTML = `
    <div class="page-head"><div><h1>Wykresy</h1>
      <div class="muted small" style="margin-top:4px">Świece dowolnej spółki, ETF-u, waluty albo kryptowaluty — z zakupami i sprzedażami Twoich botów.</div></div>
      ${sym ? `<div class="actions"><button class="btn primary" id="ch-order">Kup / sprzedaj ${esc(sym)}</button></div>` : ""}</div>
    <div class="card ch-bar">
      <form id="ch-form" class="ch-form">
        <input id="ch-sym" list="ch-list" placeholder="np. PKN.WSE, AAPL, BTC/USD, EUR.USD" value="${esc(sym)}"
          autocomplete="off" spellcheck="false" autocapitalize="characters">
        <datalist id="ch-list">${[...meta.held, ...meta.symbols].map(x => `<option value="${esc(x)}">`).join("")}</datalist>
        <div class="seg" id="ch-tf">${tfs.map(([v, l]) => `<button type="button" data-tf="${v}" class="${v === chartState.tf ? "on" : ""}">${l}</button>`).join("")}</div>
        <select id="ch-src" style="width:auto"><option value="auto">Źródło: automatycznie</option>
          ${meta.sources.map(x => `<option value="${esc(x.id)}" ${x.id === chartState.source ? "selected" : ""}>${esc(x.label)}</option>`).join("")}</select>
        <select id="ch-plan" style="width:auto" title="Plan i przykładowe ruchy wybranej strategii">
          <option value="none" ${chartState.plan === "none" ? "selected" : ""}>Plan: wyłączony</option>
          ${meta.templates.map(t => `<option value="${esc(t.id)}" ${t.id === chartState.plan ? "selected" : ""}>Plan: ${esc(t.label)}</option>`).join("")}</select>
        <button class="btn primary" type="submit">Pokaż</button>
      </form>
      ${meta.held.length ? `<div class="ch-quick"><span class="small muted">Pozycje botów:</span>
        ${meta.held.map(x => `<a class="chip" href="#/chart/${encodeURIComponent(x)}">${esc(x)}</a>`).join("")}</div>` : ""}
    </div>
    <div id="ch-body"></div>`;
  $("#ch-order") && ($("#ch-order").onclick = () => orderTicket(sym));
  $("#ch-form").onsubmit = e => {
    e.preventDefault();
    const v = $("#ch-sym").value.trim().toUpperCase();
    if (!v) return $("#ch-sym").focus();
    const h = `#/chart/${encodeURIComponent(v)}`;
    if (location.hash === h) draw(v); else location.hash = h;
  };
  $$("#ch-tf button").forEach(b => b.onclick = () => {
    chartState.tf = b.dataset.tf; save();
    $$("#ch-tf button").forEach(x => x.classList.toggle("on", x === b));
    if (sym) draw(sym);
  });
  $("#ch-src").onchange = () => { chartState.source = $("#ch-src").value; save(); if (sym) draw(sym); };
  $("#ch-plan").onchange = () => { chartState.plan = $("#ch-plan").value; save(); if (sym) draw(sym); };
  function save() { try { localStorage.setItem("chartState", JSON.stringify(chartState)); } catch (e) { /* bez znaczenia */ } }
  if (!sym) {
    $("#ch-body").innerHTML = `<div class="card empty" style="margin-top:14px">Wpisz symbol albo wybierz pozycję bota powyżej.
      <div class="small" style="margin-top:6px">GPW: <b>PKN.WSE</b> · USA: <b>AAPL</b> · krypto: <b>BTC/USD</b> · waluty: <b>EUR.USD</b> · Xetra: <b>SAP.DE</b></div></div>`;
    $("#ch-sym").focus();
    return;
  }
  await draw(sym);

  async function draw(symbol) {
    const body = $("#ch-body");
    body.innerHTML = `<div class="card empty" style="margin-top:14px">Pobieram dane ${esc(symbol)}…</div>`;
    let d;
    try {
      [d] = await Promise.all([api("GET", `/api/chart?symbol=${encodeURIComponent(symbol)}&tf=${chartState.tf}&source=${encodeURIComponent(chartState.source)}&template=${encodeURIComponent(chartState.plan || "none")}`),
        loadScript("/static/vendor/lightweight-charts.js")]);
    } catch (e) { body.innerHTML = `<div class="card" style="margin-top:14px"><b class="down">${esc(e.message)}</b></div>`; return; }
    render(d, symbol);
  }

  function render(d, symbol) {
    const body = $("#ch-body");
    if (curChart) { try { curChart.remove(); } catch (e) { /* juz usuniety */ } curChart = null; }
    const X = filterPatterns(d.patterns);
    const b = d.bars, last = b[b.length - 1], first = b[0], prev = b.length > 1 ? b[b.length - 2] : first;
    const chg = (last[4] / prev[4] - 1) * 100, chgAll = (last[4] / first[4] - 1) * 100;
    const hi = Math.max(...b.map(x => x[2])), lo = Math.min(...b.map(x => x[3]));
    const sign = v => (v >= 0 ? "+" : "−") + Math.abs(v).toFixed(2).replace(".", ",") + "%";
    const tfLabel = Object.fromEntries(tfs)[d.tf];
    body.innerHTML = `
      <div class="card ch-card">
        <div class="ch-head">
          <div><div class="ch-sym">${esc(d.symbol)}</div><div class="small muted">${esc(d.source)} · świece ${tfLabel} · ${b.length} świec</div></div>
          <div class="ch-price"><span class="mono">${price(last[4])}</span>
            <span class="mono ${tone(chg)}">${chg >= 0 ? "▲" : "▼"} ${sign(chg)}</span><span class="small muted">ostatnia świeca</span></div>
          <div class="ch-stats small">
            <span>Okres <b class="mono ${tone(chgAll)}">${sign(chgAll)}</b></span>
            <span>Max <b class="mono">${price(hi)}</b></span><span>Min <b class="mono">${price(lo)}</b></span></div>
        </div>
        <div class="ch-tools">${[20, 50, 200].map(n => `<label class="ch-sma s${n}"><input type="checkbox" data-sma="${n}" ${chartState.sma[n] ? "checked" : ""}> SMA ${n}</label>`).join("")}
          <label class="ch-sma"><input type="checkbox" id="ch-fund-cb" ${chartState.fund !== false ? "checked" : ""}> Finanse</label>
          <label class="ch-sma"><input type="checkbox" id="ch-pat" ${chartState.pat ? "checked" : ""}> Formacje i poziomy</label>
          <button class="btn small ghost" id="ch-pat-set" type="button" aria-expanded="false">Wybierz formacje</button>
          ${d.trades.length ? `<span class="small muted">▲ kupno · ▼ sprzedaż botów (${d.trades.length})</span>` : ""}</div>
        <div id="ch-pat-panel" class="pat-panel hidden">${patternSettings()}</div>
        <div id="ch-box" class="ch-box"></div>
        ${d.levels.length ? `<div class="ch-levels">${d.levels.map(l => `<span><b>${esc(l.bot)}</b>: wejście <b class="mono">${price(l.entry)}</b>
          ${l.sl ? ` · stop-loss <b class="mono down">${price(l.sl)}</b>` : ""}${l.tp ? ` · take-profit <b class="mono up">${price(l.tp)}</b>` : ""}</span>`).join("")}</div>` : ""}
      </div>
      <div id="ch-fund"></div>
      ${patternsCard(X)}
      ${planCard(d.plan)}
      ${d.trades.length ? `<div class="card table-wrap" style="margin-top:14px"><h2>Transakcje botów na ${esc(d.symbol)}</h2>${tradesTable([...d.trades].reverse(), true)}</div>` : ""}`;
    const C = LightweightCharts, el = $("#ch-box");
    const ch = curChart = C.createChart(el, {
      autoSize: true,
      layout: { background: { color: "transparent" }, textColor: css("--muted"), fontFamily: "'Plex Sans', system-ui, sans-serif", attributionLogo: true },
      grid: { vertLines: { color: css("--line") }, horzLines: { color: css("--line") } },
      rightPriceScale: { borderColor: css("--line-2") },
      timeScale: { borderColor: css("--line-2"), timeVisible: d.tf !== "1Day", secondsVisible: false },
      crosshair: { mode: C.CrosshairMode.Normal },
      localization: { locale: "pl-PL", priceFormatter: p => price(p) },
    });
    const up = css("--up"), down = css("--down");
    const candles = ch.addCandlestickSeries({ upColor: up, downColor: down, borderVisible: false, wickUpColor: up, wickDownColor: down });
    candles.setData(b.map(x => ({ time: x[0], open: x[1], high: x[2], low: x[3], close: x[4] })));
    const vol = ch.addHistogramSeries({ priceFormat: { type: "volume" }, priceScaleId: "vol" });
    ch.priceScale("vol").applyOptions({ scaleMargins: { top: .82, bottom: 0 } });
    candles.priceScale().applyOptions({ scaleMargins: { top: .08, bottom: .22 } });
    vol.setData(b.map(x => ({ time: x[0], value: x[5], color: x[4] >= x[1] ? "rgba(47,208,143,.28)" : "rgba(255,90,110,.28)" })));
    const smaColors = { 20: "#f2b544", 50: "#6fa8ff", 200: "#b493ff" }, smaLines = {};
    [20, 50, 200].forEach(n => {
      smaLines[n] = ch.addLineSeries({ color: smaColors[n], lineWidth: 1.5, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
      smaLines[n].setData(chartState.sma[n] ? smaSeries(b, n) : []);
    });
    $$("[data-sma]", body).forEach(cb => cb.onchange = () => {
      const n = +cb.dataset.sma; chartState.sma[n] = cb.checked; save();
      smaLines[n].setData(cb.checked ? smaSeries(b, n) : []);
    });
    // zakupy i sprzedaze botow na swiecach (dopasowane do swiecy, w ktorej byly)
    const t0 = b[0][0], times = b.map(x => x[0]);
    const snap = ts => { const t = Math.floor(Date.parse(ts) / 1000); let k = times.length - 1; while (k > 0 && times[k] > t) k--; return t < t0 ? null : times[k]; };
    const marks = d.trades.map(t => ({ time: snap(t.ts), t })).filter(m => m.time).map(m => {
      const sd = m.t.side, open = sd === "BUY" || sd === "SHORT", up_ = sd === "BUY" || sd === "COVER";
      const res = m.t.pnl_pct == null ? null : (m.t.pnl_pct >= 0 ? "+" : "") + m.t.pnl_pct.toFixed(1) + "%";
      return { time: m.time, position: up_ ? "belowBar" : "aboveBar", shape: up_ ? "arrowUp" : "arrowDown",
        color: open ? (sd === "SHORT" ? "#ff9f43" : css("--accent")) : (m.t.pnl == null ? css("--muted") : m.t.pnl >= 0 ? up : down),
        text: sd === "BUY" ? "K" : sd === "SHORT" ? "S↓" : (res || (sd === "COVER" ? "O" : "S")) };
    });
    const P = d.plan && !d.plan.error ? d.plan : null, info = css("--info");
    if (P) P.trades.forEach(t => {
      const et = snap(new Date(t.entry_t * 1000).toISOString()), xt = snap(new Date(t.exit_t * 1000).toISOString());
      if (et) marks.push({ time: et, position: "belowBar", shape: "circle", color: info, text: "W" });
      if (xt) marks.push({ time: xt, position: "aboveBar", shape: "circle", color: t.pct >= 0 ? up : down,
        text: (t.pct >= 0 ? "+" : "") + t.pct.toFixed(1) + "%" });
    });
    if (P && P.open) { const et = snap(new Date(P.open.entry_t * 1000).toISOString()); if (et) marks.push({ time: et, position: "belowBar", shape: "circle", color: info, text: "W" }); }
    const bullC = up, bearC = down, sCol = "#6fa8ff", rCol = "#ff9f43";
    if (X && chartState.pat && !X.error) {
      X.candles.forEach(cn => marks.push({ time: snap(new Date(cn.t * 1000).toISOString()) || cn.t, position: cn.kind === "bear" ? "aboveBar" : "belowBar",
        shape: "square", color: cn.kind === "bull" ? bullC : cn.kind === "bear" ? bearC : css("--muted"), text: CANDLE_SHORT[cn.key] || cn.name }));
      X.patterns.forEach(p => {
        const col = p.kind === "bull" ? bullC : p.kind === "bear" ? bearC : css("--muted");
        const ser = ch.addLineSeries({ color: col, lineWidth: 2, lineStyle: p.confirmed ? C.LineStyle.Solid : C.LineStyle.Dashed,
          priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
        ser.setData(p.points.map(([t, v]) => ({ time: t, value: v })).filter((x, i, a) => i === 0 || x.time > a[i - 1].time));
        marks.push({ time: p.t_end, position: p.kind === "bear" ? "aboveBar" : "belowBar", shape: "circle", color: col, text: p.name });
      });
      X.lines.forEach(l => {
        const ser = ch.addLineSeries({ color: l.kind === "support" ? sCol : rCol, lineWidth: 1, lineStyle: C.LineStyle.LargeDashed,
          priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
        if (l.t2 > l.t1) ser.setData([{ time: l.t1, value: l.p1 }, { time: l.t2, value: l.p2 }]);
      });
      X.levels.forEach(l => candles.createPriceLine({ price: l.price, color: l.kind === "support" ? sCol : rCol, lineWidth: 1,
        lineStyle: C.LineStyle.Solid, axisLabelVisible: false, title: `${l.kind === "support" ? "wsparcie" : "opór"} ×${l.touches}` }));
    }
    marks.sort((a, c) => a.time - c.time);
    candles.setMarkers(marks);
    chartMarks = { candles, marks, snap, symbol: d.symbol };
    $("#ch-pat").onchange = e => { chartState.pat = e.target.checked; save(); render(d, symbol); };
    $("#ch-fund-cb").onchange = e => { chartState.fund = e.target.checked; save(); loadFund(d, false); };
    loadFund(d, false);
    $("#ch-pat-set").onclick = () => { const p = $("#ch-pat-panel"); p.classList.toggle("hidden");
      $("#ch-pat-set").setAttribute("aria-expanded", String(!p.classList.contains("hidden"))); };
    const setOff = off => { chartState.off = off; chartState.pat = true; save(); render(d, symbol);
      $("#ch-pat-panel").classList.remove("hidden"); };
    $$("[data-fk]", body).forEach(cb => cb.onchange = () => {
      const off = new Set(chartState.off || []); cb.checked ? off.delete(cb.dataset.fk) : off.add(cb.dataset.fk); setOff([...off]); });
    $("#pf-all").onclick = () => setOff([]);
    $("#pf-none").onclick = () => setOff(FORMATIONS.flatMap(g => g.items.map(i => i[0])));
    $("#pf-def").onclick = () => setOff(["doji", "gap_up", "gap_down"]);
    const lineStyle = { entry: [css("--accent"), C.LineStyle.Dotted], trigger: [css("--accent"), C.LineStyle.Dashed],
      sl: [down, C.LineStyle.Dashed], tp: [up, C.LineStyle.Dashed], exit: [info, C.LineStyle.Dashed] };
    if (P && !d.levels.length) P.lines.forEach(l => {
      const [col, st] = lineStyle[l.kind] || [css("--muted"), C.LineStyle.Dashed];
      candles.createPriceLine({ price: l.price, color: col, lineWidth: 1, lineStyle: st, axisLabelVisible: true, title: l.label });
    });
    d.levels.forEach(l => {
      candles.createPriceLine({ price: l.entry, color: css("--accent"), lineWidth: 1, lineStyle: C.LineStyle.Dotted, axisLabelVisible: true, title: "wejście" });
      if (l.sl) candles.createPriceLine({ price: l.sl, color: down, lineWidth: 1, lineStyle: C.LineStyle.Dashed, axisLabelVisible: true, title: "SL" });
      if (l.tp) candles.createPriceLine({ price: l.tp, color: up, lineWidth: 1, lineStyle: C.LineStyle.Dashed, axisLabelVisible: true, title: "TP" });
    });
    ch.timeScale().fitContent();
    const vis = Math.min(b.length, innerWidth < 700 ? 90 : d.tf === "1Day" ? 260 : 200);
    ch.timeScale().setVisibleLogicalRange({ from: b.length - vis, to: b.length + 3 });
    charts.push({ destroy: () => { if (curChart === ch) { ch.remove(); curChart = null; } } });
  }
}

// ------------------------------------------------------------------ RYZYKO
async function renderRisk() {
  const draw = async () => {
    const d = await api("GET", "/api/risk");
    const pct = v => v ? num(v * 100, 1) + "%" : "wyłączony";
    const accRows = d.accounts.map(a => `<tr>
      <td><b>${esc(a.name)}</b><div class="small muted">${esc(a.type)}</div></td>
      <td>${a.real ? '<span class="f-tag down">prawdziwe pieniądze</span>' : '<span class="f-tag muted">papier / symulacja</span>'}</td>
      <td class="num mono">${a.equity != null ? num(a.equity, 0) : "—"}</td>
      <td class="num mono">${a.gross_lev != null ? num(a.gross_lev, 2) + "×" : "—"}</td>
      <td>${a.real ? `<label class="check small" style="padding:0"><input type="checkbox" data-consent="${esc(a.name)}" ${a.consent ? "checked" : ""}>
          ${a.consent ? "zgoda od " + when(a.since) : "brak zgody"}</label>` : '<span class="small muted">nie wymaga</span>'}</td>
      <td><select data-maxlev="${esc(a.name)}">${[0, 1, 1.5, 2, 3, 5].map(v => `<option value="${v}" ${(+a.max_leverage || 0) === v ? "selected" : ""}>${v ? v + "×" : "domyślny"}</option>`).join("")}</select></td>
      <td>${a.tripped ? '<span class="f-tag down">limit dzienny zadziałał</span>' : ""}</td></tr>`).join("");
    const bots = d.bots.map(b => `<tr><td><a href="#/bot/${b.id}">${esc(b.name)}</a></td><td>${esc(b.account)}</td>
      <td>${levTag({ mode: b.mode, direction: b.direction, leverage: b.leverage })}</td><td>${statusPill(b.status)}</td></tr>`).join("");
    const chip = (x, use) => `<span class="etf-chip ${x.ok === true ? "ok" : x.ok === false ? "bad" : ""} ${use && use[0] === x.etf ? "use" : ""}"
        title="${esc(x.name || x.why || "jeszcze nie sprawdzony")}">${esc(x.etf)} <small>${x.lev > 0 ? "+" : ""}${num(x.lev, 1)}×</small></span>`;
    const etf = d.etf.rows.map(r => `<tr><td class="mono"><a href="#/chart/${encodeURIComponent(r.base)}">${esc(r.base)}</a></td>
      <td>${r.bull.map(x => chip(x, r.use_bull)).join(" ") || '<span class="muted small">—</span>'}</td>
      <td>${r.bear.map(x => chip(x, r.use_bear)).join(" ") || '<span class="muted small">—</span>'}</td></tr>`).join("");
    view.innerHTML = `<div class="page-head"><h1>Ryzyko</h1>
        <p class="muted small" style="margin:4px 0 0">Wspólne zabezpieczenia wszystkich botów: wyłącznik, dzienny limit straty i zgoda na dźwignię.</p></div>
      <div class="grid two">
        <div class="card stack ${d.halt ? "halt-on" : ""}">
          <h2>Wyłącznik ${d.halt ? '<span class="f-tag down">WŁĄCZONY</span>' : '<span class="f-tag up">boty pracują</span>'}</h2>
          <p class="small" style="margin:0">${d.halt ? `Nowe wejścia wszystkich botów są wstrzymane${d.halt_reason ? ` (${esc(d.halt_reason)})` : ""}. Otwarte pozycje zostają ze swoimi stop-lossami.`
            : "Jednym kliknięciem wstrzymasz otwieranie nowych pozycji przez wszystkie boty. Otwarte pozycje zostaną ze stopami."}</p>
          <div class="acc-actions"><button class="btn ${d.halt ? "primary" : "danger"}" id="rk-halt">${d.halt ? "Zdejmij wyłącznik" : "Wstrzymaj nowe wejścia"}</button>
            <button class="btn danger" id="rk-close" ${d.bots.length ? "" : "disabled"}>Zamknij pozycje z dźwignią</button></div>
          <p class="small muted" style="margin:0">„Zamknij pozycje z dźwignią” sprzedaje / odkupuje po rynku wszystkie pozycje botów z dźwignią i grą na spadki,
            a potem włącza wyłącznik. Wymaga hasła.</p>
        </div>
        <div class="card stack">
          <h2>Dzienny limit straty</h2>
          <p class="small" style="margin:0">Gdy kapitał konta spadnie dziś o więcej niż limit (wobec zamknięcia poprzedniego dnia), boty tego konta
            do jutra nie otwierają nowych pozycji i dostajesz powiadomienie. Teraz: <b>${pct(d.daily_loss_pct)}</b>.</p>
          <div class="seg" id="rk-dl">${[0, 0.02, 0.03, 0.05, 0.1].map(v => `<button data-v="${v}" class="${Math.abs((d.daily_loss_pct || 0) - v) < 1e-9 ? "on" : ""}">${v ? num(v * 100, 0) + "%" : "wyłączony"}</button>`).join("")}</div>
          <label class="check small" style="padding:0"><input type="checkbox" id="rk-closelev" ${d.close_leveraged_on_limit ? "checked" : ""}>
            Po przekroczeniu limitu zamknij też pozycje botów z dźwignią</label>
        </div>
      </div>
      <div class="card" style="margin-top:14px"><h2>Konta i zgoda na dźwignię</h2>
        <p class="small muted" style="margin:0 0 10px">Na koncie z prawdziwymi pieniędzmi bot z dźwignią albo grą na spadki nie wystartuje bez zgody (hasło i kod 2FA).
          Limit dźwigni obcina ustawienie bota (domyślnie: akcje 2×, krypto 5×). Dźwignia brutto = wartość wszystkich pozycji / kapitał.</p>
        <div class="table-wrap"><table><thead><tr><th>Konto</th><th>Rodzaj</th><th class="num">Kapitał</th><th class="num">Dźwignia brutto</th><th>Zgoda</th><th>Limit dźwigni</th><th></th></tr></thead>
          <tbody>${accRows}</tbody></table></div></div>
      <div class="card" style="margin-top:14px"><h2>Boty z dźwignią i grą na spadki</h2>
        ${bots ? `<div class="table-wrap"><table><thead><tr><th>Bot</th><th>Konto</th><th>Tryb</th><th>Status</th></tr></thead><tbody>${bots}</tbody></table></div>`
          : '<p class="muted small" style="margin:0">Brak. Włączysz to w ustawieniach bota: sekcja „Dźwignia i gra na spadki”.</p>'}</div>
      <div class="card" style="margin-top:14px"><div class="card-head"><h2 style="margin:0">ETF-y lewarowane i odwrotne</h2>
          <button class="btn small" id="rk-verify">Sprawdź dostępność u brokera</button></div>
        <p class="small muted" style="margin:8px 0 10px">Sygnał liczony na spółce / indeksie, bot kupuje ETF z kolumny „wzrost” albo „spadek”.
          Zielone = dostępne u brokera, czerwone = nie ma / nie w obrocie, obwódka = ten bot użyje.
          ${d.etf.checked ? "Sprawdzono: " + when(new Date(d.etf.checked * 1000).toISOString()) + "." : "Jeszcze nie sprawdzono — kliknij przycisk."}
          Własne pary dopiszesz w ustawieniach bota („Własne pary ETF”). GPW: tylko WIG20 (Beta ETF WIG20lev / WIG20short) przez IBKR.</p>
        <div class="table-wrap"><table><thead><tr><th>Spółka / indeks</th><th>Na wzrost</th><th>Na spadek</th></tr></thead><tbody>${etf}</tbody></table></div></div>`;
    $("#rk-halt").onclick = async () => {
      const on = !d.halt;
      const why = on ? prompt("Powód (opcjonalnie):", "") : "";
      if (on && why === null) return;
      try { await api("PUT", "/api/risk", { halt: on, halt_reason: why || "" }); toast(on ? "Wyłącznik włączony" : "Wyłącznik zdjęty"); draw(); } catch (e) { toast(e.message, true); }
    };
    $("#rk-close").onclick = async () => {
      if (!confirm("Zamknąć PO RYNKU wszystkie pozycje botów z dźwignią i grą na spadki, i wstrzymać nowe wejścia?")) return;
      const pw = prompt("Hasło do panelu:"); if (!pw) return;
      let code = ""; try { if ((await api("GET", "/api/security")).totp) code = prompt("Kod 2FA:") || ""; } catch (e) { /* bez 2FA */ }
      try { const r = await api("POST", "/api/risk/close-leveraged", { password: pw, code }); toast(`Zamknięto ${r.closed} pozycji`); draw(); } catch (e) { toast(e.message, true); }
    };
    $$("#rk-dl button").forEach(b => b.onclick = async () => { try { await api("PUT", "/api/risk", { daily_loss_pct: +b.dataset.v }); toast("Zapisano"); draw(); } catch (e) { toast(e.message, true); } });
    $("#rk-closelev").onchange = async e => { try { await api("PUT", "/api/risk", { close_leveraged_on_limit: e.target.checked }); toast("Zapisano"); } catch (er) { toast(er.message, true); } };
    $$("[data-consent]").forEach(cb => cb.onchange = async () => {
      const name = cb.dataset.consent, on = cb.checked;
      let body = { account: name, leverage_ok: on };
      if (on) {
        if (!confirm(`Konto „${name}” to PRAWDZIWE pieniądze. Zgoda pozwoli botom grać z dźwignią i na spadki — strata może przekroczyć wkład. Kontynuować?`)) { cb.checked = false; return; }
        const pw = prompt("Hasło do panelu:"); if (!pw) { cb.checked = false; return; }
        body.password = pw;
        try { if ((await api("GET", "/api/security")).totp) body.code = prompt("Kod 2FA:") || ""; } catch (e) { /* bez 2FA */ }
      }
      try { await api("PUT", "/api/risk/account", body); toast("Zapisano"); draw(); } catch (e) { toast(e.message, true); cb.checked = !on; }
    });
    $$("[data-maxlev]").forEach(sel => sel.onchange = async () => {
      try { await api("PUT", "/api/risk/account", { account: sel.dataset.maxlev, max_leverage: +sel.value }); toast("Zapisano"); } catch (e) { toast(e.message, true); } });
    $("#rk-verify").onclick = async () => {
      const b = $("#rk-verify"); b.disabled = true; b.textContent = "Sprawdzam… (ok. pół minuty)";
      try { await api("POST", "/api/risk/verify-etfs"); toast("Sprawdzono"); draw(); } catch (e) { toast(e.message, true); b.disabled = false; b.textContent = "Sprawdź dostępność u brokera"; }
    };
  };
  await draw();
}

// ------------------------------------------------------------------ KALENDARZ
async function renderCalendar() {
  let days = 30;
  view.innerHTML = `<div class="page-head"><h1>Kalendarz</h1>
      <p class="muted small" style="margin:4px 0 0">Raporty okresowe i dywidendy spółek, którymi handlują boty (dane z Yahoo Finance, odświeżane raz dziennie).</p></div>
    <div class="card"><div class="seg" id="cal-days">${[7, 14, 30, 60].map(n => `<button data-d="${n}" class="${n === days ? "on" : ""}">${n} dni</button>`).join("")}</div>
      <div id="cal-body" style="margin-top:12px"></div></div>`;
  const KIND = { report: ["📅 Raport okresowy", "warn"], ex_div: ["💰 Odcięcie dywidendy", "info"], div_pay: ["Wypłata dywidendy", "muted"] };
  const draw = async () => {
    const d = await api("GET", `/api/calendar/upcoming?days=${days}`);
    const st = d.status;
    const rows = d.items.map(it => {
      const [label, cls] = KIND[it.kind];
      const blk = it.kind === "report" ? it.bots.filter(b => b.blackout && it.days <= b.blackout) : [];
      const when = it.days === 0 ? "dziś" : it.days === 1 ? "jutro" : `za ${it.days} dni`;
      return `<tr><td class="nowrap">${new Date(it.date + "T00:00:00").toLocaleDateString("pl-PL", { weekday: "short", day: "2-digit", month: "2-digit" })}
          <div class="small muted">${when}</div></td>
        <td><a class="mono" href="#/chart/${encodeURIComponent(it.symbol)}">${esc(it.symbol)}</a></td>
        <td><span class="f-tag ${cls}">${label}</span></td>
        <td class="small">${it.held_by.length ? `<b class="up">pozycja:</b> ${esc(it.held_by.join(", "))}` : ""}
          ${it.bots.length ? `<div class="muted">${esc(it.bots.map(b => b.name).join(", "))}</div>` : ""}</td>
        <td class="small">${it.kind !== "report" ? "" : blk.length ? `<span class="warn">zakupy wstrzymane (${esc(blk.map(b => b.name).join(", "))})</span>`
          : it.bots.some(b => b.blackout) ? '<span class="muted">blokada zacznie się bliżej raportu</span>' : '<span class="muted">bez blokady</span>'}</td></tr>`;
    }).join("");
    $("#cal-body").innerHTML = (rows ? `<div class="table-wrap"><table><thead><tr><th>Data</th><th>Symbol</th><th>Wydarzenie</th><th>Boty</th><th>Blokada zakupów</th></tr></thead>
      <tbody>${rows}</tbody></table></div>` : `<p class="muted">${d.demo ? "W wersji demonstracyjnej kalendarz jest wyłączony." : "Brak wydarzeń w tym okresie (albo kalendarz jeszcze się wczytuje)."}</p>`)
      + `<p class="small muted" style="margin:10px 0 0">Śledzone spółki: ${st.have} z ${st.symbols}${st.pending ? `, w kolejce do pobrania: ${st.pending}` : ""}${st.updated ? ` · ostatnia aktualizacja ${when(st.updated)}` : ""}.
        Kalendarz pobiera się w tle (ok. 40 spółek na kwadrans), więc po pierwszym uruchomieniu lista uzupełnia się przez godzinę–dwie.
        Blokadę zakupów przed raportem włączasz w ustawieniach bota: „Bez zakupów przed raportem (dni)”. ETF-y nie mają raportów.
        Daty z Yahoo bywają szacunkowe (zakres dni), zanim spółka je potwierdzi.</p>`;
  };
  $$("#cal-days button").forEach(b => b.onclick = () => { days = +b.dataset.d; $$("#cal-days button").forEach(x => x.classList.toggle("on", x === b)); draw(); });
  await draw();
}

// ------------------------------------------------------------------ RADAR: przeglad i spolki
function radarTabs(active) {
  const t = [["", "Przegląd"], ["crypto", "Altcoiny"], ["gpw", "GPW"], ["usa", "USA"]];
  return `<div class="tabs">${t.map(([k, l]) => `<a class="tab ${k === active ? "on" : ""}" href="#/radar${k ? "/" + k : ""}">${l}</a>`).join("")}</div>`;
}
function moodTag(m) {
  if (!m) return "";
  const total = m.up + m.side + m.down || 1;
  const [l, c] = m.up / total >= 0.6 ? ["rynek rośnie", "up"] : m.down / total >= 0.6 ? ["rynek spada", "down"] : ["rynek mieszany", "warn"];
  return `<span class="f-tag ${c}">${l}</span>`;
}
async function renderRadarOverview() {
  const d = await api("GET", "/api/radar/overview?top=6");
  const sec = s => {
    const link = s.id === "crypto" ? "#/radar/crypto" : `#/radar/${s.id}`;
    const rows = s.items.map((r, i) => `<li><a href="#/chart/${encodeURIComponent(r.symbol)}" class="ov-row">
        <span class="ov-rank">${i + 1}</span><b class="mono">${esc(r.symbol.replace(".WSE", ""))}</b>
        ${r.bot_signal ? '<span class="chip sig" title="Wybicie nad maks. 34 dni przy cenie nad SMA 200 — warunek bota GPW">sygnał</span>' : "<span></span>"}
        <span class="ov-bar"><i style="width:${Math.max(4, r.score)}%"></i></span>
        <span class="mono ${tone(r.rel30)}" title="30 dni względem ${s.id === "crypto" ? "BTC" : "indeksu"}">${pct(r.rel30, 1)}</span></a></li>`).join("");
    return `<div class="card ov-card">
      <div class="ov-head"><div><h2 style="margin:0">${esc(s.label)}</h2><div class="small muted">${esc(s.sub)}</div></div>${moodTag(s.mood)}</div>
      ${s.items.length ? `<ol class="ov-list">${rows}</ol>
        <div class="ov-foot small muted"><span>skan ${when(s.ts)}</span><a href="${link}">Pełny ranking</a></div>`
        : `<div class="empty small">${s.available === false ? "Brak podłączonego źródła danych." : "Brak skanu."}
            <div style="margin-top:8px"><a class="btn small" href="${link}">Otwórz i skanuj</a></div></div>`}
    </div>`;
  };
  const sig = d.sections.flatMap(s => (s.signals || []).map(x => `${x.replace(".WSE", "")} (${s.label})`));
  view.innerHTML = `<div class="page-head"><div><h1>Radar</h1>
      <div class="muted small" style="margin-top:4px">Najmocniejsze altcoiny i spółki z GPW i USA — ranking siły, momentum i trendu, odświeżany codziennie.</div></div></div>
    ${radarTabs("")}
    ${sig.length ? `<div class="note" style="margin-bottom:14px">Dzisiejsze wybicia nad maksimum z 34 dni przy cenie nad SMA 200 (warunek wejścia bota GPW): <b class="mono">${esc(sig.join(", "))}</b></div>` : ""}
    <div class="ov-grid">${d.sections.map(sec).join("")}</div>
    <p class="muted small" style="margin-top:14px">Wynik to pozycja w rankingu (0–100), a liczba obok — zmiana z 30 dni względem BTC (krypto) albo indeksu (spółki).
      To lista kandydatów do obejrzenia, nie rekomendacja zakupu. Kliknij nazwę, żeby zobaczyć wykres.</p>`;
}
async function renderRadarStocks(market) {
  view.innerHTML = `<div class="page-head"><h1>Radar</h1><div class="actions" id="rs-actions"></div></div>${radarTabs(market)}
    <div id="rs-body" class="stack"><div class="empty">Ładowanie…</div></div>`;
  const draw = async () => {
    const d = await api("GET", `/api/radar/stocks?market=${market}`);
    const running = d.job?.state === "running";
    $("#rs-actions").innerHTML = d.available ? `<button class="btn primary" id="rs-scan" ${running ? "disabled" : ""}>${running ? "Skanuję…" : "Skanuj teraz"}</button>` : "";
    if ($("#rs-scan")) $("#rs-scan").onclick = async () => {
      try { await api("POST", `/api/radar/stocks/scan?market=${market}`); toast("Skan rozpoczęty — potrwa kilka minut"); draw(); } catch (e) { toast(e.message, true); } };
    const sc = d.scan, cur = d.currency;
    const rows = sc ? sc.items.map((r, i) => `<tr class="click" data-sym="${esc(r.symbol)}"><td class="num muted">${i + 1}</td>
        <td><b class="mono">${esc(r.symbol.replace(".WSE", ""))}</b>${r.bot_signal ? ' <span class="chip sig">sygnał</span>' : ""}</td>
        <td class="num"><b>${num(r.score, 0)}</b></td>${trendCells(r.outlook)}
        <td class="num ${tone(r.rel30)}">${pct(r.rel30, 1)}</td><td class="num ${tone(r.ret30)}">${pct(r.ret30, 1)}</td>
        <td class="num ${tone(r.ret7)}">${pct(r.ret7, 1)}</td>
        <td class="num ${r.dist_high > -3 ? "up" : r.dist_high < -25 ? "down" : ""}">${pct(r.dist_high, 1)}</td>
        <td>${r.above_sma200 == null ? "—" : r.above_sma200 ? '<span class="up">nad</span>' : '<span class="down">pod</span>'}</td>
        <td class="num ${r.vol_ratio >= 1.3 ? "up" : ""}">${num(r.vol_ratio, 2)}×</td>
        <td class="num muted">${num(r.turnover20 / 1e6, 1)} mln</td></tr>`).join("") : "";
    $("#rs-body").innerHTML = `
      <div class="note">Ranking ${market === "gpw" ? "spółek z GPW (dane IBKR)" : "dużych spółek z USA (dane Alpaki)"}. Wynik 0–100 = 30% siła względem
        ${esc(d.bench)} z 30 dni + 15% momentum z 7 dni + 15% wzrost obrotu + 25% bliskość szczytu z 52 tygodni + 15% trend (nad SMA 50 i SMA 200).
        „Sygnał” = dziś wybicie nad maksimum z 34 dni przy cenie nad SMA 200. Skan odbywa się sam po każdej sesji. To lista kandydatów, nie rekomendacja.</div>
      ${!d.available ? `<p class="note warn">Brak podłączonego źródła danych (${market === "gpw" ? "konto IBKR" : "konto Alpaca"}).</p>` : ""}
      ${d.job?.state === "error" ? `<p class="note warn">Skan nieudany: ${esc(d.job.error)}</p>` : ""}
      ${sc?.market ? marketCard(sc) : ""}
      <div class="card"><div class="card-head"><h2>Ranking ${sc ? `<span class="muted small">— ${when(sc.ts)}, ${sc.candidates} z ${sc.universe} spółek</span>` : ""}</h2></div>
        ${sc ? `<div class="table-wrap"><table><thead><tr><th>#</th><th>Spółka</th><th class="num">Wynik</th><th>Trend</th>
          <th class="num" title="Jak często i o ile spółka rosła w ciągu 30 dni w przeszłości, gdy była w takim samym trendzie jak dziś">Po 30 dniach (historycznie)</th>
          <th class="num">vs indeks 30 d</th><th class="num">30 dni</th><th class="num">7 dni</th><th class="num">Od szczytu 52 tyg.</th><th>SMA 200</th>
          <th class="num">Obrót 7/30 d</th><th class="num">Obrót dzienny (${esc(cur)})</th></tr></thead><tbody>${rows}</tbody></table></div>
          ${sc.illiquid?.length ? `<p class="muted small">Pominięte — mały obrót (poniżej ${num(sc.min_turnover / 1e6, 0)} mln ${esc(cur)} dziennie):
            ${esc(sc.illiquid.map(x => x.replace(".WSE", "")).join(", "))}</p>` : ""}
          ${sc.skipped?.length ? `<p class="muted small">Brak danych albo za krótka historia: ${esc(sc.skipped.map(x => x.replace(".WSE", "")).join(", "))}</p>` : ""}`
          : `<div class="empty">Brak skanu. ${d.available ? "Kliknij „Skanuj teraz”." : ""}</div>`}</div>`;
    $$("[data-sym]").forEach(tr => tr.onclick = () => { location.hash = `#/chart/${encodeURIComponent(tr.dataset.sym)}`; });
  };
  await draw();
  every(5000, async () => { if ($("#rs-scan")?.disabled) await draw().catch(() => {}); });
}

// ------------------------------------------------------------------ WYKRESY: dane finansowe i kalendarz
let fundChart = null, chartMarks = null;
const calCache = {};
function eventMarks(ev, symbol) {
  const M = chartMarks;
  if (!M || M.symbol !== symbol || !ev) return;
  const extra = [];
  (ev.past_reports || []).forEach(dt => { const t = M.snap(dt + "T12:00:00Z"); if (t) extra.push({ time: t, position: "aboveBar", shape: "square", color: css("--info"), text: "R" }); });
  (ev.past_ex_div || []).forEach(([dt]) => { const t = M.snap(dt + "T12:00:00Z"); if (t) extra.push({ time: t, position: "belowBar", shape: "square", color: "#b493ff", text: "D" }); });
  if (!extra.length) return;
  const all = [...M.marks, ...extra].sort((a, c) => a.time - c.time);
  try { M.candles.setMarkers(all); } catch (e) { /* wykres juz zamkniety */ }
}
function eventsLine(ev) {
  if (!ev) return "";
  const days = dt => Math.round((new Date(dt + "T00:00:00") - new Date(new Date().toDateString())) / 864e5);
  const fmtD = dt => new Date(dt + "T00:00:00").toLocaleDateString("pl-PL", { day: "numeric", month: "long", year: "numeric" });
  const parts = [];
  if (ev.earnings) { const n = days(ev.earnings);
    parts.push(`<span class="ev-tag ${n <= 7 ? "warn" : ""}">📅 Raport: <b>${fmtD(ev.earnings)}</b>${ev.earnings_end && ev.earnings_end !== ev.earnings ? ` – ${fmtD(ev.earnings_end)}` : ""} (${n === 0 ? "dziś" : n === 1 ? "jutro" : "za " + n + " dni"})</span>`); }
  if (ev.ex_div) parts.push(`<span class="ev-tag">💰 Odcięcie dywidendy: <b>${fmtD(ev.ex_div)}</b> (za ${days(ev.ex_div)} dni)${ev.div_pay ? `, wypłata ${fmtD(ev.div_pay)}` : ""}</span>`);
  const past = (ev.past_reports || []).length;
  return `<div class="ev-line">${parts.join("") || '<span class="ev-tag muted">Brak zapowiedzianej daty raportu ani dywidendy</span>'}
    ${past || (ev.past_ex_div || []).length ? `<span class="small muted">Na wykresie: <b style="color:var(--info)">R</b> = dzień raportu, <b style="color:#b493ff">D</b> = odcięcie dywidendy.</span>` : ""}</div>`;
}
async function loadFund(d, refresh) {
  const box = $("#ch-fund");
  if (!box) return;
  if (fundChart) { try { fundChart.destroy(); } catch (e) { /* juz usuniety */ } fundChart = null; }
  if (chartState.fund === false) { box.innerHTML = ""; return; }
  const last = d.bars[d.bars.length - 1][4], key = d.symbol;
  const calP = (!refresh && calCache[key]) ? Promise.resolve(calCache[key])
    : api("GET", `/api/calendar?symbol=${encodeURIComponent(key)}${refresh ? "&refresh=1" : ""}`).then(r => (calCache[key] = r.event)).catch(() => null);
  calP.then(ev => eventMarks(ev, key));
  let F = !refresh && fundCache[key];
  if (!F) {
    box.innerHTML = `<div class="card fund-card" style="margin-top:14px"><h2>Finanse</h2><p class="muted small">Pobieram dane finansowe ${esc(key)}…</p></div>`;
    try { F = await api("GET", `/api/fundamentals?symbol=${encodeURIComponent(key)}&price=${last}${refresh ? "&refresh=1" : ""}`); }
    catch (e) { F = { error: e.message }; }
    fundCache[key] = F;
  }
  if (!$("#ch-fund") || chartState.fund === false) return;
  const ev = await calP;
  if (!$("#ch-fund") || chartState.fund === false) return;
  $("#ch-fund").innerHTML = fundCard(F, ev);
  const rb = $("#fund-refresh"); if (rb) rb.onclick = () => loadFund(d, true);
  const cv = $("#fund-q");
  if (cv && F.quarters) {
    const grid = css("--line"), muted = css("--muted");
    fundChart = new Chart(cv, {
      type: "bar",
      data: { labels: F.quarters.periods, datasets: [
        { label: "Przychody", data: F.quarters.revenue, backgroundColor: css("--info"), borderRadius: 3, maxBarThickness: 30 },
        { label: "Zysk netto", data: F.quarters.net_income, backgroundColor: F.quarters.net_income.map(v => v < 0 ? css("--down") : css("--up")), borderRadius: 3, maxBarThickness: 30 }] },
      options: { responsive: true, maintainAspectRatio: false, animation: false,
        plugins: { legend: { labels: { color: muted, boxWidth: 12 } },
          tooltip: { callbacks: { label: it => ` ${it.dataset.label}: ${big(it.raw, F.currency)}` } } },
        scales: { x: { grid: { color: grid }, ticks: { color: muted } },
          y: { grid: { color: grid }, ticks: { color: muted, callback: v => big(v) } } } },
    });
  }
}
function big(v, cur) {
  if (v == null || isNaN(v)) return "—";
  const a = Math.abs(v), sgn = v < 0 ? "−" : "";
  const [d, u] = a >= 1e12 ? [1e12, " bln"] : a >= 1e9 ? [1e9, " mld"] : a >= 1e6 ? [1e6, " mln"] : a >= 1e4 ? [1e3, " tys."] : [1, ""];
  const n = new Intl.NumberFormat("pl-PL", { maximumFractionDigits: d === 1 ? 2 : a / d >= 100 ? 0 : a / d >= 10 ? 1 : 2 }).format(a / d);
  return sgn + n + u + (cur ? " " + cur : "");
}
function fundVal(v, fmt, cur) {
  if (v == null || isNaN(v)) return "—";
  if (fmt === "money") return big(v, cur);
  if (fmt === "pct") return pct(v, 1, false);
  if (fmt === "x") return num(v, v >= 100 ? 0 : 1) + "×";
  if (fmt === "eps") return num(v, 2) + " " + (cur || "");
  if (fmt === "count") return big(v);
  if (fmt === "rank") return "#" + num(v, 0);
  if (fmt === "price") return price(v) + " " + (cur || "");
  return num(v);
}
function fundCard(F, ev) {
  if (!F) return "";
  if (F.error) return `<div class="card" style="margin-top:14px"><h2>Finanse</h2>${eventsLine(ev)}<p class="muted small" style="margin:0">${esc(F.error)}</p></div>`;
  const cur = F.currency;
  const tiles = (F.valuation || []).map(v => `<div class="metric" title="${esc(v.hint || "")}"><div class="label">${esc(v.label)}</div>
    <div class="value ${v.label === "Od rekordu" || v.label.startsWith("Zmiana") ? tone(v.value) : ""}">${fundVal(v.value, v.fmt, cur)}</div></div>`).join("");
  let table = "";
  if (F.annual && F.annual.years.length) {
    const Y = F.annual.years;
    const yoy = r => { const a = r.values[r.values.length - 2], b = r.values[r.values.length - 1];
      return r.fmt === "money" && a && b != null && a > 0 ? (b / a - 1) * 100 : null; };
    table = `<div class="table-wrap fund-table"><table><thead><tr><th>Rok obrotowy</th>${Y.map(y => `<th class="num">${esc(y)}</th>`).join("")}<th class="num">r/r</th></tr></thead>
      <tbody>${F.annual.rows.map(r => { const g = yoy(r); return `<tr><td>${esc(r.label)}</td>${r.values.map(v => `<td class="num mono ${r.key === "net_income" || r.key === "fcf" ? (v < 0 ? "down" : "") : ""}">${fundVal(v, r.fmt, "")}</td>`).join("")}
        <td class="num small ${tone(g)}">${g == null ? "" : pct(g, 0)}</td></tr>`; }).join("")}</tbody></table></div>
      <p class="muted small" style="margin:6px 0 0">Kwoty w ${esc(cur)}. Rok obrotowy oznaczony rokiem, w którym się kończy.</p>`;
  }
  const lr = F.last_report;
  return `<div class="card fund-card" style="margin-top:14px">
    <div class="card-head"><div><h2 style="margin:0">Finanse: ${esc(F.name || F.symbol)}</h2>
      ${F.sector ? `<div class="small muted">${esc(F.sector)}${F.employees ? ` · ${num(F.employees, 0)} pracowników` : ""}</div>` : ""}</div>
      <button class="btn small ghost" id="fund-refresh" type="button" title="Pobierz dane ponownie (zwykle odświeżają się same co 12 h)">Odśwież</button></div>
    ${F.kind !== "crypto" ? eventsLine(ev) : ""}
    ${tiles ? `<div class="grid metrics fund-metrics">${tiles}</div>` : ""}
    ${F.quarters && F.quarters.periods.length ? `<h3 class="fund-h">Ostatnie kwartały</h3><div class="fund-q"><canvas id="fund-q"></canvas></div>` : ""}
    ${table ? `<h3 class="fund-h">Wyniki roczne</h3>${table}` : ""}
    ${F.about ? `<details style="margin-top:10px"><summary class="small">O ${F.kind === "crypto" ? "projekcie" : F.kind === "fund" ? "funduszu" : "spółce"}</summary><p class="small muted">${esc(F.about)}${F.about.length >= 600 ? "…" : ""}</p></details>` : ""}
    <p class="muted small" style="margin:10px 0 0">Źródło: <a href="${esc(F.source_url)}" target="_blank" rel="noopener noreferrer">${esc(F.source)}</a>${lr && lr.period ? ` · ostatni raport za okres do ${esc(lr.period)}${lr.form ? ` (${esc(lr.form)}${lr.filed ? `, złożony ${esc(lr.filed)}` : ""})` : ""}` : ""}.
      ${F.kind === "stock" ? "Wskaźniki zależne od ceny liczone dla ostatniej ceny z wykresu. Dane z publicznych raportów mogą mieć opóźnienie albo braki" : "Dane z chwili pobrania (odświeżają się co 12 h)"} — to tło do decyzji, nie rekomendacja.</p></div>`;
}

function planCard(P) {
  if (!P) return "";
  if (P.error) return `<div class="card" style="margin-top:14px"><b class="warn">Plan:</b> ${esc(P.error)}</div>`;
  const st = { in: ["W pozycji (przykład)", "info"], signal: ["Sygnał kupna dziś", "up"], wait: ["Czeka na sygnał", "muted"] }[P.state];
  const s = P.stats, rows = [...P.trades].reverse().slice(0, 8).map(t => `<tr><td class="small">${new Date(t.entry_t * 1000).toLocaleDateString("pl-PL")}</td>
    <td class="num">${price(t.entry)}</td><td class="small">${new Date(t.exit_t * 1000).toLocaleDateString("pl-PL")}</td><td class="num">${price(t.exit)}</td>
    <td class="num ${tone(t.pct)}">${pct(t.pct, 1)}</td><td class="small muted">${esc(t.reason)}</td></tr>`).join("");
  return `<div class="card plan-card" style="margin-top:14px">
    <div class="card-head"><h2 style="margin:0">Plan: ${esc(P.template)}</h2><span class="f-tag ${st[1]}">${st[0]}</span></div>
    <p class="plan-text">${esc(P.text)}</p>
    ${P.checks.length ? `<ul class="plan-checks">${P.checks.map(c => `<li class="${c.ok ? "up" : "muted"}">${c.ok ? "✓" : "✗"} ${esc(c.label)}</li>`).join("")}</ul>` : ""}
    <div class="grid metrics">
      ${metric("Przykładowe ruchy na wykresie", s.n)}${metric("Trafne", s.win == null ? "—" : num(s.win, 0) + "%")}
      ${metric("Średnio na ruch", s.avg == null ? "—" : `<span class="${tone(s.avg)}">${pct(s.avg, 1)}</span>`)}
      ${metric("Łącznie (po kolei)", s.total == null ? "—" : `<span class="${tone(s.total)}">${pct(s.total, 1)}</span>`)}
      ${metric("Stop-loss / take-profit", `−${num(P.sl_pct * 100, 0)}% / +${num(P.tp_pct * 100, 0)}%`)}
    </div>
    ${rows ? `<details style="margin-top:10px"><summary class="small">Ostatnie przykładowe ruchy</summary><div class="table-wrap"><table>
      <thead><tr><th>Wejście</th><th class="num">Cena</th><th>Wyjście</th><th class="num">Cena</th><th class="num">Wynik</th><th>Powód</th></tr></thead>
      <tbody>${rows}</tbody></table></div></details>` : ""}
    <p class="muted small" style="margin:10px 0 0">Na wykresie: ● W = przykładowe wejście strategii, ● z % = wyjście i wynik; linie przerywane = poziomy planu.
      Ilustracja zasad na tym jednym symbolu — bez filtra rynku i kosztów. To nie rekomendacja zakupu.</p></div>`;
}

const CANDLE_SHORT = { doji: "Doji", hammer: "Młot", shooting: "Sp. gwiazda", bull_engulf: "Objęcie ↑", bear_engulf: "Objęcie ↓",
  morning: "Gw. poranna", evening: "Gw. wieczorna" };
function patternsCard(X) {
  if (!X) return "";
  if (X.error) return `<div class="card" style="margin-top:14px"><b class="warn">Formacje:</b> ${esc(X.error)}</div>`;
  const tag = k => k === "bull" ? '<span class="f-tag up">wzrostowa</span>' : k === "bear" ? '<span class="f-tag down">spadkowa</span>' : '<span class="f-tag muted">neutralna</span>';
  const b = { bull: ["Przewaga wzrostowa", "up"], bear: ["Przewaga spadkowa", "down"], neutral: ["Sygnały mieszane", "warn"] }[X.bias];
  const day = t => new Date(t * 1000).toLocaleDateString("pl-PL");
  const pats = X.patterns.map(p => `<li><div class="pt-head"><b>${esc(p.name)}</b>${tag(p.kind)}
      <span class="f-tag ${p.confirmed ? "info" : "muted"}">${p.confirmed ? "potwierdzona" : "w trakcie"}</span><span class="small muted">${day(p.t_end)}</span></div>
      <div class="small">${esc(p.text)}</div></li>`).join("");
  const cnd = X.candles.map(c => `<li><div class="pt-head"><b>${esc(c.name)}</b>${tag(c.kind)}<span class="small muted">${day(c.t)}</span></div>
      <div class="small">${esc(c.text)}</div>
      ${c.n >= 5 ? `<div class="small muted">Na tym symbolu po takiej świecy (${c.n}×): po ${c.horizon} sesjach cena wyżej w ${num(c.p_up, 0)}% przypadków,
        mediana <span class="${tone(c.med)}">${pct(c.med, 1)}</span>.</div>` : `<div class="small muted">Za mało przypadków w historii tego symbolu, żeby ocenić skuteczność.</div>`}</li>`).join("");
  const ind = X.indicators.map(i => `<li><div class="pt-head"><b>${esc(i.name)}</b>${tag(i.kind)}</div><div class="small">${esc(i.text)}</div></li>`).join("");
  const lv = [...X.levels].sort((a, c) => c.price - a.price).map(l => `<span class="lv ${l.kind}">${l.kind === "support" ? "wsparcie" : "opór"}
      <b class="mono">${price(l.price)}</b> <span class="muted">×${l.touches}</span></span>`).join("");
  const lines = X.lines.map(l => `<li class="small">${esc(l.text)}</li>`).join("");
  return `<div class="card pat-card" style="margin-top:14px">
    <div class="card-head"><h2 style="margin:0">Formacje i sygnały</h2><span class="f-tag ${b[1]}">${b[0]}</span></div>
    <p class="plan-text">${esc(X.text)}</p>
    ${lv ? `<div class="lv-row">${lv}</div>` : ""}
    <div class="pat-cols">
      <div><h3>Formacje cenowe</h3>${pats ? `<ul class="pt-list">${pats}</ul>` : '<p class="small muted">Brak wyraźnej formacji w ostatnich miesiącach.</p>'}
        ${lines ? `<h3 style="margin-top:12px">Linie trendu</h3><ul class="pt-list">${lines}</ul>` : ""}</div>
      <div><h3>Świece (ostatnie 10 sesji)</h3>${cnd ? `<ul class="pt-list">${cnd}</ul>` : '<p class="small muted">Brak charakterystycznych świec.</p>'}</div>
      <div><h3>Wskaźniki</h3><ul class="pt-list">${ind}</ul></div>
    </div>
    <p class="muted small" style="margin:0">Formacje wykrywa algorytm z punktów zwrotnych (5 świec z każdej strony) — to pomoc w czytaniu wykresu, nie pewna prognoza.
      Linia ciągła = formacja potwierdzona, przerywana = w trakcie; poziome linie = wsparcia (niebieskie) i opory (pomarańczowe) z liczbą dotknięć.</p></div>`;
}

// katalog formacji do wlaczania / wylaczania (klucze jak w app/patterns.py)
const FORMATIONS = [
  { name: "Poziomy", items: [["levels", "Wsparcia i opory"], ["trendlines", "Linie trendu"]] },
  { name: "Formacje odwrócenia", items: [["hs", "Głowa z ramionami"], ["ihs", "Odwrócona głowa z ramionami"], ["double_top", "Podwójny szczyt"],
    ["double_bottom", "Podwójne dno"], ["triple_top", "Potrójny szczyt"], ["triple_bottom", "Potrójne dno"], ["rounding", "Spodek"],
    ["wedge_up", "Klin rosnący"], ["wedge_down", "Klin opadający"]] },
  { name: "Kontynuacja i wybicia", items: [["tri_sym", "Trójkąt symetryczny"], ["tri_asc", "Trójkąt zwyżkujący"], ["tri_desc", "Trójkąt zniżkujący"],
    ["flag_bull", "Flaga wzrostowa"], ["flag_bear", "Flaga spadkowa"], ["rectangle", "Prostokąt"], ["cup", "Filiżanka z uszkiem"],
    ["breakout", "Wybicie oporu"], ["breakdown", "Przebicie wsparcia"], ["gap_up", "Luka wzrostowa"], ["gap_down", "Luka spadkowa"]] },
  { name: "Świece", items: [["doji", "Doji"], ["hammer", "Młot"], ["hanging", "Wisielec"], ["shooting", "Spadająca gwiazda"],
    ["bull_engulf", "Objęcie hossy"], ["bear_engulf", "Objęcie bessy"], ["bull_harami", "Harami hossy"], ["bear_harami", "Harami bessy"],
    ["morning", "Gwiazda poranna"], ["evening", "Gwiazda wieczorna"], ["soldiers", "Trzech białych żołnierzy"], ["crows", "Trzy czarne wrony"]] },
  { name: "Wskaźniki", items: [["indicators", "RSI, SMA 200, krzyże, wolumen"]] },
];
Object.assign(CANDLE_SHORT, { hanging: "Wisielec", bull_harami: "Harami ↑", bear_harami: "Harami ↓", soldiers: "3 żołnierzy", crows: "3 wrony" });
function patternSettings() {
  const off = new Set(chartState.off || []);
  return `<div class="pf-head"><b>Pokazuj na wykresie i w analizie</b><span class="pf-btns">
      <button class="btn small" id="pf-all" type="button">Wszystkie</button><button class="btn small" id="pf-none" type="button">Żadne</button>
      <button class="btn small" id="pf-def" type="button">Domyślne</button></span></div>
    <div class="pf-grid">${FORMATIONS.map(g => `<div><div class="pf-group">${g.name}</div>${g.items.map(([k, l]) =>
      `<label class="check pf-item"><input type="checkbox" data-fk="${k}" ${off.has(k) ? "" : "checked"}> ${esc(l)}</label>`).join("")}</div>`).join("")}</div>`;
}
function filterPatterns(P) {
  if (!P || P.error) return P;
  const off = new Set(chartState.off || []);
  const X = { levels: off.has("levels") ? [] : P.levels, lines: off.has("trendlines") ? [] : P.lines,
    patterns: P.patterns.filter(p => !off.has(p.key)), candles: P.candles.filter(c => !off.has(c.key)),
    indicators: off.has("indicators") ? [] : P.indicators };
  const score = [...X.patterns, ...X.candles, ...X.indicators, ...X.lines].reduce((a, x) => a + (x.w || 0), 0);
  X.bias = score >= 2 ? "bull" : score <= -2 ? "bear" : "neutral";
  X.text = { bull: "Przewaga sygnałów wzrostowych.", bear: "Przewaga sygnałów spadkowych.", neutral: "Sygnały mieszane — brak wyraźnej przewagi." }[X.bias]
    + (X.levels.length && P.summary.near ? " " + P.summary.near : "");
  return X;
}


// ------------------------------------------------------------------ SIATKA / USREDNIANIE (karta na stronie bota)
function specialCard(b) {
  const p = b.params, rows = b.special || [];
  if (!rows.length) return `<div class="card" style="margin-bottom:14px"><h2>${b.strategy === "grid" ? "Siatka" : "Uśrednianie"}</h2>
    <div class="empty">Stan pojawi się po pierwszym cyklu bota (po starcie).</div></div>`;
  if (b.strategy === "grid") {
    return `<div class="card" style="margin-bottom:14px"><div class="card-head"><h2>Siatka</h2>
      <span class="muted small">${p.grid_levels} poziomów · ±${num(p.grid_range_pct * 100, 0)}% · stop ${p.grid_stop_pct ? num(p.grid_stop_pct * 100, 0) + "% pod siatką" : "wyłączony"}</span></div>
      <div class="table-wrap"><table><thead><tr><th>Symbol</th><th class="num">Dół</th><th class="num">Góra</th><th class="num">Krok</th>
        <th>Zajęte poziomy</th><th class="num">Porcja</th><th class="num">Zysk siatki</th></tr></thead><tbody>
      ${rows.map(r => { const L = r.levels, n = L.length;
        const bar = L.slice(0, -1).map((_, i) => `<span title="poziom ${i + 1}: ${price(L[i])}" style="display:inline-block;width:8px;height:14px;margin-right:2px;border-radius:2px;background:${r.held.includes(i) ? "var(--up)" : "var(--panel-2)"}"></span>`).join("");
        return `<tr><td><b>${esc(r.symbol)}</b>${r.stopped ? ` <span class="f-tag down">stop</span>` : ""}</td>
          <td class="num">${price(L[0])}</td><td class="num">${price(L[n - 1])}</td><td class="num">${pct((L[1] / L[0] - 1) * 100, 2, false)}</td>
          <td>${bar} <span class="muted small">${r.held.length}/${n - 1}</span></td><td class="num">${usd(r.slot_value)}</td>
          <td class="num ${tone(r.realized)}">${usd(r.realized, true)}</td></tr>`; }).join("")}</tbody></table></div>
      <p class="muted small" style="margin-top:8px">Zielone = kupiona porcja, czeka na sprzedaż poziom wyżej. Bot nie stawia zleceń na serwerze — pilnuje cen co cykl.</p></div>`;
  }
  return `<div class="card" style="margin-bottom:14px"><div class="card-head"><h2>Uśrednianie</h2>
    <span class="muted small">${p.dca_mode === "regular" ? `zakup co ${p.dca_every_hours} h` : `dokupienie co ${num(p.dca_step_pct * 100, 1)}% spadku · ×${num(p.dca_mult, 2)} · maks. ${p.dca_max_safety}`}
    · sprzedaż ${p.dca_tp_pct ? `+${num(p.dca_tp_pct * 100, 1)}% nad średnią` : "—"}</span></div>
    <div class="table-wrap"><table><thead><tr><th>Symbol</th><th class="num">Ilość</th><th class="num">Średnia</th><th class="num">Wydane</th>
      <th class="num">Dokupienia</th><th class="num">${p.dca_mode === "regular" ? "Następny zakup" : "Następne dokupienie"}</th><th class="num">Rundy</th><th class="num">Zysk</th></tr></thead><tbody>
    ${rows.map(r => `<tr><td><b>${esc(r.symbol)}</b></td><td class="num">${num(r.qty, 6)}</td><td class="num">${r.qty ? price(r.avg) : "—"}</td>
      <td class="num">${usd(r.spent)}</td><td class="num">${r.n_safety}</td>
      <td class="num">${p.dca_mode === "regular" ? (r.next_t ? when(new Date(r.next_t * 1000).toISOString()) : "—") : (r.next_buy ? price(r.next_buy) : "—")}</td>
      <td class="num">${r.rounds}</td><td class="num ${tone(r.realized)}">${usd(r.realized, true)}</td></tr>`).join("")}</tbody></table></div></div>`;
}

// ------------------------------------------------------------------ ALERTY TRADINGVIEW (karta na stronie bota)
async function drawTv(id) {
  const box = $("#tv-box");
  const d = await api("GET", `/api/bots/${id}/tv`);
  const msg = `{"symbol": "{{ticker}}", "action": "{{strategy.order.action}}", "price": {{close}}${d.passphrase ? ', "passphrase": "TWOJE_HASLO"' : ""}}`;
  const ST = { new: ["czeka", "info"], done: ["wykonany", "up"], expired: ["za stary", "muted"], rejected: ["odrzucony", "down"] };
  box.innerHTML = `<div class="card-head"><h2>Alerty z TradingView</h2><button class="btn ghost small" id="tv-rot">Nowy adres</button></div>
    <div class="grid two">
      <div class="stack">
        <div class="field"><label>Adres webhooka (wklej w TradingView → Alert → Powiadomienia → Webhook URL)</label>
          ${d.url ? `<div style="display:flex;gap:6px"><input readonly class="mono" id="tv-url" value="${esc(d.url)}" style="flex:1"><button class="btn small" data-copy="tv-url">Kopiuj</button></div>`
            : `<p class="note warn">Brak publicznego adresu. Odbiornik działa na porcie <b>${d.port}</b>, ścieżka <span class="mono">/tv/${esc(d.token)}</span>.
               TradingView musi go widzieć z internetu — najprościej Tailscale Funnel (<span class="mono">tailscale funnel --bg ${d.port}</span>),
               potem wpisz adres w <span class="mono">.env</span> jako <span class="mono">TV_WEBHOOK_URL=https://…ts.net</span> i zrestartuj. Instrukcja w README.</p>`}
          <div class="help">Adres to hasło: kto go zna, może wysłać alert. Nie publikuj go. „Nowy adres” unieważnia stary.</div></div>
        <div class="field"><label>Treść alertu (Message)</label>
          <div style="display:flex;gap:6px"><input readonly class="mono" id="tv-msg" value="${esc(msg)}" style="flex:1"><button class="btn small" data-copy="tv-msg">Kopiuj</button></div>
          <div class="help">W alertach ze wskaźnika zamiast <span class="mono">{{strategy.order.action}}</span> wpisz <span class="mono">buy</span> albo <span class="mono">sell</span>.
            Akcje: buy, sell, short, cover, close. Symbole bota: ${esc(d.symbols.join(", "))}.</div></div>
      </div>
      <div><div class="muted small" style="margin-bottom:6px">Ostatnie alerty</div>
        ${!d.alerts.length ? `<div class="empty">Jeszcze nic nie przyszło.</div>` : `<div class="table-wrap" style="max-height:260px;overflow:auto"><table><thead><tr><th>Czas</th><th>Symbol</th><th>Akcja</th><th class="num">Cena</th><th>Status</th></tr></thead><tbody>
        ${d.alerts.map(a => { const [t, c] = ST[a.status] || [a.status, "muted"];
          return `<tr><td class="small">${when(a.ts)}</td><td><b>${esc(a.symbol)}</b></td><td>${esc(a.action)}</td><td class="num">${a.price == null ? "—" : price(a.price)}</td>
            <td><span class="f-tag ${c}">${t}</span>${a.note ? `<div class="small muted">${esc(a.note)}</div>` : ""}</td></tr>`; }).join("")}</tbody></table></div>`}</div>
    </div>`;
  $$("[data-copy]", box).forEach(b => b.onclick = async () => {
    const el = $("#" + b.dataset.copy); el.select();
    try { await navigator.clipboard.writeText(el.value); } catch { document.execCommand("copy"); }
    toast("Skopiowano");
  });
  $("#tv-rot").onclick = async () => {
    if (!confirmBox("Utworzyć nowy adres? Stary przestanie działać — trzeba będzie podmienić go w alertach TradingView.")) return;
    try { await api("POST", `/api/bots/${id}/tv/rotate`); toast("Nowy adres gotowy"); drawTv(id); } catch (e) { toast(e.message, true); }
  };
}

// ------------------------------------------------------------------ GALERIA STRATEGII + STRATEGIA Z OPISU (AI)
const RISK_TONE = { "niskie": "up", "średnie": "warn", "wysokie": "down", "bardzo wysokie": "down" };
function accountFor(source, market) {
  const want = { ibkr: "ibkr", kraken: "kraken", alpaca: "alpaca" }[source];
  return (SCHEMA.accounts.find(a => a.type === want) || SCHEMA.accounts.find(a => market === "crypto" ? ["kraken", "gielda", "alpaca", "sim"].includes(a.type) : a.type !== "kraken" && a.type !== "gielda") || SCHEMA.accounts[0])?.name;
}
function presetBacktest(it) {
  sessionStorage.setItem("bt_preset", JSON.stringify({ market: it.market, strategy: it.strategy, params: it.params, name: it.name,
    start: it.start, end: it.end, data_source: it.data_source }));
  location.hash = "#/backtests";
}
function presetBot(it) {
  botForm(null, { name: it.name, account: accountFor(it.data_source, it.market), market: it.market, strategy: it.strategy, params: it.params });
}
function galleryCard(it) {
  const r = it.results;
  return `<div class="card gal-card">
    <div class="card-head" style="align-items:flex-start"><div><div class="muted small">${esc(it.category)}</div><h2 style="margin-top:2px">${esc(it.name)}</h2></div>
      <span class="f-tag ${RISK_TONE[it.risk] || "muted"}" title="ryzyko" style="flex-shrink:0">ryzyko: ${esc(it.risk)}</span></div>
    <p class="small" style="margin:6px 0 10px">${esc(it.description)}</p>
    ${r ? `<div class="grid metrics gal-metrics">
        ${metric("Zwrot", pct(r.total), tone(r.total))}${metric("Rocznie", pct(r.cagr), tone(r.cagr))}
        ${metric("Max obsunięcie", pct(r.dd), "down")}${metric("Kup i trzymaj", r.bench == null ? "—" : pct(r.bench), tone(r.bench))}</div>
        ${r.curve ? `<div style="position:relative;height:46px;margin-top:10px" title="kapitał w czasie">${spark(r.curve, 400, 46)}</div>` : ""}
        <div class="muted small" style="margin-top:6px">Backtest ${esc(r.from)} → ${esc(r.to)}${it.strategy === "dca" ? "" : ` · ${r.trades} transakcji · skuteczność ${r.win == null ? "—" : pct(r.win, 0, false)}`}
          · Sharpe ${r.sharpe == null ? "—" : num(r.sharpe, 2)} · dane: ${esc(r.source || it.data_source)}, z kosztami transakcji</div>`
      : `<p class="note">Wyniki backtestu w przygotowaniu — kliknij „Backtest na moich danych”, żeby policzyć je od razu.</p>`}
    <div class="muted small" style="margin-top:8px">${MARKET[it.market]} · ${esc(it.strategy_name)} · ${esc(it.params.timeframe)} · ${it.params.symbols.length} symb. · wymaga: ${esc(it.needs)}</div>
    <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:12px">
      <button class="btn small" data-gbt="${esc(it.id)}">Backtest na moich danych</button>
      <button class="btn primary small" data-gbot="${esc(it.id)}">Utwórz bota</button></div>
  </div>`;
}
async function renderGallery() {
  const [g, ai] = await Promise.all([api("GET", "/api/gallery"), api("GET", "/api/ai/status")]);
  const cats = [...new Set(g.items.map(x => x.category))];
  view.innerHTML = `
    <div class="page-head"><div><h1>Galeria strategii</h1>
      <div class="muted small" style="margin-top:4px">Gotowe ustawienia z wynikami testów na prawdziwych danych historycznych, z kosztami transakcji${g.computed ? ` (policzone ${esc(g.computed)})` : ""}.
        W silnej hossie 2021–2026 większość strategii zarobiła mniej niż samo trzymanie indeksu („Kup i trzymaj”) — ich zaletą jest zwykle mniejsze obsunięcie i czas poza rynkiem.
        Wyniki z przeszłości nie gwarantują przyszłych — zacznij od konta na papierze.</div></div></div>
    <div class="card" style="margin-bottom:14px"><div class="card-head"><h2>Opisz strategię słowami</h2>
      <span class="muted small">${ai.enabled ? `AI: ${esc(ai.model)}` : "wymaga klucza API Claude"}</span></div>
      ${ai.enabled ? `<form class="form" id="ai-form">
        <textarea id="ai-text" rows="3" maxlength="3000" placeholder="Np. Kupuj duże spółki z WIG20, gdy kurs przebije maksimum z 50 dni i RSI jest poniżej 70, ale tylko gdy WIG20 jest nad średnią 200 dni. Stop 8%, zysk 20%."></textarea>
        <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
          <select id="ai-market" style="width:auto"><option value="">rynek: dowolny</option><option value="stocks">akcje / ETF</option><option value="crypto">krypto</option></select>
          <select id="ai-account" style="width:auto">${SCHEMA.accounts.map(a => `<option value="${esc(a.name)}">konto ${esc(a.name)} (${esc(accDesc(a))})</option>`).join("")}</select>
          <button class="btn primary" type="submit" id="ai-go">Zamień na ustawienia</button>
          <span class="muted small">Nic się nie uruchomi samo — najpierw zobaczysz wynik.</span></div>
      </form><div id="ai-out"></div>`
      : `<p class="note">Dopisz do pliku <span class="mono">.env</span> własny klucz <span class="mono">ANTHROPIC_API_KEY=…</span> (console.anthropic.com, płatny za użycie —
         jedno tłumaczenie to ułamek centa) i zrestartuj aplikację. Wtedy wystarczy opisać strategię zwykłym zdaniem, a panel ułoży z niej bota.</p>`}
    </div>
    ${cats.map(c => `<h2 style="margin:18px 0 10px">${esc(c)}</h2>
      <div class="grid" style="grid-template-columns:repeat(auto-fill,minmax(min(100%,400px),1fr))">${g.items.filter(x => x.category === c).map(galleryCard).join("")}</div>`).join("")}`;
  const byId = Object.fromEntries(g.items.map(x => [x.id, x]));
  $$("[data-gbt]").forEach(b => b.onclick = () => presetBacktest(byId[b.dataset.gbt]));
  $$("[data-gbot]").forEach(b => b.onclick = () => presetBot(byId[b.dataset.gbot]));
  const form = $("#ai-form");
  if (form) form.onsubmit = async e => {
    e.preventDefault();
    const out = $("#ai-out"), btn = $("#ai-go");
    btn.disabled = true; btn.textContent = "Myślę…";
    out.innerHTML = `<div class="empty">Tłumaczę opis na ustawienia (do ok. 30 s)…</div>`;
    try {
      const r = await api("POST", "/api/ai/strategy", { text: $("#ai-text").value, market: $("#ai-market").value || null, account: $("#ai-account").value });
      const p = r.params, strat = SCHEMA.strategies.find(s => s.key === r.strategy);
      out.innerHTML = `<div class="card" style="margin-top:12px;background:var(--panel-2)">
        <div class="card-head"><h2>${esc(r.name || "Strategia")}</h2><span class="muted small">${MARKET[r.market]} · ${esc(strat?.name || r.strategy)} · ${esc(p.timeframe)}</span></div>
        <p>${esc(r.explanation || "")}</p>
        ${r.strategy === "rules" ? `<p class="note">${esc(rulesSummary(p))}</p>` : ""}
        <div class="muted small" style="margin:8px 0">Symbole: ${esc(p.symbols.join(", "))} · budżet ${pct(p.allocation_pct * 100, 0, false)}
          ${["grid", "dca"].includes(r.strategy) ? "" : `· SL ${pct(p.stop_loss_pct * 100, 1, false)} · TP ${pct(p.take_profit_pct * 100, 1, false)}`}
          ${p.leverage_mode && p.leverage_mode !== "off" ? levTag({ mode: p.leverage_mode, direction: p.direction, leverage: p.leverage }) : ""}</div>
        ${r.assumptions.length ? `<div class="small"><b>Założenia:</b><ul style="margin:4px 0 8px 18px">${r.assumptions.map(x => `<li>${esc(x)}</li>`).join("")}</ul></div>` : ""}
        ${r.warnings.length ? `<div class="note warn"><b>Uwagi:</b> ${r.warnings.map(esc).join(" · ")}</div>` : ""}
        ${r.errors?.length ? `<p class="err">Panel nie przyjął ustawień: ${esc(r.errors.join("; "))}. Spróbuj opisać inaczej.</p>` : `
        <div style="display:flex;gap:8px;margin-top:12px"><button class="btn" id="ai-bt">Backtest</button>
          <button class="btn primary" id="ai-bot">Utwórz bota (do sprawdzenia)</button></div>`}</div>`;
      const it = { name: r.name || "Strategia AI", market: r.market, strategy: r.strategy, params: p, data_source: null };
      $("#ai-bt") && ($("#ai-bt").onclick = () => presetBacktest(it));
      $("#ai-bot") && ($("#ai-bot").onclick = () => botForm(null, { ...it, account: $("#ai-account").value }));
    } catch (err) { out.innerHTML = `<p class="err">${esc(err.message)}</p>`; }
    finally { btn.disabled = false; btn.textContent = "Zamień na ustawienia"; }
  };
}


// ------------------------------------------------------------------ NAUKA DŹWIGNI (karta w Laboratorium)
async function drawLevStudy() {
  const box = $("#lev-study");
  if (!box) return;
  const d = await api("GET", "/api/lab/leverage-study");
  drawLevStudy.running = d.status === "running";
  const ok = d.candidates.filter(c => c.ok);
  const cell = s => s ? `<span class="${tone(s.ret)}">${pct(s.ret, 1)}</span> <span class="muted small">/ ${pct(s.dd, 1)}</span>` : "—";
  const bots = (d.bots || []).map(r => {
    if (r.skipped) return `<p class="muted small">${esc(r.name)}: pominięty — ${esc(r.skipped)}</p>`;
    const base = r.variants.find(v => v.key === "base");
    return `<details ${r.seeded.length ? "open" : ""} style="margin-top:10px"><summary><b>${esc(r.name)}</b>
        <span class="muted small">· ${esc(r.period[0])} → ${esc(r.period[1])}, sprawdzian od ${esc(r.split || "")}</span>
        ${r.error ? `<span class="f-tag down">${esc(r.error)}</span>` : r.seeded.length ? `<span class="f-tag up">${r.seeded.length} → laboratorium</span>`
          : (r.kept || []).length ? `<span class="f-tag info">${r.kept.length} dalej w grze</span>`
          : d.status === "done" ? `<span class="f-tag muted">dźwignia nie poprawia wyniku</span>` : ""}
        ${(r.retired || []).length ? `<span class="f-tag muted">${r.retired.length} wycofane</span>` : ""}</summary>
      <div class="table-wrap"><table><thead><tr><th>Wariant</th><th class="num">Cały okres</th><th class="num">Obsunięcie</th>
        <th class="num">Nauka: zwrot / obs.</th><th class="num">Sprawdzian: zwrot / obs.</th><th>Werdykt</th></tr></thead><tbody>
      ${r.variants.map(v => `<tr><td>${v.key === "base" ? "👑 " : ""}${esc(v.label)}</td>
        <td class="num ${tone(v.total)}">${v.total == null ? "—" : pct(v.total, 1)}</td><td class="num down">${v.dd == null ? "—" : pct(v.dd, 1)}</td>
        <td class="num">${cell(v.is)}</td><td class="num">${cell(v.oos)}</td>
        <td class="small">${v.error ? `<span class="down">${esc(v.error)}</span>` : v.key === "base" ? '<span class="muted">punkt odniesienia</span>'
          : !v.why ? '<span class="muted">liczy się…</span>' : v.why.length ? `<span class="muted">${esc(v.why.join(", "))}</span>`
          : `<span class="up">${r.seeded.includes(v.label) ? "przechodzi → laboratorium" : (r.kept || []).includes(v.label) ? "przechodzi — dalej w grze" : "przechodzi"}</span>`}</td></tr>`).join("")}
      </tbody></table></div></details>`;
  }).join("");
  const pl = d.plan || {};
  const nBots = `${ok.length} bot${ok.length === 1 ? "" : "ów"}`;
  box.innerHTML = `<div class="card-head"><h2>Nauka dźwigni na historii</h2>
      ${d.status === "running" ? `<span class="muted small">trwa… ${num((d.progress || 0) * 100, 0)}%</span>`
        : `<div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
            ${pl.next_at ? "" : `<label class="check small" style="padding:0"><input type="checkbox" id="ls-weekly"> potem co tydzień</label>
              <button class="btn small primary" id="ls-night" ${ok.length ? "" : "disabled"}>Zaplanuj na noc (01:00, ${nBots})</button>`}
            <button class="btn small" id="ls-go" ${ok.length ? "" : "disabled"}>Uruchom teraz</button></div>`}</div>
    ${pl.next_at ? `<div class="note" style="margin:0 0 10px;display:flex;gap:10px;align-items:center;justify-content:space-between;flex-wrap:wrap">
        <span>🌙 Zaplanowano: <b>${when(pl.next_at)}</b>${pl.weekly ? ", potem co tydzień w nocy z soboty na niedzielę" : ""}.
        Wynik przyjdzie powiadomieniem.</span><button class="btn ghost small" id="ls-cancel">Odwołaj</button></div>` : ""}
    <p class="muted small" style="margin:0 0 6px">Dla każdego bota panel testuje na prawdziwej historii jego wersje z dźwignią i grą na spadki
      (z prowizjami i odsetkami). Wariant trafia do laboratorium tylko, gdy w <b>sprawdzianie</b> (ostatnie 35% okresu, niewidziane przy wyborze)
      zarabia więcej, a obecne ustawienia same zarabiają. Stosunek zysku do obsunięcia może spaść najwyżej o 10%, a obsunięcie musi zostać w granicach 30%.
      Bot, który już gra z dźwignią, uczy się też ją zmniejszać, gdy to wyraźnie poprawia ten stosunek. Potem wariant musi wygrać jeszcze na żywo, na niby
      (min. transakcji i dni z ustawień laboratorium, obsunięcie do 25%) — dopiero wtedy przejmuje bota
      (konto papierowe: samo, prawdziwe pieniądze: po Twojej zgodzie, a dźwignia dodatkowo wymaga zgody w zakładce Ryzyko).
      Inne warianty dźwigni do laboratorium nie wchodzą. Nauka trwa od kilkunastu minut do kilku godzin.</p>
    ${d.status === "running" ? `<div class="progress" style="margin:8px 0"><div style="width:${(d.progress || 0) * 100}%"></div></div>` : ""}
    ${d.finished ? `<p class="muted small">Ostatnia nauka: ${when(d.finished)}</p>` : ""}
    ${d.status === "error" ? `<p class="err">${esc(d.error || "błąd")}</p>` : ""}
    ${bots}
    ${d.candidates.filter(c => !c.ok).length ? `<p class="muted small" style="margin-top:8px">Nie uczą się dźwigni: ${d.candidates.filter(c => !c.ok).map(c => `${esc(c.name)} (${esc(c.why)})`).join("; ")}.</p>` : ""}`;
  $("#ls-night") && ($("#ls-night").onclick = async () => {
    try {
      await api("POST", "/api/lab/leverage-study", { bot_ids: [], when: "night", weekly: $("#ls-weekly").checked });
      toast("Nauka dźwigni zaplanowana na noc"); drawLevStudy();
    } catch (e) { toast(e.message, true); }
  });
  $("#ls-cancel") && ($("#ls-cancel").onclick = async () => {
    await api("DELETE", "/api/lab/leverage-study/plan"); toast("Odwołano"); drawLevStudy();
  });
  $("#ls-go") && ($("#ls-go").onclick = async () => {
    if (!confirmBox("Uruchomić naukę dźwigni? Backtesty idą w tle na serwerze; boty działają normalnie. Warianty, które przejdą test, trafią do laboratorium.")) return;
    try { await api("POST", "/api/lab/leverage-study", { bot_ids: [] }); toast("Nauka dźwigni uruchomiona"); drawLevStudy(); }
    catch (e) { toast(e.message, true); }
  });
}


// ------------------------------------------------------------------ KONTA GIEŁD KRYPTO W FORMULARZU BOTA
function isReal(a) { return !(a.paper || a.type === "sim" || a.sandbox); }
function isCryptoEx(a) { return a && (a.type === "kraken" || a.type === "gielda"); }
// konto giełdy krypto: tylko rynek krypto i pary w walucie konta (BTC/USD -> BTC/USDT)
function adaptToAccount(root, convert) {
  const a = SCHEMA.accounts.find(x => x.name === $("#f-account", root).value);
  const mk = $("#f-market", root);
  const stockOpt = mk.querySelector('option[value="stocks"]');
  if (stockOpt) stockOpt.disabled = isCryptoEx(a);
  if (isCryptoEx(a) && mk.value !== "crypto") {
    mk.value = "crypto";
    mk.dispatchEvent(new Event("change"));
  }
  if (isCryptoEx(a) && convert !== false) {
    const q = a.currency || "USD";
    const fix = v => v.replace(/\b([A-Z0-9]{2,10})\/(USD|USDT|USDC|EUR|BUSD)\b/g, (m, base) => `${base}/${q}`);
    ["#p_symbols", "#p_regime_symbol"].forEach(sel => { const el = $(sel, root); if (el && el.value) el.value = fix(el.value); });
    const sy = $("#p_symbols", root);
    if (sy && !sy.value.trim()) sy.value = `BTC/${q}, ETH/${q}`;
  }
  accountNote(root);
}
let ACC_EQ = null;
async function accountNote(root) {
  const box = $("#acc-note", root);
  if (!box) return;
  const a = SCHEMA.accounts.find(x => x.name === $("#f-account", root).value);
  if (!a || a.type === "sim") { box.classList.add("hidden"); return; }
  if (!ACC_EQ) {
    try { ACC_EQ = Object.fromEntries((await api("GET", "/api/overview")).accounts.map(x => [x.name, x])); }
    catch { ACC_EQ = {}; }
  }
  const e = ACC_EQ[a.name] || {}, p = readParams($("#params-box", root));
  const cur = e.currency || a.currency || "USD";
  const budget = (e.equity || 0) * (p.allocation_pct || 0), per = budget / Math.max(1, p.max_positions || 1);
  const minNote = isCryptoEx(a) ? (a.exchange === "binance" ? 5 : 5) : 0;
  const lines = [];
  if (isCryptoEx(a)) lines.push(`Giełda ${esc(a.exchange || a.type)}: tylko krypto, pary w <b>${esc(a.currency)}</b> (np. BTC/${esc(a.currency)}). Bot zarządza wyłącznie monetami, które sam kupi — Twoje inne monety zostają w spokoju.`);
  if (e.equity != null) lines.push(`Kapitał konta ${money(e.equity, cur)} → budżet bota ≈ <b>${money(budget, cur)}</b>, na jedną pozycję ≈ <b>${money(per, cur)}</b>.`);
  if (minNote && e.equity != null && per < minNote * 1.2)
    lines.push(`<b>Za mało na jedną pozycję</b> — giełda nie przyjmie zlecenia poniżej ok. ${minNote} ${esc(cur)}. Zwiększ budżet albo zmniejsz „Maks. pozycji”.`);
  if (e.cash != null && isCryptoEx(a) && budget > e.cash) lines.push(`Wolnej gotówki jest ${money(e.cash, cur)} — bot kupi tylko za tyle, ile jest w ${esc(cur)}.`);
  if (isReal(a)) lines.unshift(`<b>PRAWDZIWE PIENIĄDZE.</b> Zacznij od małego budżetu i sprawdź bota najpierw w backteście.`);
  box.className = "note" + (isReal(a) || (minNote && per < minNote * 1.2) ? " warn" : "");
  box.innerHTML = lines.join("<br>");
}

// ------------------------------------------------------------------ ZLECENIE RĘCZNE (kilka akcji / monet bez bota)
function orderTicket(symbol, side = "buy") {
  const accs = SCHEMA.accounts;
  const guess = symbol.includes("/") ? (accs.find(a => isCryptoEx(a) && symbol.endsWith("/" + a.currency)) || accs.find(a => a.type === "alpaca"))
    : symbol.includes(".") ? accs.find(a => a.type === "ibkr") : accs.find(a => a.type === "alpaca");
  openModal(`
    <div class="card-head"><h2>Zlecenie ręczne</h2><button class="btn ghost small" id="o-close">Zamknij</button></div>
    <form class="form" id="o-form" autocomplete="off">
      <div class="seg" id="o-side" style="margin-bottom:6px"><button type="button" data-s="buy">Kup</button><button type="button" data-s="sell">Sprzedaj</button></div>
      <div class="form-grid">
        <div class="field"><label>Konto</label><select id="o-acc">${accs.map(a => `<option value="${esc(a.name)}" ${guess && a.name === guess.name ? "selected" : ""}>${esc(a.name)} (${esc(accDesc(a))})</option>`).join("")}</select></div>
        <div class="field"><label>Symbol</label><input id="o-sym" value="${esc(symbol)}" placeholder="np. NVDA, PKO.WSE, BTC/USDT" spellcheck="false" autocapitalize="characters"></div>
        <div class="field"><label id="o-qty-l">Liczba akcji</label><input id="o-qty" type="number" step="any" min="0" value="1"></div>
      </div>
      <div id="o-info" class="note">Wpisz symbol, żeby zobaczyć cenę.</div>
      <div id="o-auth" class="form-grid hidden">
        <div class="field"><label>Hasło do panelu</label><input id="o-pass" type="password" autocomplete="current-password"></div>
        <div class="field"><label>Kod 2FA (jeśli włączony)</label><input id="o-code" inputmode="numeric" maxlength="11"></div>
      </div>
      <p class="err" id="o-err"></p>
      <div class="modal-foot"><button type="button" class="btn" id="o-cancel">Anuluj</button>
        <button class="btn primary" type="submit" id="o-go" disabled>Złóż zlecenie</button></div>
    </form>`);
  let q = null, sd = side, timer = null;
  const setSide = v => { sd = v; $$("#o-side button").forEach(b => b.classList.toggle("on", b.dataset.s === v)); render(); };
  const render = () => {
    const info = $("#o-info"), go = $("#o-go");
    go.disabled = true;
    if (!q) return;
    if (q.error) { info.className = "note warn"; info.textContent = q.error; return; }
    const crypto = q.kind === "crypto";
    $("#o-qty-l").textContent = crypto ? "Ilość monet" : q.whole_only ? "Liczba akcji (całe sztuki)" : "Liczba akcji (można ułamki)";
    $("#o-qty").step = q.whole_only ? "1" : "any";
    const n = +$("#o-qty").value || 0, val = n * (q.price || 0);
    const lines = [`Cena teraz: <b>${price(q.price)} ${esc(q.currency)}</b> · gotówka: ${money(q.cash, q.currency)} · na koncie: ${num(q.held, 6)} ${esc(q.symbol)}`,
      `${sd === "buy" ? "Kupisz" : "Sprzedasz"} <b>${num(n, 6)}</b> × ${price(q.price)} ≈ <b>${money(val, q.currency)}</b> (po cenie rynkowej, może się nieco różnić).`];
    let bad = "";
    if (q.locked_by) bad = `Tym symbolem na tym koncie handluje bot „${esc(q.locked_by)}” — ręczne zlecenie pomieszałoby się z jego pozycją.`;
    else if (!q.open) bad = esc(q.closed_note || "Rynek jest zamknięty.");
    else if (n <= 0) bad = "Podaj ilość większą od zera.";
    else if (q.whole_only && n !== Math.floor(n)) bad = "Na tym koncie akcje kupuje się w całych sztukach.";
    else if (sd === "buy" && val > q.cash * 0.995) bad = "Za mało gotówki na koncie.";
    else if (sd === "sell" && n > q.held + 1e-9) bad = `Na koncie masz tylko ${num(q.held, 6)}.`;
    if (q.real) lines.unshift(`<b>PRAWDZIWE PIENIĄDZE</b> — zlecenie wymaga hasła do panelu.`);
    if (bad) lines.push(`<b>${bad}</b>`);
    info.className = "note" + (bad || q.real ? " warn" : "");
    info.innerHTML = lines.join("<br>");
    $("#o-auth").classList.toggle("hidden", !q.real);
    go.disabled = !!bad;
    go.textContent = `${sd === "buy" ? "Kup" : "Sprzedaj"} ${num(n, 6)} ${q.symbol}`;
    go.className = "btn " + (sd === "buy" ? "primary" : "danger");
  };
  const load = async () => {
    const sym = $("#o-sym").value.trim().toUpperCase();
    q = null; $("#o-go").disabled = true;
    if (!sym) { $("#o-info").className = "note"; $("#o-info").textContent = "Wpisz symbol, żeby zobaczyć cenę."; return; }
    $("#o-info").className = "note"; $("#o-info").textContent = "Sprawdzam cenę…";
    try { q = await api("GET", `/api/manual/quote?account=${encodeURIComponent($("#o-acc").value)}&symbol=${encodeURIComponent(sym)}`); }
    catch (e) { q = { error: e.message }; }
    if (sd === "sell" && q && q.held > 0 && !$("#o-qty").dataset.touched) $("#o-qty").value = +q.held.toFixed(8);
    render();
  };
  $$("#o-side button").forEach(b => b.onclick = () => setSide(b.dataset.s));
  $("#o-acc").onchange = load;
  $("#o-sym").oninput = () => { clearTimeout(timer); timer = setTimeout(load, 500); };
  $("#o-qty").oninput = () => { $("#o-qty").dataset.touched = "1"; render(); };
  $("#o-close").onclick = $("#o-cancel").onclick = closeModal;
  $("#o-form").onsubmit = async e => {
    e.preventDefault();
    if (!q || $("#o-go").disabled) return;
    const n = +$("#o-qty").value;
    if (!confirmBox(`${sd === "buy" ? "Kupić" : "Sprzedać"} ${n} ${q.symbol} po cenie rynkowej (~${money(n * q.price, q.currency)}) na koncie ${q.account}${q.real ? " — PRAWDZIWE PIENIĄDZE" : ""}?`)) return;
    $("#o-err").textContent = ""; $("#o-go").disabled = true; $("#o-go").textContent = "Wysyłam…";
    try {
      const r = await api("POST", "/api/manual/order", { account: q.account, symbol: q.symbol, side: sd, qty: n,
        password: $("#o-pass").value, code: $("#o-code").value });
      closeModal(); toast(r.text);
      if ($("#t-manual-list")) drawManualOrders();
    } catch (err) { $("#o-err").textContent = err.message; render(); }
  };
  setSide(side);
  if (symbol) load();
}
async function drawManualOrders() {
  const box = $("#t-manual-list");
  if (!box) return;
  const rows = await api("GET", "/api/manual/orders?limit=30");
  if (!rows.length) { box.innerHTML = `<h2>Zlecenia ręczne</h2><div class="muted small">Jeszcze żadnych. Przycisk „Zlecenie ręczne” (albo „Kup / sprzedaj” na wykresie) kupi kilka akcji lub monet bez bota.</div>`; return; }
  const ST = { filled: ["wykonane", "up"], error: ["odrzucone", "down"], sent: ["wysłane", "info"] };
  box.innerHTML = `<h2>Zlecenia ręczne</h2><div class="table-wrap"><table><thead><tr><th>Czas</th><th>Konto</th><th>Symbol</th><th>Strona</th>
    <th class="num">Ilość</th><th class="num">Cena</th><th class="num">Wartość</th><th>Status</th></tr></thead><tbody>
    ${rows.map(r => { const [t, c] = ST[r.status] || [r.status, "muted"];
      return `<tr><td class="small">${when(r.ts)}</td><td>${esc(r.account)}</td><td><a class="sym" href="#/chart/${encodeURIComponent(r.symbol)}"><b>${esc(r.symbol)}</b></a></td>
      <td class="${r.side === "buy" ? "" : "muted"}">${r.side === "buy" ? "Kupno" : "Sprzedaż"}</td><td class="num">${num(r.qty, 6)}</td>
      <td class="num">${r.price == null ? "—" : price(r.price)}</td><td class="num">${r.value == null ? "—" : num(r.value, 2)}</td>
      <td><span class="f-tag ${c}">${t}</span>${r.note ? `<div class="small muted">${esc(r.note)}</div>` : ""}</td></tr>`; }).join("")}</tbody></table></div>`;
}


// ------------------------------------------------------------------ PORTFEL KONTA (co jest na koncie)
async function holdingsModal(name) {
  openModal(`<div class="card-head"><h2>Portfel: ${esc(name)}</h2><button class="btn ghost small" id="h-close">Zamknij</button></div>
    <div id="h-body"><div class="empty">Odczytuję konto…</div></div>`);
  $("#h-close").onclick = closeModal;
  let d;
  try { d = await api("GET", `/api/accounts/${encodeURIComponent(name)}/holdings`); }
  catch (e) { $("#h-body").innerHTML = `<p class="err">${esc(e.message)}</p>`; return; }
  const cur = d.currency, tot = d.equity || 1;
  $("#h-body").innerHTML = `
    <div class="grid metrics" style="margin-bottom:12px">${metric("Wartość konta", money(d.equity, cur))}${metric("Gotówka", money(d.cash, cur))}
      ${metric("Pozycje", d.positions.length)}</div>
    ${d.real ? `<p class="note warn" style="margin-bottom:10px">Prawdziwe pieniądze.</p>` : ""}
    ${!d.positions.length ? `<div class="empty">Brak pozycji — tylko gotówka.</div>` : `<div class="table-wrap"><table><thead><tr><th>Symbol</th>
      <th class="num">Ilość</th><th class="num">Cena</th><th class="num">Wartość</th><th class="num">Udział</th><th class="num">Wynik</th><th>Kto prowadzi</th><th></th></tr></thead><tbody>
      ${d.positions.map(p => `<tr><td><a class="sym" href="#/chart/${encodeURIComponent(p.symbol)}"><b>${esc(p.symbol)}</b></a></td>
        <td class="num">${num(p.qty, 6)}</td><td class="num">${price(p.price)}</td><td class="num">${money(p.value, cur)}</td>
        <td class="num">${pct((p.value || 0) / tot * 100, 1, false)}</td>
        <td class="num ${tone(p.unrealized)}">${p.bot ? money(p.unrealized, cur, true) : '<span class="muted small" title="cena zakupu poza botem nieznana">—</span>'}</td>
        <td class="small">${p.bot ? esc(p.bot) : '<span class="muted">Ty (poza botami)</span>'}</td>
        <td>${p.bot ? "" : `<button class="btn ghost small" data-sell="${esc(p.symbol)}">Sprzedaj…</button>`}</td></tr>`).join("")}</tbody></table></div>`}`;
  $$("[data-sell]").forEach(b => b.onclick = () => { closeModal(); orderTicket(b.dataset.sell, "sell"); });
}
