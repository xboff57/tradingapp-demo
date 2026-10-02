"""
Gieldy krypto przez biblioteke CCXT - Kraken oraz dowolna inna gielda obslugiwana przez CCXT
(Binance, Bybit, OKX, Coinbase, KuCoin, Bitget, Gate, Bitstamp ...): dane swiecowe, handel na zywo
i tryb papierowy. Kraken ma typ konta "kraken"; pozostale gieldy typ "gielda" z polem extra["exchange"].

Konto w .env:
    ACCOUNTS=demo,win,kraken
    ACCOUNT_KRAKEN_TYPE=kraken
    ACCOUNT_KRAKEN_KEY=...            # klucz API: TYLKO handel, BEZ wyplat, najlepiej z ograniczeniem IP
    ACCOUNT_KRAKEN_SECRET=...
    ACCOUNT_KRAKEN_PAPER=true         # true = handel na niby po prawdziwych cenach Krakena (klucze niepotrzebne)
    ACCOUNT_KRAKEN_QUOTE=EUR          # waluta konta: pary SOL/EUR, BTC/EUR ...
    ACCOUNT_KRAKEN_START=10000        # kapital startowy trybu papierowego

Kraken nie ma konta demo dla rynku spot, dlatego tryb papierowy liczymy sami (jak konto demo),
ale na PRAWDZIWYCH cenach z Krakena i z jego prowizja (0,40% taker).

Ograniczenie Krakena: publiczne API zwraca tylko 720 ostatnich swiec danego interwalu
(1 h = 30 dni, 4 h = 120 dni, 1 dzien = ~2 lata). Aplikacja zapisuje pobrane swiece i dokleja nowe,
wiec historia rosnie z czasem. Do nauki modelu ML brakujaca historie bierzemy z Alpaki (para /USD),
jesli takie konto jest w .env - model uczy sie na zmianach procentowych, wiec waluta nie ma znaczenia.
"""

import json
import os
import pickle
import threading
import time
from datetime import datetime, timedelta, timezone

import pandas as pd

from .config import CACHE_DIR, DATA_DIR
from .data import COLS, norm

TF = {"1Min": "1m", "5Min": "5m", "15Min": "15m", "30Min": "30m", "1Hour": "1h", "4Hour": "4h", "1Day": "1d"}
KRAKEN_FEE = 0.004                     # taker przy malych obrotach
STABLE = {"USDT", "USDC", "DAI", "PYUSD", "EURC", "EURT", "USDE", "TUSD", "FDUSD", "USDG", "RLUSD", "USDQ", "USDR",
          "UST", "BUSD", "GUSD", "USDP", "LUSD", "FRAX", "USDS", "EURQ", "EURR", "USD1", "TBTC", "WBTC", "WETH"}
FIAT = {"EUR", "USD", "GBP", "CHF", "CAD", "AUD", "JPY", "AED"}
STORE = os.path.join(CACHE_DIR, "kraken")
os.makedirs(STORE, exist_ok=True)


OHLCV_LIMIT = {"coinbase": 300, "bitstamp": 1000, "bitfinex": 1000, "binance": 1000, "bybit": 1000, "okx": 300,
               "kucoin": 1500, "gate": 1000, "bitget": 1000, "mexc": 1000, "htx": 1000, "cryptocom": 300}
DEFAULT_FEE = 0.002                    # gdy gielda nie podaje prowizji taker


def store_dir(exchange_id):
    """Kraken zostaje w starym folderze (zebrana historia), inne gieldy w podfolderach."""
    d = STORE if exchange_id == "kraken" else os.path.join(STORE, "_" + exchange_id)
    os.makedirs(d, exist_ok=True)
    return d


def _ccxt():
    try:
        import ccxt
    except ImportError as e:
        raise RuntimeError("Brak biblioteki ccxt - zainstaluje sie przy aktualizacji (requirements.txt).") from e
    return ccxt


class KrakenData:
    """Swiece i ceny z publicznego API gieldy (bez kluczy). Jedna instancja na gielde i proces."""

    def __init__(self, exchange_id="kraken"):
        self.id = exchange_id
        self.ex = getattr(_ccxt(), exchange_id)({"enableRateLimit": True, "timeout": 20000})
        self.store = store_dir(exchange_id)
        self.lock = threading.RLock()
        self._markets_at = 0
        self._prices = {}

    # --- rynki
    def markets(self):
        with self.lock:
            if time.time() - self._markets_at > 6 * 3600:
                self.ex.load_markets(reload=True)
                self._markets_at = time.time()
            return self.ex.markets

    def universe(self, quote):
        """[(symbol, obrot_24h_w_walucie_quote)] - aktywne pary spot, bez stablecoinow i walut."""
        mk = self.markets()
        syms = [s for s, m in mk.items()
                if m.get("spot") and m.get("active", True) and m.get("quote") == quote
                and m.get("base") not in STABLE and m.get("base") not in FIAT and "." not in (m.get("base") or "")]
        out = []
        with self.lock:
            tickers = self.ex.fetch_tickers(syms) if syms else {}
        for s in syms:
            t = tickers.get(s) or {}
            vol = t.get("quoteVolume")
            if vol is None and t.get("baseVolume") and t.get("last"):
                vol = t["baseVolume"] * t["last"]
            out.append((s, float(vol or 0)))
        return sorted(out, key=lambda x: -x[1])

    # --- swiece
    def _path(self, sym, tf):
        return os.path.join(self.store, f"{norm(sym)}_{tf}.pkl")

    def _load(self, sym, tf):
        p = self._path(sym, tf)
        if os.path.exists(p):
            try:
                with open(p, "rb") as f:
                    return pickle.load(f)
            except Exception:
                return None
        return None

    def _fetch(self, sym, tf):
        limit = 720 if self.id == "kraken" else OHLCV_LIMIT.get(self.id, 500)
        with self.lock:
            try:
                rows = self.ex.fetch_ohlcv(sym, TF[tf], limit=limit)
            except Exception:
                if limit <= 200:
                    raise
                rows = self.ex.fetch_ohlcv(sym, TF[tf], limit=200)     # gielda z mniejszym limitem
        if not rows:
            return None
        df = pd.DataFrame(rows, columns=["ts"] + COLS)
        df.index = pd.to_datetime(df.pop("ts"), unit="ms", utc=True)
        return df.astype(float)

    def update(self, sym, tf):
        """Pobiera ostatnie swiece i dokleja je do zapisanej historii."""
        new = self._fetch(sym, tf)
        old = self._load(sym, tf)
        if new is None:
            return old
        df = new if old is None else pd.concat([old[~old.index.isin(new.index)], new]).sort_index()
        with open(self._path(sym, tf), "wb") as f:
            pickle.dump(df, f)
        return df

    def bars(self, symbols, timeframe, start, end=None):
        out = {}
        for s in symbols:
            try:
                df = self.update(s, timeframe)
            except Exception:
                df = self._load(s, timeframe)
            if df is None or df.empty:
                continue
            df = df[df.index >= pd.Timestamp(start)]
            if end is not None:
                df = df[df.index < pd.Timestamp(end)]
            if not df.empty:
                out[s] = df[COLS]
        return out

    def first_bar(self, sym, tf):
        df = self._load(sym, tf)
        return df.index[0] if df is not None and len(df) else None

    def price(self, symbol, at=None):
        hit = self._prices.get(symbol)
        if hit and time.time() - hit[0] < 5:
            return hit[1]
        with self.lock:
            t = self.ex.fetch_ticker(symbol)
        px = float(t.get("last") or t.get("close") or 0)
        self._prices[symbol] = (time.time(), px)
        return px

    def prices(self, symbols):
        """Ceny wielu par jednym zapytaniem (gdy gielda pozwala)."""
        symbols = [s for s in symbols if s]
        if not symbols:
            return {}
        if self.ex.has.get("fetchTickers") and len(symbols) > 1:
            try:
                with self.lock:
                    tk = self.ex.fetch_tickers(symbols)
                now = time.time()
                out = {}
                for s in symbols:
                    t = tk.get(s) or {}
                    px = t.get("last") or t.get("close")
                    if px:
                        out[s] = float(px)
                        self._prices[s] = (now, float(px))
                if out:
                    return out
            except Exception:
                pass
        return {s: self.price(s) for s in symbols}

    def fee(self):
        """Prowizja taker (ulamek) - z danych gieldy, Kraken 0,40%."""
        if self.id == "kraken":
            return KRAKEN_FEE
        try:
            for m in self.markets().values():
                if m.get("spot") and m.get("taker"):
                    return float(m["taker"])
        except Exception:
            pass
        return DEFAULT_FEE


_shared = {}
_shared_lock = threading.Lock()


def kraken_data(exchange_id="kraken"):
    with _shared_lock:
        if exchange_id not in _shared:
            _shared[exchange_id] = KrakenData(exchange_id)
        return _shared[exchange_id]


def exchange_name(exchange_id):
    try:
        return getattr(_ccxt(), exchange_id)().name
    except Exception:
        return exchange_id


class HistoryWithFallback:
    """Dane do nauki ML / testu: Kraken, a gdy jego historia jest za krotka - ta sama moneta z Alpaki (/USD)."""

    def __init__(self, kraken, alpaca=None):
        self.kraken = kraken
        self.alpaca = alpaca
        self.used_fallback = set()

    def bars(self, symbols, timeframe, start, end=None):
        out = self.kraken.bars(symbols, timeframe, start, end)
        if not self.alpaca:
            return out
        need = [s for s in symbols if s not in out or out[s].index[0] > pd.Timestamp(start) + pd.Timedelta(days=3)]
        alt = {s: s.split("/")[0] + "/USD" for s in need}
        if alt:
            try:
                got = self.alpaca.bars(list(set(alt.values())), timeframe, start, end)
            except Exception:
                got = {}
            for s, a in alt.items():
                if a in got and (s not in out or len(got[a]) > len(out[s])):
                    out[s] = got[a]
                    self.used_fallback.add(s)
        return out

    def price(self, symbol, at=None):
        return self.kraken.price(symbol)


# ------------------------------------------------------------------ handel na zywo
class KrakenBroker:
    """Handel spot na gieldzie krypto przez CCXT (Kraken albo inna gielda). Tylko krypto."""

    def __init__(self, acc):
        from .brokers import BrokerError
        from .config import exchange_id
        self.id = exchange_id(acc)
        self.label = exchange_name(self.id)
        if not acc.key or not acc.secret:
            raise BrokerError(f"Konto '{acc.name}': brak klucza lub sekretu ({self.label}) "
                              "(albo włącz tryb na niby)")
        self.name = acc.name
        self.quote = acc.extra.get("quote", "EUR")
        opts = {"apiKey": acc.key, "secret": acc.secret, "enableRateLimit": True, "timeout": 20000}
        if acc.extra.get("password"):
            opts["password"] = acc.extra["password"]
        self.ex = getattr(_ccxt(), self.id)(opts)
        self.sandbox = bool(acc.extra.get("sandbox"))
        if self.sandbox:
            try:
                self.ex.set_sandbox_mode(True)
            except Exception:
                raise BrokerError(f"{self.label} nie ma sieci testowej (testnet) w CCXT - wyłącz tę opcję.")
        self.data = kraken_data(self.id)
        self.soft_stops = set()          # pary, na ktorych gielda nie przyjela stopu - pilnuje go bot
        self.lock = threading.RLock()
        prefix = "kraken" if self.id == "kraken" else f"gielda_{self.id}"
        self.path = os.path.join(DATA_DIR, f"{prefix}_{acc.name}_wejscia.json")   # sredni kurs zakupu per moneta
        self.entries = json.load(open(self.path, encoding="utf-8")) if os.path.exists(self.path) else {}
        self._day = (None, None)

    def _save(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.entries, f)

    def _err(self, e):
        from .brokers import BrokerError
        return BrokerError(f"{self.label}: {e}")

    def _balance(self):
        with self.lock:
            return self.ex.fetch_balance()

    def _mk(self):
        """Rynki: przy sieci testowej z wlasnego polaczenia (inne pary niz na prawdziwej gieldzie)."""
        if not self.sandbox:
            return self.data.markets()
        with self.lock:
            if not self.ex.markets:
                self.ex.load_markets()
            return self.ex.markets

    def _px(self, syms):
        if not self.sandbox:
            return self.data.prices(syms)
        out = {}
        for s in syms:
            with self.lock:
                t = self.ex.fetch_ticker(s)
            out[s] = float(t.get("last") or t.get("close") or 0)
        return out

    def account(self):
        bal = self._balance()
        cash_total = float(bal.get("total", {}).get(self.quote) or 0)
        cash_free = float(bal.get("free", {}).get(self.quote) or 0)
        mv = sum(p["market_value"] for p in self.positions(bal).values())
        eq = cash_total + mv
        today = datetime.now(timezone.utc).date()
        if self._day[0] != today:
            self._day = (today, eq)
        return {"equity": eq, "cash": cash_total, "buying_power": cash_free, "crypto_buying_power": cash_free,
                "last_equity": self._day[1], "currency": self.quote}

    def positions(self, bal=None):
        bal = bal or self._balance()
        mk = self._mk()
        held = {}
        for base, qty in (bal.get("total") or {}).items():
            qty = float(qty or 0)
            sym = f"{base}/{self.quote}"
            if qty > 0 and base not in FIAT and base != self.quote and sym in mk:
                held[sym] = (base, qty)
        prices = self._px(list(held)) if held else {}
        out = {}
        for sym, (base, qty) in held.items():
            price = prices.get(sym) or 0
            if qty * price < 1:
                continue
            entry = self.entries.get(sym, {}).get("avg", price)
            free = float((bal.get("free") or {}).get(base) or 0)
            out[norm(sym)] = {"symbol": sym, "qty": qty, "qty_available": free, "avg_entry": entry, "price": price,
                              "market_value": qty * price, "unrealized": (price - entry) * qty}
        return out

    def clock(self):
        now = datetime.now(timezone.utc)
        return {"is_open": True, "now": now, "next_open": now, "next_close": now + timedelta(days=365)}

    def open_orders(self, symbol=None):
        with self.lock:
            orders = self.ex.fetch_open_orders(symbol) if symbol else self.ex.fetch_open_orders()
        return [{"id": o["id"], "symbol": o["symbol"], "side": o["side"], "type": o.get("type")} for o in orders]

    def cancel(self, order_id):
        with self.lock:
            self.ex.cancel_order(order_id)

    def buy_bracket(self, symbol, qty, stop, take, overnight):
        raise self._err("akcji nie obslugujemy - konto Kraken jest tylko do krypto")

    def _wait(self, order, symbol):
        for _ in range(30):
            with self.lock:
                o = self.ex.fetch_order(order["id"], symbol)
            if o.get("status") == "closed":
                return o
            if o.get("status") in ("canceled", "expired", "rejected"):
                raise self._err(f"zlecenie {o.get('status')}")
            time.sleep(1)
        raise self._err("zlecenie nie zrealizowalo sie w 30 s")

    def buy_notional(self, symbol, notional):
        m = self._mk().get(symbol)
        if not m:
            raise self._err(f"brak pary {symbol}")
        price = self._px([symbol])[symbol]
        amount = float(self.ex.amount_to_precision(symbol, notional / price))
        min_amt = ((m.get("limits") or {}).get("amount") or {}).get("min") or 0
        min_cost = ((m.get("limits") or {}).get("cost") or {}).get("min") or 0
        if amount < min_amt or amount * price < min_cost:
            raise self._err(f"kwota {notional:.2f} {self.quote} ponizej minimum gieldy dla {symbol}")
        with self.lock:
            o = self.ex.create_order(symbol, "market", "buy", amount)
        o = self._wait(o, symbol)
        fill = float(o.get("average") or o.get("price") or price)
        qty = float(o.get("filled") or amount)
        prev = self.entries.get(symbol, {"avg": fill, "qty": 0.0})
        tot = prev["qty"] + qty
        self.entries[symbol] = {"avg": (prev["avg"] * prev["qty"] + fill * qty) / tot if tot else fill, "qty": tot}
        self._save()
        return fill, qty

    def sell_stop_limit(self, symbol, qty, stop, limit):
        """Stop-limit na serwerze gieldy. Gdy gielda go nie przyjmie, stop pilnuje bot (sprawdza cene co cykl)."""
        if symbol in self.soft_stops:
            return "soft"
        try:
            with self.lock:
                amount = float(self.ex.amount_to_precision(symbol, qty))
                self.ex.create_order(symbol, "limit", "sell", amount, float(self.ex.price_to_precision(symbol, limit)),
                                     {"stopLossPrice": float(self.ex.price_to_precision(symbol, stop))})
        except Exception as e:
            if self.id == "kraken":
                raise
            self.soft_stops.add(symbol)
            self.soft_reason = str(e)[:200]
            return "soft"
        return "server"

    def close_position(self, symbol):
        base = symbol.split("/")[0]
        time.sleep(1)                          # anulowane zlecenia zwalniaja srodki z opoznieniem
        bal = self._balance()
        free = float((bal.get("free") or {}).get(base) or 0)
        if free <= 0:
            return None
        amount = float(self.ex.amount_to_precision(symbol, free))
        if amount <= 0:
            return None
        with self.lock:
            o = self.ex.create_order(symbol, "market", "sell", amount)
        o = self._wait(o, symbol)
        self.entries.pop(symbol, None)
        self._save()
        return {"qty": float(o.get("filled") or amount), "price": float(o.get("average") or o.get("price") or 0)}


def paper_broker_class():
    from .brokers import SimBroker

    class KrakenPaperBroker(SimBroker):
        """Konto 'na niby' na prawdziwych cenach gieldy, z jej prowizja taker przy kupnie i sprzedazy."""
        FEE = KRAKEN_FEE

        def __init__(self, acc):
            self.START_CASH = float(acc.extra.get("start", 10_000))
            super().__init__(acc)
            from .config import exchange_id
            self.data = kraken_data(exchange_id(acc))
            self.quote = acc.extra.get("quote", "EUR")
            try:
                self.FEE = self.data.fee()
            except Exception:
                pass

        def _price(self, symbol):
            return self.data.price(symbol)

        def _market_open(self, now=None):
            return True

        def _buy(self, symbol, qty, price):
            cost = qty * price * (1 + self.FEE)
            from .brokers import BrokerError
            if cost > self.state["cash"] + 1e-6:
                raise BrokerError("za malo srodkow na koncie papierowym")
            self.state["cash"] -= cost
            s = norm(symbol)
            p = self.state["positions"].get(s)
            if p:
                p["avg_entry"] = (p["avg_entry"] * p["qty"] + price * qty) / (p["qty"] + qty)
                p["qty"] += qty
            else:
                self.state["positions"][s] = {"symbol": symbol, "qty": qty, "avg_entry": price}

        def _sell(self, symbol, qty, price):
            s = norm(symbol)
            p = self.state["positions"][s]
            self.state["cash"] += qty * price * (1 - self.FEE)
            p["qty"] -= qty
            if p["qty"] <= 1e-12:
                del self.state["positions"][s]

    return KrakenPaperBroker
