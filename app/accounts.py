"""
Konta brokerskie dodawane z panelu (zakładka „Konta”) — obok kont z pliku .env.

- Klucze API są w bazie ZASZYFROWANE (Fernet/AES). Klucz szyfrujący:
  zmienna ACCOUNTS_MASTER_KEY w .env, a gdy jej nie ma — plik data/.accounts_key (tworzony raz, uprawnienia 600).
  Kopia zapasowa bazy bez tego klucza nie odszyfruje kluczy API (i dobrze).
- Panel nigdy nie odsyła kluczy: pokazuje tylko, czy są ustawione, i końcówkę identyfikatora.
- Dodanie, zmiana i usunięcie konta wymagają ponownego podania hasła do panelu.
- Konta z .env są tylko do odczytu w panelu (zmieniasz je w pliku).

Nowa platforma = wpis w PLATFORMS + klasa brokera w brokers.BROKER_TYPES.
Gieldy krypto z biblioteki CCXT dzialaja bez nowego kodu: platforma "gielda" + extra["exchange"].
"""

import json
import os
import re
import threading
from datetime import datetime, timezone

from . import config
from .config import AccountConfig, DATA_DIR

NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,19}$")
KEY_PATH = os.path.join(DATA_DIR, ".accounts_key")
_lock = threading.Lock()
_db = None
_fernet = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS broker_accounts (
    name        TEXT PRIMARY KEY,
    type        TEXT NOT NULL,
    paper       INTEGER NOT NULL DEFAULT 1,
    key_enc     TEXT,
    secret_enc  TEXT,
    extra       TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT,
    updated_at  TEXT
);
"""

# ------------------------------------------------------------------ katalog platform
# Pole "mode": tryb konta (na niby / sieć testowa / prawdziwe). Pola z "when" są pokazywane tylko w tych trybach.
# "hint" = krótka podpowiedź pod polem. "steps" = jak zdobyć klucz (lista rozwijana w panelu).
MODE_LABELS = {
    "paper": ("Na niby", "Wirtualne pieniądze, prawdziwe ceny. Bez ryzyka."),
    "testnet": ("Sieć testowa", "Konto testowe giełdy z osobnymi kluczami."),
    "live": ("Prawdziwe pieniądze", "Bot składa prawdziwe zlecenia na Twoim koncie."),
}
POPULAR_EXCHANGES = ["binance", "bybit", "okx", "coinbase", "kucoin", "bitget", "gate", "bitstamp", "bitfinex",
                     "mexc", "htx", "cryptocom", "bitvavo", "gemini"]
EXCHANGE_LABELS = {
    "binance": "Binance", "bybit": "Bybit", "okx": "OKX", "coinbase": "Coinbase", "kucoin": "KuCoin",
    "bitget": "Bitget", "gate": "Gate", "bitstamp": "Bitstamp", "bitfinex": "Bitfinex", "mexc": "MEXC", "htx": "HTX",
    "cryptocom": "Crypto.com", "bitvavo": "Bitvavo", "gemini": "Gemini"}

PLATFORMS = [
    {"type": "alpaca", "label": "Alpaca", "short": "Akcje i ETF-y z USA, krypto w USD", "tags": ["Akcje USA", "Krypto"],
     "available": True,
     "fields": [
         {"key": "mode", "label": "Tryb konta", "type": "mode", "options": ["paper", "live"], "default": "paper"},
         {"key": "key", "label": "API Key ID", "type": "secret", "required": True},
         {"key": "secret", "label": "Secret Key", "type": "secret", "required": True},
     ],
     "steps": ["Zaloguj się na app.alpaca.markets.",
               "Wybierz konto Paper albo Live (lewy górny róg) i otwórz API Keys → Generate New Keys.",
               "Skopiuj oba klucze — Secret Key Alpaca pokazuje tylko raz.",
               "Klucze konta papierowego i prawdziwego są różne: wybierz powyżej właściwy tryb."]},
    {"type": "kraken", "label": "Kraken", "short": "Krypto w EUR lub USD, radar altcoinów", "tags": ["Krypto"],
     "available": True,
     "fields": [
         {"key": "mode", "label": "Tryb konta", "type": "mode", "options": ["paper", "live"], "default": "paper"},
         {"key": "quote", "label": "Waluta konta", "type": "choice", "options": ["EUR", "USD"], "default": "EUR"},
         {"key": "start", "label": "Kapitał na start", "type": "float", "default": 10000, "when": ["paper"],
          "hint": "Wirtualne pieniądze bota."},
         {"key": "key", "label": "API Key", "type": "secret", "when": ["live"], "required": True},
         {"key": "secret", "label": "Private Key", "type": "secret", "when": ["live"], "required": True},
     ],
     "steps": ["Kraken → ikona profilu → Settings → API → Create API key.",
               "Zaznacz: Query Funds, Query Open Orders & Trades, Query Closed Orders & Trades, "
               "Create & Modify Orders, Cancel/Close Orders.",
               "Nie zaznaczaj Withdraw Funds ani Deposit."],
     "warn": "Klucz twórz bez prawa do wypłat."},
    {"type": "ibkr", "label": "Interactive Brokers", "short": "GPW, Xetra, USA i waluty przez IB Gateway",
     "tags": ["GPW", "Akcje świat", "Waluty"], "available": True,
     "fields": [
         {"key": "mode", "label": "Tryb konta", "type": "mode", "options": ["paper", "live"], "default": "paper"},
         {"key": "host", "label": "Adres IB Gateway", "type": "text", "default": "127.0.0.1",
          "hint": "Na NAS-ie: 127.0.0.1"},
         {"key": "port", "label": "Port", "type": "int", "default": 4002, "hint": "4002 = papier, 4001 = prawdziwe"},
         {"key": "client_id", "label": "Client ID", "type": "int", "default": 7, "hint": "Dowolna liczba, inna dla każdego konta"},
     ],
     "steps": ["IBKR nie wydaje kluczy API — aplikacja łączy się z IB Gateway (osobny kontener na NAS-ie).",
               "Login i hasło IBKR są tylko w pliku .env bramki, nigdy w aplikacji.",
               "Symbole: PKN.WSE (GPW), SAP.DE (Xetra), AAPL (USA), EUR.USD (waluty). Jeden bot = jeden rynek."]},
    {"type": "gielda", "label": "Inna giełda krypto", "short": "Binance, Bybit, OKX, Coinbase i ok. 100 innych",
     "tags": ["Krypto"], "available": True,
     "fields": [
         {"key": "exchange", "label": "Giełda", "type": "choice", "options": POPULAR_EXCHANGES, "labels": EXCHANGE_LABELS,
          "default": "binance", "other": "exchange_other"},
         {"key": "exchange_other", "label": "Identyfikator innej giełdy", "type": "text", "required": False,
          "hint": "Nazwa z biblioteki CCXT, np. bitvavo. Puste = giełda z listy.", "advanced": True},
         {"key": "mode", "label": "Tryb konta", "type": "mode", "options": ["paper", "testnet", "live"], "default": "paper"},
         {"key": "quote", "label": "Waluta konta", "type": "text", "default": "USDT",
          "hint": "USDT, USDC, EUR, USD albo PLN — pary bota w tej walucie, np. BTC/USDT."},
         {"key": "start", "label": "Kapitał na start", "type": "float", "default": 10000, "when": ["paper"],
          "hint": "Wirtualne pieniądze bota."},
         {"key": "key", "label": "API Key", "type": "secret", "when": ["testnet", "live"], "required": True},
         {"key": "secret", "label": "Secret", "type": "secret", "when": ["testnet", "live"], "required": True},
         {"key": "password", "label": "Passphrase", "type": "secret", "when": ["testnet", "live"],
          "hint": "Tylko OKX, KuCoin i Bitget."},
     ],
     "steps": ["W ustawieniach giełdy utwórz klucz API z prawem do odczytu i handlu spot.",
               "Nie włączaj wypłat (Withdraw) ani transferów. Jeśli giełda pozwala, ogranicz klucz do adresu IP NAS-a.",
               "Sieć testowa ma osobne konto i osobne klucze (np. testnet.binance.vision, testnet.bybit.com).",
               "Gdy giełda nie przyjmie zlecenia stop, stop-loss pilnuje bot przy każdym cyklu."],
     "warn": "Klucz twórz bez prawa do wypłat."},
    {"type": "sim", "label": "Symulator", "short": "Wymyślone dane do nauki panelu", "tags": ["Bez internetu"],
     "available": True,
     "fields": [{"key": "sim_start", "label": "Kapitał na start", "type": "float", "default": 100000}],
     "steps": ["Konto na niby z danymi syntetycznymi. Nie łączy się z żadną giełdą."]},
    {"type": "xtb", "label": "XTB", "short": "Brak publicznego API od 2025 r.", "tags": [], "available": False,
     "fields": [], "steps": ["Akcje z GPW są dostępne przez Interactive Brokers."]},
]
PLATFORM = {p["type"]: p for p in PLATFORMS}
EXTRA_SECRETS = ("password",)          # dodatkowe pola tajne - w bazie zaszyfrowane jak klucze


# ------------------------------------------------------------------ szyfrowanie
def _master_key():
    k = os.environ.get("ACCOUNTS_MASTER_KEY", "").strip()
    if k:
        return k.encode()
    from cryptography.fernet import Fernet
    if not os.path.exists(KEY_PATH):
        key = Fernet.generate_key()
        fd = os.open(KEY_PATH, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(key)
    with open(KEY_PATH, "rb") as f:
        return f.read().strip()


def _f():
    global _fernet
    if _fernet is None:
        from cryptography.fernet import Fernet
        _fernet = Fernet(_master_key())
    return _fernet


def _enc(s):
    return _f().encrypt(s.encode()).decode() if s else None


def _dec(s):
    if not s:
        return ""
    from cryptography.fernet import InvalidToken
    try:
        return _f().decrypt(s.encode()).decode()
    except InvalidToken:
        raise ValueError("Nie mogę odszyfrować kluczy — inny klucz szyfrujący (data/.accounts_key "
                         "albo ACCOUNTS_MASTER_KEY) niż przy zapisie.")


# ------------------------------------------------------------------ baza i rejestr kont
def init(db, log=lambda m: None):
    """Wywolywane raz przy starcie: tworzy tabele i dokleja konta z bazy do config.ACCOUNTS."""
    global _db
    _db = db
    with db.lock:
        db.conn.executescript(SCHEMA)
    for r in db.all("SELECT * FROM broker_accounts ORDER BY name"):
        if r["name"] in config.ACCOUNTS:
            log(f"Konto '{r['name']}' z panelu pominięte — konto o tej nazwie jest w .env")
            continue
        try:
            config.ACCOUNTS[r["name"]] = _to_config(r)
        except Exception as e:
            log(f"Konto '{r['name']}' z panelu nie wczytane: {e}")


def _to_config(r):
    extra = json.loads(r["extra"] or "{}")
    for k in EXTRA_SECRETS:
        enc = extra.pop(k + "_enc", None)
        if enc:
            extra[k] = _dec(enc)
    if r["type"] == "kraken":
        extra.setdefault("quote", "EUR")
    extra.setdefault("start", 100000.0 if r["type"] == "sim" else 10000.0)
    extra.setdefault("quote", "USD")
    return AccountConfig(name=r["name"], type=r["type"], key=_dec(r["key_enc"]), secret=_dec(r["secret_enc"]),
                         paper=bool(r["paper"]), extra=extra)


def _extra_json(extra):
    """extra do bazy: pola tajne (np. passphrase) zaszyfrowane, nigdy jawnie."""
    out = dict(extra)
    for k in EXTRA_SECRETS:
        v = out.pop(k, None)
        if v:
            out[k + "_enc"] = _enc(v)
    return json.dumps(out)


def source(name):
    if _db and _db.one("SELECT name FROM broker_accounts WHERE name=?", (name,)):
        return "panel"
    return "env"


def _mask(v):
    return ("…" + v[-4:]) if v and len(v) > 8 else ("ustawiony" if v else "")


def listing(bots):
    out = []
    for name, acc in config.ACCOUNTS.items():
        p = PLATFORM.get(acc.type, {})
        label = p.get("label", acc.type)
        if acc.type == "gielda":
            from .kraken import exchange_name
            ex = acc.extra.get("exchange", "")
            label = EXCHANGE_LABELS.get(ex) or exchange_name(ex)
        mode = "paper" if acc.paper else ("testnet" if acc.extra.get("sandbox") else "live")
        out.append({"name": name, "type": acc.type, "platform": label, "paper": acc.paper, "mode": mode,
                    "currency": acc.extra.get("quote", "USD"), "source": source(name),
                    "key": _mask(acc.key), "secret": bool(acc.secret),
                    "extra": {k: v for k, v in acc.extra.items()
                              if k in ("quote", "start", "sim_start", "host", "port", "client_id", "exchange", "sandbox")},
                    "password": bool(acc.extra.get("password")),
                    "bots": [b["name"] for b in bots if b["account"] == name]})
    return out


def _mode(p, values, existing):
    """Tryb konta z formularza (pole 'mode') albo ze starych pól paper/sandbox."""
    opts = next((f["options"] for f in p["fields"] if f["type"] == "mode"), None)
    if not opts:
        return "paper"
    m = values.get("mode")
    if m is None and "paper" in values:
        m = "paper" if values["paper"] else ("testnet" if values.get("sandbox") else "live")
    if m is None and existing:
        m = "paper" if existing.paper else ("testnet" if existing.extra.get("sandbox") else "live")
    m = m or "paper"
    if m not in opts:
        raise ValueError("Wybierz tryb konta.")
    return m


def _clean(ptype, values, existing=None):
    """Sprawdza pola formularza wg katalogu platformy. existing = obecna konfiguracja (edycja)."""
    p = PLATFORM.get(ptype)
    if not p:
        raise ValueError("Nieznana platforma.")
    if not p["available"]:
        raise ValueError(f"{p['label']}: obsługa tej platformy jest jeszcze w przygotowaniu.")
    key = secret = ""
    paper = True
    extra = dict(existing.extra) if existing else {}
    mode = _mode(p, values, existing)
    for f in p["fields"]:
        k, v = f["key"], values.get(f["key"])
        if f["type"] == "mode":
            paper = mode == "paper"
            if "testnet" in f["options"]:
                extra["sandbox"] = mode == "testnet"
            continue
        if f.get("when") and mode not in f["when"]:           # pole niewidoczne w tym trybie
            if f["type"] == "secret" and existing:
                if k == "key":
                    key = existing.key
                elif k == "secret":
                    secret = existing.secret
            elif f["type"] != "secret" and k not in extra and "default" in f:
                extra[k] = f["default"]
            continue
        if f["type"] == "secret":
            v = (v or "").strip()
            if not v and existing and k in ("key", "secret"):   # puste pole przy edycji = bez zmian
                v = existing.key if k == "key" else existing.secret
            if k in EXTRA_SECRETS:
                if not v and existing:
                    v = existing.extra.get(k, "")
                extra[k] = v
            elif k == "key":
                key = v
            else:
                secret = v
            if f.get("required") and not v:
                raise ValueError(f"Podaj: {f['label']}.")
            if v and (len(v) > 300 or any(c.isspace() for c in v)):
                raise ValueError(f"{f['label']}: nieprawidłowy format (spacje albo za długie).")
        elif f["type"] == "bool":
            v = f.get("default", False) if v is None else bool(v)
            if k == "paper":
                paper = v
            else:
                extra[k] = v
        elif f["type"] == "choice":
            v = v or (existing.extra.get(k) if existing else None) or f.get("default")
            if v not in f["options"] and not f.get("other"):     # "other" = dowolna wartosc sprawdzana pozniej
                raise ValueError(f"{f['label']}: wybierz jedną z opcji.")
            extra[k] = v
        elif f["type"] in ("float", "int"):
            if v in (None, "") and existing and existing.extra.get(k) is not None:
                v = existing.extra[k]
            v = f.get("default") if v in (None, "") else v
            try:
                v = float(v) if f["type"] == "float" else int(v)
            except (TypeError, ValueError):
                raise ValueError(f"{f['label']}: podaj liczbę.")
            if v < 0:
                raise ValueError(f"{f['label']}: liczba nie może być ujemna.")
            extra[k] = v
        else:
            extra[k] = str(v or f.get("default", "")).strip()[:100]
    if ptype == "kraken" and not paper and not (key and secret):
        raise ValueError("Prawdziwe konto Kraken wymaga klucza i Private Key.")
    if ptype == "gielda":
        _clean_exchange(extra, key, secret, paper)
    return key, secret, paper, extra


def _clean_exchange(extra, key, secret, paper):
    import ccxt
    other = (extra.pop("exchange_other", "") or "").strip().lower()
    ex = other or extra.get("exchange", "")
    if not re.fullmatch(r"[a-z0-9_]{2,30}", ex or "") or ex not in ccxt.exchanges:
        raise ValueError(f"Nie znam giełdy „{ex}”. Sprawdź nazwę na liście CCXT (github.com/ccxt/ccxt).")
    if ex == "kraken":
        raise ValueError("Krakena dodaj jako platformę „Kraken”.")
    extra["exchange"] = ex
    q = (extra.get("quote") or "USDT").strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{2,10}", q):
        raise ValueError("Waluta konta: np. USDT, USDC, EUR, USD, PLN.")
    extra["quote"] = q
    if not paper and not (key and secret):
        raise ValueError("Podaj API Key i Secret (prawdziwe konto i sieć testowa ich wymagają).")
    needs_pwd = bool(getattr(ccxt, ex)().requiredCredentials.get("password"))
    if not paper and needs_pwd and not extra.get("password"):
        raise ValueError("Ta giełda wymaga też Passphrase — tej, którą ustawiłeś przy tworzeniu klucza API.")
    if extra.get("sandbox") and paper:
        raise ValueError("Wybierz jedno: tryb „na niby” albo sieć testową giełdy.")


def test(ptype, values, name="test", existing=None):
    """Łączy się z platformą (tylko odczyt) i zwraca podsumowanie konta. Nic nie zapisuje."""
    key, secret, paper, extra = _clean(ptype, values, existing)
    return probe(AccountConfig(name=f"_test_{name}", type=ptype, key=key, secret=secret, paper=paper, extra=extra))


def probe(acc):
    from .brokers import BROKER_TYPES
    extra = acc.extra
    if acc.type == "sim":
        start = extra.get("sim_start", 100000)
        return {"ok": True, "mode": "symulator — nie łączy się z giełdą", "equity": start, "cash": start,
                "currency": "USD", "paper": True}
    if acc.type == "kraken" and not (acc.key and acc.secret):
        from .kraken import kraken_data
        q = extra.get("quote", "EUR")
        px = kraken_data().price(f"BTC/{q}")
        return {"ok": True, "mode": f"tryb na niby — dane Krakena działają (BTC/{q} = {px:,.2f})",
                "equity": extra.get("start"), "cash": extra.get("start"), "currency": q, "paper": True}
    if acc.type == "gielda":
        return _probe_exchange(acc)
    if acc.type == "kraken":                       # klucze sprawdzamy na prawdziwym API (odczyt salda), nawet w trybie na niby
        from .kraken import KrakenBroker
        info = KrakenBroker(acc).account()
        mode = "klucze działają (odczyt salda)" + (" — bot handluje na niby" if acc.paper else "")
    else:
        cls = BROKER_TYPES.get(acc.type)
        if not cls:
            raise ValueError(f"Nieznany typ konta: {acc.type}")
        info = cls(acc).account()
        mode = "połączono"
    return {"ok": True, "mode": mode, "equity": info.get("equity"), "cash": info.get("cash"),
            "currency": info.get("currency") or extra.get("quote", "USD"), "paper": acc.paper}


def add(name, ptype, values):
    name = (name or "").strip().lower()
    if not NAME_RE.match(name):
        raise ValueError("Nazwa konta: 2–20 znaków, małe litery, cyfry i _, na początku litera (np. ibkr_papier).")
    with _lock:
        if name in config.ACCOUNTS:
            raise ValueError(f"Konto '{name}' już istnieje.")
        key, secret, paper, extra = _clean(ptype, values)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        _db.execute("INSERT INTO broker_accounts VALUES (?,?,?,?,?,?,?,?)",
                    (name, ptype, int(paper), _enc(key), _enc(secret), _extra_json(extra), now, now))
        config.ACCOUNTS[name] = AccountConfig(name=name, type=ptype, key=key, secret=secret, paper=paper, extra=extra)
    return name


def update(name, values, bots_running):
    with _lock:
        if source(name) != "panel":
            raise ValueError("To konto jest zdefiniowane w pliku .env — zmieniasz je tam.")
        if bots_running:
            raise ValueError("Najpierw zatrzymaj boty tego konta: " + ", ".join(bots_running))
        cur = config.ACCOUNTS[name]
        key, secret, paper, extra = _clean(cur.type, values, existing=cur)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        _db.execute("UPDATE broker_accounts SET paper=?, key_enc=?, secret_enc=?, extra=?, updated_at=? WHERE name=?",
                    (int(paper), _enc(key), _enc(secret), _extra_json(extra), now, name))
        config.ACCOUNTS[name] = AccountConfig(name=name, type=cur.type, key=key, secret=secret, paper=paper, extra=extra)
        _forget(name)


def remove(name, bots_using):
    with _lock:
        if source(name) != "panel":
            raise ValueError("To konto jest zdefiniowane w pliku .env — usuń je z pliku.")
        if bots_using:
            raise ValueError("Z tego konta korzystają boty: " + ", ".join(bots_using) + ". Najpierw je usuń.")
        _db.execute("DELETE FROM broker_accounts WHERE name=?", (name,))
        config.ACCOUNTS.pop(name, None)
        _forget(name)


def _forget(name):
    from . import brokers
    with brokers._cache_lock:
        brokers._cache.pop(name, None)


def _probe_exchange(acc):
    """Giełda CCXT: dane publiczne (cena BTC w walucie konta), a gdy są klucze — odczyt salda."""
    from .kraken import kraken_data, exchange_name, KrakenBroker
    ex, q = acc.extra["exchange"], acc.extra.get("quote", "USDT")
    name = exchange_name(ex)
    data = kraken_data(ex)
    mk = data.markets()
    pairs = [s for s, m in mk.items() if m.get("spot") and m.get("quote") == q and m.get("active", True)]
    if not pairs:
        raise ValueError(f"{name}: brak par spot w walucie {q} — wybierz inną walutę konta.")
    btc = f"BTC/{q}" if f"BTC/{q}" in mk else pairs[0]
    px = data.price(btc)
    note = f"{name}: dane działają ({btc} = {px:,.2f}, {len(pairs)} par w {q}, prowizja ~{data.fee() * 100:.2f}%)"
    if not (acc.key and acc.secret):
        return {"ok": True, "mode": note + " — tryb na niby", "equity": acc.extra.get("start"),
                "cash": acc.extra.get("start"), "currency": q, "paper": True}
    info = KrakenBroker(acc).account()
    mode = note + "; klucze działają (odczyt salda)" + (" — sieć testowa" if acc.extra.get("sandbox") else "") + \
        (" — bot handluje na niby" if acc.paper else "")
    return {"ok": True, "mode": mode, "equity": info.get("equity"), "cash": info.get("cash"), "currency": q,
            "paper": acc.paper or bool(acc.extra.get("sandbox"))}
