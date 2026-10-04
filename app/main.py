"""
TradingApp - panel webowy do botow.

Uruchomienie:  python -m app.main   (albo start.bat / docker compose up)
Panel:         http://ADRES:8420
"""

import hmac
import json
import re
import os
import sys

# pythonw.exe (start w tle na Windows, aktualizuj.ps1) nie ma konsoli: sys.stdout/stderr = None,
# a uvicorn i logging wymagaja strumieni - bez tego aplikacja konczy sie od razu po starcie.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")
import secrets
import threading
import time
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config
from .backtest import period_error, run_backtest
from .brokers import get_broker
from .data import SimData, AlpacaData, norm
from .db import DB
from .engine import Manager, log
from .strategies import STRATEGIES, full_params, leverage_warning, schema, validate

STATIC = os.path.join(os.path.dirname(__file__), "static")

db = DB()
from . import accounts  # noqa: E402  konta z panelu (zaszyfrowane klucze) - przed startem botow
accounts.init(db, log=log.warning)
from . import auth as panel_auth
panel_auth.init(db)
manager = Manager(db)
from . import notify


def _sym_currency(symbol, acc):
    if "/" in symbol:
        return symbol.split("/")[1]
    if acc and acc.type == "ibkr":
        if "." in symbol:
            suf = symbol.rsplit(".", 1)[1]
            from .ibkr import EXCHANGES
            return EXCHANGES[suf][1] if suf in EXCHANGES else suf
        return "USD"
    return (acc.extra.get("quote") if acc else None) or "USD"


def _pl(v, nd=2):
    return f"{v:,.{nd}f}".replace(",", " ").replace(".", ",")


def _pp(v):
    return f"{v:+.1f}%".replace(".", ",").replace("-", "−")


def _signal_notify(bot, symbol, side, qty, price, reason, pnl_pct):
    """Okazja do recznego kopiowania: cena, stop-loss, kwota dla Ciebie (z ustawienia bota)."""
    p = bot["params"] or {}
    if not p.get("copy_signals"):
        return
    acc = config.ACCOUNTS.get(bot["account"])
    cur = _sym_currency(symbol, acc)
    pr = _pl(price, 2 if price >= 1 else 6)
    if side in ("BUY", "SHORT"):
        st = db.pos_states(bot["id"]).get(norm(symbol)) or {}
        if side == "SHORT":
            lines = [f"Bot „{bot['name']}” gra na SPADEK {symbol}: krótka sprzedaż po ~{pr} {cur}.",
                     "Ręcznie: krótka sprzedaż (short / CFD) albo ETF odwrotny — tylko jeśli wiesz, jak to działa."]
        else:
            lines = [f"Bot „{bot['name']}” kupił {symbol} po ~{pr} {cur}."]
            if st.get("meta"):
                lines.append(f"To ETF z dźwignią — sygnał liczony na {st['meta']}.")
        if st.get("sl"):
            lines.append(f"Stop-loss: {_pl(st['sl'], 2 if st['sl'] >= 1 else 6)} {cur} ({_pp((st['sl'] / price - 1) * 100)})")
        lines.append("Sprzedaż: przyjdzie osobne powiadomienie, gdy bot wyjdzie z pozycji.")
        cap = float(p.get("manual_capital") or 0)
        eq = db.one("SELECT equity FROM equity WHERE scope='account' AND ref=? AND equity IS NOT NULL "
                    "ORDER BY ts DESC LIMIT 1", (bot["account"],))
        acc_cur = (acc.extra.get("quote") if acc and acc.type != "ibkr" else None) or ("PLN" if acc and acc.type == "ibkr" else "USD")
        if cap > 0 and eq and eq["equity"] and cur == acc_cur:
            budget = eq["equity"] * float(p.get("allocation_pct") or 1)
            share = min(1.0, qty * price / budget) if budget else 0
            amount = share * cap
            n = amount / price if "/" in symbol or p.get("fractional_shares") else int(amount // price)
            lines.append(f"Dla Ciebie ({_pl(cap, 0)} {cur}): ~{_pl(amount, 0)} {cur} ({share * 100:.0f}% kapitału)"
                         + (f" = {n:g} szt." if n else " — za mało na 1 akcję"))
        notify.send("\n".join(lines), "signals", title=f"Okazja: {'SPADEK' if side == 'SHORT' else 'KUPNO'} {symbol}",
                    tags=["chart_with_downwards_trend" if side == "SHORT" else "chart_with_upwards_trend"], priority=4)
    else:
        res = f" Wynik bota: {_pp(pnl_pct)}." if pnl_pct is not None else ""
        if side == "COVER":
            notify.send(f"Bot „{bot['name']}” zakończył grę na spadek {symbol} po ~{pr} {cur} ({reason}).{res}\n"
                        f"Jeśli grałeś na spadek za jego sygnałem — to moment na zamknięcie.", "signals",
                        title=f"Okazja: KONIEC SPADKU {symbol}", tags=["chart_with_upwards_trend"], priority=4)
            return
        notify.send(f"Bot „{bot['name']}” sprzedał {symbol} po ~{pr} {cur} ({reason}).{res}\n"
                    f"Jeśli kupiłeś za jego sygnałem — to moment na sprzedaż.", "signals",
                    title=f"Okazja: SPRZEDAŻ {symbol}", tags=["chart_with_downwards_trend"], priority=4)


def _trade_notify(bot_id, symbol, side, qty, price, reason, pnl, pnl_pct):
    bot = db.bot(bot_id)
    name = bot["name"] if bot else f"bot #{bot_id}"
    if bot:
        try:
            _signal_notify(bot, symbol, side, qty, price, reason, pnl_pct)
        except Exception:
            log.exception("Powiadomienie o okazji")
    if side in ("SELL", "COVER"):
        icon = "🟢" if (pnl or 0) > 0 else "🔴"
        what = "koniec gry na spadek" if side == "COVER" else "sprzedaż"
        notify.send(f"{icon} {name}: {what} {symbol} po {price:,.4g} ({reason}) | wynik {pnl or 0:+,.2f}"
                    + (f" ({pnl_pct:+.2f}%)" if pnl_pct is not None else ""), "sells")
    else:
        notify.send(f"{'📉' if side == 'SHORT' else '🛒'} {name}: {'krótka sprzedaż (gra na spadek)' if side == 'SHORT' else 'kupno'} "
                    f"{symbol} x{qty:g} po {price:,.4g}", "buys")


db.on_trade = _trade_notify


def startup_notice():
    """Po starcie: wiadomosc o starcie, wyniku ostatniej aktualizacji i ewentualnym restarcie przez straznika."""
    msg = [f"▶️ Aplikacja uruchomiona (wersja {app_version()})."]
    st_path = os.path.join(config.DATA_DIR, "update_status.json")
    seen = os.path.join(config.DATA_DIR, "update_notified.txt")
    try:
        st = json.load(open(st_path, encoding="utf-8"))
        stamp = st.get("updated")
        if st.get("state") in ("done", "rolled_back", "failed") and stamp != (open(seen).read().strip() if os.path.exists(seen) else ""):
            msg.append({"done": "✅ ", "rolled_back": "⚠️ ", "failed": "❌ "}[st["state"]] + st.get("message", ""))
            open(seen, "w").write(stamp or "")
    except (OSError, ValueError):
        pass
    wd = os.path.join(config.DATA_DIR, "straznik_restart.txt")
    if os.path.exists(wd):
        msg.append("🛡️ Strażnik zrestartował aplikację: " + open(wd, encoding="utf-8", errors="replace").read().strip()[:200])
        try:
            os.remove(wd)
        except OSError:
            pass
    notify.send(" ".join(msg), "system")
from . import signals as ext_signals
ext_signals.set_db(db, lambda m: log.info(m))

# ------------------------------------------------------------------ haslo panelu
# Wersja z instalatora Windows: zamiast hasla z pliku pierwsze uruchomienie pokazuje kreator
# „Ustaw login i hasło” (tylko z tego komputera, tylko dopóki nie ma własnego loginu).
INSTALLED = os.environ.get("TRADINGAPP_INSTALLED", "").strip() == "1"
PASSWORD = config.PANEL_PASSWORD
if not PASSWORD:
    pw_file = os.path.join(config.DATA_DIR, "haslo_panelu.txt")
    if os.path.exists(pw_file):
        PASSWORD = open(pw_file, encoding="utf-8").read().strip()
    else:
        PASSWORD = secrets.token_urlsafe(12)
        with open(pw_file, "w", encoding="utf-8") as f:
            f.write(PASSWORD)
    log.warning(f"Brak PANEL_PASSWORD w .env - haslo panelu zapisane w {pw_file}")

# sesje: skrot tokenu -> wygasa; zapisane w data/, zeby restart (np. aktualizacja) nie wylogowywal
SESSIONS_FILE = os.path.join(config.DATA_DIR, "sesje.json")
failed = {}             # ip -> (liczba, od kiedy)


def _load_sessions():
    try:
        data = json.load(open(SESSIONS_FILE, encoding="utf-8"))
        return {k: v for k, v in data.items() if v > time.time()}
    except (OSError, ValueError):
        return {}


sessions = _load_sessions()


def _save_sessions():
    try:
        with open(SESSIONS_FILE + ".tmp", "w", encoding="utf-8") as f:
            json.dump({k: v for k, v in sessions.items() if v > time.time()}, f)
        os.replace(SESSIONS_FILE + ".tmp", SESSIONS_FILE)
    except OSError:
        pass


def _sid(token):
    import hashlib
    return hashlib.sha256((token or "").encode()).hexdigest()


@asynccontextmanager
async def lifespan(app):
    from . import demo
    if demo.on() and demo.needs_seed(db):
        demo.start_seed(db, manager)
    manager.resume()
    manager.start_monitor()
    if not demo.on():
        from . import gateway
        gateway.start(threading.Event())
        from . import calendar_events, heartbeat
        calendar_events.start(db, threading.Event())
        heartbeat.start(db, threading.Event())
        from . import webhook
        try:
            webhook.start(db)
        except Exception as e:
            log.warning(f"Odbiornik alertów TradingView nie wystartował: {e}")
        threading.Thread(target=startup_notice, daemon=True).start()
    if os.name == "nt" and not demo.on() and not INSTALLED:
        threading.Thread(target=windows_update_loop, daemon=True, name="windows-updater").start()
    log.info(f"Panel: http://{config.PANEL_HOST}:{config.PANEL_PORT}  (konta: {', '.join(config.ACCOUNTS)})")
    yield
    manager.shutdown()


app = FastAPI(title="TradingApp", lifespan=lifespan, docs_url=None, redoc_url=None)


# wersja demo: to, czego nie wolno ruszac (klucze, bramka, powiadomienia, logowanie, aktualizacje)
DEMO_BLOCKED = re.compile(r"^/api/(accounts|gateway/|notify/|security/|system/|login|logout)")


@app.middleware("http")
async def auth(request: Request, call_next):
    path = request.url.path
    if config.DEMO and path.startswith("/api/"):
        if request.method != "GET" and DEMO_BLOCKED.match(path):
            return JSONResponse({"detail": "To jest wersja demonstracyjna — ta funkcja jest w niej wyłączona."},
                                status_code=403)
    elif path.startswith("/api/") and path not in ("/api/login", "/api/session", "/api/setup"):
        exp = sessions.get(_sid(request.cookies.get("session")))
        bearer = request.headers.get("authorization", "")
        if (not exp or exp < time.time()) and not (bearer.startswith("Bearer ") and panel_auth.token_check(bearer[7:].strip())):
            return JSONResponse({"detail": "Zaloguj sie"}, status_code=401)
    response = await call_next(request)
    if path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"      # zawsze sprawdz, czy plik sie nie zmienil
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


class Login(BaseModel):
    username: str = ""
    password: str
    code: str = ""


def _too_many(ip):
    n, since = failed.get(ip, (0, time.time()))
    if n >= 5 and time.time() - since < 300:
        raise HTTPException(429, "Za dużo prób. Spróbuj za 5 minut.")


def _fail(ip):
    n, since = failed.get(ip, (0, time.time()))
    failed[ip] = (n + 1 if time.time() - since < 300 else 1, since if n and time.time() - since < 300 else time.time())
    time.sleep(1)


@app.post("/api/login")
def login(body: Login, request: Request, response: Response):
    ip = request.client.host if request.client else "?"
    _too_many(ip)
    if not (panel_auth.username_ok(body.username) and panel_auth.password_ok(body.password, PASSWORD)):
        _fail(ip)
        raise HTTPException(401, "Nieprawidłowy login lub hasło")
    if panel_auth.totp_on():
        if not body.code.strip():
            return {"ok": False, "need_code": True}
        how = panel_auth.check_code(body.code)
        if not how:
            _fail(ip)
            raise HTTPException(401, "Nieprawidłowy kod")
        if how == "recovery":
            log.warning(f"Logowanie kodem awaryjnym (zostało {panel_auth.recovery_left()})")
    failed.pop(ip, None)
    token = secrets.token_urlsafe(32)
    sessions[_sid(token)] = time.time() + config.SESSION_HOURS * 3600
    _save_sessions()
    response.set_cookie("session", token, httponly=True, samesite="strict",
                        max_age=config.SESSION_HOURS * 3600)
    return {"ok": True}


@app.post("/api/logout")
def logout(request: Request, response: Response):
    sessions.pop(_sid(request.cookies.get("session")), None)
    _save_sessions()
    response.delete_cookie("session")
    return {"ok": True}


def _local(request):
    return (request.client.host if request.client else "") in ("127.0.0.1", "::1", "localhost")


def setup_needed(request=None):
    return INSTALLED and not config.PANEL_PASSWORD and not panel_auth.user() and (request is None or _local(request))


@app.get("/api/session")
def session(request: Request):
    exp = sessions.get(_sid(request.cookies.get("session")))
    u = panel_auth.user()
    if config.DEMO:
        return {"logged_in": True, "custom_login": False, "demo": True}
    return {"logged_in": bool(exp and exp > time.time()), "custom_login": bool(u), "installed": INSTALLED,
            "setup": setup_needed(request)}


class SetupIn(BaseModel):
    username: str
    password: str


@app.post("/api/setup")
def first_setup(body: SetupIn, request: Request, response: Response):
    """Pierwsze uruchomienie wersji z instalatora: własny login i hasło, od razu zalogowany."""
    if not setup_needed(request):
        raise HTTPException(403, "Login i hasło są już ustawione — zaloguj się.")
    try:
        panel_auth.set_login(body.username, body.password)
    except ValueError as e:
        raise HTTPException(400, str(e))
    log.info(f"Ustawiono login panelu: {body.username.strip()}")
    token = secrets.token_urlsafe(32)
    sessions[_sid(token)] = time.time() + config.SESSION_HOURS * 3600
    _save_sessions()
    response.set_cookie("session", token, httponly=True, samesite="strict", max_age=config.SESSION_HOURS * 3600)
    return {"ok": True}


@app.get("/api/demo")
def demo_status():
    from . import demo
    return {"demo": demo.on(), **demo.status()}


@app.post("/api/demo/reset")
def demo_reset():
    from . import demo
    if not demo.on():
        raise HTTPException(400, "To nie jest wersja demonstracyjna.")
    if demo.status()["state"] == "running":
        raise HTTPException(400, "Dane demo są właśnie przygotowywane — poczekaj chwilę.")
    demo._set(state="running", step="Zatrzymuję boty i czyszczę dane…", progress=0.01, error=None)
    threading.Thread(target=demo.reset, args=(db, manager), daemon=True).start()
    return {"ok": True}


# ------------------------------------------------------------------ pulpit
def bot_summary(bot, positions_by_account):
    p = full_params(bot["market"], bot["strategy"], bot["params"])
    owned = db.pos_states(bot["id"])
    pos = {s: v for s, v in positions_by_account.get(bot["account"], {}).items() if s in owned}
    realized, n, wins = db.realized_pnl(bot["id"])
    unreal = sum(v["unrealized"] for v in pos.values())
    last = db.logs(bot["id"], limit=1)
    return {
        "id": bot["id"], "name": bot["name"], "account": bot["account"], "market": bot["market"],
        "strategy": bot["strategy"], "strategy_name": STRATEGIES[bot["strategy"]].name,
        "timeframe": p["timeframe"], "symbols": p["symbols"], "allocation_pct": p["allocation_pct"],
        "status": manager.status(bot["id"]), "last_error": bot["last_error"],
        "realized": realized, "unrealized": unreal, "total_pnl": realized + unreal,
        "closed_trades": n, "win_rate": wins / n * 100 if n else None,
        "open_positions": len(pos), "exposure": sum(abs(v["market_value"]) for v in pos.values()),
        "last_log": last[0] if last else None,
        "lev": {"mode": p.get("leverage_mode", "off"), "direction": p.get("direction", "long"),
                "leverage": p.get("leverage", 1)} if p.get("leverage_mode", "off") != "off" else None,
    }


@app.get("/api/overview")
def overview():
    accounts, positions_by_account = [], {}
    for acc in config.ACCOUNTS.values():
        item = acc.public()
        try:
            b = get_broker(acc)
            a = b.account()
            pos = b.positions()
            positions_by_account[acc.name] = pos
            if a.get("currency"):
                item["currency"] = a["currency"]          # np. IBKR: waluta bazowa konta (PLN)
            item.update(ok=True, equity=a["equity"], cash=a["cash"], day_change=a["equity"] - a["last_equity"],
                        positions=len(pos), exposure=sum(p["market_value"] for p in pos.values()))
        except Exception as e:
            item.update(ok=False, error=str(e))
        accounts.append(item)
    bots = [bot_summary(b, positions_by_account) for b in db.bots()]
    return {"accounts": accounts, "bots": bots, "server_time": datetime.now(timezone.utc).isoformat()}


@app.get("/api/equity")
def equity(days: int = 30):
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    return {
        "accounts": {name: [[r["ts"], r["equity"]] for r in db.equity_series("account", name, since)]
                     for name in config.ACCOUNTS},
        "bots": {str(b["id"]): {"name": b["name"],
                                "points": [[r["ts"], r["equity"]] for r in db.equity_series("bot", b["id"], since)]}
                 for b in db.bots()},
    }


@app.get("/api/schema")
def get_schema():
    s = schema()
    s["accounts"] = [a.public() for a in config.ACCOUNTS.values()]
    s["data_source"] = "alpaca" if config.data_account() else "sim"
    return s


@app.get("/api/trades")
def all_trades(limit: int = 200):
    names = {b["id"]: b["name"] for b in db.bots()}
    rows = db.trades(limit=limit)
    for r in rows:
        r["bot_name"] = names.get(r["bot_id"], "?")
    return rows


# ------------------------------------------------------------------ boty
class BotIn(BaseModel):
    name: str
    account: str
    market: str
    strategy: str
    params: dict


def check_bot(body: BotIn):
    if body.account not in config.ACCOUNTS:
        raise HTTPException(400, f"Konto '{body.account}' nie istnieje w .env")
    if body.market not in ("stocks", "crypto"):
        raise HTTPException(400, "Rynek musi byc 'stocks' albo 'crypto'")
    if body.strategy not in STRATEGIES:
        raise HTTPException(400, "Nieznana strategia")
    p = full_params(body.market, body.strategy, body.params)
    errors = validate(body.market, body.strategy, p)
    acc = config.ACCOUNTS[body.account]
    if acc.type in config.CRYPTO_EX_TYPES:
        quote = acc.extra.get("quote", "EUR")
        if body.market != "crypto":
            errors.append(f"Konto {body.account} (giełda krypto) obsługuje tylko krypto.")
        bad = [x for x in p["symbols"] if not x.endswith("/" + quote)]
        if bad:
            errors.append(f"Na koncie {body.account} waluta to {quote} - użyj par typu SOL/{quote} (nie: {', '.join(bad)}).")
    if p.get("fractional_shares") and acc.type == "ibkr":
        errors.append("Ułamki akcji działają tylko na Alpace — na IBKR (GPW) wyłącz tę opcję.")
    if acc.type == "ibkr":
        from .ibkr import venue_error
        if body.market != "stocks":
            errors.append("Konto IBKR: wybierz rynek akcje (GPW, waluty i USA działają jak akcje).")
        err = venue_error(p["symbols"])
        if err:
            errors.append(err)
        if p.get("regime_filter") and p.get("regime_symbol") and venue_error([p["regime_symbol"]]):
            errors.append("Filtr reżimu: symbol musi być w formacie IBKR (np. SPY, EUR.USD, WIG20 ETF .WSE) albo wyłącz filtr.")
        if (p.get("regime2_symbol") or "").strip() and venue_error([p["regime2_symbol"].strip().upper()]):
            errors.append("Drugi termometr: symbol musi być w formacie IBKR (np. SPY, ETFBW20TR.WSE) albo zostaw puste.")
    mode = p.get("leverage_mode", "off")
    if mode == "margin" and body.market == "crypto" and acc.type == "gielda" and not acc.paper:
        errors.append("Margin na krypto obsługujemy tylko na Krakenie (albo na koncie na niby).")
    if mode == "margin" and body.market == "crypto" and acc.type == "alpaca":
        errors.append("Alpaca nie pozwala na margin ani krótką sprzedaż krypto — użyj Krakena.")
    if mode == "etf" and acc.type in config.CRYPTO_EX_TYPES:
        errors.append("ETF-y lewarowane są na kontach z akcjami (Alpaca, IBKR).")
    if mode == "margin" and p.get("direction") != "long" and p.get("fractional_shares"):
        errors.append("Krótka sprzedaż działa tylko na całych akcjach — wyłącz „Ułamki akcji”.")
    if mode != "off":
        from . import risk as risk_mod
        ok, why = risk_mod.leverage_allowed(acc)
        if not ok:
            errors.append(why)
    uses_sec = body.market == "stocks" and (p.get("funds_mode", "off") != "off" or p.get("insider_mode", "off") != "off")
    if uses_sec and acc.type != "sim" and not os.environ.get("SEC_USER_AGENT", "").strip():
        errors.append("Ten bot czyta raporty z SEC - dopisz w .env SEC_USER_AGENT=Imię Nazwisko email@przyklad.pl "
                      "(SEC wymaga przedstawienia się) i zrestartuj aplikację.")
    if errors:
        raise HTTPException(400, " ".join(errors))
    return p


@app.get("/api/bots/{bot_id}")
def get_bot(bot_id: int):
    bot = db.bot(bot_id)
    if not bot:
        raise HTTPException(404, "Nie ma takiego bota")
    p = full_params(bot["market"], bot["strategy"], bot["params"])
    bot["params"] = p
    bot["status"] = manager.status(bot_id)
    bot["warning"] = leverage_warning(p)
    realized, n, wins = db.realized_pnl(bot_id)
    bot["stats"] = {"realized": realized, "closed_trades": n, "win_rate": wins / n * 100 if n else None}
    bot["ml"] = ml_bot_info(bot)
    if bot["strategy"] in ("grid", "dca"):
        from .special import dca_avg, dca_next_trigger
        sp = []
        for s in p.get("symbols", []):
            st = db.get_state(bot_id, s)
            if not st:
                continue
            if bot["strategy"] == "grid":
                sp.append({"symbol": s, "levels": st["levels"], "held": sorted(int(k) for k in st["held"]),
                           "slot_value": st["slot_value"], "stopped": st["stopped"], "realized": st["realized"]})
            else:
                sp.append({"symbol": s, "qty": st["qty"], "avg": dca_avg(st), "spent": st["cost"],
                           "n_safety": st.get("n_safety", 0), "rounds": st.get("rounds", 0), "realized": st["realized"],
                           "next_buy": dca_next_trigger(st, p) if st.get("last_buy") and p["dca_mode"] == "safety" else None,
                           "next_t": st.get("next_t") or None})
        bot["special"] = sp
    try:
        broker = get_broker(config.ACCOUNTS[bot["account"]])
        states = db.pos_states(bot_id)
        bot["positions"] = [dict(v, sl=states[s]["sl"], tp=states[s]["tp"])
                            for s, v in broker.positions().items() if s in states]
    except Exception as e:
        bot["positions"] = []
        bot["positions_error"] = str(e)
    return bot


@app.post("/api/bots")
def create_bot(body: BotIn):
    p = check_bot(body)
    if any(b["name"] == body.name for b in db.bots()):
        raise HTTPException(400, "Bot o tej nazwie juz istnieje")
    if body.strategy == "tv_alerts":
        from .webhook import new_token
        p["tv_token"] = new_token()
    bot_id = db.create_bot(body.name.strip(), body.account, body.market, body.strategy, p)
    db.log(bot_id, "INFO", "Utworzono bota.")
    return {"id": bot_id, "warning": leverage_warning(p)}


@app.put("/api/bots/{bot_id}")
def update_bot(bot_id: int, body: BotIn):
    bot = db.bot(bot_id)
    if not bot:
        raise HTTPException(404, "Nie ma takiego bota")
    p = check_bot(body)
    if body.strategy == "tv_alerts":                  # token webhooka nie jest polem formularza - zachowujemy go
        from .webhook import new_token
        p["tv_token"] = (bot["params"] or {}).get("tv_token") or new_token()
    if body.strategy != bot["strategy"] or p["symbols"] != (bot["params"] or {}).get("symbols"):
        db.del_state(bot_id)                           # siatka / DCA: nowa konfiguracja = nowy stan
    was_running = manager.status(bot_id) == "running"
    if was_running:
        manager.stop(bot_id, report=False)
    db.update_bot(bot_id, name=body.name.strip(), account=body.account, market=body.market,
                  strategy=body.strategy, params=p)
    db.log(bot_id, "INFO", "Zmieniono ustawienia.")
    if db.one("SELECT 1 FROM lab_variants WHERE bot_id=? AND status='active' LIMIT 1", (bot_id,)):
        db.execute("UPDATE lab_variants SET status='retired' WHERE bot_id=? AND status='active'", (bot_id,))
        db.execute("INSERT INTO lab_events(bot_id, ts, kind, status, text) VALUES (?,?,?,?,?)",
                   (bot_id, datetime.now(timezone.utc).isoformat(timespec="seconds"), "reset", "info",
                    "Zmieniono ustawienia bota - laboratorium zaczyna porównanie od nowa."))
    if was_running:
        try:
            manager.start(bot_id, resume=True)
        except ValueError as e:
            raise HTTPException(400, f"Zapisano, ale bot nie wystartowal: {e}")
    return {"ok": True, "restarted": was_running, "warning": leverage_warning(p)}


@app.post("/api/bots/{bot_id}/start")
def start_bot(bot_id: int):
    try:
        manager.start(bot_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.post("/api/bots/{bot_id}/stop")
def stop_bot(bot_id: int, close_positions: bool = False):
    manager.stop(bot_id, close_positions)
    return {"ok": True}


@app.delete("/api/bots/{bot_id}")
def delete_bot(bot_id: int):
    if manager.status(bot_id) == "running":
        raise HTTPException(400, "Najpierw zatrzymaj bota")
    db.delete_bot(bot_id)
    return {"ok": True}


@app.get("/api/bots/{bot_id}/trades")
def bot_trades(bot_id: int, limit: int = 300):
    return db.trades(bot_id, limit)


@app.get("/api/bots/{bot_id}/logs")
def bot_logs(bot_id: int, after: int = 0):
    return db.logs(bot_id, after_id=after)


# ------------------------------------------------------------------ raporty z dzialania botow
@app.get("/api/reports")
def list_reports(bot_id: int | None = None):
    return db.reports(bot_id)


@app.get("/api/reports/{report_id}")
def get_report(report_id: int):
    r = db.report(report_id)
    if not r:
        raise HTTPException(404, "Nie ma takiego raportu")
    return r


class ReportIn(BaseModel):
    bot_id: int
    period: str = "day"          # day | week | month | run | custom
    start: str | None = None
    end: str | None = None


@app.post("/api/reports")
def create_report(body: ReportIn):
    bot = db.bot(body.bot_id)
    if not bot:
        raise HTTPException(404, "Nie ma takiego bota")
    now = datetime.now(timezone.utc)
    if body.period == "custom":
        try:
            start = datetime.fromisoformat(body.start).replace(tzinfo=timezone.utc)
            end = min(datetime.fromisoformat(body.end).replace(tzinfo=timezone.utc) + timedelta(days=1), now)
        except (TypeError, ValueError):
            raise HTTPException(400, "Daty w formacie RRRR-MM-DD")
    elif body.period == "run":
        start = datetime.fromisoformat(bot["started_at"]) if bot.get("started_at") else \
            datetime.fromisoformat(bot["created_at"])
        end = now
    else:
        start = now - {"day": timedelta(days=1), "week": timedelta(days=7), "month": timedelta(days=30)}.get(
            body.period, timedelta(days=1))
        end = now
    if end <= start:
        raise HTTPException(400, "Nieprawidlowy okres")
    rid = manager.generate_report(body.bot_id, "manual", start, end)
    if not rid:
        raise HTTPException(500, "Nie udalo sie wygenerowac raportu - szczegoly w dzienniku bota")
    return {"id": rid}


@app.delete("/api/reports/{report_id}")
def delete_report(report_id: int):
    db.execute("DELETE FROM bot_reports WHERE id=?", (report_id,))
    return {"ok": True}


# ------------------------------------------------------------------ sygnaly zewnetrzne
@app.get("/api/signals")
def get_signals(symbols: str = "", days: int = 90, source: str = "auto"):
    """Ostatnie transakcje insiderow i ruchy funduszy dla symboli (domyslnie: symbole botow akcyjnych)."""
    syms = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if not syms:
        for b in db.bots():
            if b["market"] == "stocks":
                syms += [s for s in b["params"].get("symbols", []) if s not in syms]
    syms = syms[:15]
    tag = "sim" if source == "sim" or (source == "auto" and not ext_signals.SEC_USER_AGENT) else "sec"
    src = ext_signals.source_for("sim" if tag == "sim" else "live")
    now = datetime.now(timezone.utc)
    since = (now - timedelta(days=days)).date().isoformat()
    until = now.date().isoformat()
    out, fund_err = [], None
    try:
        src.sync_funds((now - timedelta(days=400)).date().isoformat())
        changes = src.fund_changes((now - timedelta(days=400)).date().isoformat(), until)
    except Exception as e:
        changes, fund_err = [], str(e)
    latest = {}
    for fd, mgr, ch, period in sorted(changes, key=lambda c: c[0]):
        latest[mgr] = (fd, period, ch)
    for sym in syms:
        item = {"symbol": sym}
        try:
            src.sync_insiders(sym, since)
            ev = src.insider_events(sym, since, until)
            recent = [e for e in ev if e["filed"] >= (now - timedelta(days=30)).date().isoformat()]
            item["insider"] = {
                "buyers_30d": len({e["owner"] for e in recent if e["code"] == "P"}),
                "sellers_30d": len({e["owner"] for e in recent if e["code"] == "S"}),
                "buy_value_30d": sum(e["value"] for e in recent if e["code"] == "P"),
                "sell_value_30d": sum(e["value"] for e in recent if e["code"] == "S"),
                "events": sorted(ev, key=lambda e: e["filed"], reverse=True)[:40],
                "count": len(ev)}
        except Exception as e:
            item["error"] = str(e)
        item["funds"] = [{"manager": m, "filed": fd, "period": per,
                          "change": ch.get(sym, ch.get("*", 0) if "*" in ch else 0)}
                         for m, (fd, per, ch) in latest.items()]
        out.append(item)
    return {"source": tag, "symbols": out, "fund_error": fund_err, "days": days,
            "managers": [m for m, _ in ext_signals.parse_managers()]}


# ------------------------------------------------------------------ backtesty
class BacktestIn(BaseModel):
    name: str = ""
    market: str
    strategy: str
    params: dict
    start: str
    end: str
    initial_capital: float = 100_000
    cost_pct: float | None = None
    data_source: str = "auto"      # auto | alpaca | sim


def provider_for(source):
    acc = config.data_account()
    if source == "sim" or (source == "auto" and not acc):
        return SimData(), "sim"
    if source == "kraken":
        from .kraken import kraken_data
        return kraken_data(), "kraken"
    if source.startswith("ccxt:"):
        ex = source[5:]
        acc_ex = config.exchange_account(ex)
        if not acc_ex:
            raise HTTPException(400, f"Brak konta giełdy {ex} - dodaj je w zakładce Konta.")
        from .kraken import kraken_data
        return kraken_data(ex), config.data_tag(acc_ex)
    if source == "ibkr":
        ib = next((a for a in config.ACCOUNTS.values() if a.type == "ibkr"), None)
        if not ib:
            raise HTTPException(400, "Brak konta IBKR - dodaj je w zakładce Konta.")
        return get_broker(ib).data, "ibkr"
    if not acc:
        raise HTTPException(400, "Brak konta Alpaca w .env - dane rynkowe niedostepne. Wybierz dane symulowane.")
    return AlpacaData(acc.key, acc.secret, acc.paper), "alpaca"


@app.post("/api/backtests")
def create_backtest(body: BacktestIn):
    p = full_params(body.market, body.strategy, body.params)
    errors = validate(body.market, body.strategy, p)
    try:
        start, end = datetime.fromisoformat(body.start), datetime.fromisoformat(body.end)
    except ValueError:
        raise HTTPException(400, "Daty w formacie RRRR-MM-DD")
    if end.date() > datetime.now().date():
        errors.append("Data konca nie moze byc w przyszlosci.")
    err = period_error(p["timeframe"], start, end)
    if err:
        errors.append(err)
    if errors:
        raise HTTPException(400, " ".join(errors))
    provider, tag = provider_for(body.data_source)
    cfg = body.model_dump()
    cfg["params"] = p
    cfg["data_source"] = tag
    if cfg["cost_pct"] is None:
        cfg["cost_pct"] = (0.004 if tag == "kraken" else 0.001 if tag == "ibkr"
                           else provider.fee() + 0.001 if tag.startswith("ccxt_")
                           else 0.0025 if body.market == "crypto" else 0.0005)
    if tag == "kraken" or tag.startswith("ccxt_"):
        if body.market != "crypto":
            raise HTTPException(400, "Dane giełdy krypto są tylko dla krypto.")
        ka = config.kraken_account() if tag == "kraken" else config.exchange_account(tag[5:])
        cfg["quote"] = ka.extra.get("quote", "EUR") if ka else "EUR"
    bt_id = db.create_backtest(cfg)

    def work():
        db.update_backtest(bt_id, status="running")
        try:
            res = run_backtest(provider, cfg, tag,
                               progress=lambda x: db.update_backtest(bt_id, progress=round(x, 3)))
            db.update_backtest(bt_id, status="done", progress=1, result=res)
        except Exception as e:
            log.exception("Backtest nieudany")
            db.update_backtest(bt_id, status="error", error=str(e))

    threading.Thread(target=work, daemon=True, name=f"backtest-{bt_id}").start()
    return {"id": bt_id}


@app.get("/api/backtests")
def list_backtests():
    return db.backtests()


@app.get("/api/backtests/{bt_id}")
def get_backtest(bt_id: int):
    bt = db.backtest(bt_id)
    if not bt:
        raise HTTPException(404, "Nie ma takiego backtestu")
    return bt


@app.post("/api/backtests/{bt_id}/optimize")
def optimize_backtest(bt_id: int):
    """Propozycje poprawek sprawdzane w czasie (walk-forward + okres poza proba)."""
    from .optimize import run_optimization
    bt = db.backtest(bt_id)
    if not bt:
        raise HTTPException(404, "Nie ma takiego backtestu")
    if bt["status"] != "done":
        raise HTTPException(400, "Najpierw poczekaj na wynik backtestu")
    if bt["opt_status"] == "running":
        return {"ok": True}
    cfg = bt["config"]
    if cfg.get("strategy") in ("grid", "dca"):
        raise HTTPException(400, "Propozycje poprawek działają dla strategii z sygnałami — siatkę i uśrednianie "
                                 "porównaj, zmieniając ich ustawienia i uruchamiając test ponownie.")
    provider, tag = provider_for(cfg.get("data_source", "auto"))
    db.update_backtest(bt_id, opt_status="running", opt_progress=0, opt_error=None)

    def work():
        try:
            res = run_optimization(provider, cfg, tag,
                                   progress=lambda x: db.update_backtest(bt_id, opt_progress=round(x, 3)))
            db.update_backtest(bt_id, opt_status="done", opt_progress=1, opt_result=res)
        except Exception as e:
            log.exception("Analiza poprawek nieudana")
            db.update_backtest(bt_id, opt_status="error", opt_error=str(e))

    threading.Thread(target=work, daemon=True, name=f"optimize-{bt_id}").start()
    return {"ok": True}


@app.delete("/api/backtests/{bt_id}")
def delete_backtest(bt_id: int):
    db.execute("DELETE FROM backtests WHERE id=?", (bt_id,))
    return {"ok": True}


# ------------------------------------------------------------------ uczenie maszynowe
from . import ml as ml_engine


def ml_bot_info(bot):
    p = full_params(bot["market"], bot["strategy"], bot["params"])
    uses = ml_engine.uses_ml(bot["strategy"], p)
    active = db.one("SELECT id, created_at FROM ml_models WHERE bot_id=? AND status='active' ORDER BY id DESC LIMIT 1",
                    (bot["id"],))
    stale = False
    if active:
        m = db.one("SELECT signature FROM ml_models WHERE id=?", (active["id"],))
        stale = m["signature"] != ml_engine.signature(p)
    return {"uses_ml": uses, "needs_model": ml_engine.needs_model(bot["strategy"], p),
            "signature": ml_engine.signature(p), "active": active, "active_stale": stale,
            "threshold": ml_engine.threshold(p, _bot_cost(bot)) * 100,
            "breakeven": ml_engine.breakeven(p, _bot_cost(bot)) * 100}


def _bot_cost(bot):
    acc = config.ACCOUNTS.get(bot["account"])
    return ml_engine.cost_for(bot["market"], acc.type if acc else None)


@app.get("/api/ml/summary")
def ml_summary():
    rows = db.all("SELECT status, COUNT(*) n FROM ml_models GROUP BY status")
    c = {r["status"]: r["n"] for r in rows}
    lab = db.one("SELECT COUNT(*) n FROM lab_events WHERE kind='proposal' AND status='pending'")["n"]
    return {"pending": c.get("candidate", 0), "training": c.get("training", 0), "lab_pending": lab}


@app.get("/api/ml/models")
def ml_models(bot_id: int | None = None):
    bots = {b["id"]: b for b in db.bots()}
    out = {"models": db.models(bot_id), "bots": []}
    for b in bots.values():
        if bot_id and b["id"] != bot_id:
            continue
        info = ml_bot_info(b)
        if info["uses_ml"] or bot_id:
            out["bots"].append({"id": b["id"], "name": b["name"], "strategy": b["strategy"],
                                "strategy_name": STRATEGIES[b["strategy"]].name,
                                "status": manager.status(b["id"]), **info})
    return out


@app.get("/api/ml/models/{model_id}")
def ml_model(model_id: int):
    m = db.model(model_id)
    if not m:
        raise HTTPException(404, "Nie ma takiego modelu")
    m.pop("path", None)
    return m


@app.post("/api/bots/{bot_id}/ml/train")
def ml_train(bot_id: int):
    bot = db.bot(bot_id)
    if not bot:
        raise HTTPException(404, "Nie ma takiego bota")
    p = full_params(bot["market"], bot["strategy"], bot["params"])
    if not ml_engine.uses_ml(bot["strategy"], p):
        raise HTTPException(400, "Ten bot nie używa ML - włącz filtr ML, wielkość pozycji wg ML albo strategię ML.")
    if db.one("SELECT 1 FROM ml_models WHERE bot_id=? AND status='training'", (bot_id,)):
        raise HTTPException(400, "Model tego bota właśnie się uczy.")
    threading.Thread(target=manager.train_model, args=(bot_id, "manual"), daemon=True, name=f"ml-{bot_id}").start()
    return {"ok": True}


@app.post("/api/ml/models/{model_id}/approve")
def ml_approve(model_id: int):
    m = db.model(model_id)
    if not m:
        raise HTTPException(404, "Nie ma takiego modelu")
    if not m["path"] or m["status"] in ("training", "error", "active"):
        raise HTTPException(400, "Tego modelu nie można zatwierdzić.")
    bot = db.bot(m["bot_id"])
    if not bot:
        raise HTTPException(400, "Bot tego modelu już nie istnieje.")
    p = full_params(bot["market"], bot["strategy"], bot["params"])
    if m["signature"] != ml_engine.signature(p):
        raise HTTPException(400, "Model był uczony na innych ustawieniach bota (SL/TP/interwał/horyzont). "
                                 "Naucz nowy model.")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    db.execute("UPDATE ml_models SET status='retired', decided_at=? WHERE bot_id=? AND status='active'",
               (now, m["bot_id"]))
    db.update_model(model_id, status="active", decided_at=now)
    passed = (m["summary"] or {}).get("passed")
    db.log(m["bot_id"], "INFO" if passed else "WARNING",
           f"ML: zatwierdzono model #{model_id}" + ("" if passed else " mimo niezaliczonego testu w czasie") +
           " - bot użyje go od następnego cyklu.")
    return {"ok": True}


@app.post("/api/ml/models/{model_id}/reject")
def ml_reject(model_id: int):
    m = db.model(model_id)
    if not m or m["status"] not in ("candidate", "active"):
        raise HTTPException(400, "Tego modelu nie można odrzucić.")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    db.update_model(model_id, status="retired" if m["status"] == "active" else "rejected", decided_at=now)
    db.log(m["bot_id"], "INFO", f"ML: model #{model_id} " + ("wyłączony." if m["status"] == "active" else "odrzucony."))
    return {"ok": True}


# ------------------------------------------------------------------ radar altcoinow
from . import radar as radar_mod


def radar_providers():
    if config.DEMO:
        return [{"name": "sim", "label": "Symulacja (demo)", "quote": "USD"}]
    out = [{"name": "kraken", "label": "Kraken", "quote": (config.kraken_account().extra.get("quote", "EUR")
                                                        if config.kraken_account() else "EUR")}]
    seen = set()
    for a in config.ACCOUNTS.values():
        ex = config.exchange_id(a) if a.type == "gielda" else None
        if ex and ex not in seen:
            seen.add(ex)
            from .kraken import exchange_name
            out.append({"name": "ccxt:" + ex, "label": exchange_name(ex), "quote": a.extra.get("quote", "USDT")})
    if config.data_account():
        out.append({"name": "alpaca", "label": "Alpaca", "quote": "USD"})
    out.append({"name": "sim", "label": "Symulacja (demo)", "quote": "USD"})
    return out


def radar_source(name):
    if name == "kraken":
        from .kraken import kraken_data
        return kraken_data()
    if name.startswith("ccxt:"):
        if not config.exchange_account(name[5:]):
            raise HTTPException(400, "Brak konta tej giełdy")
        from .kraken import kraken_data
        return kraken_data(name[5:])
    if name == "alpaca":
        acc = config.data_account()
        if not acc:
            raise HTTPException(400, "Brak konta Alpaca w .env")
        return AlpacaData(acc.key, acc.secret, acc.paper)
    return SimData()


radar_jobs = {}


@app.get("/api/radar")
def radar_get(provider: str = "kraken", quote: str = ""):
    prov = next((x for x in radar_providers() if x["name"] == provider), None)
    if not prov:
        raise HTTPException(400, "Nieznane źródło")
    quote = (quote or prov["quote"]).upper()
    return {"providers": radar_providers(), "provider": provider, "quote": quote,
            "scan": radar_mod.latest(db, provider, quote), "new": radar_mod.new_listings(db, provider),
            "job": radar_jobs.get((provider, quote))}


@app.get("/api/radar/coin")
def radar_coin(symbol: str, provider: str = "kraken"):
    """Wykres monety: cena, SMA 20/50, trend i 'lejek' mozliwych cen na 30 dni (statystyka z historii)."""
    src = radar_source(provider)
    daily = src.bars([symbol], "1Day", datetime.now(timezone.utc) - timedelta(days=730)).get(symbol)
    det = radar_mod.coin_detail(daily)
    if not det:
        raise HTTPException(400, "Za krótka historia tej monety (potrzeba min. 60 dni notowań).")
    det["symbol"] = symbol
    return det


@app.post("/api/radar/scan")
def radar_scan(provider: str = "kraken", quote: str = ""):
    prov = next((x for x in radar_providers() if x["name"] == provider), None)
    if not prov:
        raise HTTPException(400, "Nieznane źródło")
    quote = (quote or prov["quote"]).upper()
    key = (provider, quote)
    if (radar_jobs.get(key) or {}).get("state") == "running":
        return {"ok": True}
    src = radar_source(provider)
    radar_jobs[key] = {"state": "running", "started": datetime.now(timezone.utc).isoformat(timespec="seconds")}

    def work():
        try:
            radar_mod.scan(src, provider, quote, db=db)
            radar_jobs[key] = {"state": "done"}
        except Exception as e:
            log.exception("Radar")
            radar_jobs[key] = {"state": "error", "error": str(e)}

    threading.Thread(target=work, daemon=True, name="radar-scan").start()
    return {"ok": True}


# ------------------------------------------------------------------ radar spolek (GPW, USA) i przeglad
from . import stock_radar


def stock_markets():
    if config.DEMO:
        return dict(stock_radar.MARKETS)
    ids = {x["id"] for x in chart_sources()}
    return {k: v for k, v in stock_radar.MARKETS.items() if v["source"] in ids}


def start_stock_scan(market):
    key = ("stocks", market)
    if (radar_jobs.get(key) or {}).get("state") == "running":
        return
    provider = SimData() if config.DEMO else chart_provider("X", stock_radar.MARKETS[market]["source"])[0]
    radar_jobs[key] = {"state": "running", "started": datetime.now(timezone.utc).isoformat(timespec="seconds")}

    def work():
        try:
            with stock_radar._lock:
                stock_radar.scan(provider, market, db=db)
            radar_jobs[key] = {"state": "done"}
        except Exception as e:
            log.exception("Radar spółek")
            radar_jobs[key] = {"state": "error", "error": str(e)[:300]}

    threading.Thread(target=work, daemon=True, name=f"radar-{market}").start()


@app.get("/api/radar/stocks")
def radar_stocks(market: str = "gpw"):
    if market not in stock_radar.MARKETS:
        raise HTTPException(400, "Nieznany rynek")
    cfg = stock_radar.MARKETS[market]
    return {"market": market, "label": cfg["label"], "bench": cfg["bench_label"], "currency": cfg["currency"],
            "available": market in stock_markets(), "scan": stock_radar.latest(db, market),
            "job": radar_jobs.get(("stocks", market))}


@app.post("/api/radar/stocks/scan")
def radar_stocks_scan(market: str = "gpw"):
    if market not in stock_markets():
        raise HTTPException(400, "Brak podłączonego źródła danych dla tego rynku.")
    start_stock_scan(market)
    return {"ok": True}


@app.get("/api/radar/overview")
def radar_overview(top: int = 5):
    """Najlepsze propozycje z trzech radarów: altcoiny, GPW, USA."""
    top = max(1, min(int(top), 15))
    out = []
    prov = radar_providers()[0]
    sc = radar_mod.latest(db, prov["name"], prov["quote"])
    out.append({"id": "crypto", "label": "Altcoiny", "sub": f"{prov['label']} /{prov['quote']}", "ts": sc and sc["ts"],
                "currency": prov["quote"], "mood": sc and sc.get("market"), "items": (sc or {}).get("items", [])[:top]})
    for m, cfg in stock_radar.MARKETS.items():
        sc = stock_radar.latest(db, m)
        out.append({"id": m, "label": cfg["label"], "sub": cfg["bench_label"], "ts": sc and sc["ts"],
                    "currency": cfg["currency"], "mood": sc and sc.get("market"), "available": m in stock_markets(),
                    "items": (sc or {}).get("items", [])[:top],
                    "signals": [r["symbol"] for r in (sc or {}).get("items", []) if r.get("bot_signal")]})
    return {"sections": out}


def stock_radar_loop():
    """Automatyczny skan spolek raz dziennie po zamknieciu sesji (GPW ok. 17:30, USA ok. 22:30)."""
    time.sleep(120)
    while True:
        for m in stock_markets():
            try:
                if stock_radar.due(db, m):
                    start_stock_scan(m)
            except Exception:
                log.exception("Radar spółek - harmonogram")
        time.sleep(600)


threading.Thread(target=stock_radar_loop, daemon=True, name="radar-stocks-auto").start()


# ------------------------------------------------------------------ laboratorium
from . import lab as lab_mod


@app.get("/api/lab")
def lab_list():
    out = []
    for b in db.bots():
        p = full_params(b["market"], b["strategy"], b["params"])
        if not p.get("lab_enabled"):
            continue
        winner, champ, items = lab_mod.judge(db, b, p)
        acc = config.ACCOUNTS.get(b["account"])
        out.append({"id": b["id"], "name": b["name"], "status": manager.status(b["id"]),
                    "auto": bool(acc and (acc.paper or acc.type == "sim")),
                    "min_trades": p["lab_min_trades"], "min_days": p["lab_min_days"],
                    "variants": items, "leader": winner["id"] if winner else None,
                    "events": db.all("SELECT * FROM lab_events WHERE bot_id=? ORDER BY id DESC LIMIT 20", (b["id"],))})
    return {"bots": out, "pending": db.one("SELECT COUNT(*) n FROM lab_events WHERE kind='proposal' AND status='pending'")["n"]}


@app.post("/api/lab/events/{event_id}/{action}")
def lab_decide(event_id: int, action: str):
    ev = db.one("SELECT * FROM lab_events WHERE id=?", (event_id,))
    if not ev or ev["kind"] != "proposal" or ev["status"] != "pending":
        raise HTTPException(400, "Ta propozycja jest już nieaktualna.")
    bot = db.bot(ev["bot_id"])
    if action == "reject":
        db.execute("UPDATE lab_events SET status='rejected' WHERE id=?", (event_id,))
        return {"ok": True}
    if action != "approve" or not bot:
        raise HTTPException(400, "Nieznana akcja")
    data = json.loads(ev["data"] or "{}")
    v = db.one("SELECT * FROM lab_variants WHERE id=? AND status='active'", (data.get("id"),))
    if not v:
        db.execute("UPDATE lab_events SET status='rejected' WHERE id=?", (event_id,))
        raise HTTPException(400, "Wariant już nie istnieje (laboratorium zaczęło od nowa).")
    winner = dict(data, changes=json.loads(v["changes"] or "{}"), model_id=v["model_id"], kind=v["kind"])
    text = lab_mod.apply_winner(db, manager, bot, winner, auto=False)
    db.execute("UPDATE lab_events SET status='applied' WHERE id=?", (event_id,))
    return {"ok": True, "text": text}


@app.post("/api/lab/{bot_id}/reset")
def lab_reset(bot_id: int):
    db.execute("UPDATE lab_variants SET status='retired' WHERE bot_id=? AND status='active'", (bot_id,))
    db.execute("INSERT INTO lab_events(bot_id, ts, kind, status, text) VALUES (?,?,?,?,?)",
               (bot_id, datetime.now(timezone.utc).isoformat(timespec="seconds"), "reset", "info",
                "Laboratorium wyzerowane ręcznie - nowy zestaw wariantów."))
    return {"ok": True}


# ------------------------------------------------------------------ powiadomienia
@app.get("/api/notify")
def notify_status():
    return notify.status()


@app.post("/api/notify/connect")
def notify_connect():
    try:
        who = notify.detect_chat()
    except Exception as e:
        raise HTTPException(400, str(e))
    notify.send(f"🔔 Powiadomienia połączone. Cześć, {who}!", "system")
    return {"ok": True, "who": who}


class NtfyIn(BaseModel):
    enable: bool


@app.post("/api/notify/ntfy")
def notify_ntfy(body: NtfyIn):
    if body.enable:
        topic = notify.ntfy_enable()
        return {"ok": True, "topic": topic, "server": notify.ntfy_server()}
    notify.ntfy_disable()
    return {"ok": True}


@app.post("/api/notify/test")
def notify_test():
    try:
        notify.send_now("Wiadomość testowa - powiadomienia działają. 👍")
    except Exception as e:
        raise HTTPException(400, f"Nie udało się wysłać: {e}")
    return {"ok": True}


class HeartbeatIn(BaseModel):
    url: str = ""


@app.get("/api/notify/heartbeat")
def heartbeat_get():
    from . import heartbeat
    return heartbeat.public()


@app.put("/api/notify/heartbeat")
def heartbeat_put(body: HeartbeatIn):
    from . import heartbeat
    try:
        heartbeat.set_url(body.url)
        if body.url.strip():
            heartbeat.ping(db, test=True)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(400, f"Nie udało się wysłać pingu: {str(e)[:150]}")
    return heartbeat.public()


@app.post("/api/notify/heartbeat/test")
def heartbeat_test():
    from . import heartbeat
    try:
        heartbeat.ping(db)
    except Exception as e:
        raise HTTPException(400, str(e)[:200])
    return heartbeat.public()


class NotifyEvents(BaseModel):
    events: dict


@app.put("/api/notify/events")
def notify_events(body: NotifyEvents):
    ev = {k: bool(v) for k, v in body.events.items() if k in notify.EVENTS}
    s = notify.settings()
    s["events"].update(ev)
    notify.save(events=s["events"])
    return notify.status()


# ------------------------------------------------------------------ system i aktualizacje
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UPDATES = os.path.join(config.DATA_DIR, "updates")


def app_version():
    for path in (os.path.join(BASE, "VERSION"), os.path.join(os.getcwd(), "VERSION")):
        if os.path.exists(path):
            return open(path, encoding="utf-8").read().strip()
    return "?"


def version_key(v):
    """1.4.0 -> (1, 4, 0). Stare numery z data (2026.09.30-11) sa starsze niz kazda wersja 1.x."""
    m = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", (v or "").strip())
    return tuple(int(x) for x in m.groups()) if m else (0, 0, 0)


# ------------------------------------------------------------------ konta brokerskie (zakladka Konta)
class AccountIn(BaseModel):
    name: str = ""
    type: str = ""
    values: dict = {}
    password: str = ""


def _confirm_password(request: Request, password: str):
    """Zmiany kont wymagaja ponownego podania hasla do panelu (jak przy logowaniu - z limitem prob)."""
    ip = request.client.host if request.client else "?"
    _too_many(ip)
    if not panel_auth.password_ok(password, PASSWORD):
        _fail(ip)
        raise HTTPException(403, "Nieprawidłowe hasło do panelu.")
    failed.pop(ip, None)


def _confirm_code(request: Request, code: str):
    """Gdy 2FA jest wlaczone: kod z aplikacji (albo awaryjny) do waznych zmian."""
    if not panel_auth.totp_on():
        return
    ip = request.client.host if request.client else "?"
    _too_many(ip)
    if not panel_auth.check_code(code):
        _fail(ip)
        raise HTTPException(403, "Nieprawidłowy kod 2FA.")


# ------------------------------------------------------------------ bezpieczenstwo: login, 2FA, klucze skryptow
class SecIn(BaseModel):
    password: str = ""
    code: str = ""
    username: str = ""
    new_password: str = ""
    name: str = ""


def _keep_only(request: Request):
    """Po zmianie hasla / 2FA: wyloguj wszystkie inne sesje."""
    me = _sid(request.cookies.get("session"))
    for k in list(sessions):
        if k != me:
            sessions.pop(k, None)
    _save_sessions()


@app.get("/api/security")
def security_get():
    return panel_auth.status()


@app.post("/api/security/login")
def security_login(body: SecIn, request: Request):
    _confirm_password(request, body.password)
    _confirm_code(request, body.code)
    try:
        panel_auth.set_login(body.username, body.new_password)
    except ValueError as e:
        raise HTTPException(400, str(e))
    _keep_only(request)
    log.warning("Zmieniono login/hasło panelu")
    return {"ok": True}


@app.post("/api/security/2fa/begin")
def security_2fa_begin(body: SecIn, request: Request):
    _confirm_password(request, body.password)
    try:
        return panel_auth.totp_begin()
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/security/2fa/confirm")
def security_2fa_confirm(body: SecIn, request: Request):
    try:
        codes = panel_auth.totp_confirm(body.code)
    except ValueError as e:
        raise HTTPException(400, str(e))
    _keep_only(request)
    log.warning("Włączono 2FA dla panelu")
    return {"ok": True, "recovery": codes}


@app.post("/api/security/2fa/disable")
def security_2fa_disable(body: SecIn, request: Request):
    _confirm_password(request, body.password)
    _confirm_code(request, body.code)
    panel_auth.totp_disable()
    log.warning("Wyłączono 2FA dla panelu")
    return {"ok": True}


@app.post("/api/security/recovery")
def security_recovery(body: SecIn, request: Request):
    _confirm_password(request, body.password)
    _confirm_code(request, body.code)
    return {"ok": True, "recovery": panel_auth.new_recovery()}


@app.post("/api/security/tokens")
def security_token_add(body: SecIn, request: Request):
    _confirm_password(request, body.password)
    _confirm_code(request, body.code)
    return {"ok": True, "token": panel_auth.token_create(body.name)}


@app.delete("/api/security/tokens/{tid}")
def security_token_del(tid: int, request: Request):
    _confirm_password(request, request.headers.get("X-Confirm-Password", ""))
    panel_auth.token_delete(tid)
    return {"ok": True}


def _account_bots(name, running_only=False):
    return [f"#{b['id']} {b['name']}" for b in db.bots() if b["account"] == name
            and (not running_only or manager.status(b["id"]) == "running")]


# ------------------------------------------------------------------ wykresy (zakladka "Wykresy")
CHART_TF = {"15Min": 12, "1Hour": 60, "4Hour": 200, "1Day": 1100}     # ile dni historii na interwal


def chart_sources():
    out = []
    if config.data_account():
        out.append({"id": "alpaca", "label": "Alpaca (USA, krypto USD)"})
    if any(a.type == "ibkr" for a in config.ACCOUNTS.values()):
        out.append({"id": "ibkr", "label": "IBKR (GPW, Xetra, USA, waluty)"})
    if config.kraken_account():
        out.append({"id": "kraken", "label": f"Kraken (krypto {config.kraken_account().extra.get('quote', 'EUR')})"})
    seen = set()
    for a in config.ACCOUNTS.values():
        if a.type == "gielda" and config.exchange_id(a) not in seen:
            seen.add(config.exchange_id(a))
            from .kraken import exchange_name
            out.append({"id": "ccxt:" + config.exchange_id(a), "label": f"{exchange_name(config.exchange_id(a))} (krypto)"})
    out.append({"id": "sim", "label": "Symulacja"})
    return out


def chart_provider(symbol, source):
    """Zrodlo danych dla symbolu: wybrane recznie albo dobrane automatycznie po formacie symbolu."""
    ids = {s["id"] for s in chart_sources()}
    if source == "auto":
        if "/" in symbol:
            q = symbol.split("/")[1]
            ka = config.kraken_account()
            if ka and ka.extra.get("quote", "EUR") == q:
                source = "kraken"
            else:
                ex = next((a for a in config.ACCOUNTS.values() if a.type == "gielda" and a.extra.get("quote") == q), None)
                source = ("ccxt:" + config.exchange_id(ex)) if ex else ("alpaca" if "alpaca" in ids and q == "USD" else
                                                                       "kraken" if ka else "sim")
        elif "." in symbol:
            source = "ibkr" if "ibkr" in ids else "sim"
        else:
            source = "alpaca" if "alpaca" in ids else "ibkr" if "ibkr" in ids else "sim"
    if source not in ids:
        raise HTTPException(400, "To źródło danych nie jest podłączone.")
    if source == "kraken":
        from .kraken import kraken_data
        return kraken_data(), source
    if source.startswith("ccxt:"):
        from .kraken import kraken_data
        return kraken_data(source[5:]), source
    if source == "ibkr":
        ib = next(a for a in config.ACCOUNTS.values() if a.type == "ibkr")
        return get_broker(ib).data, source
    if source == "alpaca":
        acc = config.data_account()
        return AlpacaData(acc.key, acc.secret, acc.paper), source
    return SimData(), "sim"


@app.get("/api/chart/symbols")
def chart_symbols():
    """Podpowiedzi: symbole z botow (najpierw te, ktore boty teraz trzymaja)."""
    held, seen = [], []
    for b in db.bots():
        for st in db.pos_states(b["id"]).values():
            held.append(st["symbol"])
        for sym in (b["params"] or {}).get("symbols", []):
            if sym not in seen:
                seen.append(sym)
    by_norm = {norm(s): s for s in seen}
    held = [by_norm.get(h, h) for h in held]
    from . import plan as plan_mod
    return {"held": list(dict.fromkeys(held)), "symbols": seen[:400], "sources": chart_sources(),
            "templates": plan_mod.templates(db.bots())}


@app.get("/api/chart")
def chart(symbol: str, tf: str = "1Day", source: str = "auto", template: str = ""):
    symbol = symbol.strip().upper()
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9./\-]{0,24}", symbol):
        raise HTTPException(400, "Nieprawidłowy symbol.")
    if tf not in CHART_TF:
        raise HTTPException(400, "Nieznany interwał.")
    provider, src = chart_provider(symbol, source)
    start = datetime.now(timezone.utc) - timedelta(days=CHART_TF[tf])
    try:
        df = provider.bars([symbol], tf, start).get(symbol)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"Nie udało się pobrać danych: {str(e)[:200]}")
    if df is None or df.empty:
        hint = " Symbole GPW: PKN.WSE, akcje USA: AAPL, krypto: BTC/USD." if src != "sim" else ""
        raise HTTPException(404, f"Brak danych dla {symbol} ({src}).{hint}")
    df = df.tail(1500)
    bars = [[int(ts.timestamp()), float(r.open), float(r.high), float(r.low), float(r.close), float(r.volume or 0)]
            for ts, r in df.iterrows()]
    n = norm(symbol)
    names = {b["id"]: b["name"] for b in db.bots()}
    trades = [dict(t, bot_name=names.get(t["bot_id"], "?"))
              for t in db.all("SELECT * FROM trades WHERE symbol=? OR symbol=? ORDER BY ts", (symbol, n))]
    levels = []
    for b in db.bots():
        st = db.pos_states(b["id"]).get(n) or db.pos_states(b["id"]).get(symbol)
        if st:
            levels.append({"bot": b["name"], "entry": st["entry"], "sl": st["sl"], "tp": st["tp"], "qty": st["qty"]})
    plan_res = None
    if template and template != "none":
        from . import plan as plan_mod
        try:
            plan_res = plan_mod.build(df, template, db)
        except ValueError as e:
            plan_res = {"error": str(e)}
        except Exception as e:
            log.exception("Plan na wykresie")
            plan_res = {"error": f"Nie udało się policzyć planu: {str(e)[:150]}"}
    from . import patterns as patterns_mod
    try:
        pat = patterns_mod.analyze(df)
    except Exception as e:
        log.exception("Formacje na wykresie")
        pat = {"error": f"Nie udało się przeanalizować formacji: {str(e)[:150]}"}
    return {"symbol": symbol, "tf": tf, "source": src, "bars": bars, "trades": trades, "levels": levels, "plan": plan_res,
            "patterns": pat}


@app.get("/api/fundamentals")
def fundamentals_get(symbol: str, price: float | None = None, refresh: bool = False):
    symbol = symbol.strip().upper()
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9./\-]{0,24}", symbol):
        raise HTTPException(400, "Nieprawidłowy symbol.")
    from . import fundamentals
    return fundamentals.get(symbol, price, refresh)


@app.get("/api/calendar")
def calendar_symbol(symbol: str, refresh: bool = False):
    symbol = symbol.strip().upper()
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9./\-]{0,24}", symbol):
        raise HTTPException(400, "Nieprawidłowy symbol.")
    from . import calendar_events as cal
    return {"symbol": symbol, "event": cal.get(symbol, refresh=refresh)}


@app.get("/api/calendar/upcoming")
def calendar_upcoming(days: int = 30):
    from . import calendar_events as cal
    syms = cal.watched(db)
    held = cal.held(db)
    bots_by_sym = {}
    for b in db.bots():
        if b["market"] == "stocks":
            for s in (b["params"] or {}).get("symbols", []):
                bots_by_sym.setdefault(s.upper(), []).append({"id": b["id"], "name": b["name"],
                    "blackout": int((full_params(b["market"], b["strategy"], b["params"]) or {}).get("earnings_blackout_days") or 0)})
    items = cal.upcoming(syms, max(1, min(days, 90)))
    for it in items:
        it["held_by"] = held.get(it["symbol"], [])
        it["bots"] = bots_by_sym.get(it["symbol"], [])
    return {"items": items, "status": cal.status(syms), "demo": config.DEMO}


# ------------------------------------------------------------------ alerty TradingView (dane dla strony bota)
@app.get("/api/bots/{bot_id}/tv")
def bot_tv(bot_id: int):
    bot = db.bot(bot_id)
    if not bot or bot["strategy"] != "tv_alerts":
        raise HTTPException(404, "To nie jest bot alertów TradingView.")
    from .webhook import PORT
    base = os.environ.get("TV_WEBHOOK_URL", "").strip().rstrip("/")
    tok = (bot["params"] or {}).get("tv_token", "")
    alerts = db.all("SELECT id, ts, symbol, action, price, status, note FROM tv_alerts WHERE bot_id=? "
                    "ORDER BY id DESC LIMIT 30", (bot_id,))
    return {"token": tok, "port": PORT, "url": f"{base}/tv/{tok}" if base else None, "base_set": bool(base),
            "passphrase": bool((bot["params"] or {}).get("tv_passphrase")), "alerts": alerts,
            "symbols": bot["params"].get("symbols", [])}


@app.post("/api/bots/{bot_id}/tv/rotate")
def bot_tv_rotate(bot_id: int):
    bot = db.bot(bot_id)
    if not bot or bot["strategy"] != "tv_alerts":
        raise HTTPException(404, "To nie jest bot alertów TradingView.")
    from .webhook import new_token
    p = dict(bot["params"], tv_token=new_token())
    db.update_bot(bot_id, params=p)
    db.log(bot_id, "INFO", "Nowy adres webhooka TradingView (stary przestał działać).")
    return {"ok": True}


# ------------------------------------------------------------------ strategia z opisu słownego (AI) i galeria
class AiIn(BaseModel):
    text: str
    market: str | None = None
    account: str | None = None


_ai_hits = deque()


@app.get("/api/ai/status")
def ai_status():
    from . import ai_builder
    return {"enabled": ai_builder.enabled(), "model": ai_builder.MODEL}


@app.post("/api/ai/strategy")
def ai_strategy(body: AiIn):
    from . import ai_builder
    now = time.time()
    while _ai_hits and now - _ai_hits[0] > 3600:
        _ai_hits.popleft()
    if len(_ai_hits) >= 30:
        raise HTTPException(429, "Limit 30 tłumaczeń na godzinę — spróbuj później.")
    acc = config.ACCOUNTS.get(body.account) if body.account else None
    try:
        _ai_hits.append(now)
        return ai_builder.build(body.text, body.market if body.market in ("stocks", "crypto") else None,
                                acc.type if acc else None)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        log.warning(f"AI strategia: {e}")
        raise HTTPException(502, "Nie udało się połączyć z API Claude — spróbuj ponownie.")


GALLERY_PATH = os.path.join(os.path.dirname(__file__), "gallery.json")


@app.get("/api/gallery")
def gallery():
    with open(GALLERY_PATH, encoding="utf-8") as f:
        g = json.load(f)
    for it in g["items"]:
        it["strategy_name"] = STRATEGIES[it["strategy"]].name if it["strategy"] in STRATEGIES else it["strategy"]
        it["params"] = full_params(it["market"], it["strategy"], it["params"])
    return g


# ------------------------------------------------------------------ ryzyko: bezpiecznik, dzwignia, ETF-y lewarowane
class RiskIn(BaseModel):
    halt: bool | None = None
    halt_reason: str | None = None
    daily_loss_pct: float | None = None
    close_leveraged_on_limit: bool | None = None


class RiskAccIn(BaseModel):
    account: str
    leverage_ok: bool | None = None
    max_leverage: float | None = None
    password: str = ""
    code: str = ""


def _lev_bots():
    out = []
    for b in db.bots():
        p = full_params(b["market"], b["strategy"], b["params"])
        if p.get("leverage_mode", "off") != "off":
            out.append({"id": b["id"], "name": b["name"], "account": b["account"], "status": b["status"],
                        "mode": p["leverage_mode"], "direction": p["direction"], "leverage": p["leverage"]})
    return out


@app.get("/api/risk")
def risk_get():
    from . import levetf, risk as risk_mod
    eq = {}
    for acc in config.ACCOUNTS.values():
        r = db.one("SELECT equity, exposure, ts FROM equity WHERE scope='account' AND ref=? AND equity IS NOT NULL "
                   "ORDER BY ts DESC LIMIT 1", (acc.name,))
        if r:
            eq[acc.name] = {"equity": r["equity"], "exposure": r["exposure"], "gross_lev":
                            (abs(r["exposure"] or 0) / r["equity"]) if r["equity"] else None, "ts": r["ts"]}
    st = risk_mod.status(list(config.ACCOUNTS.values()), eq)
    st["bots"] = _lev_bots()
    st["etf"] = levetf.table()
    return st


@app.put("/api/risk")
def risk_put(body: RiskIn):
    from . import risk as risk_mod
    ch = {k: v for k, v in body.model_dump().items() if v is not None}
    if "daily_loss_pct" in ch and not 0 <= ch["daily_loss_pct"] <= 0.5:
        raise HTTPException(400, "Dzienny limit straty: od 0 (wyłączony) do 50%.")
    if "halt_reason" in ch:
        ch["halt_reason"] = ch["halt_reason"][:200]
    d = risk_mod.update(**ch)
    if "halt" in ch:
        notify.send("⏸ Wyłącznik: nowe wejścia wszystkich botów WSTRZYMANE." if ch["halt"]
                    else "▶ Wyłącznik zdjęty: boty mogą znowu otwierać pozycje.", "system")
    return d


@app.put("/api/risk/account")
def risk_account(body: RiskAccIn, request: Request):
    from . import risk as risk_mod
    acc = config.ACCOUNTS.get(body.account)
    if not acc:
        raise HTTPException(404, "Nie ma takiego konta.")
    ch = {}
    if body.leverage_ok is not None:
        if body.leverage_ok and risk_mod.is_real(acc):     # zgoda na dzwignie na prawdziwych pieniadzach: haslo + 2FA
            _confirm_password(request, body.password)
            _confirm_code(request, body.code)
        ch["leverage_ok"] = body.leverage_ok
    if body.max_leverage is not None:
        if not (body.max_leverage == 0 or 1 <= body.max_leverage <= 5):
            raise HTTPException(400, "Limit dźwigni: 1–5 (0 = domyślny limit rynku).")
        ch["max_leverage"] = body.max_leverage
    a = risk_mod.set_account(acc.name, **ch)
    if body.leverage_ok and risk_mod.is_real(acc):
        notify.send(f"⚠️ Włączono zgodę na dźwignię i grę na spadki na koncie z prawdziwymi pieniędzmi: {acc.name}.",
                    "system", priority=5)
    return a


@app.post("/api/risk/verify-etfs")
def risk_verify_etfs():
    from . import levetf
    alp = None
    da = config.data_account()
    if da:
        try:
            from alpaca.trading.client import TradingClient
            alp = TradingClient(da.key, da.secret, paper=da.paper)
        except Exception as e:
            raise HTTPException(400, f"Alpaca niedostępna: {e}")
    ib = next((a for a in config.ACCOUNTS.values() if a.type == "ibkr"), None)
    ibb = None
    if ib:
        try:
            ibb = get_broker(ib)
        except Exception:
            ibb = None
    if not alp and not ibb:
        raise HTTPException(400, "Do sprawdzenia ETF-ów potrzebne jest konto Alpaca albo IBKR.")
    levetf.verify(alp, ibb)
    return levetf.table()


class CloseLevIn(BaseModel):
    password: str = ""
    code: str = ""


@app.post("/api/risk/close-leveraged")
def risk_close_leveraged(body: CloseLevIn, request: Request):
    """Zamyka wszystkie pozycje botów z dźwignią / grą na spadki (po rynku) i wstrzymuje nowe wejścia."""
    _confirm_password(request, body.password)
    _confirm_code(request, body.code)
    from . import risk as risk_mod
    risk_mod.update(halt=True, halt_reason="zamknięto pozycje z dźwignią")
    closed, errors = 0, []
    for bid, runner in list(manager.runners.items()):
        if getattr(runner, "lev_mode", "off") == "off":
            continue
        try:
            for s, pos in runner.mine(runner.broker.positions()).items():
                runner.close(runner.by_norm.get(s, pos["symbol"]), "zamknięcie z zakładki Ryzyko", pos)
                closed += 1
        except Exception as e:
            errors.append(f"{runner.bot['name']}: {e}")
    notify.send(f"🛑 Zamknięto {closed} pozycji z dźwignią; nowe wejścia wstrzymane (wyłącznik).", "system", priority=5)
    return {"closed": closed, "errors": errors}


# ------------------------------------------------------------------ bramka IB Gateway
class GatewayIn(BaseModel):
    action: str = ""
    password: str = ""
    code: str = ""
    auto_restart: bool | None = None
    threshold_min: int | None = None
    weekend_pause: bool | None = None


@app.get("/api/gateway")
def gateway_get():
    from . import gateway
    return gateway.state()


@app.post("/api/gateway/action")
def gateway_action(body: GatewayIn, request: Request):
    from . import gateway
    if body.action in ("stop", "start"):            # zatrzymanie wylacza handel na IBKR - wymaga hasla (i kodu 2FA)
        _confirm_password(request, body.password)
        _confirm_code(request, body.code)
    try:
        cid = gateway.request(body.action, "z panelu")
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "id": cid}


@app.put("/api/gateway/settings")
def gateway_settings(body: GatewayIn):
    from . import gateway
    cur = gateway.settings()
    try:
        gateway.save_settings(cur["auto_restart"] if body.auto_restart is None else body.auto_restart,
                              cur["threshold_min"] if body.threshold_min is None else body.threshold_min,
                              cur.get("weekend_pause", True) if body.weekend_pause is None else body.weekend_pause)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return gateway.settings()


@app.get("/api/accounts")
def list_accounts():
    return {"accounts": accounts.listing(db.bots()), "platforms": accounts.PLATFORMS, "modes": accounts.MODE_LABELS,
            "master_key": "env" if os.environ.get("ACCOUNTS_MASTER_KEY") else "plik data/.accounts_key"}


@app.post("/api/accounts/test")
def test_account(body: AccountIn):
    try:
        existing = config.ACCOUNTS.get(body.name) if body.name else None
        if existing and not any(v not in (None, "") for v in body.values.values()):
            return accounts.probe(existing)                  # test zapisanego konta (takze z .env)
        if existing and accounts.source(body.name) != "panel":
            existing = None
        return accounts.test(body.type or (existing.type if existing else ""), body.values, body.name or "nowe",
                             existing=existing)
    except Exception as e:
        raise HTTPException(400, f"Połączenie nieudane: {str(e)[:300]}")


@app.post("/api/accounts")
def add_account(body: AccountIn, request: Request):
    _confirm_password(request, body.password)
    try:
        name = accounts.add(body.name, body.type, body.values)
    except ValueError as e:
        raise HTTPException(400, str(e))
    log.info(f"Dodano konto '{name}' ({body.type}) z panelu")
    return {"ok": True, "name": name}


@app.put("/api/accounts/{name}")
def update_account(name: str, body: AccountIn, request: Request):
    _confirm_password(request, body.password)
    if name not in config.ACCOUNTS:
        raise HTTPException(404, "Nie ma takiego konta")
    try:
        accounts.update(name, body.values, _account_bots(name, running_only=True))
    except ValueError as e:
        raise HTTPException(400, str(e))
    log.info(f"Zmieniono konto '{name}' z panelu")
    return {"ok": True}


@app.delete("/api/accounts/{name}")
def delete_account(name: str, request: Request):
    _confirm_password(request, request.headers.get("X-Confirm-Password", ""))   # haslo w naglowku, nie w adresie
    if name not in config.ACCOUNTS:
        raise HTTPException(404, "Nie ma takiego konta")
    try:
        accounts.remove(name, _account_bots(name))
    except ValueError as e:
        raise HTTPException(400, str(e))
    log.info(f"Usunięto konto '{name}' z panelu")
    return {"ok": True}


@app.get("/api/system")
def system_info():
    status = None
    st_path = os.path.join(config.DATA_DIR, "update_status.json")
    if os.path.exists(st_path):
        try:
            status = json.load(open(st_path, encoding="utf-8"))
        except ValueError:
            status = None
    log_path = os.path.join(config.DATA_DIR, "update.log")
    log_tail = open(log_path, encoding="utf-8", errors="replace").read().splitlines()[-40:] \
        if os.path.exists(log_path) else []
    pending = sorted(f for f in os.listdir(UPDATES) if f.endswith(".zip")) if os.path.isdir(UPDATES) else []
    backups = sorted(os.listdir(os.path.join(config.DATA_DIR, "backups")), reverse=True) \
        if os.path.isdir(os.path.join(config.DATA_DIR, "backups")) else []
    from . import release
    signing = {"required": os.environ.get("ALLOW_UNSIGNED_UPDATES", "").strip().lower() != "true",
               "key": release.public_key_info()}
    return {"version": app_version(), "status": status, "log": log_tail, "pending": pending, "signing": signing,
            "backups": backups[:5], "force": os.path.exists(os.path.join(UPDATES, "force")),
            "updater_detected": status is not None, "platform": "windows" if os.name == "nt" else "docker",
            "installed": INSTALLED}


@app.post("/api/system/upload")
async def upload_update(request: Request, force: bool = False, filename: str = "tradingapp.zip"):
    """Przyjmuje paczke .zip z nowa wersja. Wdraza ja kontener 'updater' (NAS) albo aktualizuj.ps1 (Windows)."""
    if INSTALLED:
        raise HTTPException(400, "Tę wersję aktualizujesz nowszym instalatorem z GitHuba — dane i ustawienia zostają.")
    import io
    import zipfile
    body = await request.body()
    if len(body) > 30 * 1024 * 1024:
        raise HTTPException(400, "Plik jest za duzy (max 30 MB).")
    try:
        names = zipfile.ZipFile(io.BytesIO(body)).namelist()
    except zipfile.BadZipFile:
        raise HTTPException(400, "To nie jest plik .zip.")
    if "tradingapp/app/main.py" not in names:
        raise HTTPException(400, "To nie jest paczka TradingApp (brak tradingapp/app/main.py).")
    if any(n.startswith("/") or ".." in n.split("/") for n in names):
        raise HTTPException(400, "Paczka zawiera niedozwolone sciezki.")
    new_ver = "?"
    try:
        new_ver = zipfile.ZipFile(io.BytesIO(body)).read("tradingapp/VERSION").decode().strip()
    except KeyError:
        pass
    from . import release
    if os.environ.get("ALLOW_UNSIGNED_UPDATES", "").strip().lower() == "true":
        log.warning(f"Wgrano paczke BEZ sprawdzania podpisu (ALLOW_UNSIGNED_UPDATES=true), wersja {new_ver}")
    else:
        try:
            new_ver = release.verify(body)
        except release.ReleaseError as e:
            log.warning(f"Odrzucono paczke aktualizacji: {e}")
            raise HTTPException(400, str(e))
        except Exception as e:
            raise HTTPException(400, f"Nie udało się sprawdzić podpisu paczki: {e}")
    os.makedirs(UPDATES, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(filename))[:60] or "tradingapp.zip"
    path = os.path.join(UPDATES, datetime.now().strftime("%Y%m%d-%H%M%S_") + safe)
    with open(path, "wb") as f:
        f.write(body)
    if force:
        open(os.path.join(UPDATES, "force"), "w").close()
    now_open = market_open() and not force
    try:
        with open(os.path.join(config.DATA_DIR, "update_status.json"), "w", encoding="utf-8") as f:
            json.dump({"state": "waiting" if now_open else "queued", "step": "queued", "file": os.path.basename(path),
                       "message": ("Nowa wersja czeka na zamknięcie rynku (22:00 czasu PL)." if now_open else
                                   f"Wersja {new_ver} wgrana - aktualizator zaraz ją przejmie."),
                       "platform": "windows" if os.name == "nt" else "docker",
                       "started": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                       "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "updater": True}, f)
    except OSError:
        pass
    cur = app_version()
    downgrade = new_ver != cur and version_key(new_ver) <= version_key(cur)
    log.info(f"Wgrano paczke aktualizacji {os.path.basename(path)} (wersja {new_ver}, od razu: {force})"
             + (f" - POWROT do starszej wersji (teraz {cur})" if downgrade else ""))
    when = "od razu" if force or not market_open() else "po zamknięciu rynku (22:00)"
    return {"ok": True, "file": os.path.basename(path), "version": new_ver, "current": cur, "downgrade": downgrade,
            "next": (f"To starsza wersja niż obecna ({cur}) - aplikacja wróci do {new_ver}. " if downgrade else "")
            + f"Aktualizacja uruchomi się automatycznie {when}."}


def market_open(now=None):
    """Sesja w USA ok. 13:30-20:00 UTC w dni robocze (z zapasem 15 min) - jak w updaterze."""
    now = now or datetime.now(timezone.utc)
    hm = now.hour * 100 + now.minute
    return now.weekday() < 5 and 1315 <= hm < 2015


def windows_update_loop():
    """Na Windowsie nie ma kontenera 'updater' - aplikacja sama uruchamia aktualizuj.ps1."""
    import subprocess
    script = os.path.join(BASE, "aktualizuj.ps1")
    while True:
        try:
            pending = sorted(f for f in os.listdir(UPDATES) if f.endswith(".zip")) if os.path.isdir(UPDATES) else []
            force = os.path.exists(os.path.join(UPDATES, "force"))
            marker = os.path.join(UPDATES, ".launched")
            recent = os.path.exists(marker) and time.time() - os.path.getmtime(marker) < 15 * 60
            status_file = os.path.join(config.DATA_DIR, "update_status.json")
            if pending and os.path.exists(marker) and time.time() - os.path.getmtime(marker) > 180:
                # skrypt uruchomiony 3 min temu, a status nadal nie ruszyl -> powiedz o tym w panelu
                st = {}
                try:
                    st = json.load(open(status_file, encoding="utf-8"))
                except (OSError, ValueError):
                    pass
                if st.get("state") in (None, "queued", "idle") and not st.get("launch_warned"):
                    st.update(state="failed", step=st.get("step") or "queued", launch_warned=True,
                              message="Aktualizator (aktualizuj.ps1) nie wystartował w ciągu 3 minut - szczegóły w "
                                      "data\\update_launch.log. Kolejna próba za kilkanaście minut.",
                              updated=datetime.now(timezone.utc).isoformat(timespec="seconds"))
                    json.dump(st, open(status_file, "w", encoding="utf-8"))
                    log.warning("aktualizuj.ps1 nie zmienil statusu w 3 min - sprawdz data/update_launch.log")
            if pending and os.path.exists(script) and not recent and (force or not market_open()):
                open(marker, "w").close()
                args = ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                        "-File", script] + (["-Teraz"] if force else [])
                # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP: niewidoczna konsola, proces przezyje restart aplikacji.
                # (DETACHED_PROCESS + -WindowStyle Hidden potrafilo nie uruchomic skryptu wcale)
                flags = 0x08000000 | 0x00000200
                out = open(os.path.join(config.DATA_DIR, "update_launch.log"), "a", encoding="utf-8")
                out.write(f"\n=== {datetime.now():%Y-%m-%d %H:%M:%S} start aktualizuj.ps1 ({pending[0]})\n")
                out.flush()
                subprocess.Popen(args, cwd=BASE, creationflags=flags, close_fds=True, stdin=subprocess.DEVNULL,
                                 stdout=out, stderr=subprocess.STDOUT)
                out.close()
                log.info(f"Uruchomiono aktualizuj.ps1 dla {pending[0]} - aplikacja zaraz sie zrestartuje")
        except Exception:
            log.exception("Windows: uruchomienie aktualizacji nieudane")
        time.sleep(10)


@app.delete("/api/system/pending/{name}")
def cancel_update(name: str):
    path = os.path.join(UPDATES, os.path.basename(name))
    if os.path.exists(path):
        os.remove(path)
    return {"ok": True}


# ------------------------------------------------------------------ frontend
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index():
    """Strona z numerem wersji w adresach plikow - po aktualizacji przegladarka nie uzyje starego app.js z pamieci."""
    from fastapi.responses import HTMLResponse
    v = re.sub(r"[^0-9A-Za-z.-]", "", app_version() or "0")
    html = open(os.path.join(STATIC, "index.html"), encoding="utf-8").read()
    for name in ("app.js", "style.css", "chart.umd.min.js", "qrcode.js"):
        html = html.replace(f"/static/{name}\"", f"/static/{name}?v={v}\"")
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


def main():
    import uvicorn
    uvicorn.run(app, host=config.PANEL_HOST, port=config.PANEL_PORT, log_level="warning")


if __name__ == "__main__":
    main()
