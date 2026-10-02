"""
Backtest na TYCH SAMYCH strategiach co boty na zywo.

Zalozenia (celowo ostrozne):
  - jeden wspolny portfel dla wszystkich symboli bota, z limitem pozycji i budzetem
  - wejscie po cenie zamkniecia swiecy z sygnalem (tak dziala bot na zywo)
  - stop-loss / take-profit sprawdzane na high/low kolejnych swiec;
    luka cenowa -> wyjscie po cenie otwarcia; SL i TP w tej samej swiecy -> liczymy SL
  - koszt transakcji (prowizja + poslizg) doliczany przy wejsciu i wyjsciu
  - filtr rezimu liczony z POPRZEDNIEGO dnia (bez zagladania w przyszlosc)
  - akcje: pelne sztuki; krypto: ulamki

Dane laduje Context (raz), a simulate() mozna wolac wiele razy z roznymi
parametrami - z tego korzysta modul propozycji poprawek (optimize.py).
Wyniki historyczne nie gwarantuja przyszlych.
"""

import json
import math
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from .data import cached_bars
from . import signals as ext
from . import ml
from . import context
from .rules import atr as atr_series
from .strategies import STRATEGIES, TIMEFRAME_MINUTES, full_params, sized_notional

MAX_DAYS = {"1Min": 60, "5Min": 240, "15Min": 730}
COPY_POOL_MAX = 400          # najwiecej spolek-kandydatow w backteście kopiowania


def period_error(timeframe, start, end):
    days = (end - start).days
    if days < 14:
        return "Okres testu musi miec co najmniej 14 dni."
    limit = MAX_DAYS.get(timeframe)
    if limit and days > limit:
        return f"Dla interwalu {timeframe} maksymalny okres to {limit} dni (ilosc danych)."
    return None


class Context:
    """Dane i ustawienia wspolne dla wszystkich symulacji jednego testu."""

    def __init__(self, provider, cfg, tag):
        self.provider = provider
        self.tag = tag
        self.market = cfg["market"]
        self.start = datetime.fromisoformat(cfg["start"]).replace(tzinfo=timezone.utc)
        self.end = datetime.fromisoformat(cfg["end"]).replace(tzinfo=timezone.utc)
        self.capital = float(cfg.get("initial_capital", 100_000))
        self.cost = float(cfg.get("cost_pct") if cfg.get("cost_pct") is not None
                          else (0.0025 if self.market == "crypto" else 0.0005))
        self._bars = {}
        self._universe = {}
        self._radar = {}
        self.quote = cfg.get("quote") or self._default_quote(tag)
        self._regime = {}
        self._signals = {}
        self._ext = {}

    @staticmethod
    def _default_quote(tag):
        if tag == "kraken":
            from .config import kraken_account
            acc = kraken_account()
            return acc.extra.get("quote", "EUR") if acc else "EUR"
        if tag.startswith("ccxt_"):
            from .config import exchange_account
            acc = exchange_account(tag[5:])
            return acc.extra.get("quote", "USDT") if acc else "USDT"
        return "USD"

    def expand(self, p):
        if "copy_managers" in p:
            return self.expand_copy(p)
        return self.expand_radar(p)

    def expand_copy(self, p):
        """Kopiowanie funduszy: kandydaci = wszystkie spolki, ktore sledzone fundusze dokupily w okresie testu.
        O wejsciu danego dnia decyduje stan raportow znany przed tym dniem (signals.apply_copy)."""
        if p.get("_copy") or self.market != "stocks":
            return p
        key = ("copy", p.get("copy_managers"), tuple(p["symbols"]))
        if key not in self._universe:
            src = ext.source_for(self.tag)
            managers = ext.managers_for(p)
            since = (self.start - timedelta(days=200)).date().isoformat()
            src.sync_funds(since, managers)
            changes = src.fund_changes(since, self.end.date().isoformat(), managers)
            pool = ext.SIM_COPY_TICKERS if self.tag == "sim" else ext.copy_pool(changes)
            self._universe[key] = list(dict.fromkeys(list(p["symbols"]) + pool[:COPY_POOL_MAX]))
        return dict(p, symbols=self._universe[key], _copy={"fixed": list(p["symbols"])})

    def expand_radar(self, p):
        """Tryb radaru: lista symboli = najplynniejsze monety gieldy (kandydaci), a o wejsciu decyduje
        cotygodniowy ranking liczony tylko z danych sprzed danego dnia."""
        if self.market != "crypto" or p.get("universe_mode") != "radar" or p.get("_radar"):
            return p
        key = (float(p.get("radar_min_volume", 0)), tuple(p["symbols"]))
        if key not in self._universe:
            uni = self.provider.universe(self.quote)
            pool = [s for s, v in uni if v >= key[0]][:30]
            self._universe[key] = list(dict.fromkeys(list(p["symbols"]) + pool))
        return dict(p, symbols=self._universe[key], _radar={"fixed": list(p["symbols"]), "top": int(p["radar_top"])})

    def radar_allowed(self, p):
        r = p["_radar"]
        key = (tuple(p["symbols"]), r["top"])
        if key not in self._radar:
            from .radar import weekly_allowed
            pre = timedelta(days=120)
            daily = cached_bars(self.provider, list(dict.fromkeys(p["symbols"] + [f"BTC/{self.quote}"])), "1Day",
                                self.start - pre, self.end, self.tag)
            btc = daily.get(f"BTC/{self.quote}")
            cand = {s: d for s, d in daily.items() if s in p["symbols"]}
            if btc is not None:
                cand[f"BTC/{self.quote}"] = btc
            self._radar[key] = weekly_allowed(cand, self.quote, self.start, self.end, r["top"])
        return self._radar[key]

    def pre_days(self, tf, bars_needed):
        per_day = 1440 if self.market == "crypto" else 390
        if TIMEFRAME_MINUTES[tf] >= 1440:
            return int(bars_needed * (1 if self.market == "crypto" else 1.5)) + 10
        return math.ceil(bars_needed * TIMEFRAME_MINUTES[tf] / per_day * 1.6) + 5

    def bars(self, tf, symbols, warmup):
        need = 200 if warmup <= 200 else 450          # staly zapas -> jeden plik cache na interwal
        key = (tf, need)
        have = self._bars.setdefault(key, {})
        missing = [s for s in symbols if s not in have]
        if missing:
            start = self.start - timedelta(days=self.pre_days(tf, need))
            have.update(cached_bars(self.provider, missing, tf, start, self.end, self.tag))
        return {s: have[s] for s in symbols if s in have}

    def regime(self, p):
        """{data: bool} - czy wejscia sa dozwolone danego dnia (wg zamkniecia dnia poprzedniego)."""
        fixed = (p.get("_copy") or p.get("_radar") or {}).get("fixed")
        if fixed is not None:
            p = dict(p, symbols=fixed)                 # szerokosc rynku = spolki wpisane w bota
        parts = context.parts(p)
        if not parts:
            return None
        uses_breadth = any(x[0] == "breadth" for x in parts)
        key = json.dumps([parts, sorted(p["symbols"]) if uses_breadth else None])
        if key not in self._regime:
            def daily_for(syms, n):
                pre = timedelta(days=int(n * 1.6) + 10)
                return cached_bars(self.provider, list(syms), "1Day", self.start - pre, self.end, self.tag)
            self._regime[key] = context.allowed_by_day(p, daily_for)
        return self._regime[key]

    def external(self, p, symbols):
        """Filtr sygnalow zewnetrznych (insiderzy / fundusze) - cache po jego parametrach."""
        if self.market != "stocks" or not ext.active(p):
            return None
        key = json.dumps([sorted(symbols), p.get("copy_managers")] + [p.get(k) for k in ext.EXT_KEYS])
        if key not in self._ext:
            self._ext[key] = ext.build_filter(ext.source_for(self.tag), symbols, p, self.start, self.end)
        return self._ext[key]

    def signals(self, strategy, p):
        """Sygnaly per symbol - cache po parametrach strategii i filtrow (SL/TP ich nie zmieniaja)."""
        p = self.expand(p)
        strat = STRATEGIES[strategy]
        spec_keys = sorted(k for k in strat.defaults)
        flatten = p.get("flatten_before_close_min", 0) if self.market == "stocks" else 0
        use_ml = ml.uses_ml(strategy, p)
        key = json.dumps([strategy, p["timeframe"], sorted(p["symbols"]), bool(flatten),
                          {k: p[k] for k in spec_keys}, [p.get(k) for k in ext.EXT_KEYS],
                          [p.get(k) for k in ml.ML_KEYS] if use_ml else None,
                          [p["stop_loss_pct"], p["take_profit_pct"], p.get("regime_symbol")] if use_ml else None,
                          [p.get("_radar", {}).get("top")]],
                         sort_keys=True)
        if key in self._signals:
            return self._signals[key]
        mlres = ml.context_probs(self, strategy, p, self.cost) if use_ml else None
        bars = mlres["bars"] if mlres else self.bars(p["timeframe"], p["symbols"], strat.warmup(p))
        bars = {s: bars[s] for s in p["symbols"] if s in bars}
        flt = self.external(p, list(bars))
        intraday = TIMEFRAME_MINUTES[p["timeframe"]] < 1440
        frames, info = {}, {"active": flt is not None, "raw": 0, "blocked": 0, "per_symbol": {}}
        ml_blocked = 0
        for sym, df in bars.items():
            if mlres:
                d = ml.signal_frame(strat, df, p, mlres["probs"].get(sym, pd.Series(dtype=float)), self.cost)
            else:
                d = strat.compute(df, p)
                d["ml_prob"] = np.nan
            d["atr"] = atr_series(d, 14)
            d = d[d.index >= self.start]
            if d.empty:
                continue
            if mlres:
                ml_blocked += int(d["ml_blocked"].sum())
            if p.get("_radar") and sym not in p["_radar"]["fixed"]:
                days = self.radar_allowed(p).get(sym, set())
                d["entry"] = d["entry"] & pd.Series([x in days for x in d.index.date], index=d.index)
            raw = int(d["entry"].sum())
            d, blocked = ext.apply_filter(d, flt, sym)
            if p.get("_copy"):
                after = int(d["entry"].sum())
                d = ext.apply_copy(d, flt, sym, p)
                blocked += after - int(d["entry"].sum())
            if "prio" not in d:
                d["prio"] = 0
            info["raw"] += raw
            info["blocked"] += blocked
            if flt:
                info["per_symbol"][sym] = {"raw": raw, "blocked": blocked, **flt["stats"].get(sym, {})}
            if intraday and flatten:
                dates = d.index.date
                d["last_of_day"] = np.append(dates[1:] != dates[:-1], True)
            else:
                d["last_of_day"] = False
            frames[sym] = d
        rows = {sym: d[["open", "high", "low", "close", "entry", "exit", "last_of_day", "ml_prob", "atr", "prio"]].to_dict("index")
                for sym, d in frames.items()}
        if mlres:
            info["ml"] = dict(mlres["info"], blocked=ml_blocked,
                              mode="strategy" if strategy == "ml_model" else "filter" if p.get("ml_filter") else "sizing")
        self._signals[key] = (frames, rows, info)
        return self._signals[key]


def simulate(ctx, strategy, p, progress=lambda x: None):
    p = ctx.expand(p)
    frames, rows, ext_info = ctx.signals(strategy, p)
    if not frames:
        raise ValueError("Brak danych dla wybranych symboli i okresu.")
    regime = ctx.regime(p)
    timeline = sorted(set().union(*[set(d.index) for d in frames.values()]))
    market, cost = ctx.market, ctx.cost
    cash = ctx.capital
    positions, last_price = {}, {}
    trades, curve = [], []
    exposure_bars = 0
    n = len(timeline)

    def equity():
        return cash + sum(pos["qty"] * last_price[s] for s, pos in positions.items())

    def exit_pos(sym, price, ts, reason):
        nonlocal cash
        pos = positions.pop(sym)
        fee = pos["qty"] * price * cost
        cash += pos["qty"] * price - fee
        gross = (price - pos["entry"]) * pos["qty"]
        pnl = gross - fee - pos["cost_in"]
        trades.append({"symbol": sym, "t_in": pos["t_in"].isoformat(), "t_out": ts.isoformat(),
                       "entry": pos["entry"], "exit": price, "qty": pos["qty"], "pnl": pnl, "gross": gross,
                       "fees": fee + pos["cost_in"], "pnl_pct": pnl / (pos["entry"] * pos["qty"]) * 100,
                       "reason": reason, "hours": (ts - pos["t_in"]).total_seconds() / 3600})

    trail = p.get("trail_atr_mult", 0) or 0
    be = p.get("breakeven_after_pct", 0) or 0
    max_hold = timedelta(days=p.get("max_hold_days", 0)) if p.get("max_hold_days") else None

    for i, ts in enumerate(timeline):
        if i % 2000 == 0:
            progress(i / n)
        allowed = True if regime is None else regime.get(ts.date(), False)

        for sym in list(positions):                         # 1) wyjscia
            r = rows[sym].get(ts)
            if r is None:
                continue
            pos = positions[sym]
            last_price[sym] = r["close"]
            # stop liczony z informacji do POPRZEDNIEJ swiecy (szczyt od wejscia, ATR), sprawdzany na biezacej
            stop, why = pos["sl"], "stop-loss"
            if trail and pos["atr"] == pos["atr"]:          # atr nie jest NaN
                t = pos["peak"] - trail * pos["atr"]
                if t > stop:
                    stop, why = t, "stop kroczący"
            if be and pos["peak"] >= pos["entry"] * (1 + be):
                b = pos["entry"] * (1 + 2 * cost)
                if b > stop:
                    stop, why = b, "stop na wejściu"
            if r["low"] <= stop:
                exit_pos(sym, min(r["open"], stop), ts, why)
            elif r["high"] >= pos["tp"]:
                exit_pos(sym, max(r["open"], pos["tp"]), ts, "take-profit")
            elif r["exit"]:
                exit_pos(sym, r["close"], ts, "sygnal wyjscia")
            elif r["last_of_day"]:
                exit_pos(sym, r["close"], ts, "koniec sesji")
            elif max_hold and ts - pos["t_in"] >= max_hold:
                exit_pos(sym, r["close"], ts, "limit czasu")
            else:
                pos["peak"] = max(pos["peak"], r["high"])
                pos["atr"] = r["atr"]

        if allowed and len(positions) < p["max_positions"]:  # 2) wejscia
            budget = equity() * p["allocation_pct"]
            order = p["symbols"]
            if p.get("_copy"):                              # pierwszenstwo: spolki, ktore dokupilo najwiecej funduszy
                order = sorted(order, key=lambda s_: -(rows.get(s_, {}).get(ts) or {}).get("prio", 0))
            for sym in order:
                if len(positions) >= p["max_positions"]:
                    break
                r = rows.get(sym, {}).get(ts)
                if r is None or sym in positions or not r["entry"] or r["exit"] or r["last_of_day"]:
                    continue
                used = sum(pos["qty"] * last_price[s] for s, pos in positions.items())
                notional = sized_notional(budget, p, min(cash, budget - used))
                if p.get("ml_sizing"):
                    notional *= ml.size_scale(r["ml_prob"], p, cost)
                price = r["close"]
                qty = notional / price if market == "crypto" or p.get("fractional_shares") else math.floor(notional / price)
                if qty <= 0 or qty * price < 10:
                    continue
                fee = qty * price * cost
                cash -= qty * price + fee
                positions[sym] = {"qty": qty, "entry": price, "t_in": ts, "cost_in": fee, "peak": price, "atr": r["atr"],
                                  "sl": price * (1 - p["stop_loss_pct"]), "tp": price * (1 + p["take_profit_pct"])}
                last_price[sym] = price

        for sym in positions:
            r = rows[sym].get(ts)
            if r is not None:
                last_price[sym] = r["close"]
        if positions:
            exposure_bars += 1
        curve.append((ts, equity()))

    for sym in list(positions):                             # zamkniecie na koniec testu
        exit_pos(sym, last_price[sym], timeline[-1], "koniec testu")
    curve[-1] = (timeline[-1], cash)
    eq = pd.Series([v for _, v in curve], index=pd.DatetimeIndex([t for t, _ in curve]))
    return {"equity": eq, "trades": trades, "exposure_pct": exposure_bars / n * 100, "frames": frames,
            "ext": ext_info}


def daily_equity(eq):
    return eq.resample("1D").last().dropna()


def core_metrics(sim, ctx):
    eq, trades = sim["equity"], sim["trades"]
    daily = daily_equity(eq)
    rets = daily.pct_change().dropna()
    ann = 365 if ctx.market == "crypto" else 252
    sharpe = float(rets.mean() / rets.std() * math.sqrt(ann)) if len(rets) > 5 and rets.std() > 0 else None
    total = float(eq.iloc[-1] / ctx.capital - 1)
    days = max((eq.index[-1] - eq.index[0]).days, 1)
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    gross_win, gross_loss = sum(t["pnl"] for t in wins), -sum(t["pnl"] for t in losses)
    return {
        "total_return_pct": total * 100,
        "cagr_pct": ((1 + total) ** (365 / days) - 1) * 100 if days >= 60 and total > -1 else None,
        "max_drawdown_pct": float((eq / eq.cummax() - 1).min()) * 100,
        "sharpe": sharpe,
        "trades": len(trades),
        "win_rate_pct": len(wins) / len(trades) * 100 if trades else None,
        "avg_win_pct": float(np.mean([t["pnl_pct"] for t in wins])) if wins else None,
        "avg_loss_pct": float(np.mean([t["pnl_pct"] for t in losses])) if losses else None,
        "profit_factor": gross_win / gross_loss if gross_loss > 0 else None,
        "exposure_pct": sim["exposure_pct"],
        "avg_hours": float(np.mean([t["hours"] for t in trades])) if trades else None,
        "final_equity": float(eq.iloc[-1]),
    }


def benchmark(sim, ctx):
    closes = pd.DataFrame({s: d["close"] for s, d in sim["frames"].items()}).ffill().bfill()
    return (closes / closes.iloc[0]).mean(axis=1) * ctx.capital


def run_backtest(provider, cfg, tag, progress=lambda x: None):
    from .report import build_report
    p = full_params(cfg["market"], cfg["strategy"], cfg["params"])
    ctx = Context(provider, cfg, tag)
    progress(0.05)
    ctx.signals(cfg["strategy"], p)
    progress(0.4)
    ctx.regime(p)
    sim = simulate(ctx, cfg["strategy"], p, progress=lambda x: progress(0.45 + 0.45 * x))
    progress(0.92)

    metrics = core_metrics(sim, ctx)
    bench = benchmark(sim, ctx)
    metrics["benchmark_return_pct"] = float(bench.iloc[-1] / ctx.capital - 1) * 100
    metrics["fees_note"] = f"koszt {ctx.cost * 100:.2f}% na strone"

    daily = daily_equity(sim["equity"])
    bench_daily = daily_equity(bench)
    step = max(1, len(daily) // 1500)
    per_symbol = {}
    for t in sim["trades"]:
        s = per_symbol.setdefault(t["symbol"], {"symbol": t["symbol"], "trades": 0, "wins": 0, "pnl": 0.0})
        s["trades"] += 1
        s["wins"] += t["pnl"] > 0
        s["pnl"] += t["pnl"]
    for s in per_symbol.values():
        s["win_rate"] = s["wins"] / s["trades"] * 100

    return {
        "metrics": metrics,
        "equity": [[t.isoformat(), round(v, 2)] for t, v in daily.iloc[::step].items()],
        "benchmark": [[t.isoformat(), round(v, 2)] for t, v in bench_daily.iloc[::step].items()],
        "trades": sim["trades"][-1000:],
        "per_symbol": sorted(per_symbol.values(), key=lambda s: s["pnl"], reverse=True),
        "params": p,
        "leverage_ratio": p["risk_per_trade_pct"] / p["stop_loss_pct"],
        "report": build_report(sim, ctx, p, cfg, metrics, bench),
        "ml": sim["ext"].get("ml"),
    }
