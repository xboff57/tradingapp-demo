"""
Interactive Brokers (IBKR) przez IB Gateway — GPW, waluty (forex) i akcje z USA.

IBKR nie daje kluczy API: aplikacja laczy sie z IB Gateway (osobny kontener na NAS-ie, zalogowany na konto),
domyslnie 127.0.0.1:4002 = konto papierowe. Biblioteka: ib_async (asyncio) — wszystkie wywolania ida przez
jeden watek z petla zdarzen, bo boty dzialaja w wielu watkach.

Symbole:
    PKN.WSE, CDR.WSE    akcje z GPW (w PLN)
    EUR.USD, EUR.PLN    waluty (IDEALPRO); ilosc = jednostki waluty bazowej pary
    AAPL, SPY           akcje i ETF-y z USA (SMART, USD)
"""

import asyncio
import inspect
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd

CURRENCIES = {"USD", "EUR", "PLN", "GBP", "CHF", "JPY", "CAD", "AUD", "NZD", "SEK", "NOK", "DKK", "CZK", "HUF"}
EXCHANGES = {"WSE": ("WSE", "PLN"),            # sufiks symbolu -> (gielda IB, waluta)
             "DE": ("IBIS", "EUR")}            # Xetra (Niemcy)
IB_EXCHANGE_SUFFIX = {v[0]: k for k, v in EXCHANGES.items()}
# kolejnosc walut w parach IB (EUR.USD, a nie USD.EUR) - do przeliczania kursow
FX_ORDER = ["EUR", "GBP", "AUD", "NZD", "USD", "CAD", "CHF", "JPY", "PLN", "SEK", "NOK", "DKK", "CZK", "HUF"]
BAR = {"1Min": "1 min", "5Min": "5 mins", "15Min": "15 mins", "30Min": "30 mins", "1Hour": "1 hour",
       "4Hour": "4 hours", "1Day": "1 day"}
# ile historii w jednym zapytaniu (limity IB) - dluzsze okresy pobieramy kawalkami wstecz
CHUNK = {"1Min": ("1 D", 1), "5Min": ("1 W", 7), "15Min": ("2 W", 14), "30Min": ("1 M", 28),
         "1Hour": ("1 M", 28), "4Hour": ("6 M", 180)}
TZ_ALIAS = {"MET": "Europe/Warsaw", "CET": "Europe/Warsaw", "EST": "America/New_York", "EST5EDT": "America/New_York",
            "US/Eastern": "America/New_York", "GMT": "UTC"}
SYMBOL_RE = re.compile(r"^[A-Z0-9]{1,12}(\.[A-Z]{2,3})?$")


class IBKRError(Exception):
    pass


class UnknownSymbol(IBKRError):
    pass


def kind(symbol):
    s = symbol.upper()
    if "." in s:
        a, b = s.rsplit(".", 1)
        if a in CURRENCIES and b in CURRENCIES:
            return "fx"
        return b.lower() if b in EXCHANGES else "?"
    return "us"


def venue_error(symbols):
    """Jeden bot IBKR = jeden rynek (GPW, waluty albo USA) - inne godziny sesji. Zwraca opis bledu albo None."""
    bad = [s for s in symbols if not SYMBOL_RE.match(s.upper()) or kind(s) == "?"]
    if bad:
        return ("IBKR: nieznany format symbolu: " + ", ".join(bad) +
                ". Użyj: PKN.WSE (GPW), SAP.DE (Xetra), EUR.USD (waluty), AAPL (USA).")
    kinds = {kind(s) for s in symbols}
    if len(kinds) > 1:
        return "IBKR: jeden bot = jeden rynek (GPW, Xetra, waluty albo USA) — mają różne godziny sesji. Rozdziel symbole na boty."
    return None


def contract_for(symbol):
    from ib_async import Forex, Stock
    s = symbol.upper()
    k = kind(s)
    if k == "fx":
        a, b = s.split(".")
        return Forex(a + b)
    if k.upper() in EXCHANGES:
        sym, ex = s.rsplit(".", 1)
        exch, cur = EXCHANGES[ex]
        return Stock(sym, exch, cur)
    return Stock(s, "SMART", "USD")


def key_for(contract):
    """Kontrakt IB -> symbol w formacie aplikacji (PKN.WSE / EUR.USD / AAPL)."""
    if contract.secType == "CASH":
        return f"{contract.symbol}.{contract.currency}"
    for ex in (contract.exchange, contract.primaryExchange):
        if ex in IB_EXCHANGE_SUFFIX:
            return f"{contract.symbol}.{IB_EXCHANGE_SUFFIX[ex]}"
    if contract.currency == "PLN":
        return f"{contract.symbol}.WSE"
    if contract.currency == "EUR":
        return f"{contract.symbol}.DE"
    return contract.symbol


class IBConn:
    """Jedno polaczenie z IB Gateway na (host, port, client_id), z wlasnym watkiem petli asyncio."""
    _all = {}
    _all_lock = threading.Lock()

    @classmethod
    def get(cls, host, port, client_id):
        k = (host, int(port), int(client_id))
        with cls._all_lock:
            if k not in cls._all:
                cls._all[k] = cls(*k)
            return cls._all[k]

    def __init__(self, host, port, client_id):
        self.host, self.port, self.client_id = host, port, client_id
        self.loop = asyncio.new_event_loop()
        self.ready = threading.Event()
        self.ib = None
        self.lock = threading.Lock()
        self.last_try = 0
        # stan polaczenia bramki z serwerami IBKR (komunikaty 1100/1101/1102/2110) - dla monitora bramki
        self.server_ok = True
        self.server_since = time.time()
        self.api_since = None                     # od kiedy brak polaczenia aplikacja <-> bramka (None = jest)
        self.last_error = None
        threading.Thread(target=self._run, daemon=True, name=f"ibkr-{port}-{client_id}").start()
        self.ready.wait(10)

    def _run(self):
        asyncio.set_event_loop(self.loop)
        from ib_async import IB
        self.ib = IB()
        self.ib.errorEvent += self._on_error
        self.ib.disconnectedEvent += self._on_disconnect
        self.ib.connectedEvent += self._on_connect
        self.ready.set()
        self.loop.run_forever()

    LOST, RESTORED = {1100, 2110}, {1101, 1102}

    def _on_error(self, req_id, code, msg, *a):
        if code in self.LOST and self.server_ok:
            self.server_ok, self.server_since = False, time.time()
        elif code in self.RESTORED and not self.server_ok:
            self.server_ok, self.server_since = True, time.time()
        if code in self.LOST or code >= 500 and code < 600:
            self.last_error = (time.time(), code, str(msg)[:200])

    def _on_disconnect(self):
        if self.api_since is None:
            self.api_since = time.time()

    def _on_connect(self):
        self.api_since = None
        self.server_ok, self.server_since = True, time.time()   # po polaczeniu bramka zglosi 1100, jesli serwery leza

    def health(self):
        """Stan dla monitora: polaczenie aplikacja-bramka i bramka-serwery IBKR."""
        api = bool(self.ib and self.ib.isConnected())
        if not api and self.api_since is None:
            self.api_since = time.time()
        return {"api": api, "api_since": self.api_since, "server_ok": self.server_ok,
                "server_since": self.server_since, "last_error": self.last_error}

    def ping(self):
        """Proba polaczenia + zapytanie o czas serwera (sprawdza, czy bramka naprawde odpowiada)."""
        return self.call(lambda ib: ib.reqCurrentTimeAsync(), timeout=20)

    def call(self, fn, timeout=60):
        """fn(ib) -> wartosc albo korutyna; wykonywane w watku petli IB."""
        self.ensure()

        async def job():
            r = fn(self.ib)
            if inspect.isawaitable(r):                   # korutyna albo Future (czesc metod *Async zwraca Future)
                r = await r
            return r
        return asyncio.run_coroutine_threadsafe(job(), self.loop).result(timeout)

    def ensure(self):
        if self.ib.isConnected():
            return
        with self.lock:
            if self.ib.isConnected():
                return
            if time.time() - self.last_try < 10:
                raise IBKRError("Brak połączenia z IB Gateway (ponawiam co 10 s).")
            self.last_try = time.time()
            fut = asyncio.run_coroutine_threadsafe(
                self.ib.connectAsync(self.host, self.port, clientId=self.client_id, timeout=15), self.loop)
            try:
                fut.result(25)
                self.api_since = None
            except Exception as e:
                if self.api_since is None:
                    self.api_since = time.time()
                raise IBKRError(f"Nie mogę połączyć się z IB Gateway {self.host}:{self.port} ({e or type(e).__name__}). "
                                f"Sprawdź, czy kontener ib-gateway działa i jest zalogowany.")
            self.ib.reqMarketDataType(3)            # bez subskrypcji: dane opoznione zamiast bledu


class IBKRData:
    """Swiece z IBKR w formacie aplikacji: {symbol: DataFrame[open, high, low, close, volume]} z indeksem UTC."""

    def __init__(self, conn, broker=None):
        self.conn = conn
        self.broker = broker
        self.unknown = set()
        self._memo = {}                                  # te same zapytania z kilku watkow naraz = jedno do IB
        self._memo_lock = threading.Lock()
        self._hist_lock = threading.Lock()

    def bars(self, symbols, timeframe, start, end=None):
        out = {}
        for s in symbols:
            try:
                df = self._bars_one(s, timeframe, start, end)
            except UnknownSymbol:
                self.unknown.add(s)                      # nieznany symbol pomijamy (reszta listy dziala)
                continue
            except IBKRError:
                raise                                    # brak polaczenia z bramka - to trzeba zglosic
            except Exception:
                continue
            if df is not None and not df.empty:
                out[s] = df
        return out

    def _bars_one(self, symbol, timeframe, start, end):
        key = (symbol.upper(), timeframe, start.strftime("%Y%m%d%H"), (end or datetime.now(timezone.utc)).strftime("%Y%m%d%H"))
        with self._memo_lock:
            hit = self._memo.get(key)
            if hit and time.time() - hit[0] < 600:
                return hit[1]
        with self._hist_lock:                            # IB nie lubi rownoleglych i powtarzanych zapytan o historie
            with self._memo_lock:
                hit = self._memo.get(key)
                if hit and time.time() - hit[0] < 600:
                    return hit[1]
            df = self._fetch(symbol, timeframe, start, end)
            with self._memo_lock:
                if len(self._memo) > 500:
                    self._memo.clear()
                self._memo[key] = (time.time(), df)
            return df

    def _fetch(self, symbol, timeframe, start, end):
        c = self.broker.contract(symbol) if self.broker else contract_for(symbol)
        what = "MIDPOINT" if kind(symbol) == "fx" else "TRADES"
        rth = kind(symbol) != "fx"
        end = end or datetime.now(timezone.utc)
        frames = []
        if timeframe == "1Day":
            days = max(2, (end - start).days + 2)
            dur = f"{days} D" if days <= 365 else f"{-(-days // 365)} Y"
            frames.append(self._req(c, end, dur, BAR[timeframe], what, rth))
        else:
            dur, step = CHUNK[timeframe]
            cur = end
            for _ in range(60):
                df = self._req(c, cur, dur, BAR[timeframe], what, rth)
                if df is None or df.empty:
                    break
                frames.append(df)
                if df.index[0] <= start:
                    break
                cur = df.index[0].to_pydatetime()
                time.sleep(0.5)                          # limity zapytan IB o historie
        if not frames:
            return None
        df = pd.concat(frames).sort_index()
        df = df[~df.index.duplicated(keep="last")]
        return df[(df.index >= start) & (df.index <= end)]

    def _req(self, contract, end, duration, bar, what, rth):
        endstr = end.astimezone(timezone.utc).strftime("%Y%m%d-%H:%M:%S")
        bars = self.conn.call(lambda ib: ib.reqHistoricalDataAsync(
            contract, endDateTime=endstr, durationStr=duration, barSizeSetting=bar, whatToShow=what,
            useRTH=rth, formatDate=2, timeout=60), timeout=90)
        if not bars:
            return None
        rows = []
        for b in bars:
            t = b.date
            if not isinstance(t, datetime):                  # swiece dzienne: data -> polnoc UTC
                t = datetime(t.year, t.month, t.day, tzinfo=timezone.utc)
            elif t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            rows.append((t.astimezone(timezone.utc), b.open, b.high, b.low, b.close, max(float(b.volume or 0), 0.0)))
        df = pd.DataFrame(rows, columns=["t", "open", "high", "low", "close", "volume"]).set_index("t")
        df.index = pd.DatetimeIndex(df.index)
        return df


class IBKRBroker:
    """Broker dla silnika botow - ten sam interfejs co AlpacaBroker (brokers.Broker)."""
    per_symbol_clock = True

    def __init__(self, acc):
        ex = acc.extra
        self.name = acc.name
        self.paper = acc.paper
        self.conn = IBConn.get(ex.get("host", "127.0.0.1"), int(ex.get("port", 4002)), int(ex.get("client_id", 7)))
        self.data = IBKRData(self.conn, self)
        self._contracts, self._details, self._rules, self._fx = {}, {}, {}, {}
        self._base = None

    # --- kontrakty
    def contract(self, symbol):
        s = symbol.upper()
        if s not in self._contracts:
            c = contract_for(s)
            q = self.conn.call(lambda ib: ib.qualifyContractsAsync(c))
            if not q or not q[0] or not getattr(q[0], "conId", 0):
                raise UnknownSymbol(f"IBKR nie zna symbolu {symbol}")
            self._contracts[s] = q[0]
        return self._contracts[s]

    def details(self, symbol):
        s = symbol.upper()
        d = self._details.get(s)
        if not d or time.time() - d[0] > 6 * 3600:
            c = self.contract(s)
            res = self.conn.call(lambda ib: ib.reqContractDetailsAsync(c))
            if not res:
                raise IBKRError(f"Brak szczegółów kontraktu {symbol}")
            self._details[s] = d = (time.time(), res[0])
        return d[1]

    def tick(self, symbol, price):
        """Krok ceny dla danego poziomu (GPW ma kroki zalezne od ceny - tabela 'market rule')."""
        det = self.details(symbol)
        inc = float(det.minTick or 0.01)
        rid = (det.marketRuleIds or "").split(",")[0]
        if rid:
            if rid not in self._rules:
                try:
                    self._rules[rid] = self.conn.call(lambda ib: ib.reqMarketRuleAsync(int(rid))) or []
                except Exception:
                    self._rules[rid] = []
            for r in self._rules[rid]:
                if price >= r.lowEdge:
                    inc = float(r.increment)
        return inc

    def round_price(self, symbol, price, down=True):
        inc = self.tick(symbol, price)
        n = price / inc
        n = int(n) if down else int(-(-n // 1))
        return round(n * inc, 8)

    # --- waluty
    def base(self):
        if not self._base:
            self.account()
        return self._base or "USD"

    def fx(self, cur):
        """Ile waluty bazowej konta kosztuje 1 jednostka waluty 'cur' (cache 1 h)."""
        base = self.base()
        if cur == base:
            return 1.0
        v = self._fx.get(cur)
        if v and time.time() - v[0] < 3600:
            return v[1]
        rate = None
        rank = lambda c: FX_ORDER.index(c) if c in FX_ORDER else 99
        pairs = [(f"{cur}.{base}", False), (f"{base}.{cur}", True)]
        if rank(base) < rank(cur):                   # konwencja IB: najpierw "mocniejsza" waluta pary
            pairs.reverse()
        for pair, inv in pairs:
            try:
                df = self.data._bars_one(pair, "1Day", datetime.now(timezone.utc) - timedelta(days=10), None)
                if df is not None and len(df):
                    px = float(df["close"].iloc[-1])
                    rate = 1 / px if inv else px
                    break
            except Exception:
                continue
        if not rate:
            raise IBKRError(f"Nie mogę pobrać kursu {cur}/{base}")
        self._fx[cur] = (time.time(), rate)
        return rate

    def fx_for(self, symbol):
        """Mnoznik: cena symbolu (w jego walucie) -> waluta bazowa konta."""
        return self.fx(self.contract(symbol).currency)

    # --- konto
    def account(self):
        rows = self.conn.call(lambda ib: ib.accountSummaryAsync())
        vals = {}
        for r in rows:
            if r.currency and r.currency != "":
                vals.setdefault(r.tag, (r.value, r.currency))
        if "NetLiquidation" not in vals:
            raise IBKRError("IB Gateway nie zwrócił danych konta")
        self._base = vals["NetLiquidation"][1]
        f = lambda tag, default=0.0: float(vals.get(tag, (default, ""))[0] or default)
        eq = f("NetLiquidation")
        return {"equity": eq, "cash": f("TotalCashValue"), "buying_power": f("AvailableFunds", eq),
                "crypto_buying_power": f("AvailableFunds", eq),
                "last_equity": f("PreviousDayEquityWithLoanValue", eq) or eq, "currency": self._base}

    def positions(self):
        out = {}
        items = self.conn.call(lambda ib: list(ib.portfolio()))
        for p in items:
            if not p.position:
                continue
            c = p.contract
            key = key_for(c)
            try:
                rate = self.fx(c.currency)
            except Exception:
                rate = 1.0
            price = float(p.marketPrice or 0)
            avg = float(p.averageCost or 0) / (float(c.multiplier) if c.multiplier else 1.0)
            qty = float(p.position)
            out[key.upper()] = {"symbol": key, "qty": qty, "qty_available": qty, "avg_entry": avg, "price": price,
                                "market_value": qty * price * rate, "unrealized": (price - avg) * qty * rate}
        return out

    def clock(self, symbols=None):
        """Sesja z godzin handlu IBKR (z uwzglednieniem swiat) dla rynku symboli bota."""
        now = datetime.now(timezone.utc)
        sym = (symbols or ["SPY"])[0]
        det = self.details(sym)
        tz = ZoneInfo(TZ_ALIAS.get(det.timeZoneId, det.timeZoneId or "UTC"))
        hours = det.liquidHours if kind(sym) != "fx" else det.tradingHours
        sessions = []
        for part in (hours or "").split(";"):
            if "-" not in part or "CLOSED" in part:
                continue
            a, b = part.split("-")
            try:
                s = datetime.strptime(a, "%Y%m%d:%H%M").replace(tzinfo=tz).astimezone(timezone.utc)
                e = datetime.strptime(b, "%Y%m%d:%H%M").replace(tzinfo=tz).astimezone(timezone.utc)
            except ValueError:
                continue
            sessions.append((s, e))
        sessions.sort()
        cur = next(((s, e) for s, e in sessions if s <= now < e), None)
        nxt = next(((s, e) for s, e in sessions if s > now), None)
        if cur:
            return {"is_open": True, "now": now, "next_open": nxt[0] if nxt else cur[1], "next_close": cur[1],
                    "session_min": (cur[1] - cur[0]).total_seconds() / 60}
        return {"is_open": False, "now": now, "next_open": nxt[0] if nxt else now + timedelta(hours=12),
                "next_close": nxt[1] if nxt else now + timedelta(hours=20),
                "session_min": ((nxt[1] - nxt[0]).total_seconds() / 60) if nxt else 390}

    # --- zlecenia
    def open_orders(self, symbol=None):
        trades = self.conn.call(lambda ib: list(ib.openTrades()))
        out = [{"id": str(t.order.orderId), "symbol": key_for(t.contract), "side": t.order.action.lower(),
                "type": t.order.orderType.lower()} for t in trades]
        return [o for o in out if symbol is None or o["symbol"].upper() == symbol.upper()]

    def cancel(self, order_id):
        def do(ib):
            for t in ib.openTrades():
                if str(t.order.orderId) == str(order_id):
                    ib.cancelOrder(t.order)
        self.conn.call(do)

    def buy_bracket(self, symbol, qty, stop, take, overnight):
        from ib_async import LimitOrder, MarketOrder, StopOrder
        c = self.contract(symbol)
        tif = "GTC" if overnight else "DAY"
        sl = self.round_price(symbol, stop, down=True)
        tp = self.round_price(symbol, take, down=False)

        def do(ib):
            parent = MarketOrder("BUY", qty, orderId=ib.client.getReqId(), tif=tif, transmit=False)
            tpo = LimitOrder("SELL", qty, tp, orderId=ib.client.getReqId(), parentId=parent.orderId, tif="GTC",
                             transmit=False)
            slo = StopOrder("SELL", qty, sl, orderId=ib.client.getReqId(), parentId=parent.orderId, tif="GTC",
                            transmit=True)
            oca = f"ta{parent.orderId}"
            for o in (tpo, slo):
                o.ocaGroup, o.ocaType = oca, 1
            for o in (parent, tpo, slo):
                ib.placeOrder(c, o)
            return parent.orderId
        oid = self.conn.call(do)
        bad = ("Cancelled", "ApiCancelled", "Inactive", "ValidationError")
        for _ in range(30):                                  # czekamy na przyjecie przez IB (albo odrzucenie)
            time.sleep(0.5)
            info = self.conn.call(lambda ib: [(t.orderStatus.status, [e.message for e in t.log if e.message])
                                              for t in ib.trades() if t.order.orderId == oid])
            if not info:
                continue
            st, msgs = info[0]
            if st in bad:
                why = msgs[-1] if msgs else st
                if "Read-Only" in why:
                    why = "bramka IB Gateway jest w trybie tylko do odczytu (READ_ONLY_API=no w jej docker-compose.yml)"
                raise IBKRError(f"IBKR odrzucił zlecenie {symbol}: {why}")
            if st in ("Submitted", "PreSubmitted", "Filled"):
                return
        raise IBKRError(f"IBKR nie potwierdził zlecenia {symbol} w 15 s - sprawdź bramkę")

    def buy_notional(self, symbol, notional):
        raise IBKRError("Krypto przez IBKR nie jest obsługiwane — użyj Krakena albo Alpaki.")

    def sell_stop_limit(self, symbol, qty, stop, limit):
        raise IBKRError("Krypto przez IBKR nie jest obsługiwane — użyj Krakena albo Alpaki.")

    def close_position(self, symbol):
        from ib_async import MarketOrder
        pos = self.positions().get(symbol.upper())
        if not pos:
            return None
        for o in self.open_orders(symbol):                    # najpierw zdejmujemy TP/SL tej pozycji
            try:
                self.cancel(o["id"])
            except Exception:
                pass
        c = self.contract(symbol)
        qty = abs(pos["qty"])
        side = "SELL" if pos["qty"] > 0 else "BUY"
        self.conn.call(lambda ib: ib.placeOrder(c, MarketOrder(side, qty)))
        return {"qty": pos["qty"], "price": pos["price"]}
