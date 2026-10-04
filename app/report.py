"""
Raport z backtestu: wyniki w czasie, diagnostyka i wnioski.

Wnioski sa HIPOTEZAMI z reguly "co wyglada podejrzanie". Kazdy ma powiazany
wariant, ktory modul optimize.py sprawdza w kolejnych okresach i poza proba -
dopiero tam okazuje sie, czy poprawka naprawde pomaga.
"""

import math
from collections import defaultdict

import numpy as np
import pandas as pd

from .strategies import TIMEFRAME_MINUTES

PL_TZ = "Europe/Warsaw"
WEEKDAYS = ["pon", "wt", "śr", "czw", "pt", "sob", "nd"]
MONTHS = ["sty", "lut", "mar", "kwi", "maj", "cze", "lip", "sie", "wrz", "paź", "lis", "gru"]
HOLD_BUCKETS = [(0, 1, "< 1 h"), (1, 4, "1-4 h"), (4, 24, "4-24 h"), (24, 72, "1-3 dni"),
                (72, 168, "3-7 dni"), (168, 1e9, "> 7 dni")]


def time_windows(start, end, market):
    """Kolejne, rozlaczne okresy do sprawdzania stabilnosci wynikow w czasie."""
    days = (end - start).days
    if days >= 300:
        k = min(8, max(3, round(days / 91)))       # ~kwartaly
    elif days >= 60:
        k = 4
    else:
        k = 3 if days >= 30 else 2
    step = (end - start) / k
    return [(start + step * i, start + step * (i + 1)) for i in range(k)]


def oos_split(start, end, share=0.3):
    """Poczatek okresu 'poza proba' - ostatnie 30% testu."""
    return end - (end - start) * share


def seg_return(daily, a, b, base):
    """Zwrot w okresie [a, b): od wartosci na koniec poprzedniego dnia do ostatniej w okresie."""
    before = daily[daily.index < a]
    inside = daily[(daily.index >= a) & (daily.index < b)]
    if inside.empty:
        return None
    start_val = before.iloc[-1] if not before.empty else base
    return float(inside.iloc[-1] / start_val - 1) * 100


def _stats(trades):
    n = len(trades)
    pnl = sum(t["pnl"] for t in trades)
    wins = sum(1 for t in trades if t["pnl"] > 0)
    return {"trades": n, "pnl": pnl, "win_rate": wins / n * 100 if n else None,
            "avg_pct": float(np.mean([t["pnl_pct"] for t in trades])) if n else None}


def periods(daily, bench_daily, trades, capital, freq):
    idx = daily.index.to_period(freq)
    out = []
    by_exit = defaultdict(list)
    for t in trades:
        by_exit[pd.Timestamp(t["t_out"]).tz_convert("UTC").to_period(freq)].append(t)
    for per in sorted(set(idx)):
        a, b = per.start_time.tz_localize("UTC"), per.end_time.tz_localize("UTC")
        r = seg_return(daily, a, b, capital)
        rb = seg_return(bench_daily, a, b, capital)
        label = f"{MONTHS[per.month - 1]} {per.year}" if freq == "M" else f"Q{per.quarter} {per.year}"
        out.append({"label": label, "key": str(per), "start": a.date().isoformat(),
                    "end": min(b, daily.index[-1]).date().isoformat(),
                    "ret": r, "bench": rb, **_stats(by_exit.get(per, []))})
    return out


def build_report(sim, ctx, p, cfg, metrics, bench):
    eq, trades = sim["equity"], sim["trades"]
    daily = eq.resample("1D").last().dropna()
    bench_daily = bench.resample("1D").last().dropna()
    cap = ctx.capital
    intraday = TIMEFRAME_MINUTES[p["timeframe"]] < 1440

    months = periods(daily, bench_daily, trades, cap, "M")
    quarters = periods(daily, bench_daily, trades, cap, "Q")
    full_months = [m for m in months if m["ret"] is not None]
    active_months = [m for m in full_months if m["trades"] > 0 or abs(m["ret"]) > 0.05]

    # --- stabilnosc w czasie
    windows = time_windows(ctx.start, ctx.end, ctx.market)
    win_rows = []
    for a, b in windows:
        tr = [t for t in trades if a <= pd.Timestamp(t["t_out"]) < b]
        win_rows.append({"start": a.date().isoformat(), "end": (b - pd.Timedelta(days=1)).date().isoformat(),
                         "ret": seg_return(daily, a, b, cap), "bench": seg_return(bench_daily, a, b, cap),
                         **_stats(tr)})
    mid = ctx.start + (ctx.end - ctx.start) / 2
    halves = {"first": seg_return(daily, ctx.start, mid, cap), "second": seg_return(daily, mid, ctx.end, cap),
              "bench_first": seg_return(bench_daily, ctx.start, mid, cap),
              "bench_second": seg_return(bench_daily, mid, ctx.end, cap), "mid": mid.date().isoformat()}

    streak = best_streak = 0
    for m in active_months:
        streak = streak + 1 if m["ret"] < 0 else 0
        best_streak = max(best_streak, streak)
    consistency = {
        "months": len(full_months),
        "active_months": len(active_months),
        "positive_months": sum(1 for m in active_months if m["ret"] > 0),
        "beat_bench_months": sum(1 for m in full_months if m["bench"] is not None and m["ret"] > m["bench"]),
        "best": max(full_months, key=lambda m: m["ret"]) if full_months else None,
        "worst": min(full_months, key=lambda m: m["ret"]) if full_months else None,
        "longest_losing_streak": best_streak,
        "windows_positive": sum(1 for w in win_rows if (w["ret"] or 0) > 0),
        "windows_beat_bench": sum(1 for w in win_rows if w["ret"] is not None and w["bench"] is not None
                                  and w["ret"] > w["bench"]),
        "windows": len(win_rows),
    }

    # --- obsuniecia
    dd = daily / daily.cummax() - 1
    under, longest, start_u = 0, 0, None
    for ts, v in dd.items():
        if v < -1e-9:
            under += 1
            longest = max(longest, under)
        else:
            under = 0
    step = max(1, len(dd) // 1500)
    drawdown = {"series": [[t.isoformat(), round(v * 100, 2)] for t, v in dd.iloc[::step].items()],
                "max_pct": float(dd.min()) * 100 if len(dd) else 0,
                "max_date": dd.idxmin().date().isoformat() if len(dd) else None,
                "longest_days": longest, "current_pct": float(dd.iloc[-1]) * 100 if len(dd) else 0}

    # --- diagnostyka transakcji
    by_reason = defaultdict(list)
    by_weekday = defaultdict(list)
    by_hour = defaultdict(list)
    by_hold = defaultdict(list)
    for t in trades:
        by_reason[t["reason"]].append(t)
        tin = pd.Timestamp(t["t_in"]).tz_convert(PL_TZ)
        by_weekday[tin.weekday()].append(t)
        by_hour[tin.hour].append(t)
        for lo, hi, label in HOLD_BUCKETS:
            if lo <= t["hours"] < hi:
                by_hold[label].append(t)
                break
    exits = sorted([{"reason": r, **_stats(ts)} for r, ts in by_reason.items()], key=lambda x: -x["trades"])
    weekdays = [{"label": WEEKDAYS[d], **_stats(by_weekday[d])} for d in range(7) if by_weekday[d]]
    hours = [{"label": f"{h:02d}:00", **_stats(by_hour[h])} for h in sorted(by_hour)] if intraday else []
    holding = [{"label": label, **_stats(by_hold[label])} for _, _, label in HOLD_BUCKETS if by_hold[label]]

    gross_profit = sum(t["gross"] for t in trades if t["gross"] > 0)
    fees = sum(t["fees"] for t in trades)
    costs = {"fees": fees, "gross": sum(t["gross"] for t in trades), "net": sum(t["pnl"] for t in trades),
             "fees_share_of_gross_profit": fees / gross_profit * 100 if gross_profit > 0 else None}

    sym_half = defaultdict(lambda: [0.0, 0.0, 0])
    for t in trades:
        k = 0 if pd.Timestamp(t["t_out"]) < mid else 1
        sym_half[t["symbol"]][k] += t["pnl"]
        sym_half[t["symbol"]][2] += 1
    symbols = sorted([{"symbol": s, "pnl_first": v[0], "pnl_second": v[1], "trades": v[2],
                       "consistent_loser": v[0] < 0 and v[1] < 0 and v[2] >= 4}
                      for s, v in sym_half.items()], key=lambda x: x["pnl_first"] + x["pnl_second"])

    report = {
        "months": months, "quarters": quarters, "windows": win_rows, "halves": halves,
        "consistency": consistency, "drawdown": drawdown, "exits": exits, "weekdays": weekdays,
        "hours": hours, "holding": holding, "costs": costs, "symbols": symbols,
        "oos_start": oos_split(ctx.start, ctx.end).date().isoformat(),
        "ml": (sim.get("ext") or {}).get("ml"),
        "external": dict({k: v for k, v in (sim.get("ext") or {}).items() if k != "ml"}, insider_mode=p.get("insider_mode", "off"),
                         funds_mode=p.get("funds_mode", "off")),
    }
    report["findings"] = findings(report, metrics, p, cfg, trades)
    return report


def _f(sev, title, detail, suggestion=None, variant=None):
    return {"severity": sev, "title": title, "detail": detail, "suggestion": suggestion, "variant": variant}


def findings(r, m, p, cfg, trades):
    """Wnioski regułowe - każdy z liczbami, z których wynika, i propozycją do sprawdzenia."""
    out = []
    n = m["trades"]
    c = r["consistency"]
    exits = {e["reason"]: e for e in r["exits"]}
    loss_sum = -sum(t["pnl"] for t in trades if t["pnl"] <= 0) or 1e-9

    if n < 30:
        out.append(_f("warn", "Mała próba",
                      f"Tylko {n} transakcji w całym okresie. Przy tak małej liczbie wynik może być dziełem przypadku.",
                      "Wydłuż okres testu albo dodaj symbole, zanim wyciągniesz wnioski."))

    beat = m["total_return_pct"] - m["benchmark_return_pct"]
    if beat < 0:
        out.append(_f("bad" if beat < -10 else "warn", "Przegrywa z „kup i trzymaj”",
                      f"Strategia {m['total_return_pct']:+.1f}% vs kup i trzymaj {m['benchmark_return_pct']:+.1f}% "
                      f"(różnica {beat:+.1f} pp). W rynku przez {m['exposure_pct']:.0f}% czasu.",
                      "Sprawdź w propozycjach, czy zmiana filtra reżimu albo luźniejsze wejścia zwiększają czas "
                      "w rynku bez wzrostu obsunięcia.", "regime_toggle"))
    else:
        out.append(_f("good", "Lepsza niż „kup i trzymaj”",
                      f"Strategia {m['total_return_pct']:+.1f}% vs kup i trzymaj {m['benchmark_return_pct']:+.1f}% "
                      f"(+{beat:.1f} pp)."))

    if c["windows"]:
        share = c["windows_positive"] / c["windows"]
        mshare = c["positive_months"] / c["active_months"] if c["active_months"] else 0
        wtxt = f"Zyskowne okresy: {c['windows_positive']} z {c['windows']}"
        if c["active_months"]:
            wtxt += f"; zyskowne miesiące (z transakcjami): {c['positive_months']} z {c['active_months']}"
        if share >= 0.75 and mshare >= 0.5:
            out.append(_f("good", "Wynik stabilny w czasie", wtxt + "."))
        elif share < 0.5 or mshare < 0.4:
            out.append(_f("bad", "Wynik niestabilny w czasie",
                          wtxt + f". Najdłuższa seria stratnych miesięcy: {c['longest_losing_streak']}.",
                          "Zysk pochodzi z nielicznych okresów — traktuj go ostrożnie. Szukaj wariantu, który "
                          "poprawia większość okresów, a nie tylko sumę."))

    h = r["halves"]
    if h["first"] is not None and h["second"] is not None and h["first"] - h["second"] > 5:
        out.append(_f("warn", "Pogorszenie w drugiej połowie",
                      f"Pierwsza połowa (do {h['mid']}): {h['first']:+.1f}%, druga: {h['second']:+.1f}%. "
                      f"Kup i trzymaj: {h['bench_first'] or 0:+.1f}% / {h['bench_second'] or 0:+.1f}%.",
                      "Możliwa zmiana charakteru rynku. Wynik z ostatnich miesięcy lepiej przewiduje najbliższą "
                      "przyszłość niż średnia z całego okresu."))

    cost = r["costs"]
    if cost["fees_share_of_gross_profit"] and cost["fees_share_of_gross_profit"] > 30:
        per_month = n / max(c["months"], 1)
        out.append(_f("bad" if cost["fees_share_of_gross_profit"] > 60 else "warn", "Koszty zjadają zysk",
                      f"Koszty transakcji {cost['fees']:,.0f} USD to {cost['fees_share_of_gross_profit']:.0f}% "
                      f"zysku brutto z wygranych transakcji. Średnio {per_month:.0f} transakcji miesięcznie.",
                      "Dłuższy interwał świec zwykle daje mniej, ale lepszych sygnałów.", "tf_slower"))

    sl = exits.get("stop-loss")
    if sl and n >= 10:
        sl_loss = -sum(t["pnl"] for t in trades if t["reason"] == "stop-loss" and t["pnl"] < 0)
        share = sl_loss / loss_sum * 100
        if share > 60:
            out.append(_f("warn", "Straty głównie ze stop-lossa",
                          f"{sl['trades']} wyjść na stopie odpowiada za {share:.0f}% wszystkich strat "
                          f"(stop {p['stop_loss_pct'] * 100:.1f}%).",
                          "Stop może być za ciasny względem zmienności — sprawdź szerszy stop albo ostrzejszy "
                          "filtr wejścia.", "sl_wider"))
    special = cfg.get("strategy") in ("grid", "dca")
    tp = exits.get("take-profit")
    tp_share = (tp["trades"] if tp else 0) / n * 100 if n else 0
    if n >= 20 and tp_share < 5 and not special:
        out.append(_f("info", "Take-profit prawie nie działa",
                      f"Tylko {tp_share:.0f}% transakcji kończy się na TP ({p['take_profit_pct'] * 100:.1f}%). "
                      "Pozycje zamyka głównie sygnał wyjścia albo stop.",
                      "Niższy take-profit może zamykać zyski, zanim cena zawróci.", "tp_lower"))
    sig = exits.get("sygnal wyjscia")
    if sig and sig["trades"] >= 10 and sig["avg_pct"] is not None and sig["avg_pct"] < 0:
        out.append(_f("warn", "Wyjścia z sygnału średnio stratne",
                      f"{sig['trades']} wyjść z sygnału, średni wynik {sig['avg_pct']:+.2f}%.",
                      "Sygnał wyjścia może reagować za późno albo wejścia są zbyt częste — sprawdź warianty "
                      "parametrów strategii w propozycjach.", "entry_stricter"))

    losers = [s["symbol"] for s in r["symbols"] if s["consistent_loser"]]
    if losers:
        out.append(_f("warn", "Symbole stratne w obu połowach testu",
                      f"{', '.join(losers)} tracą zarówno w pierwszej, jak i w drugiej połowie okresu.",
                      "Kandydaci do usunięcia z listy — propozycja sprawdza to bez zaglądania w dane poza próbą.",
                      "drop_symbols"))

    for title, rows in (("Słaby dzień tygodnia", r["weekdays"]), ("Słaba godzina wejścia (czas PL)", r["hours"])):
        bad = [x for x in rows if x["trades"] >= 10 and x["pnl"] < 0 and (x["win_rate"] or 0) < 35]
        if bad:
            worst = min(bad, key=lambda x: x["pnl"])
            out.append(_f("info", f"{title}: {worst['label']}",
                          f"{worst['trades']} transakcji, wynik {worst['pnl']:+,.0f} USD, "
                          f"skuteczność {worst['win_rate']:.0f}%.",
                          "Można rozważyć pomijanie wejść w tym czasie — ale tylko jeśli wzorzec powtarza się "
                          "w kolejnych miesiącach (sprawdź tabelę miesięczną)."))

    dd = abs(m["max_drawdown_pct"])
    if dd > 15 and (m["total_return_pct"] <= 0 or dd > 2 * abs(m["total_return_pct"])):
        out.append(_f("bad", "Obsunięcie duże względem zysku",
                      f"Maks. obsunięcie {dd:.1f}% przy zwrocie {m['total_return_pct']:+.1f}%. "
                      f"Najdłużej pod wodą: {r['drawdown']['longest_days']} dni.",
                      ("Ustaw stop (siatka: pod dolną krawędzią, DCA: pod średnią) albo mniejszy budżet / mniej "
                       "dokupień." if special else
                       f"Zmniejsz ryzyko na transakcję (teraz {p['risk_per_trade_pct'] * 100:.1f}%) albo liczbę "
                       "jednoczesnych pozycji."), None if special else "fewer_positions"))

    ex = r.get("external") or {}
    if ex.get("active"):
        raw, blocked = ex.get("raw", 0), ex.get("blocked", 0)
        share = blocked / raw * 100 if raw else 0
        modes = []
        if ex.get("insider_mode") != "off":
            modes.append(f"insiderzy: {ex['insider_mode']}")
        if ex.get("funds_mode") != "off":
            modes.append(f"fundusze: {ex['funds_mode']}")
        out.append(_f("info", "Sygnały zewnętrzne",
                      f"Filtr ({', '.join(modes)}) zablokował {blocked} z {raw} sygnałów wejścia ({share:.0f}%).",
                      "Czy filtr pomaga, pokazuje porównanie z wariantem bez filtra w zakładce propozycji.",
                      "ext_off"))
        if ex.get("insider_mode") != "off":
            empty = [s for s, v in ex.get("per_symbol", {}).items() if not v.get("insider_events")]
            if empty:
                out.append(_f("warn" if ex["insider_mode"] == "confirm" else "info", "Brak danych insiderów",
                              f"Dla {', '.join(empty)} nie ma zgłoszeń Form 4 w tym okresie (np. ETF albo spółka "
                              "spoza USA).",
                              "W trybie „confirm” te symbole nie dostaną żadnego wejścia — usuń je albo użyj „veto”."))

    mlr = r.get("ml")
    if mlr:
        a = mlr.get("auc")
        if a is None:
            out.append(_f("bad", "Model ML nie miał z czego się uczyć",
                          "Za mało danych albo przykładów jednej klasy - model nie powstał w żadnym okresie, "
                          "więc wejścia z ML były wstrzymane.",
                          "Wydłuż okres testu, okno uczenia albo dodaj symbole."))
        elif a < 0.52:
            out.append(_f("bad", "Model ML nie odróżnia dobrych wejść od złych",
                          f"AUC poza próbą {a:.2f} (0,50 = rzut monetą). Trafność powyżej progu "
                          f"{mlr.get('hit_rate') or 0:.0f}% vs średnio {mlr.get('base_rate') or 0:.0f}%.",
                          "Na tych danych ML nie wnosi przewagi - lepiej zostać przy strategii bez modelu.",
                          "ml_filter_off" if p.get("ml_filter") else None))
        else:
            lift = (mlr.get("hit_rate") or 0) - (mlr.get("base_rate") or 0)
            out.append(_f("good" if a >= 0.55 and lift > 3 else "info", "Model ML poza próbą",
                          f"AUC {a:.2f}; wejścia powyżej progu ({mlr['threshold']:.0f}%) trafiały TP w "
                          f"{mlr.get('hit_rate') or 0:.0f}% przypadków vs {mlr.get('base_rate') or 0:.0f}% średnio "
                          f"({lift:+.0f} pp). Model uczony krocząco {mlr.get('refits_ok', 0)} razy.",
                          "Czy ML poprawia wynik bota, pokazuje porównanie w zakładce propozycji."))
        if mlr.get("mode") == "filter" and mlr.get("blocked"):
            out.append(_f("info", "Filtr ML",
                          f"Model odrzucił {mlr['blocked']} sygnałów wejścia strategii.",
                          "Porównanie z wariantem bez filtra ML jest w propozycjach.", "ml_filter_off"))

    ratio = p["risk_per_trade_pct"] / p["stop_loss_pct"]
    if ratio > 1:
        out.append(_f("warn", "Ryzyko większe niż stop",
                      f"Ryzyko/stop = {ratio:.2f}. Pozycje są przycinane limitem budżetu, więc ustawione ryzyko "
                      "nie odpowiada realnemu.", "Ustaw ryzyko poniżej wartości stopu."))

    order = {"bad": 0, "warn": 1, "info": 2, "good": 3}
    return sorted(out, key=lambda f: order[f["severity"]])
