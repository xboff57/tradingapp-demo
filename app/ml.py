"""
Silnik uczenia maszynowego.

Model nie handluje "z glowy". Uczy sie na historii odpowiedzi na jedno pytanie:
    "jesli kupie na zamknieciu tej swiecy, z TYM stop-lossem i TYM take-profitem,
     to czy trafie take-profit, zanim trafie stop?"   (etykieta potrojnej bariery)

Z tej samej odpowiedzi (prawdopodobienstwa sukcesu) korzystaja trzy tryby:
  - filtr sygnalow   - strategia proponuje wejscie, model odrzuca te ze zbyt niska szansa,
  - strategia ML     - model sam wybiera wejscia, wychodzi gdy szansa spada,
  - wielkosc pozycji - przy progu pol pozycji, przy wyzszej pewnosci pelna (nigdy wiecej niz dzis).

Prog nie jest "na oko": liczymy prog oplacalnosci  p* = (SL + 2 x koszt) / (TP + SL),
czyli szanse, przy ktorej wejscie wychodzi na zero. Wejscie dopiero powyzej p* + margines.

Uczciwosc testow:
  - cechy licza sie wylacznie z zamknietych swiec, dane rynku (rezim) z dnia poprzedniego,
  - w backtescie model uczy sie KROCZACO: dla kazdego miesiaca tylko na danych sprzed niego,
    a przyklady, ktorych wynik nie byl jeszcze znany w chwili uczenia, sa wyrzucane (purging),
  - nowy model dla bota na zywo przechodzi ten sam test w czasie co propozycje poprawek
    i czeka na Twoja akceptacje w panelu.

Model: HistGradientBoosting (scikit-learn) - drzewa decyzyjne, dziala na zwyklym procesorze.
"""

import json
import math
import os
import re
import threading
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from .config import DATA_DIR

MODELS_DIR = os.path.join(DATA_DIR, "models")
os.makedirs(MODELS_DIR, exist_ok=True)

ML_WARMUP = 110                       # swiece potrzebne do policzenia cech (najdluzsza: 100)
ML_KEYS = ["ml_filter", "ml_sizing", "ml_confidence", "ml_horizon", "ml_train_days", "ml_exit_drop"]
CONF_MARGIN = {"low": 0.0, "mid": 0.05, "high": 0.10}
TRAIN_CAP = {"1Min": 30, "5Min": 90, "15Min": 180}   # dluzej = za duzo swiec, a stare 1-min dane malo mowia
MAX_ROWS = 80_000
MIN_ROWS = 300
MIN_CLASS = 30
AUC_MIN = 0.52

FEATURE_LABELS = {
    "r1": "zmiana ceny - 1 świeca", "r3": "zmiana ceny - 3 świece", "r6": "zmiana ceny - 6 świec",
    "r12": "zmiana ceny - 12 świec", "r24": "zmiana ceny - 24 świece",
    "vol20": "zmienność (20 świec)", "vol_ratio": "zmienność teraz vs zwykle", "atr": "średni zasięg świecy (ATR)",
    "rsi": "RSI 14", "d_sma20": "odległość od SMA 20", "d_sma50": "odległość od SMA 50",
    "d_sma100": "odległość od SMA 100", "slope20": "nachylenie SMA 20", "volz": "wolumen vs średni",
    "pos20": "pozycja w zakresie 20 świec", "body": "korpus ostatniej świecy",
    "hour": "godzina", "dow": "dzień tygodnia",
    "mkt_d50": "rynek: odległość od SMA 50", "mkt_r5": "rynek: zmiana 5 dni",
    "breadth": "szerokość rynku (% symboli nad SMA 50)", "rel_r24": "siła vs pozostałe symbole (24 świece)",
}
FEATURES = list(FEATURE_LABELS)


# ------------------------------------------------------------------ progi
def uses_ml(strategy, p):
    return strategy == "ml_model" or bool(p.get("ml_filter")) or bool(p.get("ml_sizing"))


def needs_model(strategy, p):
    """Bez modelu: filtr i strategia ML wstrzymuja wejscia; sama wielkosc pozycji dziala po staremu."""
    return strategy == "ml_model" or bool(p.get("ml_filter"))


def cost_for(market, acc_type=None):
    if acc_type == "kraken":
        return 0.004                            # Kraken taker przy malych obrotach
    if acc_type == "gielda":
        return 0.003                            # inne gieldy krypto: taker ok. 0,1-0,6% + poslizg
    if acc_type == "ibkr":
        return 0.001                            # IBKR: prowizja (GPW ok. 0,05%, min. kwota) + poslizg
    return 0.0025 if market == "crypto" else 0.0005


def breakeven(p, cost):
    return (p["stop_loss_pct"] + 2 * cost) / (p["take_profit_pct"] + p["stop_loss_pct"])


def threshold(p, cost):
    return min(0.95, breakeven(p, cost) + CONF_MARGIN.get(p.get("ml_confidence", "mid"), 0.05))


def exit_threshold(p, cost):
    return max(0.0, breakeven(p, cost) - p.get("ml_exit_drop", 0.05))


def size_scale(prob, p, cost):
    """Mnoznik pozycji 0,5-1,0: przy progu polowa, przy pewnosci o 10 pp wyzszej pelna. Nigdy > 1."""
    if not p.get("ml_sizing") or prob is None or (isinstance(prob, float) and math.isnan(prob)):
        return 1.0
    return float(np.clip(0.5 + 0.5 * (prob - threshold(p, cost)) / 0.10, 0.5, 1.0))


def train_days(p):
    return min(int(p.get("ml_train_days", 365)), TRAIN_CAP.get(p["timeframe"], 10_000))


def signature(p):
    """Model jest zwiazany z tym, czego sie uczyl - zmiana SL/TP/interwalu/horyzontu = nowy model."""
    return json.dumps(["f2", p["timeframe"], round(p["stop_loss_pct"], 5), round(p["take_profit_pct"], 5),
                       int(p.get("ml_horizon", 24))])


# ------------------------------------------------------------------ cechy
def _rsi(close, n=14):
    d = close.diff()
    up = d.clip(lower=0).rolling(n).mean()
    dn = (-d.clip(upper=0)).rolling(n).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def market_table(daily):
    """Dzienne dane symbolu rezimu -> cechy rynku liczone na zamknieciu dnia (uzywane NASTEPNEGO dnia)."""
    if daily is None or len(daily) < 10:
        return None
    c = daily["close"]
    t = pd.DataFrame({"mkt_d50": c / c.rolling(50, min_periods=20).mean() - 1, "mkt_r5": c / c.shift(5) - 1})
    t.index = pd.Index([ts.date() for ts in daily.index])
    return t.dropna(how="all")


def cross_section(bars):
    """Cechy 'calego rynku' z symboli bota w tej samej chwili: jaka czesc jest nad SMA 50
    i sredni ruch z 24 swiec (do liczenia sily danej monety wzgledem reszty)."""
    closes = pd.DataFrame({s: d["close"] for s, d in bars.items() if d is not None and len(d)})
    if closes.empty:
        return None
    closes = closes.sort_index()
    above = (closes > closes.rolling(50, min_periods=50).mean()).astype(float).where(closes.notna())
    return pd.DataFrame({"breadth": above.mean(axis=1),
                         "mean_r24": (closes / closes.shift(24) - 1).mean(axis=1)})


def features(df, mkt=None, cross=None):
    c, h, l, o, v = df["close"], df["high"], df["low"], df["open"], df["volume"]
    X = pd.DataFrame(index=df.index)
    for n in (1, 3, 6, 12, 24):
        X[f"r{n}"] = c / c.shift(n) - 1
    r1 = X["r1"]
    X["vol20"] = r1.rolling(20).std()
    X["vol_ratio"] = X["vol20"] / r1.rolling(100).std()
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    X["atr"] = tr.rolling(14).mean() / c
    X["rsi"] = _rsi(c) / 100
    for n in (20, 50, 100):
        X[f"d_sma{n}"] = c / c.rolling(n).mean() - 1
    sma20 = c.rolling(20).mean()
    X["slope20"] = sma20 / sma20.shift(5) - 1
    X["volz"] = np.log((v + 1) / (v.rolling(20).mean() + 1))
    hi, lo = h.rolling(20).max(), l.rolling(20).min()
    X["pos20"] = (c - lo) / (hi - lo).replace(0, np.nan)
    X["body"] = (c - o) / o
    X["hour"] = df.index.hour + df.index.minute / 60
    X["dow"] = df.index.dayofweek
    X["mkt_d50"] = np.nan
    X["mkt_r5"] = np.nan
    if mkt is not None and len(mkt):
        mdates = np.array(mkt.index, dtype="datetime64[D]")
        bdates = np.array(df.index.date, dtype="datetime64[D]")
        idx = np.searchsorted(mdates, bdates, side="left") - 1      # ostatni dzien PRZED dniem swiecy
        ok = idx >= 0
        for col in ("mkt_d50", "mkt_r5"):
            vals = np.full(len(df), np.nan)
            vals[ok] = mkt[col].values[idx[ok]]
            X[col] = vals
    X["breadth"] = np.nan
    X["rel_r24"] = np.nan
    if cross is not None and len(cross):
        cr = cross.reindex(df.index)
        X["breadth"] = cr["breadth"].values
        X["rel_r24"] = X["r24"] - cr["mean_r24"].values
    return X[FEATURES].replace([np.inf, -np.inf], np.nan).astype(float)


def labels(df, sl, tp, horizon, cost):
    """1 = take-profit przed stop-lossem, 0 = stop-loss (albo wynik czesciowy po 'horizon' swiecach).
    SL i TP w tej samej swiecy = SL (jak w backtescie). Zwraca (y, chwila poznania wyniku)."""
    c, h, l = df["close"].values, df["high"].values, df["low"].values
    n = len(df)
    y = np.full(n, np.nan)
    end = np.full(n, -1)
    done = np.zeros(n, bool)
    up, dn = c * (1 + tp), c * (1 - sl)
    idx = np.arange(n)
    for k in range(1, horizon + 1):
        hk = np.full(n, np.nan)
        lk = np.full(n, np.nan)
        if k < n:
            hk[:-k], lk[:-k] = h[k:], l[k:]
        valid = ~done & ~np.isnan(hk)
        hit_sl = valid & (lk <= dn)
        hit_tp = valid & (hk >= up) & ~hit_sl
        y[hit_sl], y[hit_tp] = 0, 1
        end[hit_sl | hit_tp] = idx[hit_sl | hit_tp] + k
        done |= hit_sl | hit_tp
    if horizon < n:
        ck = np.full(n, np.nan)
        ck[:-horizon] = c[horizon:]
        t = ~done & ~np.isnan(ck)
        # ani TP, ani SL w horyzoncie: wynik czesciowy liniowo miedzy -SL (0) a +TP (1) - dzieki temu
        # srednia etykieta = oczekiwany wynik i prog oplacalnosci p* = (SL + 2 koszt) / (TP + SL) jest dokladny
        y[t] = np.clip((ck[t] / c[t] - 1 + sl) / (tp + sl), 0, 1)
        end[t] = idx[t] + horizon
    end_ts = pd.Series(pd.NaT, index=df.index, dtype="datetime64[ns, UTC]")
    has = end >= 0
    end_ts.iloc[np.where(has)[0]] = df.index[end[has]]
    return pd.Series(y, index=df.index), end_ts


def dataset(bars, mkt, p, cost):
    parts = []
    cross = cross_section(bars)
    for sym, df in bars.items():
        if len(df) < ML_WARMUP + 10:
            continue
        X = features(df, mkt, cross)
        y, y_end = labels(df, p["stop_loss_pct"], p["take_profit_pct"], int(p.get("ml_horizon", 24)), cost)
        d = X.copy()
        d["y"], d["y_end"], d["sym"] = y, y_end, sym
        d["ts"] = pd.Series(df.index, index=df.index)
        parts.append(d.iloc[ML_WARMUP:])
    if not parts:
        return None
    return pd.concat(parts, ignore_index=True).sort_values("ts", kind="stable").reset_index(drop=True)


# ------------------------------------------------------------------ model
def new_model():
    from sklearn.ensemble import HistGradientBoostingClassifier
    return HistGradientBoostingClassifier(max_iter=150, learning_rate=0.05, max_leaf_nodes=15,
                                          min_samples_leaf=80, l2_regularization=1.0, random_state=7)


def _safe_columns(X):
    """Cecha bez co najmniej 2 roznych wartosci (np. krotka historia indeksu rynku) wywraca podzial na koszyki
    w niektorych wersjach scikit-learn - dostaje wtedy znikomy, staly szum (model i tak jej nie uzyje)."""
    X = X.copy()
    for j in range(X.shape[1]):
        fin = X[:, j][np.isfinite(X[:, j])]
        if len(np.unique(fin)) < 2:
            X[:, j] = (fin[0] if len(fin) else 0.0) + np.linspace(0, 1e-9, len(X))
    return X


def fit(train):
    """Uczy model na przykladach o znanym wyniku. None, gdy danych za malo albo jedna klasa."""
    train = train[train["y"].notna()]
    if len(train) > MAX_ROWS:
        train = train.iloc[-MAX_ROWS:]
    y = train["y"].values.astype(float)
    pos = float(y.sum())
    if len(train) < MIN_ROWS or pos < MIN_CLASS or len(train) - pos < MIN_CLASS:
        return None
    # wynik czesciowy y = dwa przyklady: "sukces" z waga y i "porazka" z waga 1-y
    X = _safe_columns(train[FEATURES].values.astype(float))
    Xs = np.vstack([X, X])
    ys = np.concatenate([np.ones(len(y), int), np.zeros(len(y), int)])
    ws = np.concatenate([y, 1 - y])
    keep = ws > 0
    m = new_model()
    m.fit(Xs[keep], ys[keep], sample_weight=ws[keep])
    return m


def predict(model, X):
    return model.predict_proba(np.asarray(X, dtype=float))[:, 1]


def auc(y, prob):
    from sklearn.metrics import roc_auc_score
    y, prob = np.asarray(y, dtype=float), np.asarray(prob, dtype=float)
    ok = ~np.isnan(y) & ~np.isnan(prob)
    yb = (y[ok] >= 0.5).astype(int)
    if ok.sum() < 50 or len(set(yb)) < 2:
        return None
    return float(roc_auc_score(yb, prob[ok]))


def calibration(y, prob, bins=5):
    """Czy pewnosc modelu sie sprawdza: przedzialy prawdopodobienstwa vs faktyczny odsetek sukcesow."""
    d = pd.DataFrame({"y": y, "p": prob}).dropna()
    if len(d) < 100:
        return []
    d["b"] = pd.qcut(d["p"], bins, duplicates="drop")
    out = []
    for b, g in d.groupby("b", observed=True):
        out.append({"from": float(b.left), "to": float(b.right), "predicted": float(g["p"].mean()) * 100,
                    "actual": float(g["y"].mean()) * 100, "n": int(len(g))})
    return out


def importance(model, sample):
    """Ktore cechy najbardziej wplywaja na decyzje (permutacja na probce danych)."""
    from sklearn.inspection import permutation_importance
    sample = sample[sample["y"].notna()]
    if len(sample) < 200 or model is None:
        return []
    sample = sample.iloc[-3000:]
    try:
        r = permutation_importance(model, sample[FEATURES].values, (sample["y"].values >= 0.5).astype(int),
                                   n_repeats=3, random_state=7, scoring="roc_auc")
    except Exception:
        return []
    items = sorted(zip(FEATURES, r.importances_mean), key=lambda x: -x[1])
    return [{"feature": f, "label": FEATURE_LABELS[f], "value": float(v)} for f, v in items[:8] if v > 0]


def walk_forward(data, start, end, days, progress=lambda x: None):
    """Prawdopodobienstwa dla [start, end) - kazdy odcinek przewiduje model uczony tylko na przeszlosci."""
    span = max((end - start).days, 1)
    step = timedelta(days=max(14, math.ceil(span / 12)))
    probs = np.full(len(data), np.nan)
    refits, last_model, last_train = [], None, None
    ts = data["ts"]
    d = start
    steps = math.ceil(span / step.days)
    i = 0
    while d < end:
        nxt = min(d + step, end)
        tr = data[(ts >= d - timedelta(days=days)) & (ts < d) & data["y_end"].notna() & (data["y_end"] < d)]
        model = fit(tr)
        mask = ((ts >= d) & (ts < nxt)).values
        if model is not None and mask.any():
            probs[mask] = predict(model, data.loc[mask, FEATURES].values)
            last_model, last_train = model, tr
        refits.append({"date": d.date().isoformat(), "train_rows": int(tr["y"].notna().sum()),
                       "base_rate": float(tr["y"].mean() * 100) if len(tr) else None, "ok": model is not None})
        i += 1
        progress(i / max(steps, 1))
        d = nxt
    return probs, refits, last_model, last_train


def summarize(data, probs, refits, thr, start, end):
    m = (data["ts"] >= start) & (data["ts"] < end)
    y, pr = data.loc[m, "y"].values, probs[m.values]
    ok = ~np.isnan(pr) & ~np.isnan(y)
    above = ok & (pr >= thr)
    return {
        "auc": auc(y, pr),
        "base_rate": float(y[ok].mean() * 100) if ok.any() else None,
        "hit_rate": float(y[above].mean() * 100) if above.any() else None,
        "coverage": float(above.sum() / ok.sum() * 100) if ok.any() else None,
        "threshold": thr * 100,
        "refits": refits,
        "refits_ok": sum(r["ok"] for r in refits),
        "calibration": calibration(y[ok], pr[ok]),
    }


# ------------------------------------------------------------------ backtest (wolane z Context)
def context_probs(ctx, strategy, p, cost, progress=None):
    """Dla backtestu: kroczace prawdopodobienstwa per symbol + podsumowanie. Cache w ctx."""
    from .data import cached_bars
    from .strategies import TIMEFRAME_MINUTES
    days = train_days(p)
    key = json.dumps([p["timeframe"], sorted(p["symbols"]), signature(p), days, p.get("regime_symbol"), cost])
    cache = ctx.__dict__.setdefault("_ml", {})
    if key in cache:
        return cache[key]
    pre = timedelta(days=days) + timedelta(days=ctx.pre_days(p["timeframe"], ML_WARMUP + 20))
    bars = cached_bars(ctx.provider, p["symbols"], p["timeframe"], ctx.start - pre, ctx.end, ctx.tag)
    mkt = None
    if p.get("regime_symbol"):
        daily = cached_bars(ctx.provider, [p["regime_symbol"]], "1Day", ctx.start - pre - timedelta(days=90),
                            ctx.end, ctx.tag).get(p["regime_symbol"])
        mkt = market_table(daily)
    data = dataset(bars, mkt, p, cost)
    if data is None:
        raise ValueError("Za mało danych do nauki modelu ML.")
    probs, refits, model, last_train = walk_forward(data, ctx.start, ctx.end, days,
                                                    progress=progress or (lambda x: None))
    info = summarize(data, probs, refits, threshold(p, cost), ctx.start, ctx.end)
    info.update(train_days=days, horizon=int(p.get("ml_horizon", 24)), breakeven=breakeven(p, cost) * 100,
                importance=importance(model, last_train) if model is not None else [])
    per_sym = {}
    for sym, g in data.assign(prob=probs).groupby("sym"):
        per_sym[sym] = pd.Series(g["prob"].values, index=pd.DatetimeIndex(g["ts"]))
    cache[key] = {"bars": bars, "probs": per_sym, "info": info, "data": data, "mkt": mkt}
    return cache[key]


def signal_frame(strat, df, p, prob, cost):
    """Sygnaly strategii z udzialem modelu. prob: Series zgodna z df.index (NaN = brak modelu)."""
    d = df.copy()
    d["ml_prob"] = prob.reindex(d.index) if isinstance(prob, pd.Series) else prob
    d = strat.compute(d, p)
    thr = threshold(p, cost)
    ok = (d["ml_prob"] >= thr).fillna(False)
    d["ml_blocked"] = False
    if strat.key == "ml_model":
        d["entry"] = ok.astype(bool)
        d["exit"] = (d["ml_prob"] < exit_threshold(p, cost)).fillna(False).astype(bool)
    elif p.get("ml_filter"):
        d["ml_blocked"] = (d["entry"] & ~ok).astype(bool)
        d["entry"] = (d["entry"] & ok).astype(bool)
    return d


# ------------------------------------------------------------------ modele botow na zywo
def model_path(bot_id):
    return os.path.join(MODELS_DIR, f"bot{bot_id}_{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.joblib")


def save(path, obj):
    import joblib
    joblib.dump(obj, path, compress=3)


def load(path):
    import joblib
    # szukamy po samej nazwie pliku w data/models - sciezki zapisane na innym komputerze (np. Windows -> NAS) tez dzialaja
    name = re.split(r"[\\/]", str(path))[-1]
    real = os.path.realpath(os.path.join(MODELS_DIR, name))
    if not name or not real.startswith(os.path.realpath(MODELS_DIR) + os.sep):   # tylko wlasne pliki modeli
        raise ValueError("Nieprawidlowa sciezka modelu")
    return joblib.load(real)


def judge_absolute(v, n_windows):
    """Strategia ML nie ma wersji 'bez ML' - oceniamy ja wprost."""
    prof = sum(1 for x in v["windows"] if (x or 0) > 0)
    reasons = []
    if v["total"] <= 0:
        reasons.append("strata w całym okresie")
    if (v["oos_ret"] or 0) <= 0:
        reasons.append("strata poza próbą (ostatnie 30%)")
    if n_windows and prof / n_windows < 0.6:
        reasons.append(f"zysk tylko w {prof} z {n_windows} okresów")
    if v["trades"] < 10:
        reasons.append("za mało transakcji")
    if v["dd"] < -25:
        reasons.append("obsunięcie większe niż 25%")
    v.update(status="confirmed" if not reasons else "uncertain", reasons=reasons,
             windows_better=prof, windows_better_flags=[(x or 0) > 0 for x in v["windows"]])
    return v


def train_for_bot(bot, provider, tag, progress=lambda x: None, acc_type=None):
    """
    Nowy model dla bota:
      1) test w czasie: ostatnie ~180 dni, model uczony kroczaco, bot z ML vs bez ML (albo ocena wprost),
      2) model koncowy uczony na wszystkich danych do dzis,
    Zwraca (status, sciezka_pliku | None, podsumowanie). status: 'candidate' (przeszedl) / 'rejected'.
    """
    from .backtest import Context, MAX_DAYS
    from .optimize import evaluate, judge
    from .report import oos_split, time_windows
    from .strategies import full_params

    market, strategy = bot["market"], bot["strategy"]
    p = full_params(market, strategy, bot["params"])
    cost = cost_for(market, acc_type)
    now = datetime.now(timezone.utc)
    eval_days = min(180, MAX_DAYS.get(p["timeframe"], 10_000) - 5, train_days(p) + 30)
    cfg = {"market": market, "strategy": strategy, "params": p, "initial_capital": 100_000, "cost_pct": cost,
           "start": (now - timedelta(days=eval_days)).date().isoformat(), "end": now.date().isoformat()}
    ctx = Context(provider, cfg, tag)
    windows = time_windows(ctx.start, ctx.end, market)
    oos_start = oos_split(ctx.start, ctx.end)
    progress(0.05)

    ml_p = dict(p)
    if strategy != "ml_model" and not (p.get("ml_filter") or p.get("ml_sizing")):
        ml_p["ml_filter"] = True
    progress(0.08)
    res = context_probs(ctx, strategy, ml_p, cost, progress=lambda x: progress(0.15 + 0.4 * x))
    progress(0.55)
    v = evaluate(ctx, strategy, ml_p, windows, oos_start)
    v.pop("trades_list", None)
    base = None
    if strategy == "ml_model":
        verdict = judge_absolute(v, len(windows))
    else:
        base = evaluate(ctx, strategy, dict(p, ml_filter=False, ml_sizing=False), windows, oos_start)
        base.pop("trades_list", None)
        verdict = judge(v, base, len(windows))
    progress(0.8)

    info = res["info"]
    reasons = list(verdict.get("reasons", []))
    if info["auc"] is None or info["auc"] < AUC_MIN:
        reasons.append(f"model nie odróżnia dobrych wejść od złych (AUC {info['auc'] or 0:.2f} < {AUC_MIN})")
    passed = verdict["status"] == "confirmed" and not (info["auc"] is None or info["auc"] < AUC_MIN)

    # model koncowy: wszystkie przyklady o znanym wyniku z ostatnich train_days dni
    data = res["data"]
    days = train_days(p)
    final_train = data[(data["ts"] >= now - timedelta(days=days)) & data["y_end"].notna()]
    model = fit(final_train)
    if model is None:
        passed = False
        reasons.append("za mało danych do nauki modelu końcowego")
    progress(0.95)

    summary = {
        "eval": {"start": cfg["start"], "end": cfg["end"], "oos_start": oos_start.date().isoformat(),
                 "windows": [{"start": a.date().isoformat(), "end": (b - timedelta(days=1)).date().isoformat()}
                             for a, b in windows],
                 "ml": {k: verdict.get(k) for k in ("total", "dd", "sharpe", "trades", "win_rate", "is_ret",
                                                    "oos_ret", "windows", "windows_better", "windows_better_flags")},
                 "base": ({k: base.get(k) for k in ("total", "dd", "sharpe", "trades", "win_rate", "is_ret",
                                                    "oos_ret", "windows")} if base else None),
                 "mode": "absolute" if strategy == "ml_model" else "vs_base",
                 "tested_filter": strategy != "ml_model" and not (p.get("ml_filter") or p.get("ml_sizing"))},
        "quality": {k: info[k] for k in ("auc", "base_rate", "hit_rate", "coverage", "threshold", "refits_ok",
                                         "calibration")},
        "refits": len(info["refits"]),
        "importance": importance(model, final_train) if model is not None else [],
        "train": {"from": (now - timedelta(days=days)).date().isoformat(), "to": now.date().isoformat(),
                  "rows": int(final_train["y"].notna().sum()),
                  "base_rate": float(final_train["y"].mean() * 100) if len(final_train) else None,
                  "symbols": sorted(data["sym"].unique().tolist())},
        "params": {"timeframe": p["timeframe"], "stop_loss_pct": p["stop_loss_pct"],
                   "take_profit_pct": p["take_profit_pct"], "horizon": int(p.get("ml_horizon", 24)),
                   "train_days": days, "threshold": threshold(p, cost) * 100,
                   "breakeven": breakeven(p, cost) * 100, "confidence": p.get("ml_confidence", "mid")},
        "passed": passed,
        "reasons": reasons,
    }
    path = None
    if model is not None:
        path = model_path(bot["id"])
        save(path, {"model": model, "features": FEATURES, "signature": signature(p),
                    "trained_to": now.isoformat(), "regime_symbol": p.get("regime_symbol")})
    progress(1)
    return ("candidate" if passed else "rejected"), path, summary


class LiveModel:
    """Aktywny (zatwierdzony) model bota - przeladowuje sie sam po akceptacji nowej wersji."""

    def __init__(self, db, bot_id):
        self.db = db
        self.bot_id = bot_id
        self.loaded_id = None
        self.obj = None
        self.mkt = None
        self.mkt_day = None
        self.state = None                       # ostatni zalogowany stan, zeby nie spamowac logu

    def current(self, p):
        row = self.db.one("SELECT id, path FROM ml_models WHERE bot_id=? AND status='active' "
                          "ORDER BY id DESC LIMIT 1", (self.bot_id,))
        if not row:
            return None, "brak zatwierdzonego modelu"
        if row["id"] != self.loaded_id:
            try:
                self.obj = load(row["path"])
                self.loaded_id = row["id"]
            except Exception as e:
                self.obj, self.loaded_id = None, None
                return None, f"nie udało się wczytać modelu #{row['id']} ({e})"
        if self.obj.get("signature") != signature(p):
            return None, f"model #{row['id']} uczony na innych ustawieniach (SL/TP/interwał) - potrzebny nowy"
        return row["id"], None

    def market(self, data, p):
        today = datetime.now(timezone.utc).date()
        sym = p.get("regime_symbol")
        if not sym or self.mkt_day == today:
            return self.mkt
        try:
            daily = data.bars([sym], "1Day", datetime.now(timezone.utc) - timedelta(days=200)).get(sym)
            self.mkt = market_table(daily)
        except Exception:
            self.mkt = None
        self.mkt_day = today
        return self.mkt

    def prob(self, df, p, data, cross=None):
        mid, why = self.current(p)
        if mid is None:
            return None, mid, why
        X = features(df, self.market(data, p), cross).iloc[[-1]]
        return float(predict(self.obj["model"], X.values)[0]), mid, None

    def probs(self, df, p, data, cross=None, model=None):
        """Prawdopodobienstwa dla wszystkich swiec (laboratorium). model: inny model niz aktywny (pretendent)."""
        if model is None:
            mid, why = self.current(p)
            if mid is None:
                return None
            model = self.obj["model"]
        X = features(df, self.market(data, p), cross)
        out = pd.Series(np.nan, index=df.index)
        ok = np.arange(len(df)) >= ML_WARMUP
        if ok.any():
            out.iloc[np.where(ok)[0]] = predict(model, X.values[ok])
        return out


_train_lock = threading.Lock()


def training_lock():
    return _train_lock
