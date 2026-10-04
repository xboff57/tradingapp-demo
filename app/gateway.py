"""
Kontrola bramki IB Gateway (zakładka „Bramka”).

Podział ról (aplikacja NIE ma dostępu do Dockera):
- aplikacja pilnuje połączenia (aplikacja ↔ bramka i bramka ↔ serwery IBKR), pokazuje stan i zapisuje
  polecenia do pliku data/gateway/cmd.json,
- kontener „updater” (ma już dostęp do Dockera) co kilkanaście sekund wykonuje polecenie (restart / stop / start
  wyłącznie kontenera bramki) i zapisuje stan kontenera oraz ostatnie linie jego dziennika (z zamaskowanymi numerami).

Automatyczny restart: gdy bramka nie ma połączenia z IBKR (albo aplikacja z bramką) dłużej niż ustalony czas.
Bez restartów w nocnym oknie bramki (23:45–00:15, sama się wtedy restartuje), najwyżej raz na 30 minut
i najwyżej 4 razy na 6 godzin — dalej tylko powiadomienie.
W weekend (pt 23:00 – pn 06:00) giełdy są zamknięte, a IBKR prowadzi prace na serwerach: wtedy bez restartów
i bez powiadomień (tylko wpis w historii). Jeśli w poniedziałek o 6:00 połączenia nadal nie ma — zwykły restart,
żeby bramka była gotowa przed sesją.
"""

import json
import logging
import os
import re
import secrets
import threading
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from . import config, notify
from .config import DATA_DIR

log = logging.getLogger("tradingapp")
DIR = os.path.join(DATA_DIR, "gateway")
CMD = os.path.join(DIR, "cmd.json")
RESULT = os.path.join(DIR, "result.json")
STATE = os.path.join(DIR, "state.json")
LOGS = os.path.join(DIR, "logs.txt")
SETTINGS = os.path.join(DIR, "settings.json")
EVENTS = os.path.join(DIR, "events.json")
ACTIONS = ("restart", "stop", "start")
DEFAULTS = {"auto_restart": True, "threshold_min": 15, "weekend_pause": True}
PL = ZoneInfo("Europe/Warsaw")
_lock = threading.Lock()
os.makedirs(DIR, exist_ok=True)


def _read(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _write(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)


_ACC = re.compile(r"\b[A-Za-z]{1,4}\d{5,}\b|\d{8,}")


def mask(text):
    """Numery kont IBKR (np. U1234567, DU…, DUR…) i dlugie liczby -> ***."""
    return _ACC.sub("***", text or "")


def settings():
    s = dict(DEFAULTS)
    s.update(_read(SETTINGS, {}) or {})
    return s


def save_settings(auto_restart, threshold_min, weekend_pause=True):
    t = int(threshold_min)
    if not 5 <= t <= 240:
        raise ValueError("Czas: od 5 do 240 minut.")
    _write(SETTINGS, {"auto_restart": bool(auto_restart), "threshold_min": t, "weekend_pause": bool(weekend_pause)})


def weekend(now=None):
    """Weekend giełdowy: od piątku 23:00 do poniedziałku 6:00 (czas polski)."""
    t = now or datetime.now(PL)
    wd, h = t.weekday(), t.hour
    return (wd == 4 and h >= 23) or wd in (5, 6) or (wd == 0 and h < 6)


def events(limit=30):
    return (_read(EVENTS, []) or [])[-limit:][::-1]


def event(kind, text):
    with _lock:
        ev = _read(EVENTS, []) or []
        ev.append({"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "kind": kind, "text": text})
        _write(EVENTS, ev[-200:])
    log.warning(f"Bramka IBKR: {text}")


def request(action, reason, auto=False):
    """Zapisuje polecenie dla updatera. Zwraca id polecenia."""
    if action not in ACTIONS:
        raise ValueError("Nieznane polecenie.")
    cur = _read(CMD)
    if cur and time.time() - cur.get("t", 0) < 120:
        raise ValueError("Poprzednie polecenie jeszcze czeka na wykonanie.")
    cid = secrets.token_hex(6)
    _write(CMD, {"id": cid, "action": action, "reason": reason[:200], "auto": auto, "t": time.time()})
    event("auto" if auto else "manual", f"{'Automatyczny ' if auto else ''}{dict(restart='restart', stop='zatrzymanie', start='uruchomienie')[action]} — {reason}")
    return cid


def ibkr_conns():
    from .ibkr import IBConn
    out = []
    for acc in config.ACCOUNTS.values():
        if acc.type == "ibkr":
            ex = acc.extra
            out.append((acc.name, IBConn.get(ex.get("host", "127.0.0.1"), int(ex.get("port", 4002)),
                                          int(ex.get("client_id", 7)))))
    return out


def state():
    """Wszystko dla zakładki Bramka."""
    st = _read(STATE) or {}
    age = time.time() - st.get("t", 0) if st else None
    conns = []
    for name, c in ibkr_conns():
        h = c.health()
        conns.append({"account": name, "host": c.host, "port": c.port, **h})
    try:
        with open(LOGS, encoding="utf-8", errors="replace") as f:
            logs = mask(f.read()[-20000:])
    except OSError:
        logs = ""
    s = settings()
    return {"container": st, "updater_ok": age is not None and age < 180, "updater_age": age,
            "weekend": bool(s.get("weekend_pause") and weekend()),
            "connections": conns, "settings": s, "events": events(),
            "pending": _read(CMD), "result": _read(RESULT), "logs": logs, "now": time.time()}


# ------------------------------------------------------------------ monitor (watek w aplikacji)
class Monitor:
    def __init__(self):
        self.alerted = False
        self.restarts = []            # czasy automatycznych restartow
        self.grace_until = 0
        self.weekend_noted = False

    def night(self):
        t = datetime.now(PL)
        hm = t.hour * 60 + t.minute
        return hm >= 23 * 60 + 45 or hm < 15

    def loop(self, stop):
        while not stop.wait(60):
            try:
                self.tick()
            except Exception:
                log.exception("Monitor bramki")

    def tick(self):
        conns = ibkr_conns()
        if not conns:
            return
        now = time.time()
        s = settings()
        limit = s["threshold_min"] * 60
        worst = None
        for name, c in conns:
            try:
                c.ping()                              # odswieza polaczenie (i stan api)
            except Exception:
                pass
            h = c.health()
            if not h["api"]:
                down = now - (h["api_since"] or now)
                why = f"aplikacja nie łączy się z bramką od {int(down // 60)} min"
            elif not h["server_ok"]:
                down = now - h["server_since"]
                why = f"bramka nie ma połączenia z serwerami IBKR od {int(down // 60)} min"
            else:
                continue
            if worst is None or down > worst[0]:
                worst = (down, why)
        if worst is None:
            if self.alerted:
                self.alerted = False
                event("ok", "Połączenie z IBKR wróciło.")
                notify.send("Bramka IBKR: połączenie wróciło.", "errors")
            return
        down, why = worst
        if s.get("weekend_pause", True) and weekend():
            if not self.weekend_noted:                    # weekend: giełdy zamknięte - bez restartów i bez powiadomień
                self.weekend_noted = True
                event("info", why[0].upper() + why[1:] + " — weekend, giełdy zamknięte: bez restartu i powiadomień "
                      "do poniedziałku 6:00.")
            return
        self.weekend_noted = False
        if down < limit or now < self.grace_until:
            return
        if not self.alerted:
            self.alerted = True
            event("alert", why[0].upper() + why[1:] + ".")
        self.restarts = [t for t in self.restarts if now - t < 6 * 3600]
        can = (s["auto_restart"] and not self.night() and len(self.restarts) < 4
               and (not self.restarts or now - self.restarts[-1] > 30 * 60) and (_read(STATE) or {}).get("t"))
        if can:
            try:
                request("restart", why, auto=True)
                self.restarts.append(now)
                self.grace_until = now + 10 * 60          # bramka loguje sie 1-3 min - dajemy jej czas
                notify.send(f"Bramka IBKR: {why} — restartuję ją automatycznie.", "errors")
            except ValueError:
                pass
        else:
            notify.send(f"Bramka IBKR: {why}. Automatyczny restart "
                        f"{'wyłączony' if not s['auto_restart'] else 'wstrzymany (limit albo noc)'} — sprawdź zakładkę Bramka.",
                        "errors", key="gw-down", cooldown=3600)


_monitor = {}


def start(stop_event):
    if "m" in _monitor:
        return
    _monitor["m"] = Monitor()
    threading.Thread(target=_monitor["m"].loop, args=(stop_event,), daemon=True, name="gateway-monitor").start()
