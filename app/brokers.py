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
    # dzwignia / gra na spadki (opcjonalne):
    # short_bracket(symbol, qty, stop, take, overnight)  - krotka sprzedaz akcji ze stopem nad cena i TP pod cena
    # margin_open(symbol, side, notional, leverage) -> (cena, ilosc)  - krypto na marginesie (long/short)
    # margin_stop(symbol, side, qty, stop) -> "server" | "soft"


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
                "margin_buying_power": float(getattr(a, "regt_buying_power", None) or a.buying_power),
                "last_equity": float(a.last_equity or a.equity),
                "shorting_enabled": bool(getattr(a, "shorting_enabled", False))}

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

    def short_bracket(self, symbol, qty, stop, take, overnight):
        """Krotka sprzedaz (gra na spadek): sprzedaz pozyczonych akcji + stop-loss NAD cena i take-profit POD cena.
        Alpaca: tylko cale akcje, spolka musi byc 'shortable' i 'easy to borrow', konto z marginesem."""
        from alpaca.trading.requests import MarketOrderRequest, TakeProfitRequest, StopLossRequest
        from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass
        try:
            asset = self.client.get_asset(symbol)
        except Exception as e:
            raise BrokerError(f"{symbol}: nie znam tej spółki w Alpace ({e})")
        if not getattr(asset, "shortable", False) or not getattr(asset, "easy_to_borrow", False):
            raise BrokerError(f"{symbol}: Alpaca nie pozwala teraz grać na spadek tej spółki (brak akcji do pożyczenia)")
        self.client.submit_order(MarketOrderRequest(
            symbol=symbol, qty=int(qty), side=OrderSide.SELL,
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

    def _market_qty(self, symbol, qty, side):
        from alpaca.trading.requests import MarketOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce
        qty = int(qty * 1e9) / 1e9
        tif = TimeInForce.GTC if is_crypto(symbol) else TimeInForce.DAY
        o = self.client.submit_order(MarketOrderRequest(symbol=symbol, qty=qty,
                                                        side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
                                                        time_in_force=tif))
        for _ in range(30):
            o = self.client.get_order_by_id(o.id)
            st = str(getattr(o.status, "value", o.status)).lower()
            if st == "filled":
                return float(o.filled_avg_price), float(o.filled_qty)
            if st in ("canceled", "rejected", "expired"):
                raise BrokerError(f"zlecenie {st}")
            time.sleep(1)
        raise BrokerError("zlecenie nie zrealizowalo sie w 30 s")

    def buy_qty(self, symbol, qty):
        """Kupno po rynku bez zlecen ochronnych (siatka, DCA - pilnuje bot). Zwraca (cena, ilosc)."""
        return self._market_qty(symbol, qty, "buy")

    def sell_qty(self, symbol, qty):
        return self._market_qty(symbol, qty, "sell")

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

    MAX_LEV = {"stocks": 2.0, "crypto": 5.0}            # limit brutto: wartosc pozycji / kapital

    def _gross(self, extra=0.0, sym=None):
        with self.lock:
            tot = abs(extra)
            for p in self.state["positions"].values():
                tot += abs(p["qty"]) * self._price(p["symbol"])
            return tot

    def _equity_now(self):
        return self.state["cash"] + sum(p["qty"] * self._price(p["symbol"]) for p in self.state["positions"].values())

    def _check_gross(self, symbol, value):
        lim = self.MAX_LEV["crypto" if is_crypto(symbol) else "stocks"]
        if self._gross(value) > self._equity_now() * lim + 1e-6:
            raise BrokerError(f"za mało środków (limit dźwigni konta {lim:g}×)")

    def _process(self):
        """Realizacja zlecen oczekujacych po biezacej cenie (dlugie: stop/TP ponizej/powyzej; krotkie - lustrzanie)."""
        with self.lock:
            self._accrue()
            remaining = []
            for o in self.state["orders"]:
                sym = o["symbol"]
                if not is_crypto(sym) and not self._market_open():
                    remaining.append(o)
                    continue
                price = self._price(sym)
                pos = self.state["positions"].get(norm(sym))
                hit = False
                short = bool(pos and pos["qty"] < 0)
                if pos and o["type"] == "stop" and (price >= o["stop"] if short else price <= o["stop"]):
                    fill, hit = price, True
                elif pos and o["type"] == "stop_limit" and not short and o["limit"] <= price <= o["stop"]:
                    fill, hit = price, True        # jak na gieldzie: przy luce ponizej limitu nie wykona sie
                elif pos and o["type"] == "limit" and (price <= o["limit"] if short else price >= o["limit"]):
                    fill, hit = price, True
                if hit and short:
                    self._buy(sym, min(o["qty"], -pos["qty"]), fill)
                    if o.get("oco"):
                        self.state["orders"] = [x for x in self.state["orders"] if x.get("oco") != o["oco"]]
                        remaining = [x for x in remaining if x.get("oco") != o["oco"]]
                elif hit:
                    qty = min(o["qty"], pos["qty"])
                    self._sell(sym, qty, fill)
                    if o.get("oco"):   # druga noga bracketu znika
                        self.state["orders"] = [x for x in self.state["orders"] if x.get("oco") != o["oco"]]
                        remaining = [x for x in remaining if x.get("oco") != o["oco"]]
                else:
                    remaining.append(o)
            self.state["orders"] = [o for o in remaining if norm(o["symbol"]) in self.state["positions"]]
            self._save()

    FEE_RATE = None                                      # None = 0,15% krypto, 0 akcje

    def _fee(self, symbol):
        return self.FEE_RATE if self.FEE_RATE is not None else (0.0015 if is_crypto(symbol) else 0.0)

    MARGIN_RATE = 0.07                                   # odsetki od pozyczonej gotowki (ok. jak Alpaca), rocznie

    def _accrue(self):
        """Odsetki od pożyczonej gotówki (ujemne saldo) - naliczane za każdą pełną dobę."""
        now = time.time()
        last = self.state.get("int_t") or now
        days = int((now - last) // 86400)
        if days > 0:
            if self.state["cash"] < 0:
                self.state["cash"] += self.state["cash"] * self.MARGIN_RATE / 365 * days
            self.state["int_t"] = last + days * 86400
        elif "int_t" not in self.state:
            self.state["int_t"] = now

    def _buy(self, symbol, qty, price, margin=False):
        s = norm(symbol)
        p = self.state["positions"].get(s)
        fee = qty * price * self._fee(symbol)
        if p and p["qty"] < 0:                           # odkupienie pozyczonych (zamkniecie krotkiej pozycji)
            self.state["cash"] -= qty * price + fee
            p["qty"] += qty
            if abs(p["qty"]) <= 1e-9:
                del self.state["positions"][s]
            return
        cost = qty * price + fee
        if cost > self.state["cash"] + 1e-6:
            if not margin:
                raise BrokerError("insufficient buying power")
            self._check_gross(symbol, qty * price)
        self.state["cash"] -= cost
        if p:
            p["avg_entry"] = (p["avg_entry"] * p["qty"] + price * qty) / (p["qty"] + qty)
            p["qty"] += qty
        else:
            self.state["positions"][s] = {"symbol": symbol, "qty": qty, "avg_entry": price}
        if margin:
            self.state["positions"][s]["margin"] = True

    def _sell(self, symbol, qty, price, margin=False):
        s = norm(symbol)
        p = self.state["positions"].get(s)
        fee = qty * price * self._fee(symbol)
        if not p or p["qty"] < 0:                        # krotka sprzedaz (pozyczone akcje / monety)
            if not margin:
                raise BrokerError("brak pozycji do sprzedania")
            self._check_gross(symbol, qty * price)
            self.state["cash"] += qty * price - fee
            if p:
                p["avg_entry"] = (p["avg_entry"] * -p["qty"] + price * qty) / (-p["qty"] + qty)
                p["qty"] -= qty
            else:
                self.state["positions"][s] = {"symbol": symbol, "qty": -qty, "avg_entry": price, "margin": True}
            return
        self.state["cash"] += qty * price - fee
        p["qty"] -= qty
        if p["qty"] <= 1e-9:
            del self.state["positions"][s]

    # --- interfejs
    def account(self):
        self._process()
        pos = self.positions()
        mv = sum(p["market_value"] for p in pos.values())
        gross = sum(abs(p["market_value"]) for p in pos.values())
        eq = self.state["cash"] + mv
        day = datetime.now(timezone.utc).date().isoformat()
        if self.state.get("day") != day:                 # kapital na poczatku dnia (do dziennego limitu straty)
            self.state["day"], self.state["day_equity"] = day, eq
        lev = self.MAX_LEV["crypto"] if getattr(self, "quote", None) else self.MAX_LEV["stocks"]
        return {"equity": eq, "cash": self.state["cash"], "buying_power": max(0.0, self.state["cash"]),
                "crypto_buying_power": max(0.0, self.state["cash"]),
                "margin_buying_power": max(0.0, eq * lev - gross), "last_equity": self.state.get("day_equity", eq)}

    def positions(self):
        out = {}
        with self.lock:
            for s, p in self.state["positions"].items():
                price = self._price(p["symbol"])
                reserved = sum(o["qty"] for o in self.state["orders"]
                               if norm(o["symbol"]) == s and o["type"] in ("stop", "stop_limit"))
                out[s] = {"symbol": p["symbol"], "qty": p["qty"],
                          "qty_available": max(0.0, p["qty"] - reserved) if p["qty"] > 0 else p["qty"],
                          "avg_entry": p["avg_entry"], "price": price, "market_value": p["qty"] * price,
                          "unrealized": (price - p["avg_entry"]) * p["qty"], "margin": bool(p.get("margin"))}
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
        return [{"id": o["id"], "symbol": o["symbol"], "side": o.get("side", "sell"), "type": o["type"]}
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

    def short_bracket(self, symbol, qty, stop, take, overnight):
        if not self._market_open():
            raise BrokerError("rynek zamkniety")
        with self.lock:
            self._sell(symbol, qty, self._price(symbol), margin=True)
            oco = str(uuid.uuid4())
            self.state["orders"] += [
                {"id": str(uuid.uuid4()), "symbol": symbol, "type": "stop", "side": "buy", "qty": qty, "stop": stop,
                 "oco": oco},
                {"id": str(uuid.uuid4()), "symbol": symbol, "type": "limit", "side": "buy", "qty": qty, "limit": take,
                 "oco": oco}]
            self._save()

    def buy_qty(self, symbol, qty):
        if not is_crypto(symbol) and not self._market_open():
            raise BrokerError("rynek zamkniety")
        with self.lock:
            price = self._price(symbol)
            self._buy(symbol, qty, price)
            self._save()
            return price, qty

    def sell_qty(self, symbol, qty):
        if not is_crypto(symbol) and not self._market_open():
            raise BrokerError("rynek zamkniety")
        with self.lock:
            p = self.state["positions"].get(norm(symbol))
            if not p or p["qty"] <= 0:
                raise BrokerError("brak pozycji do sprzedania")
            qty = min(qty, p["qty"])
            price = self._price(symbol)
            self._sell(symbol, qty, price)
            self._save()
            return price, qty

    def margin_open(self, symbol, side, notional, leverage):
        """Pozycja na marginesie za kwote (wartosc pozycji). Zwraca (cena, ilosc)."""
        if not is_crypto(symbol) and not self._market_open():
            raise BrokerError("rynek zamkniety")
        with self.lock:
            price = self._price(symbol)
            qty = notional / price
            if side == "buy":
                self._buy(symbol, qty, price, margin=True)
            else:
                self._sell(symbol, qty, price, margin=True)
            self._save()
            return price, qty

    def margin_stop(self, symbol, side, qty, stop):
        """Stop na serwerze (tu: w symulatorze). side = strona zlecenia zamykajacego ('sell' dla long, 'buy' dla short)."""
        with self.lock:
            self.state["orders"].append({"id": str(uuid.uuid4()), "symbol": symbol, "type": "stop", "side": side,
                                         "qty": abs(qty), "stop": stop})
            self._save()
        return "server"

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
            if qty < 0:
                self._buy(p["symbol"], -qty, price)
            else:
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
