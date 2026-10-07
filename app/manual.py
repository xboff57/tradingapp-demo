"""
Zlecenia ręczne: kupno albo sprzedaż kilku akcji (albo monet) jednym kliknięciem w panelu, bez bota.

Zasady bezpieczeństwa:
  - zlecenie po rynku, bez dźwigni (gotówka konta);
  - symbol, którym handluje bot na tym koncie (albo który bot trzyma), jest zablokowany — inaczej bot uznałby
    ręcznie kupione akcje za swoje i sam by je sprzedał (albo dokupił);
  - akcje: tylko w trakcie sesji (poza sesją broker trzymałby zlecenie do otwarcia po nieznanej cenie);
  - sprzedaż: najwyżej tyle, ile jest na koncie;
  - konto z prawdziwymi pieniędzmi: hasło do panelu (i kod 2FA, gdy włączony) przy każdym zleceniu — sprawdza main.py.
Każde zlecenie trafia do tabeli manual_orders (historia w zakładce Transakcje) i do powiadomień.
"""

import math
from datetime import datetime, timedelta, timezone

from .brokers import get_broker
from .config import CRYPTO_EX_TYPES
from .data import norm, is_crypto


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def kind(acc, symbol):
    """'crypto' albo 'stock' + walidacja formatu symbolu dla konta (None = OK)."""
    crypto = is_crypto(symbol)
    if acc.type in CRYPTO_EX_TYPES:
        q = acc.extra.get("quote", "EUR")
        if not crypto or not symbol.endswith("/" + q):
            return "crypto", f"Na koncie {acc.name} handlujesz parami w {q}, np. BTC/{q}."
        return "crypto", None
    if acc.type == "ibkr":
        if crypto:
            return "crypto", "Krypto przez IBKR nie jest obsługiwane — użyj Krakena, Binance albo Alpaki."
        from .ibkr import venue_error
        return "stock", venue_error([symbol])
    if acc.type == "alpaca" and crypto and not symbol.endswith("/USD"):
        return "crypto", "Alpaca: krypto tylko w parach z USD, np. BTC/USD."
    return ("crypto" if crypto else "stock"), None


def bot_lock(db, acc_name, symbol):
    """Nazwa bota, który handluje tym symbolem na tym koncie (albo go trzyma) — wtedy zlecenie ręczne jest blokowane."""
    from .strategies import full_params
    n = norm(symbol)
    for b in db.bots():
        if b["account"] != acc_name:
            continue
        p = full_params(b["market"], b["strategy"], b["params"])
        if n in {norm(s) for s in p.get("symbols") or []} or n in db.pos_states(b["id"]):
            return b["name"]
    return None


def last_price(broker, symbol, positions):
    pos = positions.get(norm(symbol))
    if pos and pos.get("price"):
        return float(pos["price"])
    d = getattr(broker, "data", None)
    if d is not None and hasattr(d, "price"):
        try:
            return float(d.price(symbol))
        except Exception:
            pass
    if d is not None:
        bars = d.bars([symbol], "1Min", datetime.now(timezone.utc) - timedelta(days=4)).get(symbol)
        if bars is not None and not bars.empty:
            return float(bars["close"].iloc[-1])
        bars = d.bars([symbol], "1Day", datetime.now(timezone.utc) - timedelta(days=10)).get(symbol)
        if bars is not None and not bars.empty:
            return float(bars["close"].iloc[-1])
    raise ValueError(f"{symbol}: brak aktualnej ceny")


def session(broker, symbol, k):
    if k == "crypto":
        return True, None
    try:
        c = broker.clock([symbol]) if getattr(broker, "per_symbol_clock", False) else broker.clock()
    except Exception as e:
        return False, f"nie udało się sprawdzić godzin sesji: {e}"
    if c.get("is_open"):
        return True, None
    nxt = c.get("next_open")
    when = ""
    if hasattr(nxt, "astimezone"):
        try:
            from zoneinfo import ZoneInfo
            pl = ZoneInfo("Europe/Warsaw")
        except Exception:
            pl = timezone(timedelta(hours=2))
        if nxt.tzinfo is None:
            nxt = nxt.replace(tzinfo=timezone.utc)
        when = f" — sesja od {nxt.astimezone(pl):%d.%m %H:%M} czasu polskiego"
    return False, "Rynek jest zamknięty" + when + "."


def quote(db, acc, symbol):
    """Podgląd przed zleceniem: cena, gotówka, posiadane sztuki, sesja, blokada przez bota."""
    from . import risk
    symbol = symbol.strip().upper()
    k, err = kind(acc, symbol)
    out = {"symbol": symbol, "account": acc.name, "kind": k, "real": risk.is_real(acc), "error": err,
           "fractional": k == "crypto" or acc.type == "alpaca", "whole_only": k == "stock" and acc.type != "alpaca"}
    if err:
        return out
    out["locked_by"] = bot_lock(db, acc.name, symbol)
    broker = get_broker(acc)
    a = broker.account()
    positions = broker.positions()
    out.update(cash=float(a.get("cash") or 0), currency=a.get("currency") or acc.extra.get("quote", "USD"),
               held=float((positions.get(norm(symbol)) or {}).get("qty") or 0))
    try:
        out["price"] = last_price(broker, symbol, positions)
    except Exception as e:
        out["error"] = str(e)
        return out
    out["open"], out["closed_note"] = session(broker, symbol, k)
    return out


def place(db, acc, symbol, side, qty=None, amount=None, notify=None):
    """Składa zlecenie po rynku. qty = sztuki (akcje / monety), amount = kwota (krypto, ułamki na Alpace)."""
    q = quote(db, acc, symbol)
    symbol = q["symbol"]
    if q.get("error"):
        raise ValueError(q["error"])
    if q.get("locked_by"):
        raise ValueError(f"Symbolem {symbol} na koncie {acc.name} handluje bot „{q['locked_by']}”. Ręczne zlecenie "
                         "pomieszałoby się z jego pozycją — kup na innym koncie albo usuń symbol z bota.")
    if not q.get("open"):
        raise ValueError(q.get("closed_note") or "Rynek jest zamknięty.")
    if side not in ("buy", "sell"):
        raise ValueError("Strona zlecenia: kupno albo sprzedaż.")
    price = q["price"]
    if amount and not qty:
        if not q["fractional"]:
            raise ValueError("Na tym koncie akcje kupuje się w całych sztukach — podaj liczbę sztuk.")
        qty = float(amount) / price
    qty = float(qty or 0)
    if q["whole_only"]:
        qty = math.floor(qty + 1e-9)
    if qty <= 0:
        raise ValueError("Podaj liczbę sztuk (albo kwotę) większą od zera.")
    value = qty * price
    if side == "buy" and value > q["cash"] * 0.995:
        raise ValueError(f"Za mało gotówki: zlecenie ~{value:,.2f} {q['currency']}, dostępne {q['cash']:,.2f}.")
    if side == "sell" and qty > q["held"] + 1e-9:
        raise ValueError(f"Na koncie jest {q['held']:g} {symbol} — tyle najwyżej możesz sprzedać.")
    broker = get_broker(acc)
    db.execute("INSERT INTO manual_orders(ts, account, symbol, side, qty, price, value, status) VALUES (?,?,?,?,?,?,?,?)",
               (now_iso(), acc.name, symbol, side, qty, price, value, "sent"))
    oid = db.one("SELECT max(id) AS id FROM manual_orders")["id"]
    try:
        if side == "buy":
            if q["kind"] == "crypto" and acc.type in CRYPTO_EX_TYPES:
                fill, filled = broker.buy_notional(symbol, value)
            elif acc.type == "alpaca" and (q["kind"] == "crypto" or qty != int(qty)):
                fill, filled = broker.buy_fractional(symbol, value) if q["kind"] == "stock" else broker.buy_notional(symbol, value)
            else:
                fill, filled = broker.buy_qty(symbol, qty)
        else:
            fill, filled = broker.sell_qty(symbol, qty)
    except Exception as e:
        db.execute("UPDATE manual_orders SET status='error', note=? WHERE id=?", (str(e)[:300], oid))
        raise ValueError(f"Broker odrzucił zlecenie: {e}")
    fill = float(fill or price)
    filled = float(filled or qty)
    db.execute("UPDATE manual_orders SET status='filled', price=?, qty=?, value=? WHERE id=?",
               (fill, filled, fill * filled, oid))
    px = f"{fill:,.2f}" if fill >= 1 else f"{fill:.6g}"
    text = (f"{'Kupno ręczne' if side == 'buy' else 'Sprzedaż ręczna'}: {filled:g} {symbol} po {px} {q['currency']} "
            f"(= {fill * filled:,.2f} {q['currency']}) na koncie {acc.name}" + (" — PRAWDZIWE PIENIĄDZE" if q["real"] else ""))
    if notify:
        notify("🖐 " + text)
    return {"ok": True, "id": oid, "price": fill, "qty": filled, "value": fill * filled, "text": text}


def history(db, limit=50):
    return db.all("SELECT * FROM manual_orders ORDER BY id DESC LIMIT ?", (limit,))
