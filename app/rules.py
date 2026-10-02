"""
Konstruktor zasad - biblioteka gotowych zasad kupna i sprzedazy.

Kazda zasada to warunek liczony WYLACZNIE na zamknietych swiecach (bez zagladania w przyszlosc)
i zwraca True/False dla kazdej swiecy. Strategia "Konstruktor zasad" (strategies.RuleBuilder) laczy je:
  kupno    - wszystkie zasady naraz (i) albo dowolna z nich (lub),
  sprzedaz - dowolna z zasad wyjscia (lub); niezaleznie dzialaja SL / TP / stop kroczacy.

Zasady "zdarzeniowe" (przeciecie, wybicie) maja parametr 'within' - zdarzenie w ciagu ostatnich X swiec,
dzieki czemu mozna je laczyc z warunkami stanu (np. "wybicie w ciagu 3 swiec" + "trend wzrostowy").

Nowa zasada = wpis w RULES (id, opis, grupa, strona, parametry, funkcja).
"""

import numpy as np
import pandas as pd

GROUPS = {"trend": "Trend", "breakout": "Wybicie", "momentum": "Momentum", "volume": "Wolumen",
          "volatility": "Zmienność", "price": "Cena"}


# ------------------------------------------------------------------ wskazniki
def sma(s, n):
    return s.rolling(int(n)).mean()


def ema(s, n):
    return s.ewm(span=int(n), adjust=False, min_periods=int(n)).mean()


def rsi(close, n):
    d = close.diff()
    up = d.clip(lower=0).rolling(int(n)).mean()
    dn = (-d.clip(upper=0)).rolling(int(n)).mean()
    return (100 - 100 / (1 + up / dn.replace(0, np.nan))).fillna(50)


def atr(df, n=14):
    c = df["close"]
    tr = pd.concat([df["high"] - df["low"], (df["high"] - c.shift()).abs(), (df["low"] - c.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(int(n)).mean()


def macd(close, fast, slow, signal):
    line = ema(close, fast) - ema(close, slow)
    return line, line.ewm(span=int(signal), adjust=False).mean()


def adx(df, n=14):
    h, l, c = df["high"], df["low"], df["close"]
    up, dn = h.diff(), -l.diff()
    plus = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=df.index)
    minus = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=df.index)
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    a = 1 / int(n)
    atr_ = tr.ewm(alpha=a, adjust=False).mean()
    pdi = 100 * plus.ewm(alpha=a, adjust=False).mean() / atr_
    mdi = 100 * minus.ewm(alpha=a, adjust=False).mean() / atr_
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=a, adjust=False).mean(), pdi, mdi


def bands(close, n, k):
    m = sma(close, n)
    sd = close.rolling(int(n)).std()
    return m - k * sd, m, m + k * sd


def recent(event, within):
    """Zdarzenie w ciagu ostatnich 'within' swiec (lacznie z biezaca)."""
    return event.fillna(False).astype(int).rolling(max(1, int(within)), min_periods=1).max().astype(bool)


def cross_up(a, b):
    return (a.shift(1) <= b.shift(1)) & (a > b)


def cross_down(a, b):
    return (a.shift(1) >= b.shift(1)) & (a < b)


# ------------------------------------------------------------------ katalog zasad
def P(key, label, typ, default, lo, hi):
    return {"key": key, "label": label, "type": typ, "default": default, "min": lo, "max": hi}


N = lambda d=20, lo=2, hi=400, label="Okres (świece)": P("n", label, "int", d, lo, hi)
WITHIN = lambda d=3: P("within", "W ciągu ostatnich (świec)", "int", d, 1, 50)


def _near_high(d, a):
    hi = d["high"].rolling(int(a["n"])).max()
    return d["close"] >= hi * (1 - a["pct"])


def _squeeze(d, a):
    lo, m, up = bands(d["close"], a["n"], 2)
    width = (up - lo) / m
    return width <= width.rolling(int(a["lookback"])).quantile(a["pct"])


RULES = [
    # ------------------------------------------------------------ KUPNO
    {"id": "price_above_sma", "side": "entry", "group": "trend", "fmt": "cena nad SMA {n}",
     "label": "Cena powyżej średniej (SMA)", "help": "Najprostszy filtr trendu: kupuj tylko, gdy cena jest nad średnią.",
     "params": [N(200)], "fn": lambda d, a: d["close"] > sma(d["close"], a["n"])},
    {"id": "sma_fast_above_slow", "side": "entry", "group": "trend", "fmt": "SMA {fast} nad SMA {slow}",
     "label": "Szybka średnia nad wolną (trend wzrostowy)", "help": "Stan po złotym krzyżu, np. SMA 50 nad SMA 200.",
     "params": [P("fast", "Szybka SMA", "int", 50, 2, 300), P("slow", "Wolna SMA", "int", 200, 3, 400)],
     "fn": lambda d, a: sma(d["close"], a["fast"]) > sma(d["close"], a["slow"])},
    {"id": "sma_cross_up", "side": "entry", "group": "trend", "fmt": "złoty krzyż SMA {fast}/{slow} (≤{within} św.)",
     "label": "Złoty krzyż: szybka SMA przecina wolną w górę",
     "params": [P("fast", "Szybka SMA", "int", 20, 2, 300), P("slow", "Wolna SMA", "int", 50, 3, 400), WITHIN(5)],
     "fn": lambda d, a: recent(cross_up(sma(d["close"], a["fast"]), sma(d["close"], a["slow"])), a["within"])},
    {"id": "ema_rising", "side": "entry", "group": "trend", "fmt": "EMA {n} rośnie ({bars} św.)",
     "label": "Średnia EMA rośnie",
     "params": [N(20), P("bars", "Porównaj z EMA sprzed (świec)", "int", 5, 1, 50)],
     "fn": lambda d, a: ema(d["close"], a["n"]) > ema(d["close"], a["n"]).shift(int(a["bars"]))},
    {"id": "adx_strong", "side": "entry", "group": "trend", "fmt": "ADX {n} > {level} i +DI > −DI",
     "label": "Silny trend wzrostowy (ADX)", "help": "ADX mierzy siłę trendu (> 25 = wyraźny trend), +DI > −DI = w górę.",
     "params": [N(14, 5, 50), P("level", "Min. ADX", "float", 25, 10, 60)],
     "fn": lambda d, a: (lambda x: (x[0] > a["level"]) & (x[1] > x[2]))(adx(d, a["n"]))},
    {"id": "macd_cross_up", "side": "entry", "group": "momentum", "fmt": "MACD przecina sygnał w górę (≤{within} św.)",
     "label": "MACD przecina linię sygnału w górę",
     "params": [P("fast", "EMA szybka", "int", 12, 2, 100), P("slow", "EMA wolna", "int", 26, 3, 200),
                P("signal", "Linia sygnału", "int", 9, 2, 50), WITHIN(3)],
     "fn": lambda d, a: recent(cross_up(*macd(d["close"], a["fast"], a["slow"], a["signal"])), a["within"])},
    {"id": "macd_positive", "side": "entry", "group": "momentum", "fmt": "MACD > 0",
     "label": "MACD powyżej zera", "help": "Szybka EMA nad wolną — momentum wzrostowe.",
     "params": [P("fast", "EMA szybka", "int", 12, 2, 100), P("slow", "EMA wolna", "int", 26, 3, 200)],
     "fn": lambda d, a: ema(d["close"], a["fast"]) > ema(d["close"], a["slow"])},
    {"id": "breakout_high", "side": "entry", "group": "breakout", "fmt": "wybicie nad maks. {n} św. (≤{within} św.)",
     "label": "Wybicie: zamknięcie nad maksimum z N świec", "help": "Klasyka strategii trendowych (kanał Donchiana).",
     "params": [N(20), WITHIN(1)],
     "fn": lambda d, a: recent(d["close"] > d["high"].shift(1).rolling(int(a["n"])).max(), a["within"])},
    {"id": "near_high", "side": "entry", "group": "breakout", "fmt": "do {pct} od maks. {n} św.",
     "label": "Blisko maksimum z N świec", "help": "Np. 252 świece dzienne = maksimum 52-tygodniowe.",
     "params": [N(252), P("pct", "Maks. odległość od szczytu", "pct", 0.05, 0.0, 0.5)], "fn": _near_high},
    {"id": "rsi_below", "side": "entry", "group": "momentum", "fmt": "RSI {n} < {level}",
     "label": "RSI poniżej progu (wyprzedanie)",
     "params": [N(14, 2, 50), P("level", "Próg RSI", "float", 30, 5, 60)],
     "fn": lambda d, a: rsi(d["close"], a["n"]) < a["level"]},
    {"id": "rsi_strong", "side": "entry", "group": "momentum", "fmt": "RSI {n} > {level}",
     "label": "RSI powyżej progu (siła)",
     "params": [N(14, 2, 50), P("level", "Próg RSI", "float", 55, 30, 90)],
     "fn": lambda d, a: rsi(d["close"], a["n"]) > a["level"]},
    {"id": "roc_above", "side": "entry", "group": "momentum", "fmt": "wzrost > {pct} w {n} św.",
     "label": "Tempo wzrostu: cena wyżej o X% niż N świec temu",
     "params": [N(10, 1, 300), P("pct", "Min. wzrost", "pct", 0.03, 0.0, 1.0)],
     "fn": lambda d, a: d["close"] / d["close"].shift(int(a["n"])) - 1 > a["pct"]},
    {"id": "volume_spike", "side": "entry", "group": "volume", "fmt": "wolumen > {mult}× średni ({n} św.)",
     "label": "Skok wolumenu", "help": "Ruch potwierdzony większym obrotem niż zwykle.",
     "params": [N(20, 5, 200), P("mult", "Wielokrotność średniej", "float", 1.5, 1.0, 10)],
     "fn": lambda d, a: d["volume"] > d["volume"].shift(1).rolling(int(a["n"])).mean() * a["mult"]},
    {"id": "min_turnover", "side": "entry", "group": "volume", "fmt": "obrót ≥ {amount} śr. z {n} św.",
     "label": "Płynność: średni obrót (cena × wolumen) co najmniej X",
     "help": "Filtr małych, mało płynnych spółek — kwota w walucie notowań (np. 2 000 000 zł dziennie).",
     "params": [N(20, 5, 250), P("amount", "Min. średni obrót", "float", 2000000, 0, 1e12)],
     "fn": lambda d, a: (d["close"] * d["volume"]).rolling(int(a["n"])).mean() >= a["amount"]},
    {"id": "bb_squeeze", "side": "entry", "group": "volatility", "fmt": "zwężone wstęgi {n} (najwęższe {pct} z {lookback} św.)",
     "label": "Zwężenie wstęg Bollingera (cisza przed ruchem)",
     "params": [N(20, 5, 100), P("lookback", "Porównaj z ostatnimi (świec)", "int", 100, 20, 400),
                P("pct", "Wśród najwęższych", "pct", 0.2, 0.05, 0.5)], "fn": _squeeze},
    {"id": "bb_lower", "side": "entry", "group": "volatility", "fmt": "cena pod dolną wstęgą {n}/{k}",
     "label": "Cena pod dolną wstęgą Bollingera", "help": "Mocne wyprzedanie — zasada pod powrót do średniej.",
     "params": [N(20, 5, 100), P("k", "Odchylenia standardowe", "float", 2, 1, 4)],
     "fn": lambda d, a: d["close"] < bands(d["close"], a["n"], a["k"])[0]},
    {"id": "pullback_sma", "side": "entry", "group": "price", "fmt": "cofnięcie pod SMA {n}",
     "label": "Cofnięcie: cena pod krótką średnią",
     "help": "W połączeniu z „cena nad SMA 200” = kupno korekty w trendzie wzrostowym.",
     "params": [N(20)], "fn": lambda d, a: d["close"] < sma(d["close"], a["n"])},
    {"id": "bullish_candle", "side": "entry", "group": "price", "fmt": "świeca wzrostowa nad maks. poprzedniej",
     "label": "Świeca wzrostowa zamknięta nad maksimum poprzedniej", "params": [],
     "fn": lambda d, a: (d["close"] > d["open"]) & (d["close"] > d["high"].shift(1))},

    # ------------------------------------------------------------ SPRZEDAZ
    {"id": "close_below_sma", "side": "exit", "group": "trend", "fmt": "cena pod SMA {n}",
     "label": "Cena spada pod średnią (SMA)", "params": [N(20)],
     "fn": lambda d, a: d["close"] < sma(d["close"], a["n"])},
    {"id": "sma_fast_below_slow", "side": "exit", "group": "trend", "fmt": "SMA {fast} pod SMA {slow}",
     "label": "Szybka średnia pod wolną (koniec trendu)",
     "params": [P("fast", "Szybka SMA", "int", 10, 2, 300), P("slow", "Wolna SMA", "int", 30, 3, 400)],
     "fn": lambda d, a: sma(d["close"], a["fast"]) < sma(d["close"], a["slow"])},
    {"id": "ema_falling", "side": "exit", "group": "trend", "fmt": "EMA {n} spada ({bars} św.)",
     "label": "Średnia EMA spada",
     "params": [N(20), P("bars", "Porównaj z EMA sprzed (świec)", "int", 3, 1, 50)],
     "fn": lambda d, a: ema(d["close"], a["n"]) < ema(d["close"], a["n"]).shift(int(a["bars"]))},
    {"id": "adx_weak", "side": "exit", "group": "trend", "fmt": "ADX {n} < {level}",
     "label": "Trend słabnie (ADX poniżej progu)",
     "params": [N(14, 5, 50), P("level", "Próg ADX", "float", 20, 5, 50)],
     "fn": lambda d, a: adx(d, a["n"])[0] < a["level"]},
    {"id": "macd_cross_down", "side": "exit", "group": "momentum", "fmt": "MACD przecina sygnał w dół",
     "label": "MACD przecina linię sygnału w dół",
     "params": [P("fast", "EMA szybka", "int", 12, 2, 100), P("slow", "EMA wolna", "int", 26, 3, 200),
                P("signal", "Linia sygnału", "int", 9, 2, 50)],
     "fn": lambda d, a: cross_down(*macd(d["close"], a["fast"], a["slow"], a["signal"]))},
    {"id": "breakdown_low", "side": "exit", "group": "breakout", "fmt": "zamknięcie pod min. {n} św.",
     "label": "Zamknięcie pod minimum z N świec", "help": "Wyjście z kanału Donchiana — para do „wybicia”.",
     "params": [N(10)], "fn": lambda d, a: d["close"] < d["low"].shift(1).rolling(int(a["n"])).min()},
    {"id": "rsi_overbought", "side": "exit", "group": "momentum", "fmt": "RSI {n} > {level}",
     "label": "RSI powyżej progu (wykupienie)",
     "params": [N(14, 2, 50), P("level", "Próg RSI", "float", 70, 50, 95)],
     "fn": lambda d, a: rsi(d["close"], a["n"]) > a["level"]},
    {"id": "rsi_weak", "side": "exit", "group": "momentum", "fmt": "RSI {n} < {level}",
     "label": "RSI spada poniżej progu (utrata siły)",
     "params": [N(14, 2, 50), P("level", "Próg RSI", "float", 45, 10, 70)],
     "fn": lambda d, a: rsi(d["close"], a["n"]) < a["level"]},
    {"id": "bb_upper", "side": "exit", "group": "volatility", "fmt": "cena nad górną wstęgą {n}/{k}",
     "label": "Cena nad górną wstęgą Bollingera (realizacja zysku)",
     "params": [N(20, 5, 100), P("k", "Odchylenia standardowe", "float", 2, 1, 4)],
     "fn": lambda d, a: d["close"] > bands(d["close"], a["n"], a["k"])[2]},
    {"id": "back_to_sma", "side": "exit", "group": "price", "fmt": "powrót do SMA {n}",
     "label": "Cena wraca do średniej (cel powrotu do średniej)",
     "params": [N(20)], "fn": lambda d, a: d["close"] >= sma(d["close"], a["n"])},
]
BY_ID = {r["id"]: r for r in RULES}
EMA_BASED = {"ema_rising", "adx_strong", "macd_cross_up", "macd_positive", "ema_falling", "adx_weak", "macd_cross_down"}


def catalog():
    return [{k: v for k, v in r.items() if k != "fn"} for r in RULES]


# ------------------------------------------------------------------ uzycie
def normalize(rules, side):
    """Lista zasad z panelu -> [{id, ...parametry}] z domyslnymi i rzutowanymi typami."""
    out = []
    for r in rules or []:
        if not isinstance(r, dict) or r.get("id") not in BY_ID or BY_ID[r["id"]]["side"] != side:
            continue
        spec = BY_ID[r["id"]]
        item = {"id": r["id"]}
        for p in spec["params"]:
            v = r.get(p["key"], p["default"])
            try:
                v = int(round(float(v))) if p["type"] == "int" else float(v)
            except (TypeError, ValueError):
                v = p["default"]
            item[p["key"]] = v
        out.append(item)
    return out


def errors(rules, side_label):
    out = []
    for r in rules:
        spec = BY_ID[r["id"]]
        for p in spec["params"]:
            v = r[p["key"]]
            if v < p["min"] or v > p["max"]:
                out.append(f"{side_label} „{spec['label']}”: {p['label']} = {v:g} poza zakresem {p['min']:g}–{p['max']:g}.")
        if "fast" in r and "slow" in r and r["fast"] >= r["slow"]:
            out.append(f"{side_label} „{spec['label']}”: szybka średnia musi być krótsza od wolnej.")
    return out


def describe(r):
    spec = BY_ID[r["id"]]
    vals = {}
    for p in spec["params"]:
        v = r.get(p["key"], p["default"])
        vals[p["key"]] = (f"{v * 100:.1f}%".replace(".0%", "%") if p["type"] == "pct"
                          else f"{v:,.0f}".replace(",", " ") if abs(v) >= 10000 else f"{v:g}")
    try:
        return spec["fmt"].format(**vals)
    except (KeyError, IndexError):
        return spec["label"]


def warmup(rules):
    w = 30
    for r in rules:
        ints = [r[p["key"]] for p in BY_ID[r["id"]]["params"] if p["type"] == "int" and p["key"] != "within"]
        if ints:
            m = max(ints)
            w = max(w, m * 3 if r["id"] in EMA_BASED else int(m * 1.5))
    return min(w, 420) + 2


def evaluate(df, rules, mode="all"):
    """Seria bool: czy zasady sa spelnione (mode: all = wszystkie, any = dowolna). Brak zasad -> False."""
    if not rules:
        return pd.Series(False, index=df.index)
    parts = [BY_ID[r["id"]]["fn"](df, r).fillna(False).astype(bool) for r in rules]
    res = parts[0]
    for x in parts[1:]:
        res = (res & x) if mode == "all" else (res | x)
    return res
