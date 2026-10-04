"""
Kalendarz spółek: najbliższy raport okresowy i dywidenda, plus historia raportów (znaczniki na wykresie).

Źródła:
- przyszłe daty raportów i dywidend: Yahoo Finance (yfinance, „calendar”) — USA, GPW i inne giełdy,
- historia raportów USA: SEC EDGAR (daty złożenia 10-Q / 10-K), pozostałe: Yahoo („earnings dates”),
- historia dywidend: Yahoo (dni odcięcia).

Do czego:
- blokada wejść: bot z ustawieniem „Bez zakupów przed raportem (dni)” nie otwiera pozycji, gdy raport wypada
  w ciągu tylu dni (cena potrafi wtedy skoczyć o kilkanaście procent w dowolną stronę),
- zakładka Kalendarz: nadchodzące raporty i dywidendy spółek botów,
- powiadomienie dzień wcześniej o raporcie / odcięciu dywidendy spółki, którą trzyma bot,
- znaczniki R (raport) i D (dywidenda) na wykresie.

Dane trzymamy w data/cache/calendar.json (odświeżane co ~20 h w tle). Silnik czyta tylko pamięć podręczną —
nigdy nie czeka na internet.
"""

import json
import logging
import os
import threading
import time
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .config import CACHE_DIR, DEMO

log = logging.getLogger("tradingapp")
PATH = os.path.join(CACHE_DIR, "calendar.json")
PL = ZoneInfo("Europe/Warsaw")
STALE = 20 * 3600
_lock = threading.Lock()
_pending = set()
_data = None


def _load():
    global _data
    if _data is None:
        try:
            with open(PATH, encoding="utf-8") as f:
                _data = json.load(f)
        except (OSError, ValueError):
            _data = {}
    return _data


def _save():
    tmp = PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(_data, f, ensure_ascii=False)
    os.replace(tmp, PATH)


def applies(symbol):
    """Kalendarz ma sens dla akcji (nie krypto, nie pary walutowe)."""
    from .fundamentals import YAHOO_SUFFIX
    s = symbol.upper()
    if "/" in s:
        return False
    if "." in s:
        base, ex = s.rsplit(".", 1)
        if len(base) == 3 and len(ex) == 3 and ex not in YAHOO_SUFFIX:
            return False                           # EUR.USD
    return True


def _d(v):
    if v is None:
        return None
    if isinstance(v, (list, tuple)):
        v = v[0] if v else None
        if v is None:
            return None
    if hasattr(v, "date") and callable(v.date):
        v = v.date()
    return v.isoformat() if isinstance(v, date) else str(v)[:10]


def fetch(symbol):
    """Pobiera dane z internetu (wolne - do 2-5 s). Zwraca wpis kalendarza."""
    import yfinance as yf
    from .fundamentals import us_symbol, yahoo_symbol
    t = yf.Ticker(yahoo_symbol(symbol))
    e = {"t": time.time(), "earnings": None, "earnings_end": None, "ex_div": None, "div_pay": None,
         "past_reports": [], "past_ex_div": [], "src": "Yahoo Finance"}
    try:
        cal = t.get_calendar() or {}
        ed = cal.get("Earnings Date") or []
        if not isinstance(ed, (list, tuple)):
            ed = [ed]
        ed = [_d(x) for x in ed if x is not None]
        e["earnings"] = ed[0] if ed else None
        e["earnings_end"] = ed[-1] if len(ed) > 1 else None
        e["ex_div"] = _d(cal.get("Ex-Dividend Date"))
        e["div_pay"] = _d(cal.get("Dividend Date"))
    except Exception as ex:
        log.info(f"Kalendarz {symbol}: {ex}")
    try:
        dv = t.dividends
        if dv is not None and len(dv):
            e["past_ex_div"] = [[_d(i), float(v)] for i, v in dv.tail(12).items()]
    except Exception as ex:
        log.info(f"Kalendarz dywidendy {symbol}: {ex}")
    past = []
    if us_symbol(symbol):
        try:
            from .signals import SEC_USER_AGENT, SecClient
            if SEC_USER_AGENT:
                sec = SecClient()
                tick = sec.tickers().get(symbol.upper().replace(".", "-"))
                if tick:
                    since = (date.today() - timedelta(days=3 * 365)).isoformat()
                    past = sorted({f["filing_date"] for f in sec.filings(tick["cik"], {"10-Q", "10-K"}, since)})
                    e["past_src"] = "SEC EDGAR (daty złożenia 10-Q / 10-K)"
        except Exception as ex:
            log.info(f"Kalendarz SEC {symbol}: {ex}")
    if not past:
        try:
            ed = t.get_earnings_dates(limit=16)
            if ed is not None and len(ed):
                today = date.today().isoformat()
                past = sorted({_d(i) for i in ed.index if _d(i) and _d(i) <= today})
        except Exception as ex:
            log.info(f"Kalendarz historia {symbol}: {ex}")
    e["past_reports"] = past[-16:]
    today = date.today().isoformat()
    if e["earnings"] and e["earnings"] < today and (e["earnings_end"] or "") < today:
        e["earnings"] = e["earnings_end"] = None       # Yahoo czasem trzyma datę już minionego raportu
    if e["ex_div"] and e["ex_div"] < today:
        e["ex_div"] = None
    return e


def get(symbol, fetch_missing=True, refresh=False):
    """Wpis kalendarza dla symbolu. fetch_missing=False: tylko pamięć (brak -> kolejka do pobrania w tle)."""
    symbol = symbol.upper()
    if DEMO or not applies(symbol):
        return None
    with _lock:
        e = _load().get(symbol)
    fresh = e and time.time() - e.get("t", 0) < STALE
    if fresh and not refresh:
        return e
    if not fetch_missing:
        _pending.add(symbol)
        return e                                   # stary wpis lepszy niż żaden
    try:
        e = fetch(symbol)
    except Exception as ex:
        log.warning(f"Kalendarz {symbol}: {ex}")
        return e
    with _lock:
        _load()[symbol] = e
        _save()
    return e


def next_report(symbol):
    e = get(symbol, fetch_missing=False)
    return (e or {}).get("earnings")


def blackout(symbol, days):
    """(True, data) gdy raport wypada dziś albo w ciągu `days` dni kalendarzowych."""
    if not days or not applies(symbol):
        return False, None
    d = next_report(symbol)
    if not d:
        return False, None
    left = (date.fromisoformat(d) - datetime.now(PL).date()).days
    return 0 <= left <= int(days), d


def upcoming(symbols, days=30):
    """Wydarzenia w najbliższych dniach dla podanych symboli (tylko pamięć podręczna)."""
    today = datetime.now(PL).date()
    end = today + timedelta(days=days)
    out = []
    for s in symbols:
        e = get(s, fetch_missing=False)
        if not e:
            continue
        for kind, key in (("report", "earnings"), ("ex_div", "ex_div"), ("div_pay", "div_pay")):
            v = e.get(key)
            if v and today.isoformat() <= v <= end.isoformat():
                out.append({"symbol": s, "kind": kind, "date": v,
                            "date_end": e.get("earnings_end") if kind == "report" else None,
                            "days": (date.fromisoformat(v) - today).days})
    return sorted(out, key=lambda x: (x["date"], x["symbol"]))


def status(symbols):
    with _lock:
        d = _load()
        have = [s for s in symbols if s in d]
        last = max((d[s].get("t", 0) for s in have), default=0)
    return {"symbols": len(symbols), "have": len(have), "pending": len(_pending),
            "updated": datetime.fromtimestamp(last, timezone.utc).isoformat(timespec="seconds") if last else None}


# ------------------------------------------------------------------ tło: odświeżanie i powiadomienia
def watched(db):
    """Symbole do śledzenia: akcje ze wszystkich botów + pozycje."""
    syms = []
    for b in db.bots():
        if b["market"] != "stocks":
            continue
        syms += (b["params"] or {}).get("symbols", [])
        syms += [st["symbol"] for st in db.pos_states(b["id"]).values()]
    return [s for s in dict.fromkeys(x.upper() for x in syms) if applies(s)]


def held(db):
    """{symbol: [nazwy botów]} dla otwartych pozycji w akcjach."""
    out = {}
    for b in db.bots():
        if b["market"] == "stocks":
            for st in db.pos_states(b["id"]).values():
                out.setdefault(st["symbol"].upper(), []).append(b["name"])
    return out


def loop(db, stop):
    from . import notify
    notified = set()
    while not stop.wait(60):
        if DEMO:
            return
        try:
            syms = watched(db)
            with _lock:
                d = _load()
                todo = [s for s in list(_pending) + syms if time.time() - (d.get(s) or {}).get("t", 0) >= STALE]
            for s in list(dict.fromkeys(todo))[:40]:          # najwyżej 40 na kwadrans, spokojnie dla Yahoo
                if stop.is_set():
                    return
                get(s, refresh=True)
                _pending.discard(s)
                stop.wait(2)
            # powiadomienie: jutro raport / odcięcie dywidendy spółki, którą trzyma bot (raz dziennie po 17:00)
            now = datetime.now(PL)
            if now.hour >= 17:
                tomorrow = (now.date() + timedelta(days=1)).isoformat()
                for s, bots in held(db).items():
                    e = get(s, fetch_missing=False) or {}
                    for key, what in (("earnings", "raport okresowy"), ("ex_div", "dzień odcięcia dywidendy")):
                        if e.get(key) == tomorrow and (s, key, tomorrow) not in notified:
                            notified.add((s, key, tomorrow))
                            notify.send(f"📅 Jutro {what}: {s} (trzyma: {', '.join(bots)}). "
                                        + ("Cena może mocno się ruszyć — stop-loss jest u brokera."
                                           if key == "earnings" else "Kurs spadnie o wartość dywidendy — to nie strata."),
                                        "calendar", title=f"Kalendarz: {s}", tags=["calendar"])
        except Exception:
            log.exception("Kalendarz w tle")
        stop.wait(15 * 60)


_started = {}


def start(db, stop):
    if "t" in _started or DEMO:
        return
    _started["t"] = threading.Thread(target=loop, args=(db, stop), daemon=True, name="calendar")
    _started["t"].start()
