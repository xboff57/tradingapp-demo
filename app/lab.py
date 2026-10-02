"""
Laboratorium wariantow - ciagle testowanie "na niby" obok prawdziwego bota.

  - "Mistrz" to wirtualna kopia obecnych ustawien bota (porownanie jablek z jablkami).
  - "Pretendenci" to te same ustawienia z JEDNA zmiana (inny prog pewnosci ML, stop kroczacy, inny SL/TP,
    inna zasada...) oraz - dla botow ML - najnowszy model uczony co noc na swiezych danych.
  - Wszystkie warianty widza te same swiece na zywo co bot; transakcje sa tylko zapisywane (zadnych zlecen).
    To najuczciwszy test: przyszlosc, ktorej zaden model ani wariant wczesniej nie widzial.
  - Pretendent wygrywa, gdy ma co najmniej lab_min_trades transakcji, trwa co najmniej lab_min_days dni,
    ma wynik lepszy od mistrza o ponad 1 pp i obsuniecie nie gorsze o wiecej niz 25%.
  - Zwyciezca przejmuje bota: na koncie papierowym automatycznie, na koncie z prawdziwymi pieniedzmi
    dopiero po Twoim kliknieciu. Po zmianie mistrza porownanie startuje od nowa.

Wyniki wariantow: kazda transakcja ma wage = udzial w kapitale (1 / max pozycji, x skalowanie pewnoscia ML),
wynik po kosztach (2 x prowizja), kapital wirtualny liczony narastajaco.
"""

import json
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from . import ml
from . import signals as ext
from .rules import atr as atr_series
from .strategies import STRATEGIES, full_params

SKIP_KEYS = {"tf_slower", "drop_symbols", "flatten_toggle", "insider_veto", "insider_confirm", "ext_off", "funds_veto",
             "funds_confirm", "funds_off", "ml_filter_on", "more_positions", "fewer_positions"}
PREFER = ["trail_on", "trail_off", "trail_wider", "breakeven_on", "breakeven_off", "sl_wider", "sl_tighter", "tp_higher",
          "tp_lower", "hold_off", "hold_longer", "regime_toggle", "entry_stricter", "rsi_looser"]
MAX_VARIANTS = 8


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def default_variants(strategy, market, p):
    """[(key, label, kind, changes)] - mistrz + pretendenci z jedna zmiana."""
    from .optimize import candidates
    out = [("champion", "Mistrz (obecne ustawienia bota)", "champion", {})]
    uses = ml.uses_ml(strategy, p)
    if uses:
        conf = p.get("ml_confidence", "mid")
        names = {"low": "niska", "mid": "średnia", "high": "wysoka"}
        for c in ("low", "mid", "high"):
            if c != conf:
                out.append((f"conf_{c}", f"Pewność ML: {names[c]}", "params", {"ml_confidence": c}))
        out.append(("sizing", "Stała wielkość pozycji" if p.get("ml_sizing") else "Wielkość pozycji wg pewności ML",
                    "params", {"ml_sizing": not p.get("ml_sizing")}))
        if strategy != "ml_model" and p.get("ml_filter"):
            out.append(("no_ml", "Bez filtra ML (sama strategia)", "params", {"ml_filter": False}))
        out.append(("challenger_model", "Najnowszy model (uczony co noc)", "challenger_model", {}))
    try:
        cands = candidates(strategy, market, p, [], 365)
    except Exception:
        cands = []
    by_key = {c["key"]: c for c in cands if c["key"] not in SKIP_KEYS and not c["key"].startswith("ml_")}
    order = [k for k in PREFER if k in by_key] + [k for k in by_key if k not in PREFER]
    for k in order:
        c = by_key[k]
        if uses and any(x in c["changes"] for x in ("stop_loss_pct", "take_profit_pct", "timeframe", "ml_horizon")):
            continue                            # zmiana SL/TP wymaga innego modelu ML - tego nie testujemy na zywo
        out.append((k, c["label"], "params", c["changes"]))
        if len(out) >= MAX_VARIANTS:
            break
    return out


class Lab:
    """Czesc bota (BotRunner) - co cykl przesuwa wirtualne portfele wariantow o nowe swiece."""

    def __init__(self, db, runner):
        self.db = db
        self.r = runner
        self.models = {}                        # model_id -> obiekt modelu pretendenta

    def variants(self):
        rows = self.db.all("SELECT * FROM lab_variants WHERE bot_id=? AND status='active' ORDER BY id", (self.r.id,))
        if not rows:
            for key, label, kind, changes in default_variants(self.r.bot["strategy"], self.r.market, self.r.p):
                self.db.execute("INSERT INTO lab_variants(bot_id, key, label, kind, changes, created_at, status, state) "
                                "VALUES (?,?,?,?,?,?, 'active', ?)",
                                (self.r.id, key, label, kind, json.dumps(changes), now_iso(), json.dumps({})))
            self.db.execute("INSERT INTO lab_events(bot_id, ts, kind, status, text) VALUES (?,?,?,?,?)",
                            (self.r.id, now_iso(), "start", "info", "Start laboratorium: nowy zestaw wariantów."))
            rows = self.db.all("SELECT * FROM lab_variants WHERE bot_id=? AND status='active' ORDER BY id", (self.r.id,))
        for v in rows:
            v["changes"] = json.loads(v["changes"] or "{}")
            v["state"] = json.loads(v["state"] or "{}")
        return rows

    def _model(self, model_id):
        if model_id not in self.models:
            row = self.db.one("SELECT path FROM ml_models WHERE id=?", (model_id,))
            self.models[model_id] = ml.load(row["path"])["model"] if row and row["path"] else None
        return self.models[model_id]

    def step(self, bars, cross):
        """Wywolywane przez bota po jego wlasnym cyklu. Bledy laboratorium nie zatrzymuja handlu."""
        base_p = self.r.p
        strat = STRATEGIES[self.r.bot["strategy"]]
        cost = self.r.cost
        probs_cache = {}
        for v in self.variants():
            if v["kind"] == "challenger_model" and not v["model_id"]:
                continue                        # pretendent czeka na pierwszy nocny model
            vp = dict(base_p, **v["changes"])
            st = v["state"]
            st.setdefault("pos", {})
            st.setdefault("last", {})
            changed = False
            for sym, df in bars.items():
                if df is None or len(df) < self.r.warmup:
                    continue
                last = st["last"].get(sym)
                if last is None:                # nowy wariant/symbol: startujemy od biezacej swiecy
                    st["last"][sym] = df.index[-1].isoformat()
                    changed = True
                    continue
                new = df[df.index > pd.Timestamp(last)]
                if new.empty:
                    continue
                prob = None
                if ml.uses_ml(self.r.bot["strategy"], vp):
                    if v["kind"] == "challenger_model":
                        model = self._model(v["model_id"])
                        prob = self.r.ml.probs(df, vp, self.r.broker.data, cross, model=model) if model else None
                    else:
                        if sym not in probs_cache:
                            probs_cache[sym] = self.r.ml.probs(df, vp, self.r.broker.data, cross)
                        prob = probs_cache[sym]
                    if prob is None:
                        prob = pd.Series(np.nan, index=df.index)
                    d = ml.signal_frame(strat, df, vp, prob, cost)
                else:
                    d = strat.compute(df, vp)
                    if getattr(self.r, "copy_mode", False):
                        if self.r.ext_filter:
                            d = ext.apply_copy(d, self.r.ext_filter, sym, vp)
                        else:
                            d["entry"] = False
                    d["ml_prob"] = np.nan
                d["atr"] = atr_series(d, 14)
                self._advance(v, vp, sym, d[d.index > pd.Timestamp(last)], cost)
                st["last"][sym] = df.index[-1].isoformat()
                changed = True
            if changed:
                self.db.execute("UPDATE lab_variants SET state=? WHERE id=?", (json.dumps(st), v["id"]))

    def _advance(self, v, vp, sym, rows, cost):
        st = v["state"]
        pos_all = st["pos"]
        trail = vp.get("trail_atr_mult") or 0
        be = vp.get("breakeven_after_pct") or 0
        hold = timedelta(days=vp["max_hold_days"]) if vp.get("max_hold_days") else None
        allowed = self.r.regime[0]
        for ts, r in rows.iterrows():
            pos = pos_all.get(sym)
            if pos:
                stop, why = pos["sl"], "stop-loss"
                if trail and pos.get("atr") == pos.get("atr") and pos.get("atr"):
                    t = pos["peak"] - trail * pos["atr"]
                    if t > stop:
                        stop, why = t, "stop kroczący"
                if be and pos["peak"] >= pos["entry"] * (1 + be) and pos["entry"] * (1 + 2 * cost) > stop:
                    stop, why = pos["entry"] * (1 + 2 * cost), "stop na wejściu"
                out = None
                if r["low"] <= stop:
                    out = (min(r["open"], stop), why)
                elif r["high"] >= pos["tp"]:
                    out = (max(r["open"], pos["tp"]), "take-profit")
                elif r["exit"]:
                    out = (r["close"], "sygnał wyjścia")
                elif hold and ts - pd.Timestamp(pos["t_in"]) >= hold:
                    out = (r["close"], "limit czasu")
                if out:
                    pnl = (out[0] / pos["entry"] - 1 - 2 * cost) * 100
                    self.db.execute("INSERT INTO lab_trades(variant_id, bot_id, symbol, t_in, t_out, entry, exit, pnl_pct, "
                                    "weight, reason) VALUES (?,?,?,?,?,?,?,?,?,?)",
                                    (v["id"], self.r.id, sym, pos["t_in"], ts.isoformat(), pos["entry"], out[0], pnl,
                                     pos["w"], out[1]))
                    del pos_all[sym]
                    continue
                pos["peak"] = max(pos["peak"], float(r["high"]))
                pos["atr"] = float(r["atr"]) if r["atr"] == r["atr"] else pos.get("atr")
                continue
            if allowed and r["entry"] and not r["exit"] and len(pos_all) < vp["max_positions"]:
                price = float(r["close"])
                prob = r.get("ml_prob")
                prob = None if prob is None or pd.isna(prob) else float(prob)
                w = 1.0 / vp["max_positions"] * (ml.size_scale(prob, vp, cost) if vp.get("ml_sizing") else 1.0)
                pos_all[sym] = {"entry": price, "t_in": ts.isoformat(), "sl": price * (1 - vp["stop_loss_pct"]),
                                "tp": price * (1 + vp["take_profit_pct"]), "peak": price, "w": w,
                                "atr": float(r["atr"]) if r["atr"] == r["atr"] else None}


# ------------------------------------------------------------------ wyniki i decyzje (watek menedzera)
def stats(db, variant):
    rows = db.all("SELECT t_out, pnl_pct, weight FROM lab_trades WHERE variant_id=? ORDER BY t_out", (variant["id"],))
    eq, peak, dd, curve = 1.0, 1.0, 0.0, []
    for r in rows:
        eq *= 1 + (r["weight"] or 0) * (r["pnl_pct"] or 0) / 100
        peak = max(peak, eq)
        dd = min(dd, eq / peak - 1)
        curve.append([r["t_out"], round((eq - 1) * 100, 3)])
    n = len(rows)
    started = datetime.fromisoformat(variant["created_at"])
    return {"trades": n, "win_rate": sum(1 for r in rows if r["pnl_pct"] > 0) / n * 100 if n else None,
            "avg_pnl": float(np.mean([r["pnl_pct"] for r in rows])) if n else None,
            "return_pct": (eq - 1) * 100, "max_dd_pct": dd * 100, "curve": curve,
            "days": (datetime.now(timezone.utc) - started).total_seconds() / 86400,
            "open": len((json.loads(variant["state"] or "{}") if isinstance(variant["state"], str)
                         else variant["state"] or {}).get("pos", {}))}


def overview(db, bot_id):
    rows = db.all("SELECT * FROM lab_variants WHERE bot_id=? AND status='active' ORDER BY id", (bot_id,))
    out = []
    for v in rows:
        s = stats(db, v)
        out.append({"id": v["id"], "key": v["key"], "label": v["label"], "kind": v["kind"],
                    "changes": json.loads(v["changes"] or "{}"), "model_id": v["model_id"],
                    "created_at": v["created_at"], **s})
    return out


def judge(db, bot, p):
    """Czy ktorys pretendent pokonal mistrza? Zwraca (zwyciezca, mistrz, wszyscy) albo (None, ...)."""
    items = overview(db, bot["id"])
    champ = next((x for x in items if x["kind"] == "champion"), None)
    if not champ:
        return None, None, items
    need_n, need_d = p.get("lab_min_trades", 30), p.get("lab_min_days", 14)
    best = None
    for x in items:
        x["eligible"] = False
        x["why"] = []
        if x is champ:
            continue
        if x["trades"] < need_n:
            x["why"].append(f"{x['trades']}/{need_n} transakcji")
        if x["days"] < need_d:
            x["why"].append(f"{x['days']:.0f}/{need_d} dni")
        if x["return_pct"] <= champ["return_pct"] + 1:
            x["why"].append("nie lepszy od mistrza o >1 pp")
        if x["max_dd_pct"] < champ["max_dd_pct"] * 1.25 - 0.5:
            x["why"].append("większe obsunięcie")
        if not x["why"]:
            x["eligible"] = True
            if not best or x["return_pct"] > best["return_pct"]:
                best = x
    return best, champ, items


def apply_winner(db, manager, bot, winner, auto):
    """Zwyciezca przejmuje bota. Zmiana ustawien = restart bota; nowy model = zatwierdzenie modelu."""
    p = full_params(bot["market"], bot["strategy"], bot["params"])
    if winner["kind"] == "challenger_model":
        mid = winner["model_id"]
        db.execute("UPDATE ml_models SET status='retired', decided_at=? WHERE bot_id=? AND status='active'",
                   (now_iso(), bot["id"]))
        db.execute("UPDATE ml_models SET status='active', decided_at=? WHERE id=?", (now_iso(), mid))
        what = f"nowy model ML #{mid}"
    else:
        newp = full_params(bot["market"], bot["strategy"], dict(p, **winner["changes"]))
        was = manager.status(bot["id"]) == "running"
        if was:
            manager.stop(bot["id"], report=False)
        db.update_bot(bot["id"], params=newp)
        if was:
            manager.start(bot["id"], resume=True)
        what = f"ustawienia: {winner['label']}"
    db.execute("UPDATE lab_variants SET status='retired' WHERE bot_id=? AND status='active'", (bot["id"],))
    text = (f"Laboratorium: {'automatycznie ' if auto else ''}wdrożono {what} "
            f"(wynik {winner['return_pct']:+.2f}% w {winner['trades']} transakcjach). Porównanie startuje od nowa.")
    db.execute("INSERT INTO lab_events(bot_id, ts, kind, status, text, data) VALUES (?,?,?,?,?,?)",
               (bot["id"], now_iso(), "promotion", "applied", text, json.dumps(winner, default=str)))
    db.log(bot["id"], "INFO", text)
    from . import notify
    notify.send(f"🧪 {bot['name']}: {text}", "lab")
    return text


def propose(db, bot, winner):
    """Konto z prawdziwymi pieniedzmi: tylko propozycja do zatwierdzenia w panelu (bez duplikatow)."""
    pend = db.one("SELECT id FROM lab_events WHERE bot_id=? AND kind='proposal' AND status='pending'", (bot["id"],))
    data = json.dumps({k: winner[k] for k in ("id", "key", "label", "kind", "changes", "model_id", "return_pct", "trades",
                                              "win_rate", "max_dd_pct")})
    text = (f"Pretendent „{winner['label']}” wygrywa z mistrzem ({winner['return_pct']:+.2f}% w {winner['trades']} "
            "transakcjach). Zatwierdź, żeby przejął bota.")
    if pend:
        db.execute("UPDATE lab_events SET text=?, data=?, ts=? WHERE id=?", (text, data, now_iso(), pend["id"]))
    else:
        db.execute("INSERT INTO lab_events(bot_id, ts, kind, status, text, data) VALUES (?,?,?,?,?,?)",
                   (bot["id"], now_iso(), "proposal", "pending", text, data))
        db.log(bot["id"], "INFO", "Laboratorium: " + text)
        from . import notify
        notify.send(f"🧪 {bot['name']}: {text}", "lab")
