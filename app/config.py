"""
Konfiguracja aplikacji z pliku .env.

Konta brokerskie definiujesz w .env - klucze NIGDY nie trafiaja do bazy ani do panelu:

    ACCOUNTS=nas,win,demo
    ACCOUNT_NAS_TYPE=alpaca
    ACCOUNT_NAS_KEY=...
    ACCOUNT_NAS_SECRET=...
    ACCOUNT_NAS_PAPER=true
    ACCOUNT_DEMO_TYPE=sim          # konto symulowane (bez internetu, do nauki panelu)
"""

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(BASE_DIR, ".env"))

DATA_DIR = os.environ.get("DATA_DIR", os.path.join(BASE_DIR, "data"))
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "tradingapp.sqlite")
CACHE_DIR = os.path.join(DATA_DIR, "cache")
os.makedirs(CACHE_DIR, exist_ok=True)
LOG_PATH = os.path.join(DATA_DIR, "tradingapp.log")

# Wersja demonstracyjna (DEMO=1): tylko konto symulowane, przykladowe dane, bez logowania,
# panel wylacznie na tym komputerze (127.0.0.1); klucze, bramka, powiadomienia i aktualizacje zablokowane.
DEMO = os.environ.get("DEMO", "").strip().lower() in ("1", "true", "tak", "yes")

PANEL_PASSWORD = os.environ.get("PANEL_PASSWORD", "").strip()
PANEL_HOST = os.environ.get("PANEL_HOST", "127.0.0.1" if DEMO else "0.0.0.0")
PANEL_PORT = int(os.environ.get("PANEL_PORT", "8420"))
SESSION_HOURS = int(os.environ.get("SESSION_HOURS", "12"))
EQUITY_SNAPSHOT_MINUTES = int(os.environ.get("EQUITY_SNAPSHOT_MINUTES", "15"))


@dataclass
class AccountConfig:
    name: str
    type: str                 # alpaca | kraken | gielda (CCXT) | sim | ibkr
    key: str = ""
    secret: str = ""
    paper: bool = True
    extra: dict = field(default_factory=dict)

    def public(self) -> dict:
        """Bezpieczny opis konta do panelu - bez kluczy."""
        d = {"name": self.name, "type": self.type, "paper": self.paper,
             "currency": self.extra.get("quote", "USD")}
        if self.type == "gielda":
            d.update(exchange=self.extra.get("exchange", ""), sandbox=bool(self.extra.get("sandbox")))
        return d


def load_accounts() -> dict:
    if DEMO:
        return {"demo": AccountConfig(name="demo", type="sim", extra={"quote": "USD", "start": 100000.0})}
    accounts = {}
    names = [n.strip().lower() for n in os.environ.get("ACCOUNTS", "demo").split(",") if n.strip()]
    for name in names:
        p = f"ACCOUNT_{name.upper()}_"
        acc_type = os.environ.get(p + "TYPE", "sim" if name == "demo" else "alpaca").strip().lower()
        accounts[name] = AccountConfig(
            name=name,
            type=acc_type,
            key=os.environ.get(p + "KEY", "").strip(),
            secret=os.environ.get(p + "SECRET", "").strip(),
            paper=os.environ.get(p + "PAPER", "true").strip().lower() != "false",
            extra={"quote": os.environ.get(p + "QUOTE", "EUR" if acc_type == "kraken" else "USD").strip().upper(),
                   "start": float(os.environ.get(p + "START", "10000") or 10000)},
        )
    return accounts


ACCOUNTS = load_accounts()


CRYPTO_EX_TYPES = ("kraken", "gielda")       # gieldy krypto przez CCXT


def is_crypto_ex(acc):
    return acc is not None and acc.type in CRYPTO_EX_TYPES


def exchange_id(acc):
    """Identyfikator gieldy w CCXT: Kraken albo extra["exchange"] konta typu "gielda"."""
    return "kraken" if acc.type == "kraken" else (acc.extra.get("exchange") or "").strip().lower()


def data_tag(acc):
    """Znacznik zrodla danych (pamiec podreczna, koszty): 'kraken' albo 'ccxt_<gielda>'."""
    ex = exchange_id(acc)
    return "kraken" if ex == "kraken" else f"ccxt_{ex}"


def kraken_account():
    for acc in ACCOUNTS.values():
        if acc.type == "kraken":
            return acc
    return None


def exchange_account(exchange):
    """Pierwsze konto danej gieldy CCXT (np. 'binance')."""
    for acc in ACCOUNTS.values():
        if is_crypto_ex(acc) and exchange_id(acc) == exchange:
            return acc
    return None


def data_account():
    """Konto, z ktorego pobieramy dane historyczne do backtestow (pierwsze konto Alpaca)."""
    for acc in ACCOUNTS.values():
        if acc.type == "alpaca" and acc.key and acc.secret:
            return acc
    return None
