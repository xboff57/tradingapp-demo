"""
Wersja demonstracyjna (DEMO=1) - do pokazania aplikacji komus innemu.

Przy pierwszym starcie (i po „Przywróć dane demo”) wypelnia pusta baze przykladami:
- kilka botow na koncie symulowanym (krypto, akcje USA, GPW, ETF-y) - dzialaja dalej na zywo na danych symulowanych,
- historia z ostatnich miesiecy: transakcje, otwarte pozycje, wykres kapitalu (z przebiegu strategii na tych danych),
- dwa zapisane backtesty i skany radaru (altcoiny, GPW, USA).
Wszystko na deterministycznych danych syntetycznych (app/data.py: SimData) - bez internetu, bez kluczy.
Ceny NIE sa prawdziwymi notowaniami spolek - nazwy symboli sa tylko etykietami.
"""

import json
import logging
import os
import threading
from datetime import datetime, timedelta, timezone

import pandas as pd

from . import config

log = logging.getLogger("tradingapp")
ACCOUNT = "demo"
START_CASH = 100_000.0
DAYS = 300
STATE = {"state": "idle", "step": "", "progress": 0.0, "error": None}
_lock = threading.Lock()

USA = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "AVGO", "LLY", "JPM", "COST", "NFLX", "AMD"]
GPW = ["PKO.WSE", "PKN.WSE", "PZU.WSE", "KGH.WSE", "CDR.WSE", "LPP.WSE", "DNP.WSE", "ALE.WSE", "PGE.WSE",
       "XTB.WSE", "MBK.WSE", "KRU.WSE"]
BREAKOUT = {"entry_rules": [{"id": "price_above_sma", "n": 200}, {"id": "breakout_high", "n": 34, "within": 1}],
            "entry_mode": "all", "exit_rules": [{"id": "breakdown_low", "n": 20}]}

BOTS = [
    {"name": "Krypto — trend na średnich", "market": "crypto", "strategy": "sma_cross",
     "params": {"symbols": ["BTC/USD", "ETH/USD", "SOL/USD", "XRP/USD", "LINK/USD"], "timeframe": "4Hour",
                "allocation_pct": 0.2, "risk_per_trade_pct": 0.01, "stop_loss_pct": 0.06, "take_profit_pct": 0.15,
                "max_positions": 3, "regime_symbol": "BTC/USD", "sma_fast": 12, "sma_slow": 40, "use_volume": False}},
    {"name": "Akcje USA — wybicie 34 dni", "market": "stocks", "strategy": "rules",
     "params": dict(BREAKOUT, symbols=USA, timeframe="1Day", allocation_pct=0.3, risk_per_trade_pct=0.02,
                    stop_loss_pct=0.12, take_profit_pct=0.6, max_positions=4, regime_symbol="SPY",
                    regime_sma_days=100, fractional_shares=True, no_entry_after_open_min=0)},
    {"name": "GPW — wybicie (agresywny)", "market": "stocks", "strategy": "rules",
     "params": dict(BREAKOUT, symbols=GPW, timeframe="1Day", allocation_pct=0.3, risk_per_trade_pct=0.03,
                    stop_loss_pct=0.15, take_profit_pct=1.0, max_positions=4, regime_filter=False,
                    fractional_shares=True, no_entry_after_open_min=0)},
    {"name": "ETF — powrót do średniej", "market": "stocks", "strategy": "mean_reversion",
     "params": {"symbols": ["SPY", "QQQ", "XLK", "XLF", "XLE", "XLV", "XLI"], "timeframe": "1Day",
                "allocation_pct": 0.2, "risk_per_trade_pct": 0.02, "stop_loss_pct": 0.08, "take_profit_pct": 0.12,
                "max_positions": 3, "regime_filter": False, "fractional_shares": True, "no_entry_after_open_min": 0}},
]


def on():
    return config.DEMO


def _set(**kw):
    with _lock:
        STATE.update(kw)


def status():
    with _lock:
        return dict(STATE)


def needs_seed(db):
    return not db.bots() and not os.path.exists(os.path.join(config.DATA_DIR, "demo_seeded.txt"))


def _history(db, bot, cfg, provider):
    """Przebieg strategii na danych symulowanych -> transakcje, otwarte pozycje, wynik dzienny."""
    from .backtest import run_backtest
    from .data import norm
    res = run_backtest(provider, cfg, "sim")
    p = res["params"]
    opened = []
    for t in res["trades"]:
        sym = t["symbol"]
        db.execute("INSERT INTO trades(bot_id, ts, symbol, side, qty, price, value, pnl, pnl_pct, reason) "
                   "VALUES (?,?,?,?,?,?,?,?,?,?)",
                   (bot["id"], t["t_in"], sym, "BUY", t["qty"], t["entry"], t["qty"] * t["entry"], None, None,
                    "sygnal wejscia"))
        db.execute("INSERT INTO logs(bot_id, ts, level, msg) VALUES (?,?,?,?)",
                   (bot["id"], t["t_in"], "INFO", f"KUPNO {sym} x{t['qty']:.6g} ~{t['entry']:,.4g} | "
                                                  f"SL {t['entry'] * (1 - p['stop_loss_pct']):,.4g} | "
                                                  f"TP {t['entry'] * (1 + p['take_profit_pct']):,.4g}"))
        if t["reason"] == "koniec testu":                     # pozycja nadal otwarta
            sl, tp = t["entry"] * (1 - p["stop_loss_pct"]), t["entry"] * (1 + p["take_profit_pct"])
            db.set_pos_state(bot["id"], norm(sym), t["entry"], t["qty"], sl, tp)
            db.execute("UPDATE positions_state SET opened_at=? WHERE bot_id=? AND symbol=?",
                       (t["t_in"], bot["id"], norm(sym)))
            opened.append(t)
            continue
        reason = {"sygnal wyjscia": "sygnał wyjścia"}.get(t["reason"], t["reason"])
        db.execute("INSERT INTO trades(bot_id, ts, symbol, side, qty, price, value, pnl, pnl_pct, reason) "
                   "VALUES (?,?,?,?,?,?,?,?,?,?)",
                   (bot["id"], t["t_out"], sym, "SELL", t["qty"], t["exit"], t["qty"] * t["exit"], t["pnl"],
                    t["pnl_pct"], reason))
        db.execute("INSERT INTO logs(bot_id, ts, level, msg) VALUES (?,?,?,?)",
                   (bot["id"], t["t_out"], "INFO", f"SPRZEDAŻ {sym} ~{t['exit']:,.4g} ({reason}) | "
                                                   f"P/L {t['pnl']:+,.2f} USD ({t['pnl_pct']:+.2f}%)"))
    eq = pd.Series({pd.Timestamp(t): v for t, v in res["equity"]})
    return res, opened, eq - cfg["initial_capital"]


def seed(db, manager):
    """Wypelnia pusta baze przykladami. Dziala w tle (ok. 1-2 min), postep w status()."""
    from .data import SimData, norm
    _set(state="running", step="Przygotowuję przykładowe boty i ich historię…", progress=0.02, error=None)
    try:
        provider = SimData()
        now = datetime.now(timezone.utc)
        start, end = (now - timedelta(days=DAYS)).date().isoformat(), (now - timedelta(days=1)).date().isoformat()
        curves, positions, realized = {}, {}, 0.0
        for i, b in enumerate(BOTS):
            _set(step=f"Historia bota „{b['name']}”…", progress=0.05 + 0.5 * i / len(BOTS))
            bot_id = db.create_bot(b["name"], ACCOUNT, b["market"], b["strategy"], b["params"])
            bot = db.bot(bot_id)
            cfg = {"name": b["name"], "market": b["market"], "strategy": b["strategy"], "params": b["params"],
                   "start": start, "end": end, "initial_capital": START_CASH * b["params"]["allocation_pct"],
                   "cost_pct": 0.0025 if b["market"] == "crypto" else 0.0005, "data_source": "sim"}
            res, opened, pnl = _history(db, bot, cfg, provider)
            curves[bot_id] = (pnl, res["trades"])
            realized += sum(t["pnl"] for t in res["trades"] if t["reason"] != "koniec testu")
            for t in opened:
                positions[norm(t["symbol"])] = {"symbol": t["symbol"], "qty": t["qty"], "avg_entry": t["entry"]}
            if i in (1, 2):                                    # dwa zapisane backtesty do obejrzenia
                bt = db.create_backtest(dict(cfg, name=f"{b['name']} — ostatnie {DAYS} dni"))
                db.update_backtest(bt, status="done", progress=1, result=res)
        # wykres wyniku botow i kapitalu konta (dzienny)
        _set(step="Wykres kapitału…", progress=0.6)
        days = pd.date_range(pd.Timestamp(start, tz="UTC"), pd.Timestamp(now).normalize(), freq="1D")
        total = pd.Series(0.0, index=days)
        db.execute("DELETE FROM equity WHERE scope='account'")   # migawki zrobione w trakcie przygotowania
        expo_total = pd.Series(0.0, index=days)
        for bot_id, (s, trades) in curves.items():
            s = s.groupby(s.index.normalize()).last().reindex(days).ffill().fillna(0.0)
            total += s
            rows = []
            for ts, v in s.items():
                eod = min(ts + timedelta(hours=21), pd.Timestamp(now) - timedelta(minutes=1))
                real = sum(t["pnl"] for t in trades if t["reason"] != "koniec testu" and pd.Timestamp(t["t_out"]) <= eod)
                expo = sum(t["qty"] * t["entry"] for t in trades if pd.Timestamp(t["t_in"]) <= eod and
                           (t["reason"] == "koniec testu" or pd.Timestamp(t["t_out"]) > eod))
                expo_total[ts] += expo
                rows.append((eod.isoformat(), "bot", str(bot_id), float(v), None, float(expo), float(real),
                             float(v) - float(real)))
            for r in rows:
                db.execute("INSERT INTO equity(ts, scope, ref, equity, cash, exposure, realized, unrealized) "
                           "VALUES (?,?,?,?,?,?,?,?)", r)
        for ts, v in total.items():
            eq = START_CASH + float(v)
            db.execute("INSERT INTO equity(ts, scope, ref, equity, cash, exposure) VALUES (?,?,?,?,?,?)",
                       (min(ts + timedelta(hours=21), pd.Timestamp(now) - timedelta(minutes=1)).isoformat(), "account", ACCOUNT, eq,
                        eq - float(expo_total[ts]), float(expo_total[ts])))
        cash = START_CASH + realized - sum(p["qty"] * p["avg_entry"] for p in positions.values())
        with open(os.path.join(config.DATA_DIR, f"sim_{ACCOUNT}.json"), "w", encoding="utf-8") as f:
            json.dump({"cash": cash, "positions": positions, "orders": []}, f)
        from . import brokers
        brokers._cache.pop(ACCOUNT, None)                      # broker wczyta nowy stan
        open(os.path.join(config.DATA_DIR, "demo_seeded.txt"), "w").write(now.isoformat())
        _set(step="Raporty botów…", progress=0.65)
        t0 = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
        for b in db.bots():
            manager.generate_report(b["id"], "manual", now - timedelta(days=30), now)
            manager.generate_report(b["id"], "manual", t0, now)
            try:
                manager.start(b["id"])
                db.update_bot(b["id"], started_at=t0.isoformat(timespec="seconds"))
            except Exception as e:
                log.warning(f"Demo: nie wystartowal bot {b['name']}: {e}")
        # radary
        from . import radar, stock_radar
        _set(step="Radar altcoinów…", progress=0.7)
        radar.scan(provider, "sim", "USD", db=db)
        for j, m in enumerate(stock_radar.MARKETS):
            _set(step=f"Radar spółek {stock_radar.MARKETS[m]['label']}…", progress=0.78 + 0.1 * j)
            with stock_radar._lock:
                stock_radar.scan(provider, m, db=db)
        _set(state="done", step="Gotowe", progress=1.0)
        log.info("Demo: przykładowe dane gotowe")
    except Exception as e:
        log.exception("Demo: przygotowanie danych nieudane")
        _set(state="error", error=str(e)[:300])


def start_seed(db, manager):
    if status()["state"] == "running":
        return
    threading.Thread(target=seed, args=(db, manager), daemon=True, name="demo-seed").start()


def reset(db, manager):
    """Przywraca stan poczatkowy: zatrzymuje boty, czysci dane i wypelnia od nowa."""
    for b in db.bots():
        try:
            manager.stop(b["id"], report=False)
        except Exception:
            pass
    for t in ("trades", "positions_state", "equity", "logs", "bot_reports", "ml_models", "radar_scans",
              "radar_seen", "lab_variants", "lab_trades", "lab_events", "backtests", "bots"):
        try:
            db.execute(f"DELETE FROM {t}")
        except Exception:
            pass
    for f in (f"sim_{ACCOUNT}.json", "demo_seeded.txt"):
        try:
            os.remove(os.path.join(config.DATA_DIR, f))
        except OSError:
            pass
    from . import brokers
    brokers._cache.pop(ACCOUNT, None)
    seed(db, manager)
