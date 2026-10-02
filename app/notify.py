"""
Powiadomienia na telefon: ntfy (aplikacja ntfy, bez konta - najprosciej) i/lub Telegram.

ntfy: panel tworzy losowy temat (dziala jak haslo), Ty subskrybujesz go w aplikacji ntfy na telefonie.
Wiadomosci ida przez publiczny serwer ntfy.sh (albo wlasny: NTFY_SERVER w .env).

Konfiguracja (jednorazowo):
  1. W Telegramie napisz do @BotFather: /newbot -> nazwa -> dostaniesz TOKEN.
  2. W .env dopisz:  TELEGRAM_BOT_TOKEN=123456:ABC...   i zrestartuj aplikacje.
  3. Napisz cokolwiek do swojego nowego bota w Telegramie, a potem w panelu: System -> Powiadomienia -> "Połącz".
     Aplikacja odczyta identyfikator Twojego czatu i zapisze go w data/telegram.json.

Wiadomosci wysyla osobny watek (kolejka), wiec wolny internet nie spowalnia botow. Te same zdarzenia
nie powtarzaja sie czesciej niz ustalony odstep (np. blad polaczenia raz na serie).
"""

import json
import os
import queue
import threading
import time
import urllib.parse
import urllib.request

from .config import DATA_DIR

PATH = os.path.join(DATA_DIR, "telegram.json")
EVENTS = {
    "errors": "Błędy i zatrzymania botów (oraz powrót do pracy)",
    "sells": "Zamknięte transakcje z wynikiem",
    "buys": "Nowe pozycje (kupno)",
    "reports": "Raport dzienny i tygodniowy",
    "lab": "Laboratorium i nowe modele ML",
    "system": "Start aplikacji, aktualizacje, restart przez strażnika",
    "signals": "Okazje do ręcznego kopiowania — kupno i sprzedaż z ceną, stop-lossem i kwotą dla Ciebie",
}
DEFAULT_EVENTS = {"errors": True, "sells": True, "buys": False, "reports": True, "lab": True, "system": True,
                  "signals": True}

_q = queue.Queue(maxsize=500)
_last = {}
_state = {"thread": None, "last_error": None, "sent": 0}


def token():
    return os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()


def ntfy_server():
    return (os.environ.get("NTFY_SERVER", "").strip() or "https://ntfy.sh").rstrip("/")


def settings():
    s = {"chat_id": os.environ.get("TELEGRAM_CHAT_ID", "").strip() or None, "events": dict(DEFAULT_EVENTS),
         "ntfy_topic": None}
    if os.path.exists(PATH):
        try:
            saved = json.load(open(PATH, encoding="utf-8"))
            s["chat_id"] = saved.get("chat_id") or s["chat_id"]
            s["ntfy_topic"] = saved.get("ntfy_topic")
            s["events"].update(saved.get("events") or {})
        except (OSError, ValueError):
            pass
    return s


def save(**kw):
    s = settings()
    s.update(kw)
    with open(PATH, "w", encoding="utf-8") as f:
        json.dump({"chat_id": s["chat_id"], "events": s["events"], "ntfy_topic": s["ntfy_topic"]}, f)
    return s


def telegram_on():
    return bool(token() and settings()["chat_id"])


def ntfy_on():
    return bool(settings()["ntfy_topic"])


def enabled():
    return telegram_on() or ntfy_on()


def status():
    s = settings()
    return {"token": bool(token()), "chat": bool(s["chat_id"]), "events": s["events"], "labels": EVENTS,
            "ntfy_topic": s["ntfy_topic"], "ntfy_server": ntfy_server(),
            "last_error": _state["last_error"], "sent": _state["sent"]}


def ntfy_enable():
    import secrets
    topic = "tradingapp-" + secrets.token_hex(10)
    save(ntfy_topic=topic)
    return topic


def ntfy_disable():
    save(ntfy_topic=None)


def _ntfy(msg, timeout=15):
    """Publikacja JSON (UTF-8 w tytule i tresci)."""
    s = settings()
    body = {"topic": s["ntfy_topic"], "message": msg["text"][:3800], "title": msg.get("title") or "TradingApp",
            "priority": msg.get("priority", 3)}
    if msg.get("tags"):
        body["tags"] = msg["tags"]
    req = urllib.request.Request(ntfy_server(), data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        r.read()


def _api(method, params=None, timeout=15):
    url = f"https://api.telegram.org/bot{token()}/{method}"
    data = urllib.parse.urlencode(params or {}).encode()
    with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=timeout) as r:
        return json.loads(r.read().decode())


def _deliver(fn):
    for attempt in range(3):
        try:
            fn()
            _state["sent"] += 1
            _state["last_error"] = None
            return
        except Exception as e:                      # brak internetu itp. - kilka prob, potem odpuszczamy
            _state["last_error"] = str(e)[:200]
            time.sleep(5 * (attempt + 1))


def _worker():
    while True:
        msg = _q.get()
        if telegram_on():
            text = (msg["title"] + "\n" + msg["text"]) if msg.get("title") else msg["text"]
            _deliver(lambda: _api("sendMessage", {"chat_id": settings()["chat_id"], "text": "TradingApp · " + text[:3900],
                                                   "disable_web_page_preview": "true"}))
        if ntfy_on():
            _deliver(lambda: _ntfy(msg))
        time.sleep(1.1)                              # limit Telegrama: ok. 1 wiadomosc / s na czat


def send(text, event="system", key=None, cooldown=0, title=None, tags=None, priority=3):
    """Wysyla w tle. key + cooldown (s): ta sama sprawa nie czesciej niz raz na cooldown."""
    if not enabled() or not settings()["events"].get(event, True):
        return False
    if key:
        now = time.time()
        if now - _last.get(key, 0) < cooldown:
            return False
        _last[key] = now
    if _state["thread"] is None or not _state["thread"].is_alive():
        _state["thread"] = threading.Thread(target=_worker, daemon=True, name="telegram")
        _state["thread"].start()
    try:
        _q.put_nowait({"text": text, "title": title, "tags": tags, "priority": priority})
        return True
    except queue.Full:
        return False


def detect_chat():
    """Czyta ostatnie wiadomosci do bota i zapisuje czat, z ktorego przyszla ostatnia."""
    if not token():
        raise ValueError("Brak TELEGRAM_BOT_TOKEN w .env (i restartu aplikacji po jego dopisaniu).")
    res = _api("getUpdates", {"timeout": 0})
    chats = [u.get("message", {}).get("chat", {}) for u in res.get("result", []) if u.get("message")]
    if not chats:
        raise ValueError("Bot nie dostał jeszcze żadnej wiadomości. Napisz do niego cokolwiek w Telegramie i spróbuj ponownie.")
    chat = chats[-1]
    save(chat_id=str(chat["id"]))
    return chat.get("first_name") or chat.get("title") or str(chat["id"])


def send_now(text):
    """Test z panelu - wysylka od razu, z bledem, jesli sie nie uda."""
    if not enabled():
        raise ValueError("Powiadomienia nie są połączone.")
    if telegram_on():
        _api("sendMessage", {"chat_id": settings()["chat_id"], "text": "TradingApp · " + text})
    if ntfy_on():
        _ntfy({"text": text, "title": "TradingApp", "tags": ["bell"]})
