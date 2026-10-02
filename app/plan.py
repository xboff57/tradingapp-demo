"""
Plan i przykładowe ruchy na wykresie (zakładka Wykresy).

Dla wybranego szablonu strategii (domyślnie „Wybicie 34 dni” jak bot GPW, albo ustawienia dowolnego bota):
1. Przykładowe ruchy: gdzie strategia kupiłaby i sprzedała w przeszłości na TYM symbolu
   (jedna pozycja naraz, wejście po zamknięciu świecy z sygnałem, stop-loss/take-profit na high/low kolejnych świec).
2. Plan na dziś: które warunki kupna są spełnione, poziom wybicia (gdy strategia na nie czeka),
   stop-loss i take-profit dla takiego wejścia, a dla otwartej (przykładowej) pozycji — poziom wyjścia.
Bez filtra rynku (reżimu) i bez kosztów — to ilustracja zasad, nie backtest.
"""

import pandas as pd

from . import rules as R
from .strategies import STRATEGIES, full_params

DEFAULT = {"label": "Wybicie 34 dni + SMA 200 (jak bot GPW)", "strategy": "rules",
           "params": {"entry_rules": [{"id": "price_above_sma", "n": 200}, {"id": "breakout_high", "n": 34, "within": 1}],
                      "entry_mode": "all", "exit_rules": [{"id": "breakdown_low", "n": 20}],
                      "stop_loss_pct": 0.15, "take_profit_pct": 1.0}}
SUPPORTED = ("rules", "sma_cross", "mean_reversion")


def templates(bots):
    out = [{"id": "default", "label": DEFAULT["label"]}]
    for b in bots:
        if b["strategy"] in SUPPORTED:
            out.append({"id": str(b["id"]), "label": f"Bot: {b['name']}"})
    return out


def resolve(template, db):
    if template in (None, "", "default"):
        return DEFAULT["label"], DEFAULT["strategy"], full_params("stocks", DEFAULT["strategy"], DEFAULT["params"])
    b = db.bot(int(template))
    if not b or b["strategy"] not in SUPPORTED:
        raise ValueError("Ten bot nie ma strategii, którą da się pokazać na wykresie (ML i kopiowanie funduszy odpadają).")
    return f"Bot: {b['name']}", b["strategy"], full_params(b["market"], b["strategy"], b["params"])


def simulate(d, sl_pct, tp_pct):
    trades, pos = [], None
    idx = d.index
    for i in range(len(d)):
        r = d.iloc[i]
        if pos:
            if r["low"] <= pos["sl"]:
                px = min(r["open"], pos["sl"])
                trades.append(dict(pos, exit_t=idx[i], exit=px, reason="stop-loss"))
                pos = None
            elif r["high"] >= pos["tp"]:
                px = max(r["open"], pos["tp"])
                trades.append(dict(pos, exit_t=idx[i], exit=px, reason="take-profit"))
                pos = None
            elif r["exit"]:
                trades.append(dict(pos, exit_t=idx[i], exit=r["close"], reason="sygnał wyjścia"))
                pos = None
            continue
        if r["entry"]:
            e = float(r["close"])
            pos = {"entry_t": idx[i], "entry": e, "sl": e * (1 - sl_pct), "tp": e * (1 + tp_pct)}
    out = [{"entry_t": int(t["entry_t"].timestamp()), "entry": t["entry"], "exit_t": int(t["exit_t"].timestamp()),
            "exit": float(t["exit"]), "pct": (float(t["exit"]) / t["entry"] - 1) * 100, "reason": t["reason"]} for t in trades]
    return out, pos


def _f(v):
    return f"{v:,.2f}".replace(",", " ").replace(".", ",") if abs(v) >= 1 else f"{v:.6f}".replace(".", ",")


def _p(v):
    return f"{v:+.1f}%".replace(".", ",").replace("-", "−")


def exit_level(df, p, strategy):
    """Poziom, pod którym zadziała zasada wyjścia (gdy da się go policzyć)."""
    if strategy != "rules":
        return None, None
    for r in p.get("exit_rules", []):
        if r["id"] == "breakdown_low":
            return float(df["low"].iloc[-int(r["n"]):].min()), f"zamknięcie pod min. {int(r['n'])} świec"
        if r["id"] == "close_below_sma":
            return float(df["close"].rolling(int(r["n"])).mean().iloc[-1]), f"zamknięcie pod SMA {int(r['n'])}"
    return None, None


def build(df, template, db):
    label, strategy, p = resolve(template, db)
    strat = STRATEGIES[strategy]
    d = strat.compute(df, p)
    sl_pct, tp_pct = float(p["stop_loss_pct"]), float(p["take_profit_pct"])
    trades, pos = simulate(d, sl_pct, tp_pct)
    last = d.iloc[-1]
    close = float(last["close"])
    lines, checks, state, text = [], [], "wait", ""

    if strategy == "rules":
        for r in p.get("entry_rules", []):
            ok = bool(R.BY_ID[r["id"]]["fn"](df, r).fillna(False).iloc[-1])
            checks.append({"label": R.describe(r), "ok": ok})
    if pos:
        state = "in"
        lines += [{"price": pos["entry"], "label": "przykładowe wejście", "kind": "entry"},
                  {"price": pos["sl"], "label": "stop-loss", "kind": "sl"},
                  {"price": pos["tp"], "label": "take-profit", "kind": "tp"}]
        lvl, why = exit_level(df, p, strategy)
        if lvl:
            lines.append({"price": lvl, "label": "wyjście", "kind": "exit"})
        text = (f"Strategia byłaby w pozycji od {pos['entry_t']:%d.%m.%Y} po {_f(pos['entry'])} "
                f"({_p((close / pos['entry'] - 1) * 100)}). Wyjście: stop-loss {_f(pos['sl'])}"
                + (f" albo {why} ({_f(lvl)})" if lvl else "") + ".")
    elif bool(last["entry"]):
        state = "signal"
        lines += [{"price": close, "label": "wejście (sygnał dziś)", "kind": "entry"},
                  {"price": close * (1 - sl_pct), "label": "stop-loss", "kind": "sl"},
                  {"price": close * (1 + tp_pct), "label": "take-profit", "kind": "tp"}]
        text = (f"Sygnał kupna na ostatniej świecy: wejście ok. {_f(close)}, stop-loss {_f(close * (1 - sl_pct))} "
                f"(−{sl_pct * 100:.0f}%), take-profit {_f(close * (1 + tp_pct))} (+{tp_pct * 100:.0f}%).")
    else:
        brk = next((r for r in p.get("entry_rules", []) if r["id"] == "breakout_high"), None) if strategy == "rules" else None
        if brk:
            trig = float(df["high"].iloc[-int(brk["n"]):].max())
            lines += [{"price": trig, "label": f"wybicie > maks. {int(brk['n'])} św.", "kind": "trigger"},
                      {"price": trig * (1 - sl_pct), "label": "stop-loss po wybiciu", "kind": "sl"}]
            others = [c for c in checks if not c["label"].startswith("wybicie")]
            missing = [c["label"] for c in others if not c["ok"]]
            text = (f"Czeka na wybicie: zamknięcie powyżej {_f(trig)} ({_p((trig / close - 1) * 100)} od obecnej ceny). "
                    f"Stop-loss po wejściu ok. {_f(trig * (1 - sl_pct))}."
                    + (f" Brakuje też: {', '.join(missing)}." if missing else ""))
        else:
            text = "Brak sygnału kupna na ostatniej świecy."
    wins = [t for t in trades if t["pct"] > 0]
    stats = {"n": len(trades), "win": len(wins) / len(trades) * 100 if trades else None,
             "avg": sum(t["pct"] for t in trades) / len(trades) if trades else None,
             "total": float((pd.Series([1 + t["pct"] / 100 for t in trades]).prod() - 1) * 100) if trades else None}
    return {"template": label, "strategy": strategy, "sl_pct": sl_pct, "tp_pct": tp_pct, "trades": trades,
            "open": {"entry_t": int(pos["entry_t"].timestamp()), "entry": pos["entry"]} if pos else None,
            "state": state, "lines": lines, "checks": checks, "text": text, "stats": stats}
