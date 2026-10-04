"""
Strażnik z zewnątrz (healthchecks.io albo zgodny serwis).

Panel co 5 minut wysyła „znak życia” na Twój adres pingu. Gdy znaki przestaną przychodzić (NAS padł, brak prądu,
brak internetu, aplikacja się zawiesiła), to serwis z zewnątrz wyśle Ci maila / SMS / powiadomienie — NAS
nie musi do tego działać.

Gdy aplikacja działa, ale coś jest nie tak (bot ma błąd od kilku cykli, bramka IBKR bez połączenia poza weekendem),
wysyłamy ping „/fail” z opisem — healthchecks.io też to zgłosi.
Adres pingu działa jak hasło (kto go zna, może udawać znak życia) — trzymany w data/heartbeat.json.
"""

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

from .config import DATA_DIR, DEMO

log = logging.getLogger("tradingapp")
PATH = os.path.join(DATA_DIR, "heartbeat.json")
EVERY = 300
_state = {"last": None, "ok": None, "error": None, "problems": []}


def settings():
    try:
        with open(PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"url": ""}


def set_url(url):
    url = (url or "").strip()
    if url:
        u = urlparse(url)
        if u.scheme != "https" or not u.netloc or len(url) > 300:
            raise ValueError("Podaj pełny adres https, np. https://hc-ping.com/xxxxxxxx-xxxx-…")
    tmp = PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"url": url}, f)
    os.replace(tmp, PATH)


def public():
    url = settings().get("url", "")
    masked = (url[:url.rfind("/") + 1] + "…" + url[-4:]) if url else ""
    return {"enabled": bool(url), "url_masked": masked, "every_min": EVERY // 60, **_state}


def problems(db):
    """Co jest nie tak (pusto = wszystko dobrze)."""
    out = []
    from . import gateway
    weekend = gateway.settings().get("weekend_pause", True) and gateway.weekend()
    from .config import ACCOUNTS
    for b in db.bots():
        if b["status"] == "running" and b.get("last_error"):
            acc = ACCOUNTS.get(b["account"])
            if weekend and acc and acc.type == "ibkr":
                continue
            out.append(f"bot {b['name']}: {str(b['last_error'])[:120]}")
    if not weekend:
        try:
            for name, c in gateway.ibkr_conns():
                h = c.health()
                if not h["api"] or not h["server_ok"]:
                    since = h.get("api_since") if not h["api"] else h.get("server_since")
                    if since and time.time() - since > 15 * 60:
                        out.append(f"bramka IBKR ({name}) bez połączenia od {int((time.time() - since) // 60)} min")
        except Exception:
            pass
    return out


def ping(db, test=False):
    import requests
    url = settings().get("url", "")
    if not url:
        raise ValueError("Najpierw wpisz adres pingu.")
    probs = [] if test else problems(db)
    body = ("Test z panelu TradingApp." if test else
            ("Problemy:\n- " + "\n- ".join(probs)) if probs else "TradingApp działa.")
    r = requests.post(url.rstrip("/") + ("/fail" if probs else ""), data=body.encode("utf-8"), timeout=15,
                      headers={"User-Agent": "TradingApp"})
    _state.update(last=datetime.now(timezone.utc).isoformat(timespec="seconds"), ok=r.status_code < 300,
                  error=None if r.status_code < 300 else f"HTTP {r.status_code}", problems=probs)
    if r.status_code >= 300:
        raise ValueError(f"Serwis odpowiedział HTTP {r.status_code} — sprawdź adres.")
    return _state


def loop(db, stop):
    while not stop.is_set():
        if settings().get("url"):
            try:
                ping(db)
            except Exception as e:
                _state.update(last=datetime.now(timezone.utc).isoformat(timespec="seconds"), ok=False,
                              error=str(e)[:200])
                log.info(f"Strażnik z zewnątrz: {e}")
        stop.wait(EVERY)


_started = {}


def start(db, stop):
    if "t" in _started or DEMO:
        return
    _started["t"] = threading.Thread(target=loop, args=(db, stop), daemon=True, name="heartbeat")
    _started["t"].start()
