"""
Analiza formacji na wykresie (zakładka Wykresy → „Formacje”).

Wszystko liczone tylko na zamkniętych świecach, bez zaglądania w przyszłość:
- szczyty i dołki (punkty zwrotne potwierdzone po K świecach z każdej strony),
- poziomy wsparcia i oporu (skupiska szczytów/dołków, liczba „dotknięć”),
- linie trendu (przez dwa ostatnie dołki rosnące albo dwa ostatnie szczyty malejące),
- formacje cenowe: podwójny szczyt / dno, głowa z ramionami (i odwrócona), trójkąty, wybicie oporu / przebicie wsparcia,
- formacje świecowe z ostatnich dni: doji, młot, spadająca gwiazda, objęcie hossy / bessy, gwiazda poranna / wieczorna,
- kontekst: trend, RSI (wykupienie / wyprzedanie, dywergencja), przecięcie SMA 50/200, wolumen.
Dla formacji świecowych podajemy, jak zachowywała się cena po takich świecach na TYM symbolu w przeszłości.
To narzędzie do czytania wykresu, nie przepowiednia.
"""

import numpy as np
import pandas as pd

from .rules import atr as atr_series, rsi, sma

K = 5                 # punkt zwrotny = najwyzszy/najnizszy w oknie +-K swiec
LOOK = 160            # ile ostatnich swiec analizujemy pod formacje


def _t(ts):
    return int(pd.Timestamp(ts).timestamp())


def _p(v):
    return f"{v:,.2f}".replace(",", " ").replace(".", ",") if abs(v) >= 1 else f"{v:.6f}".replace(".", ",")


def pivots(d, k=K):
    h, l = d["high"].values, d["low"].values
    hi, lo = [], []
    for i in range(k, len(d) - k):
        if h[i] == h[i - k:i + k + 1].max() and (h[i] > h[i - k:i]).all():
            hi.append(i)
        if l[i] == l[i - k:i + k + 1].min() and (l[i] < l[i - k:i]).all():
            lo.append(i)
    return hi, lo


def levels(d, hi, lo, tol):
    """Skupiska punktow zwrotnych -> poziomy z liczba dotkniec."""
    pts = sorted([(d["high"].iloc[i], i) for i in hi] + [(d["low"].iloc[i], i) for i in lo])
    groups, cur = [], []
    for p, i in pts:
        if cur and p > cur[0][0] * (1 + tol):
            groups.append(cur)
            cur = []
        cur.append((p, i))
    if cur:
        groups.append(cur)
    close = float(d["close"].iloc[-1])
    out = []
    for g in groups:
        if len(g) < 2:
            continue
        price = float(np.mean([p for p, _ in g]))
        out.append({"price": price, "touches": len(g), "last": _t(d.index[max(i for _, i in g)]),
                    "kind": "support" if price < close else "resistance"})
    sup = sorted([x for x in out if x["kind"] == "support"], key=lambda x: -x["price"])[:3]
    res = sorted([x for x in out if x["kind"] == "resistance"], key=lambda x: x["price"])[:3]
    return sup + res


def trendlines(d, hi, lo):
    out = []
    n = len(d) - 1
    if len(lo) >= 2:
        a, b = lo[-2], lo[-1]
        pa, pb = d["low"].iloc[a], d["low"].iloc[b]
        if pb > pa and b - a >= 5:
            slope = (pb - pa) / (b - a)
            end = pa + slope * (n - a)
            broken = d["close"].iloc[-1] < end
            out.append({"kind": "support", "t1": _t(d.index[a]), "p1": float(pa), "t2": _t(d.index[n]), "p2": float(end),
                        "broken": bool(broken),
                        "text": ("Rosnąca linia wsparcia przez dwa ostatnie dołki" +
                                 (" — cena zamknęła się POD nią (sygnał słabości)." if broken else
                                  f" — dziś na poziomie ok. {_p(end)}."))})
    if len(hi) >= 2:
        a, b = hi[-2], hi[-1]
        pa, pb = d["high"].iloc[a], d["high"].iloc[b]
        if pb < pa and b - a >= 5:
            slope = (pb - pa) / (b - a)
            end = pa + slope * (n - a)
            broken = d["close"].iloc[-1] > end
            out.append({"kind": "resistance", "t1": _t(d.index[a]), "p1": float(pa), "t2": _t(d.index[n]), "p2": float(end),
                        "broken": bool(broken),
                        "text": ("Opadająca linia oporu przez dwa ostatnie szczyty" +
                                 (" — cena wybiła się NAD nią (sygnał siły)." if broken else
                                  f" — dziś na poziomie ok. {_p(end)}."))})
    return out


def chart_patterns(d, hi, lo, tol):
    out = []
    c = d["close"]
    last = float(c.iloc[-1])
    vol_ok = d["volume"].iloc[-1] > d["volume"].iloc[-21:-1].mean() * 1.3 if len(d) > 21 else False

    def add(name, kind, idxs, prices, text, confirmed, target=None, key=None):
        out.append({"key": key or PATTERN_KEYS.get(name, name), "name": name, "kind": kind,
                    "points": [[_t(d.index[i]), float(p)] for i, p in zip(idxs, prices)],
                    "t_end": _t(d.index[idxs[-1]]), "confirmed": bool(confirmed), "text": text, "target": target})

    # podwojny szczyt / dno (dwa ostatnie szczyty / dolki)
    if len(hi) >= 2:
        a, b = hi[-2], hi[-1]
        pa, pb = d["high"].iloc[a], d["high"].iloc[b]
        if b - a >= 10 and abs(pb / pa - 1) <= tol * 2:
            trough = d["low"].iloc[a:b + 1].min()
            ti = a + int(np.argmin(d["low"].iloc[a:b + 1].values))
            if trough < min(pa, pb) * 0.95:
                conf = last < trough
                tgt = trough - (max(pa, pb) - trough)
                add("Podwójny szczyt", "bear", [a, ti, b], [pa, trough, pb],
                    f"Dwa szczyty na podobnej wysokości (~{_p(max(pa, pb))}) — rynek dwa razy nie dał rady wyżej. "
                    + (f"Potwierdzony: cena zamknęła się pod dołkiem między szczytami ({_p(trough)}). Zasięg z formacji ok. {_p(tgt)}."
                       if conf else f"Potwierdzenie dopiero przy zamknięciu pod {_p(trough)}."), conf, tgt if conf else None)
    if len(lo) >= 2:
        a, b = lo[-2], lo[-1]
        pa, pb = d["low"].iloc[a], d["low"].iloc[b]
        if b - a >= 10 and abs(pb / pa - 1) <= tol * 2:
            peak = d["high"].iloc[a:b + 1].max()
            pi = a + int(np.argmax(d["high"].iloc[a:b + 1].values))
            if peak > max(pa, pb) * 1.05:
                conf = last > peak
                tgt = peak + (peak - min(pa, pb))
                add("Podwójne dno", "bull", [a, pi, b], [pa, peak, pb],
                    f"Dwa dołki na podobnej wysokości (~{_p(min(pa, pb))}) — sprzedający dwa razy nie zepchnęli ceny niżej. "
                    + (f"Potwierdzone: zamknięcie nad szczytem między dołkami ({_p(peak)}). Zasięg z formacji ok. {_p(tgt)}."
                       if conf else f"Potwierdzenie dopiero przy zamknięciu nad {_p(peak)}."), conf, tgt if conf else None)
    # glowa z ramionami (3 ostatnie szczyty) i odwrocona (3 ostatnie dolki)
    if len(hi) >= 3:
        l_, h_, r_ = hi[-3], hi[-2], hi[-1]
        pl, ph, pr = d["high"].iloc[l_], d["high"].iloc[h_], d["high"].iloc[r_]
        if ph > pl * 1.03 and ph > pr * 1.03 and abs(pl / pr - 1) <= tol * 3:
            t1 = d["low"].iloc[l_:h_ + 1].min()
            t2 = d["low"].iloc[h_:r_ + 1].min()
            neck = (t1 + t2) / 2
            conf = last < neck
            tgt = neck - (ph - neck)
            add("Głowa z ramionami", "bear", [l_, h_, r_], [pl, ph, pr],
                f"Trzy szczyty, środkowy najwyższy — klasyczny sygnał wyczerpania trendu wzrostowego. Linia szyi ok. {_p(neck)}. "
                + (f"Potwierdzona przebiciem szyi; zasięg ok. {_p(tgt)}." if conf else "Potwierdzenie dopiero pod linią szyi."),
                conf, tgt if conf else None)
    if len(lo) >= 3:
        l_, h_, r_ = lo[-3], lo[-2], lo[-1]
        pl, ph, pr = d["low"].iloc[l_], d["low"].iloc[h_], d["low"].iloc[r_]
        if ph < pl * 0.97 and ph < pr * 0.97 and abs(pl / pr - 1) <= tol * 3:
            t1 = d["high"].iloc[l_:h_ + 1].max()
            t2 = d["high"].iloc[h_:r_ + 1].max()
            neck = (t1 + t2) / 2
            conf = last > neck
            tgt = neck + (neck - ph)
            add("Odwrócona głowa z ramionami", "bull", [l_, h_, r_], [pl, ph, pr],
                f"Trzy dołki, środkowy najniższy — sygnał końca spadków. Linia szyi ok. {_p(neck)}. "
                + (f"Potwierdzona wybiciem szyi; zasięg ok. {_p(tgt)}." if conf else "Potwierdzenie dopiero nad linią szyi."),
                conf, tgt if conf else None)
    # trojkaty (2 ostatnie szczyty i 2 ostatnie dolki)
    if len(hi) >= 2 and len(lo) >= 2:
        h1, h2, l1, l2 = d["high"].iloc[hi[-2]], d["high"].iloc[hi[-1]], d["low"].iloc[lo[-2]], d["low"].iloc[lo[-1]]
        recent = min(hi[-2], lo[-2]) > len(d) - 90
        flat_h, flat_l = abs(h2 / h1 - 1) <= tol, abs(l2 / l1 - 1) <= tol
        if recent and h2 < h1 * (1 - tol) and l2 > l1 * (1 + tol):
            name, kind, txt = "Trójkąt symetryczny", "neutral", "Coraz niższe szczyty i coraz wyższe dołki — rynek się ściska; zwykle kończy się mocnym ruchem w stronę wybicia."
        elif recent and flat_h and l2 > l1 * (1 + tol):
            name, kind, txt = "Trójkąt zwyżkujący", "bull", f"Płaski opór (~{_p(max(h1, h2))}) i rosnące dołki — kupujący naciskają; częściej kończy się wybiciem w górę."
        elif recent and flat_l and h2 < h1 * (1 - tol):
            name, kind, txt = "Trójkąt zniżkujący", "bear", f"Płaskie wsparcie (~{_p(min(l1, l2))}) i coraz niższe szczyty — sprzedający naciskają; częściej kończy się przebiciem w dół."
        else:
            name = None
        if name:
            idx = sorted([hi[-2], hi[-1], lo[-2], lo[-1]])
            pr_ = [d["high"].iloc[i] if i in hi else d["low"].iloc[i] for i in idx]
            add(name, kind, idx, pr_, txt, False)
    # wybicie oporu / przebicie wsparcia (ostatnie 3 swiece, poziom z 60 swiec)
    if len(d) > 65:
        res = d["high"].iloc[-63:-3].max()
        sup = d["low"].iloc[-63:-3].min()
        if (c.iloc[-3:] > res).any() and c.iloc[-4] <= res:
            add("Wybicie oporu", "bull", [len(d) - 4, len(d) - 1], [res, last],
                f"Cena wybiła się ponad szczyt z ostatnich ~3 miesięcy ({_p(res)})" +
                (" przy zwiększonym wolumenie — mocniejszy sygnał." if vol_ok else " (wolumen bez wyraźnego wzrostu — słabsze potwierdzenie)."),
                True)
        if (c.iloc[-3:] < sup).any() and c.iloc[-4] >= sup:
            add("Przebicie wsparcia", "bear", [len(d) - 4, len(d) - 1], [sup, last],
                f"Cena spadła pod dołek z ostatnich ~3 miesięcy ({_p(sup)})" +
                (" przy zwiększonym wolumenie." if vol_ok else "."), True)
    return out


def _slope(i1, p1, i2, p2):
    return (p2 - p1) / max(1, (i2 - i1))


def more_patterns(d, hi, lo, tol):
    """Formacje dodatkowe: potrojny szczyt/dno, kliny, prostokat, flagi/choragiewki, filizanka z uszkiem, spodek, luki."""
    out = []
    c, h, l = d["close"], d["high"], d["low"]
    n = len(d) - 1
    last = float(c.iloc[-1])

    def add(key, name, kind, idxs, prices, text, confirmed, target=None):
        out.append({"key": key, "name": name, "kind": kind, "points": [[_t(d.index[i]), float(p)] for i, p in zip(idxs, prices)],
                    "t_end": _t(d.index[idxs[-1]]), "confirmed": bool(confirmed), "text": text, "target": target})

    # potrojny szczyt / dno
    if len(hi) >= 3:
        ids = hi[-3:]
        ps = [h.iloc[i] for i in ids]
        if max(ps) / min(ps) - 1 <= tol * 2 and ids[-1] - ids[0] >= 20:
            trough = l.iloc[ids[0]:ids[-1] + 1].min()
            if trough < min(ps) * 0.95:
                conf = last < trough
                add("triple_top", "Potrójny szczyt", "bear", ids, ps,
                    f"Trzy podejścia do oporu ~{_p(max(ps))} bez skutku — silny sygnał spadkowy po zejściu pod {_p(trough)}."
                    + (" Potwierdzony." if conf else ""), conf, trough - (max(ps) - trough) if conf else None)
    if len(lo) >= 3:
        ids = lo[-3:]
        ps = [l.iloc[i] for i in ids]
        if max(ps) / min(ps) - 1 <= tol * 2 and ids[-1] - ids[0] >= 20:
            peak = h.iloc[ids[0]:ids[-1] + 1].max()
            if peak > max(ps) * 1.05:
                conf = last > peak
                add("triple_bottom", "Potrójne dno", "bull", ids, ps,
                    f"Trzy odbicia od wsparcia ~{_p(min(ps))} — silny sygnał wzrostowy po wyjściu nad {_p(peak)}."
                    + (" Potwierdzone." if conf else ""), conf, peak + (peak - min(ps)) if conf else None)
    # kliny i prostokat (2 ostatnie szczyty i dolki)
    if len(hi) >= 2 and len(lo) >= 2 and min(hi[-2], lo[-2]) > n - 100:
        h1, h2, l1, l2 = hi[-2], hi[-1], lo[-2], lo[-1]
        sh, sl = _slope(h1, h.iloc[h1], h2, h.iloc[h2]), _slope(l1, l.iloc[l1], l2, l.iloc[l2])
        up_line = h.iloc[h2] + sh * (n - h2)
        low_line = l.iloc[l2] + sl * (n - l2)
        idx = sorted([h1, h2, l1, l2])
        pts = [h.iloc[i] if i in (h1, h2) else l.iloc[i] for i in idx]
        if sh > 0 and sl > 0 and sl > sh * 1.2:
            conf = last < low_line
            add("wedge_up", "Klin rosnący", "bear", idx, pts,
                "Wzrosty w zwężającym się kanale (dołki rosną szybciej niż szczyty) — kupującym brakuje siły; zwykle kończy się spadkiem."
                + (" Potwierdzony wyjściem dołem." if conf else ""), conf)
        elif sh < 0 and sl < 0 and abs(sh) > abs(sl) * 1.2:
            conf = last > up_line
            add("wedge_down", "Klin opadający", "bull", idx, pts,
                "Spadki w zwężającym się kanale (szczyty spadają szybciej niż dołki) — sprzedający słabną; zwykle kończy się wzrostem."
                + (" Potwierdzony wyjściem górą." if conf else ""), conf)
        top, bot = max(h.iloc[h1], h.iloc[h2]), min(l.iloc[l1], l.iloc[l2])
        if abs(h.iloc[h2] / h.iloc[h1] - 1) <= tol and abs(l.iloc[l2] / l.iloc[l1] - 1) <= tol and top / bot - 1 >= tol * 3:
            if last > top:
                kind, txt = "bull", f"Konsolidacja między {_p(bot)} a {_p(top)} zakończona wybiciem w górę."
            elif last < bot:
                kind, txt = "bear", f"Konsolidacja między {_p(bot)} a {_p(top)} zakończona wybiciem w dół."
            else:
                kind, txt = "neutral", f"Cena chodzi w prostokącie {_p(bot)}–{_p(top)}; kierunek wyznaczy wybicie (zasięg ≈ wysokość prostokąta)."
            add("rectangle", "Prostokąt (konsolidacja)", kind, idx, pts, txt, kind != "neutral",
                (top + (top - bot)) if kind == "bull" else (bot - (top - bot)) if kind == "bear" else None)
    # flaga / choragiewka: maszt + krotka konsolidacja
    a_pct = float(atr_series(d, 14).iloc[-1] / last) if len(d) > 20 else 0.02
    for L in range(5, 16):
        if n - L - 10 < 0:
            break
        pole_s, pole_e = n - L - 10, n - L
        ret = c.iloc[pole_e] / c.iloc[pole_s] - 1
        seg = d.iloc[pole_e + 1:]
        if len(seg) < 3:
            continue
        rng = seg["high"].max() - seg["low"].min()
        height = abs(c.iloc[pole_e] - c.iloc[pole_s])
        if height <= 0:
            continue
        if ret >= max(0.08, 4 * a_pct) and rng <= height * 0.5 and seg["low"].min() > c.iloc[pole_s] + height * 0.5:
            conf = last > seg["high"].iloc[:-1].max() if len(seg) > 1 else False
            add("flag_bull", "Flaga / chorągiewka wzrostowa", "bull", [pole_s, pole_e, n], [c.iloc[pole_s], c.iloc[pole_e], last],
                f"Szybki wzrost ({ret * 100:+.0f}%) i krótka, płytka konsolidacja — typowa pauza przed kontynuacją."
                + (f" Wybicie z flagi; zasięg ≈ wysokość masztu (~{_p(last + height)})." if conf else ""), conf, last + height if conf else None)
            break
        if ret <= -max(0.08, 4 * a_pct) and rng <= height * 0.5 and seg["high"].max() < c.iloc[pole_s] - height * 0.5:
            conf = last < seg["low"].iloc[:-1].min() if len(seg) > 1 else False
            add("flag_bear", "Flaga / chorągiewka spadkowa", "bear", [pole_s, pole_e, n], [c.iloc[pole_s], c.iloc[pole_e], last],
                f"Szybki spadek ({ret * 100:+.0f}%) i krótkie, płytkie odbicie — typowa pauza przed dalszymi spadkami."
                + (f" Wybicie dołem; zasięg ~{_p(last - height)}." if conf else ""), conf, last - height if conf else None)
            break
    # filizanka z uszkiem (ostatnie ~120 swiec)
    if len(d) >= 80:
        w = d.iloc[-120:]
        m = len(w)
        third = m // 3
        li = int(np.argmax(w["high"].values[:third]))
        left = w["high"].iloc[li]
        bi = li + int(np.argmin(w["low"].values[li:m - 5]))
        bottom = w["low"].iloc[bi]
        if third <= bi <= m - 15:
            ri = bi + int(np.argmax(w["high"].values[bi:m - 3]))
            right = w["high"].iloc[ri]
            depth = 1 - bottom / left
            handle = w.iloc[ri:]
            hdrop = 1 - handle["low"].min() / right if len(handle) > 1 else 0
            if 0.12 <= depth <= 0.40 and abs(right / left - 1) <= 0.05 and 3 <= m - 1 - ri <= 25 \
                    and hdrop <= depth * 0.5 and handle["low"].min() > bottom + (left - bottom) / 2:
                conf = last > max(left, right)
                base = len(d) - m
                add("cup", "Filiżanka z uszkiem", "bull", [base + li, base + bi, base + ri, n], [left, bottom, right, last],
                    f"Zaokrąglone dno (głębokość {depth * 100:.0f}%) i płytka korekta „uszka” pod oporem ~{_p(max(left, right))}."
                    + (f" Wybicie potwierdzone; zasięg ok. {_p(max(left, right) + (left - bottom))}." if conf else " Sygnał przy wybiciu ponad opór."),
                    conf, max(left, right) + (left - bottom) if conf else None)
    # spodek (dno zaokraglone): parabola na ostatnich 100 zamknieciach
    if len(d) >= 100:
        y = c.iloc[-100:].values
        x = np.arange(len(y))
        a2, a1, a0 = np.polyfit(x, y, 2)
        fit = a2 * x * x + a1 * x + a0
        r2 = 1 - ((y - fit) ** 2).sum() / max(((y - y.mean()) ** 2).sum(), 1e-12)
        vx = -a1 / (2 * a2) if a2 else -1
        if a2 > 0 and r2 >= 0.6 and 30 <= vx <= 70:
            vi = int(vx)
            base = len(d) - 100
            conf = last > y[0]
            add("rounding", "Spodek (dno zaokrąglone)", "bull", [base, base + vi, n], [y[0], fit[vi], last],
                "Powolne, łagodne odwrócenie spadków w wzrosty przez kilka miesięcy — zmiana nastrojów bez paniki."
                + (" Cena wróciła nad poziom z początku formacji." if conf else ""), conf)
    # luki cenowe (ostatnie 10 swiec) - tylko najnowsza, min. 1%
    for i in range(len(d) - 1, max(0, len(d) - 10), -1):
        if l.iloc[i] > h.iloc[i - 1] * 1.01:
            filled = (l.iloc[i:] <= h.iloc[i - 1]).any()
            add("gap_up", "Luka wzrostowa", "bull", [i - 1, i], [h.iloc[i - 1], l.iloc[i]],
                f"Otwarcie powyżej poprzedniego maksimum (luka {_p(h.iloc[i - 1])}–{_p(l.iloc[i])}) — silny popyt."
                + (" Luka już zamknięta." if filled else " Luka niezamknięta — często działa jak wsparcie."), True)
            break
        if h.iloc[i] < l.iloc[i - 1] * 0.99:
            filled = (h.iloc[i:] >= l.iloc[i - 1]).any()
            add("gap_down", "Luka spadkowa", "bear", [i - 1, i], [l.iloc[i - 1], h.iloc[i]],
                f"Otwarcie poniżej poprzedniego minimum (luka {_p(h.iloc[i])}–{_p(l.iloc[i - 1])}) — silna podaż."
                + (" Luka już zamknięta." if filled else " Luka niezamknięta — często działa jak opór."), True)
            break
    return out


PATTERN_KEYS = {"Podwójny szczyt": "double_top", "Podwójne dno": "double_bottom", "Głowa z ramionami": "hs",
                "Odwrócona głowa z ramionami": "ihs", "Trójkąt symetryczny": "tri_sym", "Trójkąt zwyżkujący": "tri_asc",
                "Trójkąt zniżkujący": "tri_desc", "Wybicie oporu": "breakout", "Przebicie wsparcia": "breakdown"}

CANDLE_INFO = {
    "doji": ("Doji", "neutral", "Otwarcie ≈ zamknięcie — niezdecydowanie rynku; po silnym ruchu bywa zapowiedzią zmiany."),
    "hammer": ("Młot", "bull", "Długi dolny cień po spadkach — sprzedający zepchnęli cenę, ale kupujący ją odkupili."),
    "shooting": ("Spadająca gwiazda", "bear", "Długi górny cień po wzrostach — kupujący wypchnęli cenę, ale nie utrzymali poziomu."),
    "bull_engulf": ("Objęcie hossy", "bull", "Duża świeca wzrostowa całkowicie obejmuje poprzednią spadkową — przejęcie inicjatywy przez kupujących."),
    "bear_engulf": ("Objęcie bessy", "bear", "Duża świeca spadkowa obejmuje poprzednią wzrostową — przejęcie inicjatywy przez sprzedających."),
    "morning": ("Gwiazda poranna", "bull", "Trzy świece: spadkowa, mała, mocna wzrostowa — typowe odwrócenie po spadkach."),
    "evening": ("Gwiazda wieczorna", "bear", "Trzy świece: wzrostowa, mała, mocna spadkowa — typowe odwrócenie po wzrostach."),
    "hanging": ("Wisielec", "bear", "Kształt młota, ale po wzrostach — sprzedający pokazali siłę w trakcie sesji; ostrzeżenie przed korektą."),
    "bull_harami": ("Harami hossy", "bull", "Mała świeca wewnątrz dużej spadkowej — impet spadków słabnie."),
    "bear_harami": ("Harami bessy", "bear", "Mała świeca wewnątrz dużej wzrostowej — impet wzrostów słabnie."),
    "soldiers": ("Trzech białych żołnierzy", "bull", "Trzy mocne świece wzrostowe z rzędu, każda zamyka się wyżej — zdecydowana przewaga kupujących."),
    "crows": ("Trzy czarne wrony", "bear", "Trzy mocne świece spadkowe z rzędu, każda zamyka się niżej — zdecydowana przewaga sprzedających."),
}


def candle_flags(d):
    o, h, l, c = d["open"], d["high"], d["low"], d["close"]
    body = (c - o).abs()
    rng = (h - l).replace(0, np.nan)
    up_sh = h - np.maximum(o, c)
    dn_sh = np.minimum(o, c) - l
    avg_body = body.rolling(20).mean()
    trend = c - c.shift(10)                               # kontekst: spadki / wzrosty przed swieca
    f = pd.DataFrame(index=d.index)
    f["doji"] = body <= rng * 0.1
    f["hammer"] = (dn_sh >= body * 2) & (up_sh <= body * 0.6) & (body > 0) & (trend < 0)
    f["shooting"] = (up_sh >= body * 2) & (dn_sh <= body * 0.6) & (body > 0) & (trend > 0)
    po, pc = o.shift(1), c.shift(1)
    f["bull_engulf"] = (pc < po) & (c > o) & (c >= po) & (o <= pc) & (body > avg_body) & (trend < 0)
    f["bear_engulf"] = (pc > po) & (c < o) & (c <= po) & (o >= pc) & (body > avg_body) & (trend > 0)
    b2, b1 = body.shift(2), body.shift(1)
    f["morning"] = (c.shift(2) < o.shift(2)) & (b2 > avg_body) & (b1 < avg_body * 0.5) & (c > o) & (c > (o.shift(2) + c.shift(2)) / 2) & (trend.shift(2) < 0)
    f["evening"] = (c.shift(2) > o.shift(2)) & (b2 > avg_body) & (b1 < avg_body * 0.5) & (c < o) & (c < (o.shift(2) + c.shift(2)) / 2) & (trend.shift(2) > 0)
    f["hanging"] = (dn_sh >= body * 2) & (up_sh <= body * 0.6) & (body > 0) & (trend > 0)
    big1 = b1 > avg_body
    inside = (np.maximum(o, c) < np.maximum(po, pc)) & (np.minimum(o, c) > np.minimum(po, pc)) & (body < b1 * 0.6)
    f["bull_harami"] = big1 & (pc < po) & inside & (trend < 0)
    f["bear_harami"] = big1 & (pc > po) & inside & (trend > 0)
    bull = (c > o) & (body > avg_body * 0.6)
    bear = (c < o) & (body > avg_body * 0.6)
    f["soldiers"] = bull & bull.shift(1) & bull.shift(2) & (c > c.shift(1)) & (c.shift(1) > c.shift(2)) & (trend.shift(2) <= 0)
    f["crows"] = bear & bear.shift(1) & bear.shift(2) & (c < c.shift(1)) & (c.shift(1) < c.shift(2)) & (trend.shift(2) >= 0)
    return f.fillna(False)


def candles(d, recent=10, horizon=10):
    f = candle_flags(d)
    fwd = (d["close"].shift(-horizon) / d["close"] - 1) * 100
    out = {}
    for i in range(max(0, len(d) - recent), len(d)):
        for key in ["morning", "evening", "soldiers", "crows", "bull_engulf", "bear_engulf", "bull_harami", "bear_harami",
                    "hammer", "hanging", "shooting", "doji"]:
            if f[key].iloc[i]:
                hist = fwd[f[key] & fwd.notna()]
                name, kind, txt = CANDLE_INFO[key]
                out[key] = {"t": _t(d.index[i]), "key": key, "name": name, "kind": kind, "text": txt,
                            "n": int(len(hist)), "p_up": float((hist > 0).mean() * 100) if len(hist) else None,
                            "med": float(hist.median()) if len(hist) else None, "horizon": horizon}
                break                                     # jedna (najwazniejsza) formacja na swiece
    return sorted(out.values(), key=lambda x: x["t"])     # kazdy rodzaj raz - najnowsze wystapienie


def indicators(d):
    c = d["close"]
    out = []
    r = rsi(c, 14)
    rv = float(r.iloc[-1])
    if rv >= 70:
        out.append({"name": f"RSI {rv:.0f} — wykupienie", "kind": "bear", "text": "Szybki wzrost; często korekta albo konsolidacja (w silnym trendzie RSI potrafi długo zostać wysoko)."})
    elif rv <= 30:
        out.append({"name": f"RSI {rv:.0f} — wyprzedanie", "kind": "bull", "text": "Szybki spadek; często odbicie (w silnym trendzie spadkowym RSI potrafi długo zostać nisko)."})
    else:
        out.append({"name": f"RSI {rv:.0f}", "kind": "neutral", "text": "Bez skrajnego wykupienia ani wyprzedania."})
    s50, s200 = sma(c, 50), sma(c, 200)
    if s200.notna().iloc[-1]:
        above = c.iloc[-1] > s200.iloc[-1]
        cross = ((s50 > s200) != (s50.shift(1) > s200.shift(1))) & s50.notna() & s200.notna()
        last_cross = cross[cross].index[-1] if cross.any() else None
        txt = "Cena nad średnią 200-sesyjną — długoterminowy trend wzrostowy." if above else "Cena pod średnią 200-sesyjną — długoterminowy trend spadkowy."
        if last_cross is not None and (d.index[-1] - last_cross).days <= 30:
            golden = s50.loc[last_cross] > s200.loc[last_cross]
            out.append({"name": "Złoty krzyż (SMA 50 nad 200)" if golden else "Krzyż śmierci (SMA 50 pod 200)",
                        "kind": "bull" if golden else "bear", "text": f"W ostatnich tygodniach ({last_cross:%d.%m}). " + txt})
        else:
            out.append({"name": "Nad SMA 200" if above else "Pod SMA 200", "kind": "bull" if above else "bear", "text": txt})
    hi, _ = pivots(d.iloc[-LOOK:], 3)
    if len(hi) >= 2:
        dd = d.iloc[-LOOK:]
        rr = r.iloc[-LOOK:]
        a, b = hi[-2], hi[-1]
        if dd["high"].iloc[b] > dd["high"].iloc[a] and rr.iloc[b] < rr.iloc[a] - 3 and len(dd) - b <= 15:
            out.append({"name": "Dywergencja RSI (spadkowa)", "kind": "bear",
                        "text": "Cena zrobiła wyższy szczyt, a RSI niższy — wzrost traci siłę."})
    v = d["volume"]
    if len(d) > 60 and v.iloc[-20:].mean() > 0:
        ratio = float(v.iloc[-10:].mean() / v.iloc[-60:].mean())
        ch = float(c.iloc[-1] / c.iloc[-11] - 1)
        if ratio >= 1.3:
            out.append({"name": f"Wolumen rośnie ({ratio:.1f}× średniej)", "kind": "bull" if ch > 0 else "bear",
                        "text": "Większe obroty " + ("przy wzrostach — ruch ma poparcie." if ch > 0 else "przy spadkach — presja sprzedających.")})
    return out


def analyze(df):
    d = df.dropna(subset=["open", "high", "low", "close"])
    if len(d) < 60:
        return {"error": "Za krótka historia do analizy formacji (min. 60 świec)."}
    w = d.iloc[-LOOK:] if len(d) > LOOK else d
    a = float(atr_series(w, 14).iloc[-1] / w["close"].iloc[-1])
    tol = min(max(a * 0.8, 0.01), 0.04)
    hi, lo = pivots(w)
    pats = chart_patterns(w, hi, lo, tol) + more_patterns(w, hi, lo, tol)
    keys = {p["key"] for p in pats}
    if "triple_top" in keys:
        pats = [p for p in pats if p["key"] != "double_top"]
    if "triple_bottom" in keys:
        pats = [p for p in pats if p["key"] != "double_bottom"]
    fresh = _t(w.index[max(0, len(w) - 40)])
    unconf = sorted([p for p in pats if not p["confirmed"] and p["t_end"] >= fresh], key=lambda p: -p["t_end"])[:2]
    pats = [p for p in pats if p["confirmed"]] + unconf
    res = {"levels": levels(w, hi, lo, tol), "lines": trendlines(w, hi, lo), "patterns": pats,
           "candles": candles(d), "indicators": indicators(d)}
    for p in res["patterns"]:
        p["w"] = {"bull": 2, "bear": -2}.get(p["kind"], 0) * (1.5 if p["confirmed"] else 1) * (0.5 if p["key"].startswith("gap") else 1)
    for cnd in res["candles"]:
        cnd["w"] = {"bull": 1, "bear": -1}.get(cnd["kind"], 0)
    for x in res["indicators"]:
        x["w"] = {"bull": 1, "bear": -1}.get(x["kind"], 0)
    for ln in res["lines"]:
        ln["w"] = (-1.5 if ln["kind"] == "support" else 1.5) if ln["broken"] else 0
    score = sum(x["w"] for grp in ("patterns", "candles", "indicators", "lines") for x in res[grp])
    bias = "bull" if score >= 2 else "bear" if score <= -2 else "neutral"
    close = float(d["close"].iloc[-1])
    sup = [x for x in res["levels"] if x["kind"] == "support"]
    rs = [x for x in res["levels"] if x["kind"] == "resistance"]
    txt = {"bull": "Przewaga sygnałów wzrostowych.", "bear": "Przewaga sygnałów spadkowych.", "neutral": "Sygnały mieszane — brak wyraźnej przewagi."}[bias]
    near = []
    if sup:
        near.append(f"najbliższe wsparcie {_p(sup[0]['price'])} ({_pct(sup[0]['price'] / close - 1)})")
    if rs:
        near.append(f"najbliższy opór {_p(rs[0]['price'])} ({_pct(rs[0]['price'] / close - 1)})")
    if near:
        txt += " " + ", ".join(near).capitalize() + "."
    res["summary"] = {"bias": bias, "score": score, "text": txt,
                      "near": (", ".join(near).capitalize() + ".") if near else ""}
    return res


def _pct(x):
    return f"{x * 100:+.1f}%".replace(".", ",").replace("-", "−")
