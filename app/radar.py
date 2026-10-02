"""
Radar altcoinow - codzienny ranking kryptowalut dostepnych na gieldzie konta.

1. Bierzemy wszystkie pary w walucie konta (np. /EUR na Krakenie), bez stablecoinow i walut.
2. Zostawiamy ~40 najplynniejszych (obrot z ostatniej doby) - male monety bez obrotu to pulapka.
3. Dla kazdej liczymy na dziennych swiecach (tylko zamkniete dni):
     - sile wzgledem BTC z 30 dni (zmiana monety minus zmiana BTC),
     - momentum z 7 dni,
     - wzrost obrotu (sredni obrot 7 dni / 30 dni),
     - czy cena jest nad srednia 50-dniowa (trend).
4. Wynik 0-100 = 40% sila vs BTC + 30% momentum + 20% obrot + 10% trend (pozycje w rankingu, nie surowe liczby,
   zeby jedna szalona moneta nie zdominowala wyniku).
Bot w trybie "radar" handluje N najlepszymi z ostatniego skanu (i zawsze pilnuje monet, ktore juz ma).

Dodatkowo radar zapamietuje, kiedy pierwszy raz zobaczyl dana pare - tak powstaje lista nowych monet na gieldzie.
"""

import json
import math
import threading
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

WEIGHTS = {"rel30": 40, "ret7": 30, "vol_ratio": 20}
_lock = threading.Lock()


def provider_name(acc):
    """Nazwa zrodla radaru dla konta: kraken, alpaca, ccxt:<gielda> albo sim."""
    if acc.type == "kraken":
        return "kraken"
    if acc.type == "gielda":
        return "ccxt:" + (acc.extra.get("exchange") or "")
    return {"alpaca": "alpaca"}.get(acc.type, "sim")


def closed_days(df, asof=None):
    """Tylko zamkniete dni (dzisiejsza swieca jeszcze trwa)."""
    if df is None or df.empty:
        return df
    cut = pd.Timestamp(asof) if asof is not None else pd.Timestamp(datetime.now(timezone.utc).date(), tz="UTC")
    return df[df.index < cut]


# ------------------------------------------------------------------ trend i "co bywalo dalej"
TREND_LABEL = {1: "wzrostowy", 0: "boczny", -1: "spadkowy"}


def rolling_slope(logc, n=30):
    """Nachylenie regresji liniowej log(ceny) z ostatnich n dni, jako % na n dni (bez petli)."""
    x = logc.values.astype(float)
    t = np.arange(len(x), dtype=float)
    st, stx = pd.Series(t).rolling(n).sum().values, pd.Series(t * x).rolling(n).sum().values
    sx = pd.Series(x).rolling(n).sum().values
    stt = pd.Series(t * t).rolling(n).sum().values
    b = (n * stx - st * sx) / (n * stt - st * st)
    return pd.Series((np.exp(b * n) - 1) * 100, index=logc.index)


def trend_frame(d):
    """Stan trendu dla KAZDEGO dnia, liczony tylko z danych do tego dnia:
    wzrostowy = cena nad SMA 50, SMA 20 nad SMA 50 i dodatnie nachylenie z 30 dni; spadkowy = odwrotnie."""
    c = d["close"]
    s20, s50 = c.rolling(20).mean(), c.rolling(50).mean()
    slope = rolling_slope(np.log(c), 30)
    up = (c > s50) & (s20 > s50) & (slope > 0)
    down = (c < s50) & (s20 < s50) & (slope < 0)
    state = pd.Series(0, index=d.index).where(~up, 1).where(~down, -1)
    state[s50.isna() | slope.isna()] = np.nan
    sigma30 = c.pct_change().rolling(30).std() * math.sqrt(30) * 100
    return pd.DataFrame({"state": state, "slope30": slope, "sma20": s20, "sma50": s50,
                         "strength": slope.abs() / sigma30.replace(0, np.nan)})


def outlook(daily, asof=None):
    """Co dzialo sie z ta moneta w przeszlosci, gdy byla w TAKIM SAMYM trendzie jak dzis:
    odsetek wzrostow i mediana zmiany po 7 i 30 dniach + typowy zakres ruchu (zmiennosc z 30 dni)."""
    d = closed_days(daily, asof)
    if d is None or len(d) < 60:
        return None
    tf = trend_frame(d)
    now_state = tf["state"].iloc[-1]
    if now_state != now_state:
        return None
    c = d["close"]
    out = {"trend": int(now_state), "trend_label": TREND_LABEL[int(now_state)],
           "slope30": float(tf["slope30"].iloc[-1]), "strength": float(tf["strength"].iloc[-1] or 0),
           "days_in_trend": int((tf["state"][::-1] != now_state).values.argmax()) if (tf["state"] != now_state).any()
           else int(tf["state"].notna().sum()),
           "history_days": int(len(d))}
    sig = float(c.pct_change().iloc[-30:].std())
    for h in (7, 30):
        fwd = (c.shift(-h) / c - 1) * 100
        same = fwd[(tf["state"] == now_state) & fwd.notna()]
        allx = fwd[tf["state"].notna() & fwd.notna()]
        out[f"n{h}"] = int(len(same))
        out[f"p_up{h}"] = float((same > 0).mean() * 100) if len(same) else None
        out[f"med{h}"] = float(same.median()) if len(same) else None
        out[f"base_up{h}"] = float((allx > 0).mean() * 100) if len(allx) else None
        out[f"range{h}"] = float(sig * math.sqrt(h) * 100)       # typowy ruch (1 odchylenie) w %
    return out


def coin_detail(daily, days=180, horizon=30):
    """Dane do wykresu: cena + SMA 20/50 z ostatnich dni i 'lejek' mozliwych cen na kolejne dni
    (srodek = mediana z historii w tym trendzie, pasma = 1 i 2 odchylenia wg zmiennosci z 30 dni)."""
    d = closed_days(daily)
    if d is None or len(d) < 60:
        return None
    tf = trend_frame(d)
    o = outlook(daily) or {}
    tail = d.iloc[-days:]
    last_t, last_c = d.index[-1], float(d["close"].iloc[-1])
    sig = float(d["close"].pct_change().iloc[-30:].std())
    drift = (o.get("med30") or 0) / 100 / 30 if (o.get("n30") or 0) >= 30 else 0.0
    cone = []
    for k in range(0, horizon + 1):
        mid = last_c * (1 + drift * k)
        w = sig * math.sqrt(k)
        cone.append([(last_t + pd.Timedelta(days=k)).isoformat(), mid, last_c * math.exp(-w) * (1 + drift * k),
                     last_c * math.exp(w) * (1 + drift * k), last_c * math.exp(-2 * w) * (1 + drift * k),
                     last_c * math.exp(2 * w) * (1 + drift * k)])
    ser = lambda s: [[t.isoformat(), None if v != v else float(v)] for t, v in s.items()]
    return {"price": ser(tail["close"]), "sma20": ser(tf["sma20"].iloc[-days:]), "sma50": ser(tf["sma50"].iloc[-days:]),
            "cone": cone, "outlook": o}


def metrics(daily, btc=None, asof=None, with_outlook=False):
    d = closed_days(daily, asof)
    if d is None or len(d) < 35:
        return None
    c, v = d["close"], d["close"] * d["volume"]
    out = {"price": float(c.iloc[-1]), "ret30": float(c.iloc[-1] / c.iloc[-31] - 1) * 100,
           "ret7": float(c.iloc[-1] / c.iloc[-8] - 1) * 100,
           "vol_ratio": float(v.iloc[-7:].mean() / max(v.iloc[-30:].mean(), 1e-9)),
           "volume30": float(v.iloc[-30:].mean()),
           "volatility": float(c.pct_change().iloc[-30:].std() * math.sqrt(365) * 100)}
    sma = c.rolling(50).mean().iloc[-1]
    out["above_sma50"] = bool(c.iloc[-1] > sma) if sma == sma else None
    b = closed_days(btc, asof) if btc is not None else None
    btc30 = float(b["close"].iloc[-1] / b["close"].iloc[-31] - 1) * 100 if b is not None and len(b) >= 31 else 0.0
    out["rel30"] = out["ret30"] - btc30
    if with_outlook:
        out["outlook"] = outlook(daily, asof)
    return out


def score(items):
    """items: {sym: metrics} -> lista posortowana z wynikiem 0-100."""
    syms = [s for s, m in items.items() if m]
    if not syms:
        return []
    df = pd.DataFrame({s: items[s] for s in syms}).T
    total = pd.Series(0.0, index=df.index)
    for k, w in WEIGHTS.items():
        total += df[k].astype(float).rank(pct=True) * w
    total += df["above_sma50"].map(lambda x: 10.0 if x else 0.0)
    out = []
    for s in total.sort_values(ascending=False).index:
        m = dict(items[s])
        m.update(symbol=s, score=round(float(total[s]), 1))
        out.append(m)
    return out


def scan(provider, pname, quote, candidates=40, min_volume=0.0, db=None):
    """Pelny skan: ranking najplynniejszych monet. Zapisuje wynik i nowe pary w bazie (jesli podano db)."""
    uni = provider.universe(quote)
    now = datetime.now(timezone.utc)
    if db is not None:
        record_seen(db, pname, [s for s, _ in uni], now)
    pool = [s for s, vol in uni if vol >= min_volume][:candidates]
    btc = f"BTC/{quote}"
    daily = provider.bars(list(dict.fromkeys(pool + [btc])), "1Day", now - timedelta(days=730))
    items = {s: metrics(daily.get(s), daily.get(btc), with_outlook=True) for s in pool}
    vols = dict(uni)
    ranked = score(items)
    for r in ranked:
        r["volume24h"] = vols.get(r["symbol"], 0.0)
    trends = [m["outlook"]["trend"] for m in items.values() if m and m.get("outlook")]
    market = {"up": trends.count(1), "side": trends.count(0), "down": trends.count(-1),
              "btc": outlook(daily.get(btc)) if daily.get(btc) is not None else None}
    res = {"ts": now.isoformat(timespec="seconds"), "provider": pname, "quote": quote, "universe": len(uni),
           "market": market,
           "candidates": len(pool), "items": ranked,
           "skipped": [s for s in pool if not items.get(s)]}
    if db is not None:
        db.execute("INSERT INTO radar_scans(ts, provider, quote, data) VALUES (?,?,?,?)",
                   (res["ts"], pname, quote, json.dumps(res)))
        db.execute("DELETE FROM radar_scans WHERE id NOT IN (SELECT id FROM radar_scans ORDER BY id DESC LIMIT 200)")
    return res


def record_seen(db, pname, symbols, now):
    have = {r["symbol"] for r in db.all("SELECT symbol FROM radar_seen WHERE provider=?", (pname,))}
    baseline = 0 if have else 1                   # pierwszy skan = stan wyjsciowy, nic nie jest "nowe"
    for s in symbols:
        if s not in have:
            db.execute("INSERT OR IGNORE INTO radar_seen(provider, symbol, first_seen, baseline) VALUES (?,?,?,?)",
                       (pname, s, now.isoformat(timespec="seconds"), baseline))


def latest(db, pname, quote, max_age_hours=None):
    r = db.one("SELECT data FROM radar_scans WHERE provider=? AND quote=? ORDER BY id DESC LIMIT 1", (pname, quote))
    if not r:
        return None
    res = json.loads(r["data"])
    if max_age_hours is not None:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(res["ts"])
        if age > timedelta(hours=max_age_hours):
            return None
    return res


def new_listings(db, pname, days=60):
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    return db.all("SELECT symbol, first_seen FROM radar_seen WHERE provider=? AND baseline=0 AND first_seen>=? "
                  "ORDER BY first_seen DESC", (pname, since))


def ensure(db, provider, pname, quote, max_age_hours=26, log=None):
    """Ostatni skan, a gdy jest starszy niz doba - nowy (jeden skan naraz)."""
    res = latest(db, pname, quote, max_age_hours)
    if res:
        return res
    with _lock:
        res = latest(db, pname, quote, max_age_hours)
        if res:
            return res
        if log:
            log(f"Radar: skanuje rynek {pname} /{quote}...")
        return scan(provider, pname, quote, db=db)


def pick(res, top_n, exclude=(), min_volume=0.0):
    if not res:
        return []
    return [r["symbol"] for r in res["items"]
            if r["symbol"] not in set(exclude) and (r.get("volume24h") or 0) >= min_volume][:top_n]


# ------------------------------------------------------------------ backtest: ranking "w tamtym dniu"
def weekly_allowed(daily, quote, start, end, top_n):
    """{symbol: set(dat)} - w ktorych dniach moneta byla w pierwszej N radaru (liczone co tydzien,
    tylko z danych sprzed danego dnia). Uwaga: lista kandydatow to monety notowane DZIS."""
    btc = daily.get(f"BTC/{quote}")
    allowed = {s: set() for s in daily}
    d = pd.Timestamp(start).normalize()
    while d < pd.Timestamp(end):
        items = {s: metrics(df, btc, asof=d) for s, df in daily.items()}
        top = [r["symbol"] for r in score(items)][:top_n]
        for i in range(7):
            day = (d + pd.Timedelta(days=i)).date()
            for s in top:
                allowed[s].add(day)
        d += pd.Timedelta(days=7)
    return allowed
