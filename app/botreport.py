"""
Raport z dzialania bota za okres: co zrobil, ile zarobil, jak na tle rynku i backtestu.

Rodzaje:
  daily   - automatycznie po sesji (22:30 czasu PL), za ostatnie 24 h
  weekly  - automatycznie w sobote rano, za ostatnie 7 dni
  stop    - automatycznie przy zatrzymaniu bota, od jego uruchomienia
  manual  - na zadanie z panelu, za wybrany okres

Wszystkie liczby pochodza z bazy aplikacji (transakcje, migawki wyniku, dziennik),
a "kup i trzymaj" i porownanie z backtestem - z danych rynkowych konta bota.
"""

import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from .config import ACCOUNTS
from .strategies import STRATEGIES, TIMEFRAME_MINUTES, full_params

KIND_LABEL = {"daily": "Dzienny", "weekly": "Tygodniowy", "stop": "Podsumowanie działania", "manual": "Na żądanie"}
PL_TZ = "Europe/Warsaw"


def _ts(s):
    return pd.Timestamp(s).tz_convert("UTC") if pd.Timestamp(s).tzinfo else pd.Timestamp(s).tz_localize("UTC")


def round_trips(trades):
    """Paruje kupno -> sprzedaz (FIFO per symbol). Zwraca zamkniete pozycje z czasem trzymania."""
    open_buys = defaultdict(list)
    out = []
    for t in trades:
        if t["side"] in ("BUY", "SHORT"):
            open_buys[t["symbol"]].append(t)
        else:
            buy = open_buys[t["symbol"]].pop(0) if open_buys[t["symbol"]] else None
            hours = (_ts(t["ts"]) - _ts(buy["ts"])).total_seconds() / 3600 if buy else None
            out.append({"symbol": t["symbol"], "t_in": buy["ts"] if buy else None, "t_out": t["ts"],
                        "entry": buy["price"] if buy else None, "exit": t["price"], "qty": t["qty"],
                        "pnl": t["pnl"] or 0.0, "pnl_pct": t["pnl_pct"], "reason": t["reason"], "hours": hours})
    return out


def _stats(rows):
    n = len(rows)
    wins = [r for r in rows if r["pnl"] > 0]
    return {"trades": n, "pnl": sum(r["pnl"] for r in rows), "win_rate": len(wins) / n * 100 if n else None,
            "avg_pct": float(np.mean([r["pnl_pct"] for r in rows if r["pnl_pct"] is not None]))
            if any(r["pnl_pct"] is not None for r in rows) else None}


def benchmark(broker, symbols, start, end):
    """Kup i trzymaj symboli bota po rowno w tym samym okresie (zwrot w %)."""
    hours = (end - start).total_seconds() / 3600
    tf = "15Min" if hours <= 72 else ("1Hour" if hours <= 24 * 45 else "1Day")
    bars = broker.data.bars(symbols, tf, start - timedelta(days=4), end)
    rets, series = [], {}
    for s, df in bars.items():
        before = df[df.index <= start]
        inside = df[(df.index > start) & (df.index <= end)]
        if inside.empty:
            continue
        base = before["close"].iloc[-1] if not before.empty else inside["open"].iloc[0]
        rets.append(inside["close"].iloc[-1] / base - 1)
        series[s] = inside["close"] / base
    if not rets:
        return None, []
    curve = pd.DataFrame(series).ffill().bfill().mean(axis=1)
    step = max(1, len(curve) // 300)
    return float(np.mean(rets)) * 100, [[t.isoformat(), round((v - 1) * 100, 3)] for t, v in curve.iloc[::step].items()]


def backtest_check(bot, p, start, end, broker, tag):
    """Ten sam okres w backteście - czy bot na zywo robi to, co symulacja (liczba transakcji, wynik)."""
    from .backtest import Context, core_metrics, simulate
    tf_min = TIMEFRAME_MINUTES[p["timeframe"]]
    if (end - start).days < 1 or (tf_min == 1 and (end - start).days > 45):
        return None
    cfg = {"market": bot["market"], "strategy": bot["strategy"], "params": p,
           "start": start.date().isoformat(), "end": (end + timedelta(days=1)).date().isoformat(),
           "initial_capital": 100_000}
    ctx = Context(broker.data, cfg, tag)
    sim = simulate(ctx, bot["strategy"], p)
    trades = [t for t in sim["trades"] if start.isoformat() <= t["t_out"] <= end.isoformat()]
    m = core_metrics(sim, ctx)
    return {"trades": len(trades), "return_pct": m["total_return_pct"],
            "win_rate": (sum(1 for t in trades if t["pnl"] > 0) / len(trades) * 100) if trades else None,
            "note": "backtest z kapitałem 100 000 $ i tymi samymi ustawieniami"}


def build(db, bot, kind, start, end, broker=None):
    p = full_params(bot["market"], bot["strategy"], bot["params"])
    s_iso, e_iso = start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds")
    acc = ACCOUNTS.get(bot["account"])

    # --- wynik bota w czasie (migawki: zrealizowany + otwarty)
    eq = db.all("SELECT ts, equity, exposure, realized, unrealized FROM equity WHERE scope='bot' AND ref=? "
                "ORDER BY ts", (str(bot["id"]),))
    eq_df = pd.DataFrame(eq)
    if not eq_df.empty:
        eq_df["ts"] = pd.to_datetime(eq_df["ts"], utc=True)
        eq_df = eq_df.set_index("ts")
    before = eq_df[eq_df.index < start] if not eq_df.empty else eq_df
    inside = eq_df[(eq_df.index >= start) & (eq_df.index <= end)] if not eq_df.empty else eq_df
    start_val = float(before["equity"].iloc[-1]) if len(before) else (float(inside["equity"].iloc[0]) if len(inside) else 0.0)
    end_val = float(inside["equity"].iloc[-1]) if len(inside) else start_val
    curve = (inside["equity"] - start_val) if len(inside) else pd.Series(dtype=float)
    dd_usd = float((curve - curve.cummax()).min()) if len(curve) else 0.0
    exposure_share = float((inside["exposure"] > 0).mean() * 100) if len(inside) else None

    # --- transakcje
    trades = db.trades_between(bot["id"], s_iso, e_iso)
    closed = round_trips(db.all("SELECT * FROM trades WHERE bot_id=? AND ts<=? ORDER BY id", (bot["id"], e_iso)))
    closed = [r for r in closed if r["t_out"] >= s_iso]
    buys = [t for t in trades if t["side"] in ("BUY", "SHORT")]
    realized = sum(r["pnl"] for r in closed)
    wins = [r for r in closed if r["pnl"] > 0]
    losses = [r for r in closed if r["pnl"] <= 0]
    gross_win, gross_loss = sum(r["pnl"] for r in wins), -sum(r["pnl"] for r in losses)

    by_symbol = defaultdict(list)
    by_reason = defaultdict(list)
    for r in closed:
        by_symbol[r["symbol"]].append(r)
        by_reason[(r["reason"] or "?").split(",")[0]].append(r)
    per_symbol = sorted([{"symbol": s, **_stats(v)} for s, v in by_symbol.items()], key=lambda x: -x["pnl"])
    exits = sorted([{"reason": k, **_stats(v)} for k, v in by_reason.items()], key=lambda x: -x["trades"])

    daily = defaultdict(float)
    for r in closed:
        daily[_ts(r["t_out"]).tz_convert(PL_TZ).date().isoformat()] += r["pnl"]
    daily_pnl = [[d, round(v, 2)] for d, v in sorted(daily.items())]

    # --- budzet i wynik w %
    acct_eq = db.all("SELECT ts, equity FROM equity WHERE scope='account' AND ref=? AND ts<=? ORDER BY ts DESC LIMIT 1",
                     (bot["account"], s_iso)) or db.all("SELECT ts, equity FROM equity WHERE scope='account' AND ref=? "
                                                       "ORDER BY ts LIMIT 1", (bot["account"],))
    positions, open_pnl, errors = [], 0.0, None
    if broker is not None:
        try:
            states = db.pos_states(bot["id"])
            for s, v in broker.positions().items():
                if s in states:
                    positions.append(dict(v, sl=states[s]["sl"], tp=states[s]["tp"]))
            open_pnl = sum(x["unrealized"] for x in positions)
            if not acct_eq:
                acct_eq = [{"equity": broker.account()["equity"]}]
        except Exception as e:
            errors = str(e)
    budget = (acct_eq[0]["equity"] if acct_eq else 100_000) * p["allocation_pct"]
    period_pnl = end_val - start_val if len(inside) else realized + open_pnl
    ret_pct = period_pnl / budget * 100 if budget else None

    # --- rynek i backtest
    bench, bench_curve, bt = None, [], None
    tag = "sim" if acc and acc.type == "sim" else "live"
    if broker is not None:
        try:
            bench, bench_curve = benchmark(broker, p["symbols"], start, end)
        except Exception:
            pass
        if kind in ("stop", "weekly", "manual"):
            try:
                bt = backtest_check(bot, p, start, end, broker, tag)
            except Exception as e:
                bt = {"error": str(e)}

    # --- dziennik: bledy, blokady, restarty
    logs = db.logs_between(bot["id"], s_iso, e_iso)
    events = {
        "errors": sum(1 for l in logs if l["level"] == "ERROR"),
        "warnings": sum(1 for l in logs if l["level"] == "WARNING"),
        "blocked": sum(1 for l in logs if "zablokowany" in l["msg"]),
        "starts": sum(1 for l in logs if l["msg"].startswith("Start |")),
        "regime_weak": sum(1 for l in logs if "SLABY" in l["msg"]),
        "last_errors": [{"ts": l["ts"], "msg": l["msg"]} for l in logs if l["level"] == "ERROR"][-5:],
    }

    step = max(1, len(curve) // 400) if len(curve) else 1
    data = {
        "bot": {"id": bot["id"], "name": bot["name"], "account": bot["account"], "market": bot["market"],
                "strategy": STRATEGIES[bot["strategy"]].name, "timeframe": p["timeframe"], "symbols": p["symbols"],
                "allocation_pct": p["allocation_pct"], "risk_pct": p["risk_per_trade_pct"],
                "sl": p["stop_loss_pct"], "tp": p["take_profit_pct"],
                "account_type": acc.type if acc else "?"},
        "kind": kind, "kind_label": KIND_LABEL[kind], "from": s_iso, "to": e_iso,
        "metrics": {
            "period_pnl": period_pnl, "return_pct": ret_pct, "budget": budget, "realized": realized,
            "open_pnl": open_pnl, "closed_trades": len(closed), "opened": len(buys),
            "win_rate": len(wins) / len(closed) * 100 if closed else None,
            "avg_win_pct": float(np.mean([r["pnl_pct"] for r in wins if r["pnl_pct"] is not None])) if wins else None,
            "avg_loss_pct": float(np.mean([r["pnl_pct"] for r in losses if r["pnl_pct"] is not None])) if losses else None,
            "profit_factor": gross_win / gross_loss if gross_loss > 0 else None,
            "max_dd_usd": dd_usd, "max_dd_pct": dd_usd / budget * 100 if budget else None,
            "exposure_pct": exposure_share,
            "avg_hours": float(np.mean([r["hours"] for r in closed if r["hours"] is not None]))
            if any(r["hours"] is not None for r in closed) else None,
            "benchmark_pct": bench,
        },
        "curve": [[t.isoformat(), round(v, 2)] for t, v in curve.iloc[::step].items()] if len(curve) else [],
        "exposure": [[t.isoformat(), round(v, 2)] for t, v in inside["exposure"].iloc[::step].items()] if len(inside) else [],
        "benchmark_curve": bench_curve,
        "daily_pnl": daily_pnl,
        "per_symbol": per_symbol,
        "exits": exits,
        "trades": closed[-300:],
        "positions": positions,
        "events": events,
        "backtest": bt,
        "positions_error": errors,
    }
    data["findings"] = findings(data)
    summary = {"pnl": period_pnl, "return_pct": ret_pct, "closed_trades": len(closed),
               "win_rate": data["metrics"]["win_rate"], "benchmark_pct": bench, "errors": events["errors"]}
    return summary, data


def _f(sev, title, detail):
    return {"severity": sev, "title": title, "detail": detail}


def findings(d):
    m, ev, bt = d["metrics"], d["events"], d.get("backtest")
    out = []
    if m["closed_trades"] == 0 and m["opened"] == 0:
        why = []
        if ev["regime_weak"]:
            why.append("filtr reżimu rynku był aktywny (rynek słaby)")
        if ev["blocked"]:
            why.append(f"filtr zewnętrzny zablokował {ev['blocked']} sygnałów")
        out.append(_f("info", "Brak transakcji w tym okresie",
                      "Bot nie otworzył ani nie zamknął żadnej pozycji" + (": " + "; ".join(why) if why else
                      " — strategia nie dała sygnału. To normalne przy wolnych strategiach i krótkich okresach.") + "."))
    if m["return_pct"] is not None and m["benchmark_pct"] is not None:
        diff = m["return_pct"] - m["benchmark_pct"]
        out.append(_f("good" if diff >= 0 else "warn",
                      "Lepiej niż „kup i trzymaj”" if diff >= 0 else "Słabiej niż „kup i trzymaj”",
                      f"Bot {m['return_pct']:+.2f}% budżetu vs kup i trzymaj tych samych symboli {m['benchmark_pct']:+.2f}% "
                      f"(różnica {diff:+.2f} pp). Przy krótkim okresie to głównie szum — patrz raporty tygodniowe."))
    if m["closed_trades"] >= 5 and m["win_rate"] is not None and m["avg_win_pct"] and m["avg_loss_pct"]:
        payoff = abs(m["avg_win_pct"] / m["avg_loss_pct"])
        out.append(_f("info", "Skuteczność i stosunek zysku do straty",
                      f"Skuteczność {m['win_rate']:.0f}%, średni zysk {m['avg_win_pct']:+.2f}% vs średnia strata "
                      f"{m['avg_loss_pct']:+.2f}% (stosunek {payoff:.2f}). "
                      + ("Strategia zarabia na nielicznych dużych wygranych." if m["win_rate"] < 45 else
                         "Strategia wygrywa często, ale małymi kwotami — pilnuj, by pojedyncze straty nie były duże.")))
    if ev["errors"]:
        out.append(_f("bad", "Błędy w działaniu",
                      f"{ev['errors']} błędów w dzienniku w tym okresie. Ostatni: "
                      f"{ev['last_errors'][-1]['msg'][:160] if ev['last_errors'] else '—'}"))
    if ev["starts"] > 1:
        out.append(_f("warn", "Restarty bota", f"Bot uruchamiał się {ev['starts']} razy w tym okresie "
                      "(restart aplikacji, aktualizacja albo zmiana ustawień)."))
    if bt and not bt.get("error"):
        live_n = m["closed_trades"]
        if bt["trades"] or live_n:
            gap = live_n - bt["trades"]
            sev = "good" if abs(gap) <= max(2, 0.3 * max(bt["trades"], 1)) else "warn"
            out.append(_f(sev, "Zgodność z backtestem",
                          f"Na żywo {live_n} zamkniętych transakcji, w backteście tego okresu {bt['trades']}. "
                          + ("Bot zachowuje się jak w symulacji." if sev == "good" else
                             "Duża różnica — możliwe przyczyny: restarty, inne ceny realizacji, braki danych, "
                             "zmiana ustawień w trakcie okresu.")))
    if m["max_dd_pct"] is not None and m["max_dd_pct"] < -5:
        out.append(_f("warn", "Obsunięcie w okresie",
                      f"Największy spadek wyniku: {m['max_dd_usd']:,.0f} $ ({m['max_dd_pct']:.1f}% budżetu)."))
    order = {"bad": 0, "warn": 1, "info": 2, "good": 3}
    return sorted(out, key=lambda f: order[f["severity"]])
