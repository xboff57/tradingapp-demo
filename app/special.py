"""
Boty bez sygnałów ze świec: siatka (grid) i uśrednianie (DCA).

Siatka: przedział ± X% wokół ceny startowej podzielony na poziomy L0 < L1 < ... < Ln. Między sąsiednimi
poziomami jest „miejsce” na jedną porcję: kupiona, gdy cena zejdzie do Li, sprzedana, gdy dojdzie do L(i+1).
Na starcie bot od razu kupuje porcje dla poziomów nad ceną (żeby miał co sprzedawać, gdy cena rośnie) —
tak działa większość giełdowych botów siatkowych. Stop pod siatką sprzedaje wszystko.

DCA regularne: zakup za stałą kwotę co X godzin (opcjonalnie sprzedaż całości przy zysku Y% nad średnią).
DCA na spadkach: pierwszy zakup, potem dokupienia co X% spadku (każde × mnożnik), sprzedaż całości przy
zysku Y% nad średnią i start od nowa; opcjonalny stop pod średnią.

Zlecenia po rynku, pilnuje ich bot co cykl (brak zleceń ochronnych na serwerze). Stan w tabeli bot_state.
Ta sama logika (funkcje grid_levels, dca_*) jest używana w backteście.
"""

import time
from datetime import datetime, timedelta, timezone

from .data import norm


# ------------------------------------------------------------------ wspolne obliczenia (bot i backtest)
def grid_levels(center, p):
    n = int(p["grid_levels"])
    rng = float(p["grid_range_pct"])
    lo, hi = center * (1 - rng), center * (1 + rng)
    if p.get("grid_geometric", True):
        r = (hi / lo) ** (1 / (n - 1))
        return [lo * r ** i for i in range(n)]
    step = (hi - lo) / (n - 1)
    return [lo + step * i for i in range(n)]


def new_grid(center, p, budget):
    lv = grid_levels(center, p)
    return {"levels": lv, "slot_value": budget / (len(lv) - 1), "held": {}, "center": center, "stopped": False,
            "realized": 0.0}


def dca_next_trigger(st, p):
    return st["last_buy"] * (1 - float(p["dca_step_pct"]))


def dca_avg(st):
    return st["cost"] / st["qty"] if st["qty"] else 0.0


# ------------------------------------------------------------------ bot na zywo
class SpecialRunner:
    def __init__(self, runner):
        self.r = runner
        self.p = runner.p
        self.db = runner.db

    def log(self, msg, level="INFO"):
        self.r.log(msg, level)

    def price(self, sym, positions):
        pos = positions.get(norm(sym))
        if pos and pos.get("price"):
            return float(pos["price"])
        d = self.r.broker.data
        if hasattr(d, "price"):
            try:
                return float(d.price(sym))
            except Exception:
                pass
        bars = d.bars([sym], "1Min", datetime.now(timezone.utc) - timedelta(days=4)).get(sym)
        if bars is None or bars.empty:
            raise ValueError(f"{sym}: brak aktualnej ceny")
        return float(bars["close"].iloc[-1])

    def buy(self, sym, value, price, why):
        """Kupno za kwotę (w walucie konta). Zwraca (cena, ilość) albo None."""
        b = self.r.broker
        if value < 5:
            return None
        if self.r.market == "crypto":
            fill, qty = b.buy_notional(sym, value)
        elif getattr(b, "supports_fractional", False):
            fill, qty = b.buy_fractional(sym, value)
        else:
            fx = b.fx_for(sym) if hasattr(b, "fx_for") else 1.0
            n = int(value // (price * fx))
            if n < 1:
                self.log(f"{sym}: {why} — kwota {value:,.2f} nie wystarcza na 1 akcję ({price * fx:,.2f}).")
                return None
            fill, qty = b.buy_qty(sym, n)
        self.db.add_trade(self.r.id, sym, "BUY", qty, fill, why)
        return fill, qty

    def sell(self, sym, qty, why, cost_basis):
        fill, q = self.r.broker.sell_qty(sym, qty)
        pnl = (fill - cost_basis) * q
        pct = (fill / cost_basis - 1) * 100 if cost_basis else None
        self.db.add_trade(self.r.id, sym, "SELL", q, fill, why, pnl, pct)
        return fill, q, pnl

    def sync_state(self, sym, qty, avg):
        if qty > 1e-12:
            self.db.set_pos_state(self.r.id, norm(sym), avg, qty, None, None)
        else:
            self.db.del_pos_state(self.r.id, norm(sym))

    def cycle(self, acct, positions, budget):
        syms = self.r.symbols
        per = budget / max(len(syms), 1)
        buys_ok, why = True, ""
        from . import risk
        ok, why = risk.entries_allowed(self.r.acc, acct)
        buys_ok = ok
        if not ok and self.r.risk_logged != why:
            self.r.risk_logged = why
            self.log(f"Zakupy wstrzymane: {why}. Sprzedaże działają.", "WARNING")
        elif ok:
            self.r.risk_logged = None
        for sym in syms:
            try:
                price = self.price(sym, positions)
                if self.r.strategy.key == "grid":
                    self.grid_step(sym, price, per, buys_ok)
                else:
                    self.dca_step(sym, price, per, buys_ok)
            except Exception as e:
                self.log(f"{sym}: {e}", "WARNING")

    # --- siatka
    def grid_step(self, sym, price, per, buys_ok):
        p = self.p
        st = self.db.get_state(self.r.id, sym)
        cfg = [int(p["grid_levels"]), float(p["grid_range_pct"]), bool(p.get("grid_geometric", True)), float(p["allocation_pct"]), len(self.r.symbols)]
        if st and st.get("cfg") != cfg and "cfg" in st:
            if not st["held"]:
                self.log(f"{sym}: zmienione ustawienia siatki — buduję ją od nowa.")
                st = None
            elif not st.get("cfg_note"):
                st["cfg_note"] = True
                self.log(f"{sym}: nowe ustawienia siatki wejdą, gdy bot sprzeda trzymane porcje (albo zatrzymaj bota "
                         f"z zamknięciem pozycji).")
        if not st:
            st = new_grid(price, p, per)
            st["cfg"] = cfg
            self.log(f"{sym}: nowa siatka {st['levels'][0]:,.4g}–{st['levels'][-1]:,.4g} "
                     f"({len(st['levels'])} poziomów, porcja ~{st['slot_value']:,.2f}).")
        lv = st["levels"]
        held = st["held"]                                     # "i" -> {"qty", "price"}
        # stop pod siatka
        stop = float(p.get("grid_stop_pct") or 0)
        if stop and not st["stopped"] and price < lv[0] * (1 - stop):
            for k in list(held):
                h = held.pop(k)
                fill, q, pnl = self.sell(sym, h["qty"], "siatka: stop pod siatką", h["price"])
                st["realized"] += pnl
            st["stopped"] = True
            self.log(f"{sym}: cena {price:,.4g} spadła {stop:.0%} pod siatkę — sprzedane wszystko, siatka wstrzymana.",
                     "WARNING")
        if st["stopped"]:
            if p.get("grid_recenter") and buys_ok:
                st = dict(new_grid(price, p, per), cfg=cfg)
                self.log(f"{sym}: siatka przesunięta po stopie: {st['levels'][0]:,.4g}–{st['levels'][-1]:,.4g}.")
            else:
                self.db.set_state(self.r.id, sym, st)
                return
        lv, held = st["levels"], st["held"]
        # przesuniecie w gore: cena nad siatka i nic nie trzymamy
        if price > lv[-1] and not held and p.get("grid_recenter") and buys_ok:
            st = dict(new_grid(price, p, per), cfg=cfg)
            lv, held = st["levels"], st["held"]
            self.log(f"{sym}: cena wyszła ponad siatkę — nowa siatka {lv[0]:,.4g}–{lv[-1]:,.4g}.")
        # sprzedaze: porcja kupiona na Li sprzedawana na L(i+1)
        for k in sorted(held, key=int):
            i = int(k)
            if price >= lv[i + 1]:
                h = held.pop(k)
                fill, q, pnl = self.sell(sym, h["qty"], f"siatka: sprzedaż na poziomie {i + 2}/{len(lv)}", h["price"])
                st["realized"] += pnl
        # zakupy: kazdy wolny poziom Li >= cena (cena zeszla do poziomu albo nizej)
        if buys_ok:
            for i in range(len(lv) - 1):
                if str(i) in held or price > lv[i] or price >= lv[i + 1]:
                    continue
                res = self.buy(sym, st["slot_value"], price, f"siatka: zakup na poziomie {i + 1}/{len(lv)}")
                if res:
                    held[str(i)] = {"qty": res[1], "price": res[0]}
        qty = sum(h["qty"] for h in held.values())
        cost = sum(h["qty"] * h["price"] for h in held.values())
        self.sync_state(sym, qty, cost / qty if qty else 0)
        self.db.set_state(self.r.id, sym, st)

    # --- usrednianie
    def dca_step(self, sym, price, per, buys_ok):
        p = self.p
        st = self.db.get_state(self.r.id, sym) or {"qty": 0.0, "cost": 0.0, "n_safety": 0, "last_buy": None,
                                                  "next_t": 0, "last_order": 0.0, "realized": 0.0, "rounds": 0}
        now = time.time()
        avg = dca_avg(st)
        tp, stop = float(p.get("dca_tp_pct") or 0), float(p.get("dca_stop_pct") or 0)
        if st["qty"] > 0 and tp and price >= avg * (1 + tp):
            fill, q, pnl = self.sell(sym, st["qty"], f"uśrednianie: zysk {tp:.1%} nad średnią", avg)
            st.update(qty=0.0, cost=0.0, n_safety=0, last_buy=None, realized=st["realized"] + pnl,
                      rounds=st["rounds"] + 1, next_t=0)
            self.log(f"{sym}: sprzedaż całości ~{fill:,.4g} (średnia {avg:,.4g}) | wynik {pnl:+,.2f}")
        elif st["qty"] > 0 and stop and price <= avg * (1 - stop):
            fill, q, pnl = self.sell(sym, st["qty"], f"uśrednianie: stop {stop:.0%} pod średnią", avg)
            st.update(qty=0.0, cost=0.0, n_safety=0, last_buy=None, realized=st["realized"] + pnl,
                      next_t=now + 24 * 3600)
            self.log(f"{sym}: stop pod średnią — sprzedane ~{fill:,.4g} | wynik {pnl:+,.2f}. Nowa runda za 24 h.",
                     "WARNING")
        spent = st["cost"]
        if buys_ok:
            base = per * float(p["dca_order_pct"])
            if p["dca_mode"] == "regular":
                if now >= st["next_t"] and spent + base <= per * 1.0001:
                    res = self.buy(sym, base, price, "uśrednianie: regularny zakup")
                    if res:
                        st["qty"] += res[1]
                        st["cost"] += res[0] * res[1]
                        st["last_buy"] = res[0]
                        self.log(f"{sym}: regularny zakup ~{res[0]:,.4g} za {res[0] * res[1]:,.2f} "
                                 f"(średnia {dca_avg(st):,.4g})")
                    st["next_t"] = now + int(p["dca_every_hours"]) * 3600
                elif spent + base > per * 1.0001 and st.get("budget_note") != "full":
                    st["budget_note"] = "full"
                    self.log(f"{sym}: budżet na regularne zakupy wykorzystany — dalej tylko trzymanie.")
            else:
                if st["qty"] <= 0 and now >= st["next_t"]:
                    res = self.buy(sym, base, price, "uśrednianie: pierwszy zakup")
                    if res:
                        st.update(qty=res[1], cost=res[0] * res[1], last_buy=res[0], n_safety=0, last_order=base)
                elif st["qty"] > 0 and st["n_safety"] < int(p["dca_max_safety"]) and price <= dca_next_trigger(st, p):
                    amt = st["last_order"] * float(p["dca_mult"])
                    if spent + amt > per * 1.0001:
                        amt = per - spent
                    if amt >= 5:
                        res = self.buy(sym, amt, price, f"uśrednianie: dokupienie {st['n_safety'] + 1}")
                        if res:
                            st["qty"] += res[1]
                            st["cost"] += res[0] * res[1]
                            st["last_buy"] = res[0]
                            st["last_order"] = amt
                            st["n_safety"] += 1
                            self.log(f"{sym}: dokupienie {st['n_safety']} ~{res[0]:,.4g} (średnia {dca_avg(st):,.4g})")
        self.sync_state(sym, st["qty"], dca_avg(st))
        self.db.set_state(self.r.id, sym, st)


# ------------------------------------------------------------------ backtest
def simulate_special(ctx, strategy, p, bars, progress=lambda x: None):
    """Symulacja siatki / DCA na świecach: zakupy i sprzedaże po cenach poziomów (high/low świecy)."""
    import pandas as pd
    cost = ctx.cost
    syms = [s for s in p["symbols"] if s in bars and not bars[s].empty]
    if not syms:
        raise ValueError("Brak danych dla wybranych symboli i okresu.")
    per = ctx.capital * p["allocation_pct"] / len(syms)
    cash = ctx.capital
    trades, holdings, last = [], {}, {}
    timeline = sorted(set().union(*[set(bars[s][bars[s].index >= ctx.start].index) for s in syms]))
    rows = {s: bars[s][bars[s].index >= ctx.start].to_dict("index") for s in syms}
    state = {}
    curve, exposure = [], 0

    def buy(sym, value, px, ts):
        nonlocal cash
        qty = value / px
        fee = value * cost
        cash -= value + fee
        return qty, fee

    def sell(sym, qty, px, basis, ts, t_in, reason):
        nonlocal cash
        fee = qty * px * cost
        cash += qty * px - fee
        pnl = (px - basis) * qty - fee - qty * basis * cost
        trades.append({"symbol": sym, "t_in": t_in.isoformat() if t_in else ts.isoformat(), "t_out": ts.isoformat(),
                       "entry": basis, "exit": px, "qty": qty, "pnl": pnl, "gross": (px - basis) * qty,
                       "fees": fee + qty * basis * cost, "pnl_pct": (px / basis - 1) * 100 if basis else 0,
                       "reason": reason, "hours": ((ts - t_in).total_seconds() / 3600) if t_in else 0, "side": "long"})
        return pnl

    n = len(timeline)
    for k, ts in enumerate(timeline):
        if k % 2000 == 0:
            progress(k / n)
        for sym in syms:
            r = rows[sym].get(ts)
            if r is None:
                continue
            last[sym] = r["close"]
            if strategy == "grid":
                st = state.get(sym)
                if st is None:
                    st = state[sym] = dict(new_grid(r["open"], p, per), t={})
                lv, held = st["levels"], st["held"]
                stop = float(p.get("grid_stop_pct") or 0)
                if not st["stopped"] and stop and r["low"] < lv[0] * (1 - stop):
                    px = min(r["open"], lv[0] * (1 - stop))
                    for key in list(held):
                        h = held.pop(key)
                        sell(sym, h["qty"], px, h["price"], ts, st["t"].pop(key, None), "siatka: stop")
                    st["stopped"] = True
                if st["stopped"]:
                    if p.get("grid_recenter"):
                        st = state[sym] = dict(new_grid(r["close"], p, per), t={})
                    continue
                if r["close"] > lv[-1] and not held and p.get("grid_recenter"):
                    st = state[sym] = dict(new_grid(r["close"], p, per), t={})
                    lv, held = st["levels"], st["held"]
                for key in sorted(held, key=int):
                    i = int(key)
                    if r["high"] >= lv[i + 1]:
                        h = held.pop(key)
                        sell(sym, h["qty"], max(r["open"], lv[i + 1]), h["price"], ts, st["t"].pop(key, None),
                             "siatka: sprzedaż poziomu")
                for i in range(len(lv) - 1):
                    if str(i) in held or r["low"] > lv[i] or (k == 0 and False):
                        continue
                    px = min(r["open"], lv[i])
                    if px >= lv[i + 1]:
                        continue
                    if cash < st["slot_value"] * (1 + cost):
                        break
                    qty, _ = buy(sym, st["slot_value"], px, ts)
                    held[str(i)] = {"qty": qty, "price": px}
                    st["t"][str(i)] = ts
                holdings[sym] = sum(h["qty"] for h in held.values())
            else:
                st = state.setdefault(sym, {"qty": 0.0, "cost": 0.0, "n_safety": 0, "last_buy": None, "next_t": None,
                                            "last_order": 0.0, "t_in": None})
                avg = dca_avg(st)
                tp, stop = float(p.get("dca_tp_pct") or 0), float(p.get("dca_stop_pct") or 0)
                if st["qty"] > 0 and tp and r["high"] >= avg * (1 + tp):
                    sell(sym, st["qty"], max(r["open"], avg * (1 + tp)), avg, ts, st["t_in"], "uśrednianie: zysk")
                    st.update(qty=0.0, cost=0.0, n_safety=0, last_buy=None, t_in=None, next_t=None)
                elif st["qty"] > 0 and stop and r["low"] <= avg * (1 - stop):
                    sell(sym, st["qty"], min(r["open"], avg * (1 - stop)), avg, ts, st["t_in"], "uśrednianie: stop")
                    st.update(qty=0.0, cost=0.0, n_safety=0, last_buy=None, t_in=None, next_t=ts + timedelta(days=1))
                base = per * float(p["dca_order_pct"])
                if p["dca_mode"] == "regular":
                    if (st["next_t"] is None or ts >= st["next_t"]) and st["cost"] + base <= per * 1.0001 \
                            and cash >= base * (1 + cost):
                        qty, _ = buy(sym, base, r["close"], ts)
                        st["qty"] += qty
                        st["cost"] += base
                        st["t_in"] = st["t_in"] or ts
                        st["next_t"] = ts + timedelta(hours=int(p["dca_every_hours"]))
                else:
                    if st["qty"] <= 0 and (st["next_t"] is None or ts >= st["next_t"]) and cash >= base * (1 + cost):
                        qty, _ = buy(sym, base, r["close"], ts)
                        st.update(qty=qty, cost=base, last_buy=r["close"], n_safety=0, last_order=base, t_in=ts)
                    elif st["qty"] > 0 and st["n_safety"] < int(p["dca_max_safety"]):
                        trig = dca_next_trigger(st, p)
                        if r["low"] <= trig:
                            amt = min(st["last_order"] * float(p["dca_mult"]), per - st["cost"])
                            px = min(r["open"], trig)
                            if amt >= 5 and cash >= amt * (1 + cost):
                                qty, _ = buy(sym, amt, px, ts)
                                st["qty"] += qty
                                st["cost"] += amt
                                st["last_buy"] = px
                                st["last_order"] = amt
                                st["n_safety"] += 1
                holdings[sym] = st["qty"]
        if any(v > 0 for v in holdings.values()):
            exposure += 1
        curve.append((ts, cash + sum(q * last.get(s, 0) for s, q in holdings.items())))
    # zamkniecie na koniec testu (zeby wynik byl porownywalny)
    end = timeline[-1]
    for sym in syms:
        st = state.get(sym)
        if not st:
            continue
        if strategy == "grid":
            for key, h in list(st["held"].items()):
                sell(sym, h["qty"], last[sym], h["price"], end, st["t"].get(key), "koniec testu")
        elif st["qty"] > 0:
            sell(sym, st["qty"], last[sym], dca_avg(st), end, st["t_in"], "koniec testu")
    curve[-1] = (end, cash)
    eq = pd.Series([v for _, v in curve], index=pd.DatetimeIndex([t for t, _ in curve]))
    frames = {s: bars[s][bars[s].index >= ctx.start] for s in syms}
    return {"equity": eq, "trades": trades, "exposure_pct": exposure / max(n, 1) * 100, "frames": frames,
            "ext": {"active": False, "raw": 0, "blocked": 0, "per_symbol": {}}}
