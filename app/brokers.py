"""
Adaptery brokerow. Silnik botow zna tylko ten interfejs, wiec dodanie IBKR
to nowa klasa implementujaca te same metody - bez zmian w strategiach i panelu.

Wszystkie metody zwracaja zwykle slowniki (nie obiekty SDK).
Symbole pozycji sa znormalizowane: 'BTC/USD' -> 'BTCUSD'.
"""

import json
import os
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

from .config import DATA_DIR
from .data import AlpacaData, SimData, is_crypto, norm


class BrokerError(Exception):
    pass


class Broker:
    name = ""
    data = None

    def account(self): ...
    def positions(self): ...
    def clock(self): ...
    def open_orders(self, symbol=None): ...
    def cancel(self, order_id): ...
    def buy_bracket(self, symbol, qty, stop, take, overnight): ...
    def buy_notional(self, symbol, notional): ...
    def sell_stop_limit(self, symbol, qty, stop, limit): ...
    def close_position(self, symbol): ...


# ------------------------------------------------------------------ Alpaca
class AlpacaBroker(Broker):
    def __init__(self, acc):
        from alpaca.trading.client import TradingClient
        if not acc.key or not acc.secret:
            raise BrokerError(f"Konto '{acc.name}': brak klucza lub sekretu w .env")
        self.name = acc.name
        self.client = TradingClient(acc.key, acc.secret, paper=acc.paper)
        self.data = AlpacaData(acc.key, acc.secret, acc.paper)

    def account(self):
        a = self.client.get_account()
        return {"equity": float(a.equity), "cash": float(a.cash), "buying_power": float(a.buying_power),
                "crypto_buying_power": float(a.non_marginable_buying_power or a.cash),
                "last_equity": float(a.last_equity or a.equity)}

    def positions(self):
        out = {}
        for p in self.client.get_all_positions():
            qa = getattr(p, "qty_available", None)
            out[norm(p.symbol)] = {
                "symbol": p.symbol, "qty": float(p.qty), "qty_available": float(qa) if qa is not None else float(p.qty),
                "avg_entry": float(p.avg_entry_price), "price": float(p.current_price),
                "market_value": float(p.market_value), "unrealized": float(p.unrealized_pl)}
        return out

    def clock(self):
        c = self.client.get_clock()
        return {"is_open": c.is_open, "now": c.timestamp, "next_open": c.next_open, "next_close": c.next_close}

    def open_orders(self, symbol=None):
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums import QueryOrderStatus
        orders = self.client.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN, limit=500, nested=False))
        out = [{"id": str(o.id), "symbol": o.symbol, "side": str(getattr(o.side, "value", o.side)).lower(),
                "type": str(getattr(o.order_type, "value", o.order_type)).lower()} for o in orders]
        return [o for o in out if symbol is None or norm(o["symbol"]) == norm(symbol)]

    def cancel(self, order_id):
        self.client.cancel_order_by_id(order_id)

    def buy_bracket(self, symbol, qty, stop, take, overnight):
        from alpaca.trading.requests import MarketOrderRequest, TakeProfitRequest, StopLossRequest
        from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass
        self.client.submit_order(MarketOrderRequest(
            symbol=symbol, qty=qty, side=OrderSide.BUY,
            time_in_force=TimeInForce.GTC if overnight else TimeInForce.DAY,
            order_class=OrderClass.BRACKET,
            take_profit=TakeProfitRequest(limit_price=round(take, 2)),
            stop_loss=StopLossRequest(stop_price=round(stop, 2))))

    def buy_notional(self, symbol, notional):
        """Zakup krypto za kwote; czeka na realizacje i zwraca (cena, ilosc po oplacie)."""
        from alpaca.trading.requests import MarketOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce
        o = self.client.submit_order(MarketOrderRequest(
            symbol=symbol, notional=round(notional, 2), side=OrderSide.BUY, time_in_force=TimeInForce.GTC))
        for _ in range(30):
            o = self.client.get_order_by_id(o.id)
            st = str(getattr(o.status, "value", o.status)).lower()
            if st == "filled":
                time.sleep(1)
                pos = self.positions().get(norm(symbol))
                qty = pos["qty_available"] if pos else float(o.filled_qty)
                return float(o.filled_avg_price), qty
            if st in ("canceled", "rejected", "expired"):
                raise BrokerError(f"zlecenie {st}")
            time.sleep(1)
        raise BrokerError("zlecenie nie zrealizowalo sie w 30 s")

    supports_fractional = True

    def buy_fractional(self, symbol, notional):
        """Akcje USA za kwote (ulamek akcji). Alpaca nie przyjmuje bracket na ulamki - SL/TP pilnuje bot.
        Zwraca (cena, ilosc)."""
        from alpaca.trading.requests import MarketOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce
        try:
            asset = self.client.get_asset(symbol)
        except Exception as e:
            raise BrokerError(f"{symbol}: nie znam tej spółki w Alpace ({e})")
        if not getattr(asset, "fractionable", False):
            raise BrokerError(f"{symbol}: Alpaca nie pozwala kupować ułamków tej spółki")
        if notional < 1:
            raise BrokerError("kwota ponizej 1 USD (minimum Alpaki)")
        o = self.client.submit_order(MarketOrderRequest(
            symbol=symbol, notional=round(notional, 2), side=OrderSide.BUY, time_in_force=TimeInForce.DAY))
        for _ in range(30):
            o = self.client.get_order_by_id(o.id)
            st = str(getattr(o.status, "value", o.status)).lower()
            if st == "filled":
                return float(o.filled_avg_price), float(o.filled_qty)
            if st in ("canceled", "rejected", "expired"):
                raise BrokerError(f"zlecenie {st}")
            time.sleep(1)
        raise BrokerError("zlecenie nie zrealizowalo sie w 30 s")

    def sell_stop_limit(self, symbol, qty, stop, limit):
        from alpaca.trading.requests import StopLimitOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce
        nd = 6 if stop < 1 else 2
        qty = int(qty * 1e9) / 1e9          # Alpaca przyjmuje max 9 miejsc po przecinku
        self.client.submit_order(StopLimitOrderRequest(
            symbol=symbol, qty=qty, side=OrderSide.SELL, time_in_force=TimeInForce.GTC,
            stop_price=round(stop, nd), limit_price=round(limit, nd)))

    def close_position(self, symbol):
        pos = self.positions().get(norm(symbol))
        if not pos:
            return None
        self.client.close_position(norm(symbol))
        return {"qty": pos["qty"], "price": pos["price"]}


# ------------------------------------------------------------------ IBKR (miejsce na przyszlosc)
def IBKRBroker(acc):
    from .ibkr import IBKRBroker as _IB, IBKRError
    try:
        return _IB(acc)
    except IBKRError as e:
        raise BrokerError(str(e))


# ------------------------------------------------------------------ Symulator (konto demo)
class SimBroker(Broker):
    """
    Konto papierowe w pamieci, na danych SimData. Obsluguje zlecenia bracket,
    stop-limit i sesje gieldowa akcji. Stan zapisywany do pliku, przezywa restart.
    """
    START_CASH = 100_000.0

    def __init__(self, acc):
        self.name = acc.name
        if acc.extra.get("sim_start"):                  # konto symulatora dodane w panelu z wlasnym kapitalem
            self.START_CASH = float(acc.extra["sim_start"])
        self.data = SimData()
        self.lock = threading.RLock()
        self.path = os.path.join(DATA_DIR, f"sim_{acc.name}.json")
        self.state = {"cash": self.START_CASH, "positions": {}, "orders": []}
        if os.path.exists(self.path):
            with open(self.path, encoding="utf-8") as f:
                self.state = json.load(f)

    def _save(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.state, f)

    def _price(self, symbol):
        return self.data.price(symbol)

    def _market_open(self, now=None):
        now = now or datetime.now(timezone.utc)
        mins = now.hour * 60 + now.minute
        return now.weekday() < 5 and 13 * 60 + 30 <= mins < 20 * 60

    def _process(self):
        """Realizacja zlecen oczekujacych po biezacej cenie."""
        with self.lock:
            remaining = []
            for o in self.state["orders"]:
                sym = o["symbol"]
                if not is_crypto(sym) and not self._market_open():
                    remaining.append(o)
                    continue
                price = self._price(sym)
                pos = self.state["positions"].get(norm(sym))
                hit = False
                if pos and o["type"] == "stop" and price <= o["stop"]:
                    fill, hit = price, True
                elif pos and o["type"] == "stop_limit" and o["limit"] <= price <= o["stop"]:
                    fill, hit = price, True        # jak na gieldzie: przy luce ponizej limitu nie wykona sie
                elif pos and o["type"] == "limit" and price >= o["limit"]:
                    fill, hit = price, True
                if hit:
                    qty = min(o["qty"], pos["qty"])
                    self._sell(sym, qty, fill)
                    if o.get("oco"):   # druga noga bracketu znika
                        self.state["orders"] = [x for x in self.state["orders"] if x.get("oco") != o["oco"]]
                        remaining = [x for x in remaining if x.get("oco") != o["oco"]]
                else:
                    remaining.append(o)
            self.state["orders"] = [o for o in remaining if norm(o["symbol"]) in self.state["positions"]]
            self._save()

    def _buy(self, symbol, qty, price):
        cost = qty * price * (1.0015 if is_crypto(symbol) else 1)
        if cost > self.state["cash"] + 1e-6:
            raise BrokerError("insufficient buying power")
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
        self.state["cash"] += qty * price
        p["qty"] -= qty
        if p["qty"] <= 1e-9:
            del self.state["positions"][s]

    # --- interfejs
    def account(self):
        self._process()
        pos = self.positions()
        mv = sum(p["market_value"] for p in pos.values())
        eq = self.state["cash"] + mv
        return {"equity": eq, "cash": self.state["cash"], "buying_power": self.state["cash"],
                "crypto_buying_power": self.state["cash"], "last_equity": eq}

    def positions(self):
        out = {}
        with self.lock:
            for s, p in self.state["positions"].items():
                price = self._price(p["symbol"])
                reserved = sum(o["qty"] for o in self.state["orders"]
                               if norm(o["symbol"]) == s and o["type"] in ("stop", "stop_limit"))
                out[s] = {"symbol": p["symbol"], "qty": p["qty"], "qty_available": max(0.0, p["qty"] - reserved),
                          "avg_entry": p["avg_entry"], "price": price, "market_value": p["qty"] * price,
                          "unrealized": (price - p["avg_entry"]) * p["qty"]}
        return out

    def clock(self):
        now = datetime.now(timezone.utc)
        is_open = self._market_open(now)
        day = now.replace(hour=13, minute=30, second=0, microsecond=0)
        nxt = day if now < day else day + timedelta(days=1)
        while nxt.weekday() >= 5:
            nxt += timedelta(days=1)
        close = now.replace(hour=20, minute=0, second=0, microsecond=0)
        return {"is_open": is_open, "now": now, "next_open": nxt,
                "next_close": close if is_open else nxt.replace(hour=20, minute=0)}

    def open_orders(self, symbol=None):
        self._process()
        return [{"id": o["id"], "symbol": o["symbol"], "side": "sell", "type": o["type"]}
                for o in self.state["orders"] if symbol is None or norm(o["symbol"]) == norm(symbol)]

    def cancel(self, order_id):
        with self.lock:
            self.state["orders"] = [o for o in self.state["orders"] if o["id"] != order_id]
            self._save()

    def buy_bracket(self, symbol, qty, stop, take, overnight):
        if not self._market_open():
            raise BrokerError("rynek zamkniety")
        with self.lock:
            self._buy(symbol, qty, self._price(symbol))
            oco = str(uuid.uuid4())
            self.state["orders"] += [
                {"id": str(uuid.uuid4()), "symbol": symbol, "type": "stop", "qty": qty, "stop": stop, "oco": oco},
                {"id": str(uuid.uuid4()), "symbol": symbol, "type": "limit", "qty": qty, "limit": take, "oco": oco}]
            self._save()

    supports_fractional = True

    def buy_fractional(self, symbol, notional):
        if not is_crypto(symbol) and not self._market_open():
            raise BrokerError("rynek zamkniety")
        return self.buy_notional(symbol, notional)

    def buy_notional(self, symbol, notional):
        with self.lock:
            price = self._price(symbol)
            qty = notional / price
            self._buy(symbol, qty, price)
            self._save()
            return price, qty

    def sell_stop_limit(self, symbol, qty, stop, limit):
        with self.lock:
            self.state["orders"].append({"id": str(uuid.uuid4()), "symbol": symbol, "type": "stop_limit",
                                         "qty": qty, "stop": stop, "limit": limit})
            self._save()

    def close_position(self, symbol):
        with self.lock:
            s = norm(symbol)
            p = self.state["positions"].get(s)
            if not p:
                return None
            price = self._price(p["symbol"])
            qty = p["qty"]
            self._sell(p["symbol"], qty, price)
            self.state["orders"] = [o for o in self.state["orders"] if norm(o["symbol"]) != s]
            self._save()
            return {"qty": qty, "price": price}


def _kraken(acc):
    from .kraken import KrakenBroker, paper_broker_class
    return paper_broker_class()(acc) if acc.paper else KrakenBroker(acc)


BROKER_TYPES = {"alpaca": AlpacaBroker, "sim": SimBroker, "ibkr": IBKRBroker, "kraken": _kraken, "gielda": _kraken}
_cache = {}
_cache_lock = threading.Lock()


def get_broker(acc):
    """Jedna instancja brokera na konto (wspoldzielona przez boty tego konta)."""
    with _cache_lock:
        if acc.name not in _cache:
            cls = BROKER_TYPES.get(acc.type)
            if not cls:
                raise BrokerError(f"Nieznany typ konta: {acc.type}")
            _cache[acc.name] = cls(acc)
        return _cache[acc.name]
