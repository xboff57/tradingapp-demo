"""
Odbiornik alertów z TradingView — OSOBNY, minimalny serwer (port 8421), żeby można go było wystawić do internetu
(np. Tailscale Funnel) bez wystawiania panelu.

Ten serwer umie tylko jedno: przyjąć POST /tv/<token> i zapisać alert w kolejce bota. Nie ma tu logowania,
odczytu danych ani sterowania — token (losowy, 32 znaki) wskazuje bota, opcjonalne hasło w treści alertu
to druga warstwa. Limit: 2 KB treści, 60 alertów na minutę na bota.

Treść alertu (dowolna z dwóch):
  JSON:  {"symbol": "{{ticker}}", "action": "buy", "price": {{close}}, "passphrase": "..."}
         action: buy | sell | short | cover | close   (w alertach strategii: "{{strategy.order.action}}")
  tekst: buy NVDA   /   sell BTCUSD
"""

import hmac
import json
import logging
import os
import re
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from .data import norm

log = logging.getLogger("tradingapp")
PORT = int(os.environ.get("TV_WEBHOOK_PORT", "8421"))
ACTIONS = {"buy": "buy", "long": "buy", "sell": "sell", "exit": "sell", "short": "short", "cover": "cover",
           "close": "close", "flat": "close", "close_long": "sell", "close_short": "cover"}
_hits = defaultdict(deque)
_db = {}

hook = FastAPI(title="TradingApp webhook", docs_url=None, redoc_url=None, openapi_url=None)


def new_token():
    return secrets.token_hex(16)


def match_symbol(tick, symbols):
    """TradingView: 'NASDAQ:NVDA', 'BTCUSD', 'KRAKEN:XBTEUR', 'GPW:PKO' -> symbol bota ('NVDA', 'BTC/USD', 'PKO.WSE')."""
    t = (tick or "").upper().split(":")[-1].strip().replace(".P", "")
    t = t.replace("XBT", "BTC")
    for s in symbols:
        if norm(s) == norm(t) or s.upper() == t or s.split(".")[0].upper() == t:
            return s
    return None


def parse(body):
    try:
        d = json.loads(body)
        if isinstance(d, dict):
            return {k.lower(): v for k, v in d.items()}
    except ValueError:
        pass
    parts = body.strip().split()
    out = {}
    for x in parts:
        if x.lower() in ACTIONS and "action" not in out:
            out["action"] = x.lower()
        elif "symbol" not in out and re.fullmatch(r"[A-Za-z0-9:./\-]{1,30}", x):
            out["symbol"] = x
    return out


@hook.get("/tv/health")
def health():
    return PlainTextResponse("ok")


@hook.post("/tv/{token}")
async def receive(token: str, request: Request):
    db = _db.get("db")
    body = (await request.body())[:2048].decode("utf-8", "replace")
    if not db or not re.fullmatch(r"[0-9a-f]{32}", token or ""):
        return JSONResponse({"ok": False}, status_code=404)
    bot = None
    for b in db.bots():
        t = (b["params"] or {}).get("tv_token") or ""
        if b["strategy"] == "tv_alerts" and t and hmac.compare_digest(t, token):
            bot = b
            break
    if not bot:
        return JSONResponse({"ok": False}, status_code=404)
    q = _hits[bot["id"]]
    now = time.time()
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= 60:
        return JSONResponse({"ok": False, "error": "za dużo alertów"}, status_code=429)
    q.append(now)
    d = parse(body)
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")

    def reject(why, code=400):
        db.execute("INSERT INTO tv_alerts(bot_id, ts, symbol, action, price, raw, status, note) VALUES (?,?,?,?,?,?,?,?)",
                   (bot["id"], ts, str(d.get("symbol") or "?")[:30], str(d.get("action") or "?")[:20], None,
                    body[:500], "rejected", why))
        return JSONResponse({"ok": False, "error": why}, status_code=code)

    pw = (bot["params"] or {}).get("tv_passphrase") or ""
    if pw and not hmac.compare_digest(str(d.get("passphrase") or ""), pw):
        return reject("złe hasło w treści alertu", 403)
    act = ACTIONS.get(str(d.get("action") or "").lower().strip())
    if not act:
        return reject("nieznana akcja (użyj buy / sell / short / cover / close)")
    sym = match_symbol(str(d.get("symbol") or ""), (bot["params"] or {}).get("symbols") or [])
    if not sym:
        return reject("symbolu nie ma na liście bota")
    try:
        price = float(d.get("price")) if d.get("price") not in (None, "") else None
    except (TypeError, ValueError):
        price = None
    db.execute("INSERT INTO tv_alerts(bot_id, ts, symbol, action, price, raw, status) VALUES (?,?,?,?,?,?,?)",
               (bot["id"], ts, sym, act, price, body[:500], "new"))
    db.execute("DELETE FROM tv_alerts WHERE bot_id=? AND id NOT IN (SELECT id FROM tv_alerts WHERE bot_id=? "
               "ORDER BY id DESC LIMIT 500)", (bot["id"], bot["id"]))
    log.info(f"[{bot['name']}] Alert TradingView: {act} {sym}" + (f" @ {price}" if price else ""))
    return {"ok": True}


def start(db):
    if "t" in _db:
        return
    _db["db"] = db
    import uvicorn
    server = uvicorn.Server(uvicorn.Config(hook, host=os.environ.get("TV_WEBHOOK_HOST", "0.0.0.0"), port=PORT,
                                           log_level="warning", access_log=False))
    server.install_signal_handlers = lambda: None
    t = threading.Thread(target=server.run, daemon=True, name="tv-webhook")
    t.start()
    _db["t"] = t
    log.info(f"Odbiornik alertów TradingView: port {PORT} (tylko POST /tv/<token>)")
