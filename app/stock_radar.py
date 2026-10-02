"""
Radar spółek: codzienny ranking akcji z GPW (dane IBKR) i z USA (dane Alpaki), obok radaru altcoinów.

Dla każdej spółki z listy (tylko zamknięte dni):
  - siła względem indeksu z 30 dni (zmiana spółki minus zmiana ETF-u na WIG20 / SPY),
  - momentum z 7 dni,
  - wzrost obrotu (średni obrót 7 dni / 30 dni),
  - odległość od maksimum 52-tygodniowego (blisko szczytu = silny trend),
  - trend: cena nad SMA 50 i nad SMA 200.
Wynik 0-100 = 30% siła vs indeks + 15% momentum + 15% obrót + 25% blisko szczytu + 10% nad SMA 50 + 5% nad SMA 200
(pozycje w rankingu, nie surowe liczby). Spółki z małym obrotem są pomijane (pułapka płynności).

„Sygnał bota GPW” = dziś wybicie nad maksimum z 34 dni przy cenie nad SMA 200 — to warunek wejścia bota GPW agresywnego.
To lista kandydatów do obejrzenia, nie rekomendacja.
"""

import json
import threading
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd

from . import radar

PL = ZoneInfo("Europe/Warsaw")
_lock = threading.Lock()

GPW = ["PKO", "PKN", "PZU", "PEO", "KGH", "LPP", "DNP", "ALE", "CDR", "SPL", "MBK", "KRU", "PGE", "ALR", "OPL", "BDX",
       "CPS", "KTY", "PCO", "JSW", "TPE", "ACP", "XTB", "11B", "TXT", "ING", "BHW", "ENA", "MIL", "ASB", "GPW", "EAT",
       "DOM", "NEU", "APR", "CAR", "BFT", "GPP", "SNT", "DVL", "PLW", "VRG", "1AT", "ATT", "CCC", "ZAB", "BNP", "CBF",
       "EUR", "LWB", "MBR", "PEP", "RBW", "SLV", "TEN", "VOX", "WPL", "ABE", "ASE", "HUG", "MRC", "CMR", "AMC", "APT",
       "ARH", "ATC", "BRS", "ECH", "ENT", "GTC", "MCI", "MLG", "NWG", "PCR", "PBX", "PXM", "RVU", "SHO", "STP", "STX",
       "TOR", "UNT", "WLT", "CRJ", "DAT", "MDG", "OPN", "SGN", "VGO", "LBW", "PKP", "FTE", "BIO", "KGN"]
USA = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AVGO", "LLY", "JPM", "V", "UNH", "XOM", "MA", "COST",
       "HD", "PG", "JNJ", "ABBV", "NFLX", "CRM", "BAC", "ORCL", "CVX", "MRK", "KO", "PEP", "ADBE", "AMD", "TMO", "WMT",
       "LIN", "MCD", "CSCO", "ACN", "ABT", "DHR", "WFC", "INTU", "IBM", "GE", "CAT", "QCOM", "TXN", "AMGN", "VZ", "PM",
       "NOW", "ISRG", "UBER", "SPGI", "RTX", "NEE", "HON", "PFE", "UNP", "AMAT", "LOW", "GS", "T", "BKNG", "CMCSA", "MS",
       "PLD", "BLK", "SCHW", "SYK", "ELV", "TJX", "LMT", "DE", "BA", "C", "MDT", "ADP", "VRTX", "PANW", "MU", "ADI",
       "LRCX", "GILD", "SBUX", "MMC", "CB", "REGN", "BMY", "KLAC", "SO", "PLTR", "ANET", "MO", "DUK", "CI", "ZTS",
       "SNPS", "CDNS", "CRWD", "MCK", "WM", "ITW", "SHW", "EQIX", "GD"]

MARKETS = {
    "gpw": {"label": "GPW", "symbols": [s + ".WSE" for s in GPW], "bench": "ETFBW20TR.WSE", "bench_label": "WIG20 (ETF)",
            "currency": "PLN", "min_turnover": 1_000_000, "source": "ibkr", "after": (17, 30)},
    "usa": {"label": "USA", "symbols": USA, "bench": "SPY", "bench_label": "S&P 500 (SPY)",
            "currency": "USD", "min_turnover": 20_000_000, "source": "alpaca", "after": (22, 30)},
}
WEIGHTS = {"rel30": 30, "ret7": 15, "vol_ratio": 15, "dist_high": 25}


def metrics(daily, bench):
    m = radar.metrics(daily, bench, with_outlook=True)
    if not m:
        return None
    d = radar.closed_days(daily)
    c, h = d["close"], d["high"]
    hi252 = h.iloc[-252:].max()
    m["dist_high"] = float(c.iloc[-1] / hi252 - 1) * 100
    sma200 = c.rolling(200).mean().iloc[-1]
    m["above_sma200"] = bool(c.iloc[-1] > sma200) if sma200 == sma200 else None
    prev34 = h.shift(1).rolling(34).max().iloc[-1]
    m["breakout34"] = bool(prev34 == prev34 and c.iloc[-1] > prev34)
    m["turnover20"] = float((c * d["volume"]).iloc[-20:].mean())
    m["bot_signal"] = bool(m["breakout34"] and m["above_sma200"])
    return m


def score(items):
    syms = [s for s, m in items.items() if m]
    if not syms:
        return []
    df = pd.DataFrame({s: items[s] for s in syms}).T
    total = pd.Series(0.0, index=df.index)
    for k, w in WEIGHTS.items():
        total += df[k].astype(float).rank(pct=True) * w
    total += df["above_sma50"].map(lambda x: 10.0 if x else 0.0)
    total += df["above_sma200"].map(lambda x: 5.0 if x else 0.0)
    out = []
    for s in total.sort_values(ascending=False).index:
        m = dict(items[s])
        m.update(symbol=s, score=round(float(total[s]), 1))
        out.append(m)
    return out


def scan(provider, market, db=None):
    cfg = MARKETS[market]
    now = datetime.now(timezone.utc)
    syms = cfg["symbols"]
    daily = provider.bars(list(dict.fromkeys(syms + [cfg["bench"]])), "1Day", now - timedelta(days=560))
    bench = daily.get(cfg["bench"])
    items, illiquid, short = {}, [], []
    for s in syms:
        m = metrics(daily.get(s), bench) if daily.get(s) is not None else None
        if not m:
            short.append(s)
        elif m["turnover20"] < cfg["min_turnover"]:
            illiquid.append(s)
        else:
            items[s] = m
    ranked = score(items)
    trends = [m["outlook"]["trend"] for m in items.values() if m.get("outlook")]
    res = {"ts": now.isoformat(timespec="seconds"), "provider": market, "quote": cfg["currency"],
           "market": {"up": trends.count(1), "side": trends.count(0), "down": trends.count(-1),
                      "btc": radar.outlook(bench) if bench is not None else None, "bench": cfg["bench_label"]},
           "universe": len(syms), "candidates": len(items), "items": ranked,
           "skipped": short, "illiquid": illiquid, "min_turnover": cfg["min_turnover"]}
    if db is not None:
        db.execute("INSERT INTO radar_scans(ts, provider, quote, data) VALUES (?,?,?,?)",
                   (res["ts"], market, cfg["currency"], json.dumps(res)))
        db.execute("DELETE FROM radar_scans WHERE id NOT IN (SELECT id FROM radar_scans ORDER BY id DESC LIMIT 200)")
    return res


def latest(db, market):
    return radar.latest(db, market, MARKETS[market]["currency"])


def due(db, market, now=None):
    """Czy pora na automatyczny skan: dzień roboczy po zamknięciu sesji, a ostatni skan sprzed tej sesji."""
    now = now or datetime.now(PL)
    if now.weekday() >= 5:
        return False
    h, mi = MARKETS[market]["after"]
    if (now.hour, now.minute) < (h, mi):
        return False
    last = latest(db, market)
    if not last:
        return True
    ts = datetime.fromisoformat(last["ts"]).astimezone(PL)
    return ts.date() < now.date() or (ts.hour, ts.minute) < (h, mi)
