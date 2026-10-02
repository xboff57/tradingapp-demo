"""
Propozycje poprawek - kazda sprawdzona w czasie.

1. Okres testu dzielimy na:
     - probe (pierwsze 70%) - tu wybieramy, co wyglada na poprawe,
     - okres poza proba (ostatnie 30%) - tu sprawdzamy, czy poprawa sie utrzymala
       na danych, na ktorych nic nie bylo dobierane,
     - kilka kolejnych okresow (np. kwartaly) - sprawdzamy, w ilu z nich wariant byl lepszy.
2. Kazdy wariant zmienia JEDNA rzecz wzgledem bazowych ustawien.
3. Status:
     potwierdzona - lepsza w probie, lepsza poza proba, lepsza w >= 60% okresow,
                    obsuniecie nie gorsze o wiecej niz 25% i co najmniej 10 transakcji
     niepewna     - lepsza w probie, ale nie spelnia wszystkich warunkow
     odrzucona    - gorsza w probie
4. Na koniec laczymy potwierdzone zmiany z roznych grup i sprawdzamy polaczenie tak samo.
"""

import math

import pandas as pd

from .backtest import Context, core_metrics, simulate
from .report import oos_split, seg_return, time_windows
from .strategies import TIMEFRAMES, full_params
from . import rules as R
from .backtest import MAX_DAYS

SLOWER_TF = {tf: TIMEFRAMES[i + 1] for i, tf in enumerate(TIMEFRAMES[:-1])}


def _pct(v):
    return f"{v * 100:.1f}%".replace(".0%", "%")


def candidates(strategy, market, p, is_losers, days):
    """Lista wariantow: (klucz, grupa, opis, zmiany parametrow)."""
    out = []
    add = lambda key, group, label, ch: out.append({"key": key, "group": group, "label": label, "changes": ch})
    sl, tp = p["stop_loss_pct"], p["take_profit_pct"]
    add("sl_tighter", "sl", f"Ciaśniejszy stop-loss: {_pct(sl)} → {_pct(sl * 0.67)}", {"stop_loss_pct": round(sl * 0.67, 4)})
    add("sl_wider", "sl", f"Szerszy stop-loss: {_pct(sl)} → {_pct(sl * 1.5)}", {"stop_loss_pct": round(sl * 1.5, 4)})
    add("tp_lower", "tp", f"Niższy take-profit: {_pct(tp)} → {_pct(tp * 0.67)}", {"take_profit_pct": round(tp * 0.67, 4)})
    add("tp_higher", "tp", f"Wyższy take-profit: {_pct(tp)} → {_pct(tp * 1.5)}", {"take_profit_pct": round(tp * 1.5, 4)})
    mp = p["max_positions"]
    if mp > 1:
        add("fewer_positions", "pos", f"Mniej pozycji naraz: {mp} → {mp - 1}", {"max_positions": mp - 1})
    add("more_positions", "pos", f"Więcej pozycji naraz: {mp} → {mp + 2}", {"max_positions": mp + 2})
    add("regime_toggle", "regime",
        "Wyłączony filtr reżimu rynku" if p["regime_filter"] else f"Włączony filtr reżimu ({p['regime_symbol']} > SMA{p['regime_sma_days']})",
        {"regime_filter": not p["regime_filter"]})
    if market == "stocks" and len(p.get("symbols", [])) >= 10:
        bm = p.get("breadth_min", 0.5)
        if p.get("breadth_filter"):
            add("breadth_off", "breadth", "Bez filtra szerokości rynku", {"breadth_filter": False})
            add("breadth_looser", "breadth", f"Łagodniejszy filtr szerokości: {_pct(bm)} → {_pct(max(0.1, bm - 0.1))}",
                {"breadth_min": round(max(0.1, bm - 0.1), 2)})
            add("breadth_stricter", "breadth", f"Ostrzejszy filtr szerokości: {_pct(bm)} → {_pct(min(0.9, bm + 0.1))}",
                {"breadth_min": round(min(0.9, bm + 0.1), 2)})
        else:
            add("breadth_on", "breadth", f"Filtr szerokości: wejścia, gdy ≥{_pct(bm)} spółek nad SMA{p.get('breadth_sma_days', 200)}",
                {"breadth_filter": True})
    slower = SLOWER_TF.get(p["timeframe"])
    if slower and (MAX_DAYS.get(slower) is None or days <= MAX_DAYS[slower]):
        add("tf_slower", "tf", f"Dłuższy interwał świec: {p['timeframe']} → {slower}", {"timeframe": slower})
    if market == "stocks" and p["timeframe"] not in ("1Day",):
        fl = p.get("flatten_before_close_min", 0)
        add("flatten_toggle", "flatten",
            "Trzymanie pozycji przez noc (bez zamykania przed końcem sesji)" if fl else "Zamykanie pozycji 10 min przed końcem sesji",
            {"flatten_before_close_min": 0 if fl else 10})
    if is_losers and strategy != "copy_funds":
        keep = [s for s in p["symbols"] if s not in is_losers]
        if keep:
            add("drop_symbols", "symbols", f"Bez symboli stratnych w próbie: {', '.join(is_losers)}", {"symbols": keep})

    us_only = not any("." in x for x in p.get("symbols", []))   # SEC (insiderzy, 13F) dotyczy tylko spolek z USA
    if market == "stocks" and strategy != "copy_funds" and us_only:     # kopiowanie ma fundusze wbudowane
        labels = {"veto": "weto", "confirm": "potwierdzenie"}
        im, fm = p.get("insider_mode", "off"), p.get("funds_mode", "off")
        if im == "off":
            add("insider_veto", "ext_ins", "Insiderzy jako weto (nie kupuj przy klastrze sprzedaży)", {"insider_mode": "veto"})
            add("insider_confirm", "ext_ins", "Insiderzy jako potwierdzenie (kupuj po ich zakupach)", {"insider_mode": "confirm"})
        else:
            add("ext_off", "ext_ins", f"Bez filtra insiderów (teraz: {labels[im]})", {"insider_mode": "off"})
        if fm == "off":
            add("funds_veto", "ext_fund", "Fundusze 13F jako weto (nie kupuj, gdy sprzedają)", {"funds_mode": "veto"})
            add("funds_confirm", "ext_fund", "Fundusze 13F jako potwierdzenie (kupuj, gdy dokupili)", {"funds_mode": "confirm"})
        else:
            add("funds_off", "ext_fund", f"Bez filtra funduszy (teraz: {labels[fm]})", {"funds_mode": "off"})

    # wyjscia z pozycji
    tr = p.get("trail_atr_mult", 0) or 0
    if tr:
        add("trail_off", "trail", f"Bez stopu kroczącego (teraz {tr:g}× ATR)", {"trail_atr_mult": 0.0})
        add("trail_wider", "trail", f"Luźniejszy stop kroczący: {tr:g}× → {tr * 1.5:g}× ATR", {"trail_atr_mult": round(tr * 1.5, 2)})
    else:
        add("trail_on", "trail", "Stop kroczący 3× ATR pod szczytem", {"trail_atr_mult": 3.0})
    be = p.get("breakeven_after_pct", 0) or 0
    if be:
        add("breakeven_off", "be", "Bez przesuwania stopu na wejście", {"breakeven_after_pct": 0.0})
    else:
        add("breakeven_on", "be", f"Stop na wejście po zysku {_pct(tp / 2)}", {"breakeven_after_pct": round(tp / 2, 4)})
    hold = p.get("max_hold_days", 0) or 0
    if hold:
        add("hold_off", "hold", f"Bez limitu czasu (teraz {hold} dni)", {"max_hold_days": 0})
        add("hold_longer", "hold", f"Dłuższy limit czasu: {hold} → {hold * 2} dni", {"max_hold_days": min(365, hold * 2)})

    if strategy == "rules":
        ent, ex = p["entry_rules"], p["exit_rules"]
        if len(ent) > 1:
            for i, r in enumerate(ent):
                add(f"rule_drop_{i}", "entry_rules", f"Bez zasady kupna: {R.describe(r)}",
                    {"entry_rules": ent[:i] + ent[i + 1:]})
            other = "any" if p.get("entry_mode", "all") == "all" else "all"
            add("rule_mode", "entry_mode", "Kupno, gdy spełniona dowolna zasada (lub)" if other == "any"
                else "Kupno, gdy spełnione wszystkie zasady (i)", {"entry_mode": other})
        for i, r in enumerate(ent):                      # glowny parametr kazdej zasady +/- 50%
            spec = R.BY_ID[r["id"]]
            main = next((q for q in spec["params"] if q["key"] != "within"), None)
            if not main:
                continue
            for mult, word in ((0.67, "mniejszy"), (1.5, "większy")):
                v = r[main["key"]] * mult
                v = int(round(v)) if main["type"] == "int" else round(v, 4)
                v = min(max(v, main["min"]), main["max"])
                if v == r[main["key"]]:
                    continue
                nr = dict(r, **{main["key"]: v})
                if "fast" in nr and "slow" in nr and nr["fast"] >= nr["slow"]:
                    continue
                add(f"rule_tune_{i}_{word}", "entry_rules", f"Kupno: {R.describe(r)} → {R.describe(nr)}",
                    {"entry_rules": ent[:i] + [nr] + ent[i + 1:]})
        have = {r["id"] for r in ent}
        for rid, extra in (("price_above_sma", {"n": 200}), ("volume_spike", {"n": 20, "mult": 1.5}),
                           ("adx_strong", {"n": 14, "level": 25})):
            if rid not in have and p.get("entry_mode", "all") == "all":
                nr = R.normalize([{"id": rid, **extra}], "entry")[0]
                add(f"rule_add_{rid}", "entry_rules", f"Dodatkowa zasada kupna: {R.describe(nr)}", {"entry_rules": ent + [nr]})
        for i, r in enumerate(ex):
            add(f"exit_drop_{i}", "exit_rules", f"Bez zasady sprzedaży: {R.describe(r)}", {"exit_rules": ex[:i] + ex[i + 1:]})

    # uczenie maszynowe - model uczony kroczaco, wiec wariant jest tak samo uczciwy jak pozostale
    conf_names = {"low": "niska", "mid": "średnia", "high": "wysoka"}
    conf = p.get("ml_confidence", "mid")
    if strategy not in ("ml_model", "copy_funds"):
        if p.get("ml_filter"):
            add("ml_filter_off", "ml", "Bez filtra ML", {"ml_filter": False})
        else:
            add("ml_filter_on", "ml", "Filtr ML: model odrzuca wejścia poniżej progu opłacalności", {"ml_filter": True})
    if strategy == "ml_model" or p.get("ml_filter"):
        order = ["low", "mid", "high"]
        i = order.index(conf) if conf in order else 1
        if i < 2:
            add("ml_conf_higher", "ml_conf", f"Wyższa wymagana pewność ML: {conf_names[conf]} → {conf_names[order[i + 1]]}",
                {"ml_confidence": order[i + 1]})
        if i > 0:
            add("ml_conf_lower", "ml_conf", f"Niższa wymagana pewność ML: {conf_names[conf]} → {conf_names[order[i - 1]]}",
                {"ml_confidence": order[i - 1]})
        add("ml_sizing_toggle", "ml_size",
            "Stała wielkość pozycji (bez skalowania pewnością ML)" if p.get("ml_sizing") else "Wielkość pozycji wg pewności ML",
            {"ml_sizing": not p.get("ml_sizing")})

    if strategy == "sma_cross":
        f, s = p["sma_fast"], p["sma_slow"]
        add("sma_slower", "strat", f"Wolniejsze średnie: SMA {f}/{s} → {round(f * 1.5)}/{round(s * 1.5)}",
            {"sma_fast": round(f * 1.5), "sma_slow": round(s * 1.5)})
        if round(f * 0.67) >= 2 and round(s * 0.67) > round(f * 0.67):
            add("sma_faster", "strat", f"Szybsze średnie: SMA {f}/{s} → {round(f * 0.67)}/{round(s * 0.67)}",
                {"sma_fast": round(f * 0.67), "sma_slow": round(s * 0.67)})
        add("entry_stricter", "rsi", f"Ostrzejszy filtr RSI przy wejściu: < {p['rsi_max']:g} → < {p['rsi_max'] - 10:g}",
            {"rsi_max": p["rsi_max"] - 10})
        if p["rsi_max"] + 10 <= 100:
            add("rsi_looser", "rsi", f"Luźniejszy filtr RSI: < {p['rsi_max']:g} → < {p['rsi_max'] + 10:g}",
                {"rsi_max": p["rsi_max"] + 10})
        add("volume_toggle", "vol", "Bez potwierdzenia wolumenem" if p["use_volume"] else "Z potwierdzeniem wolumenem",
            {"use_volume": not p["use_volume"]})
    elif strategy == "copy_funds":
        mb, fr, tr = p["copy_min_bulls"], p["copy_fresh_days"], p["copy_trend_sma"]
        add("copy_bulls_more", "copy_bulls", f"Więcej zgodnych funduszy: min. {mb} → {mb + 1}", {"copy_min_bulls": mb + 1})
        if mb > 1:
            add("copy_bulls_less", "copy_bulls", f"Mniej zgodnych funduszy: min. {mb} → {mb - 1}", {"copy_min_bulls": mb - 1})
        add("copy_fresh_longer", "copy_fresh", f"Dłuższe okno po raporcie: {fr} → {min(120, fr * 3)} dni",
            {"copy_fresh_days": min(120, fr * 3)})
        if tr:
            add("copy_trend_off", "copy_trend", f"Bez filtra trendu (teraz SMA {tr})", {"copy_trend_sma": 0})
            add("copy_trend_longer", "copy_trend", f"Dłuższy filtr trendu: SMA {tr} → {min(250, tr * 2)}",
                {"copy_trend_sma": min(250, tr * 2)})
        else:
            add("copy_trend_on", "copy_trend", "Z filtrem trendu (cena nad SMA 50)", {"copy_trend_sma": 50})
        add("copy_sell_toggle", "copy_sell",
            "Nie sprzedawaj za funduszami (tylko stopy)" if p["copy_exit_on_sell"] else "Sprzedawaj, gdy fundusze sprzedają",
            {"copy_exit_on_sell": not p["copy_exit_on_sell"]})
    elif strategy == "mean_reversion":
        o, pb, ex, sp = p["rsi_oversold"], p["pct_below_sma"], p["rsi_exit"], p["sma_period"]
        add("entry_stricter", "rsi", f"Głębsze wyprzedanie: RSI < {o:g} → < {o - 5:g}", {"rsi_oversold": o - 5})
        add("rsi_looser", "rsi", f"Płytsze wyprzedanie: RSI < {o:g} → < {o + 5:g}", {"rsi_oversold": o + 5})
        add("dev_more", "dev", f"Większe odchylenie od średniej: {_pct(pb)} → {_pct(pb * 1.5)}", {"pct_below_sma": round(pb * 1.5, 4)})
        add("dev_less", "dev", f"Mniejsze odchylenie od średniej: {_pct(pb)} → {_pct(pb * 0.5)}", {"pct_below_sma": round(pb * 0.5, 4)})
        add("exit_later", "exit", f"Późniejsze wyjście: RSI > {ex:g} → > {ex + 10:g}", {"rsi_exit": min(90, ex + 10)})
        add("exit_earlier", "exit", f"Wcześniejsze wyjście: RSI > {ex:g} → > {ex - 5:g}", {"rsi_exit": max(30, ex - 5)})
        add("sma_longer", "strat", f"Dłuższa średnia: {sp} → {round(sp * 1.5)} świec", {"sma_period": round(sp * 1.5)})
        add("sma_shorter", "strat", f"Krótsza średnia: {sp} → {max(5, round(sp * 0.7))} świec", {"sma_period": max(5, round(sp * 0.7))})
    return out


def evaluate(ctx, strategy, p, windows, oos_start):
    sim = simulate(ctx, strategy, p)
    daily = sim["equity"].resample("1D").last().dropna()
    m = core_metrics(sim, ctx)
    is_ret = seg_return(daily, ctx.start, oos_start, ctx.capital)
    oos_ret = seg_return(daily, oos_start, ctx.end, ctx.capital)
    is_daily = daily[daily.index < oos_start]
    is_dd = float((is_daily / is_daily.cummax() - 1).min()) * 100 if len(is_daily) else 0
    return {
        "total": m["total_return_pct"], "dd": m["max_drawdown_pct"], "sharpe": m["sharpe"], "trades": m["trades"],
        "win_rate": m["win_rate_pct"], "is_ret": is_ret, "is_dd": is_dd, "oos_ret": oos_ret,
        "windows": [seg_return(daily, a, b, ctx.capital) for a, b in windows],
        "trades_list": sim["trades"],
    }


def judge(v, base, n_windows):
    better = [(x or 0) > (y or 0) + 0.05 for x, y in zip(v["windows"], base["windows"])]
    v["windows_better"] = sum(better)
    v["windows_better_flags"] = better
    is_better = (v["is_ret"] or 0) > (base["is_ret"] or 0) + 0.1
    oos_better = (v["oos_ret"] or 0) > (base["oos_ret"] or 0)
    dd_ok = v["dd"] >= base["dd"] * 1.25 - 0.5          # dd ujemne: nie gorsze o >25% (+0,5 pp tolerancji)
    enough = v["trades"] >= 10
    share = v["windows_better"] / n_windows if n_windows else 0
    reasons = []
    if not is_better:
        status = "rejected"
        reasons.append("gorsza albo równa w próbie")
    elif is_better and oos_better and share >= 0.6 and dd_ok and enough:
        status = "confirmed"
    else:
        status = "uncertain"
        if not oos_better:
            reasons.append("nie poprawia wyniku poza próbą")
        if share < 0.6:
            reasons.append(f"lepsza tylko w {v['windows_better']} z {n_windows} okresów")
        if not dd_ok:
            reasons.append("większe obsunięcie")
        if not enough:
            reasons.append("za mało transakcji")
    v["status"] = status
    v["reasons"] = reasons
    v["delta_total"] = v["total"] - base["total"]
    v["delta_oos"] = (v["oos_ret"] or 0) - (base["oos_ret"] or 0)
    return v


def run_optimization(provider, cfg, tag, progress=lambda x: None):
    strategy, market = cfg["strategy"], cfg["market"]
    base_p = full_params(market, strategy, cfg["params"])
    ctx = Context(provider, cfg, tag)
    windows = time_windows(ctx.start, ctx.end, market)
    oos_start = oos_split(ctx.start, ctx.end)
    days = (ctx.end - ctx.start).days

    progress(0.03)
    base = evaluate(ctx, strategy, base_p, windows, oos_start)
    # symbole stratne liczymy TYLKO z proby - okres poza proba zostaje nietkniety
    sym_pnl = {}
    for t in base["trades_list"]:
        if pd.Timestamp(t["t_out"]) < oos_start:
            s = sym_pnl.setdefault(t["symbol"], [0.0, 0])
            s[0] += t["pnl"]
            s[1] += 1
    is_losers = [s for s, (pnl, n) in sym_pnl.items() if pnl < 0 and n >= 3]

    cands = candidates(strategy, market, base_p, is_losers, days)
    results = []
    for i, c in enumerate(cands):
        progress(0.08 + 0.8 * i / max(len(cands), 1))
        p = dict(base_p, **c["changes"])
        try:
            v = evaluate(ctx, strategy, p, windows, oos_start)
        except Exception as e:
            results.append({**c, "status": "error", "reasons": [str(e)]})
            continue
        v.pop("trades_list")
        results.append({**c, **judge(v, base, len(windows))})

    # polaczenie najlepszych potwierdzonych zmian z roznych grup
    progress(0.9)
    combined = None
    best_by_group = {}
    for r in sorted([r for r in results if r.get("status") == "confirmed"], key=lambda r: -(r["is_ret"] or 0)):
        best_by_group.setdefault(r["group"], r)
    picks = list(best_by_group.values())[:3]
    if len(picks) >= 2:
        changes = {}
        for r in picks:
            changes.update(r["changes"])
        p = dict(base_p, **changes)
        try:
            v = evaluate(ctx, strategy, p, windows, oos_start)
            v.pop("trades_list")
            combined = {"key": "combined", "group": "combined", "changes": changes,
                        "label": "Połączenie: " + "; ".join(r["label"] for r in picks),
                        **judge(v, base, len(windows))}
        except Exception:
            combined = None

    base.pop("trades_list")
    order = {"confirmed": 0, "uncertain": 1, "rejected": 2, "error": 3}
    results.sort(key=lambda r: (order.get(r.get("status"), 9), -(r.get("oos_ret") or -1e9)))
    confirmed = [r for r in results if r.get("status") == "confirmed"]
    best = combined if combined and combined["status"] == "confirmed" else (confirmed[0] if confirmed else None)
    progress(1)
    return {
        "base": base,
        "windows": [{"start": a.date().isoformat(), "end": (b - pd.Timedelta(days=1)).date().isoformat()}
                    for a, b in windows],
        "oos_start": oos_start.date().isoformat(),
        "in_sample_end": (oos_start - pd.Timedelta(days=1)).date().isoformat(),
        "variants": results,
        "combined": combined,
        "best": best,
        "best_still_negative": bool(best and best["total"] < 0),
        "base_params": base_p,
        "tested": len(results) + (1 if combined else 0),
    }
