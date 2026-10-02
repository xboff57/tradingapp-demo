"""
Dostawcy danych swiecowych.

AlpacaData - prawdziwe dane (akcje: feed IEX, krypto: Alpaca Crypto).
SimData    - deterministyczne dane syntetyczne dla konta 'demo' i testow bez internetu.

Oba zwracaja {symbol: DataFrame[open, high, low, close, volume]} z indeksem UTC.
"""

import hashlib
import os
import pickle
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from .config import CACHE_DIR
from .strategies import TIMEFRAME_MINUTES

COLS = ["open", "high", "low", "close", "volume"]


def is_crypto(symbol: str) -> bool:
    return "/" in symbol


def norm(symbol: str) -> str:
    """'BTC/USD' -> 'BTCUSD' (tak Alpaca zapisuje pozycje krypto)."""
    return symbol.replace("/", "").upper()


def drop_unfinished(df: pd.DataFrame, timeframe: str, now=None) -> pd.DataFrame:
    """Sygnaly liczymy tylko na zamknietych swiecach - usuwamy ostatnia, jesli jeszcze trwa."""
    if df.empty:
        return df
    now = now or datetime.now(timezone.utc)
    if df.index[-1] + timedelta(minutes=TIMEFRAME_MINUTES[timeframe]) > now:
        return df.iloc[:-1]
    return df


# ------------------------------------------------------------------ Alpaca
class AlpacaData:
    def __init__(self, key, secret, paper=True):
        from alpaca.data.historical import StockHistoricalDataClient, CryptoHistoricalDataClient
        self.stock = StockHistoricalDataClient(key, secret)
        self.crypto = CryptoHistoricalDataClient(key, secret)
        try:
            from alpaca.trading.client import TradingClient
            self._trading = TradingClient(key, secret, paper=paper)
        except Exception:
            self._trading = None

    @staticmethod
    def _tf(timeframe):
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
        m = TIMEFRAME_MINUTES[timeframe]
        if m >= 1440:
            return TimeFrame(m // 1440, TimeFrameUnit.Day)
        if m >= 60:
            return TimeFrame(m // 60, TimeFrameUnit.Hour)
        return TimeFrame(m, TimeFrameUnit.Minute)

    def bars(self, symbols, timeframe, start, end=None):
        from alpaca.data.requests import StockBarsRequest, CryptoBarsRequest
        from alpaca.data.enums import Adjustment, DataFeed
        out = {}
        stocks = [s for s in symbols if not is_crypto(s)]
        cryptos = [s for s in symbols if is_crypto(s)]
        tf = self._tf(timeframe)
        for i in range(0, len(stocks), 100):            # dlugie listy (np. kopiowanie funduszy) w paczkach
            chunk = stocks[i:i + 100]
            # ceny skorygowane o splity i dywidendy - inaczej split (np. 10:1) wyglada jak krach o 90%
            def fetch(syms):
                req = StockBarsRequest(symbol_or_symbols=syms, timeframe=tf, start=start, end=end,
                                       feed=DataFeed.IEX, adjustment=Adjustment.ALL)
                return self._split(self.stock.get_stock_bars(req).df, syms)
            try:
                out.update(fetch(chunk))
            except Exception:
                if len(chunk) == 1:
                    raise
                for sym in chunk:                       # jeden zly symbol nie psuje calej paczki
                    try:
                        out.update(fetch([sym]))
                    except Exception:
                        pass
        if cryptos:
            req = CryptoBarsRequest(symbol_or_symbols=cryptos, timeframe=tf, start=start, end=end)
            out.update(self._split(self.crypto.get_crypto_bars(req).df, cryptos))
        return out

    def universe(self, quote="USD"):
        """Kryptowaluty handlowane na Alpace (bez stablecoinow) z obrotem z ostatniej doby."""
        from .kraken import STABLE, FIAT
        syms = self.crypto_symbols(quote)
        if not syms:
            return []
        start = datetime.now(timezone.utc) - timedelta(days=3)
        bars = self.bars(syms, "1Day", start)
        out = [(s, float((d["close"] * d["volume"]).iloc[-1])) for s, d in bars.items() if len(d)]
        return sorted([x for x in out if x[0].split("/")[0] not in STABLE | FIAT], key=lambda x: -x[1])

    def crypto_symbols(self, quote="USD"):
        if getattr(self, "_trading", None) is None:
            return []
        from alpaca.trading.requests import GetAssetsRequest
        from alpaca.trading.enums import AssetClass
        assets = self._trading.get_all_assets(GetAssetsRequest(asset_class=AssetClass.CRYPTO))
        return sorted(a.symbol for a in assets if a.tradable and a.symbol.endswith("/" + quote))

    @staticmethod
    def _split(df, symbols):
        out = {}
        if df is None or df.empty:
            return out
        level0 = set(df.index.get_level_values(0))
        for s in symbols:
            if s in level0:
                d = df.xs(s, level=0)[COLS].sort_index()
                d.index = pd.to_datetime(d.index, utc=True)
                out[s] = d.astype(float)
        return out


# ------------------------------------------------------------------ Symulacja
EPOCH = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _seed(*parts) -> int:
    return int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:12], 16)


class SimData:
    """
    Deterministyczny rynek syntetyczny: te same zapytania daja te same ceny,
    wiec bot i backtest na koncie demo widza spojny swiat.
    Dzienny poziom = spacer losowy z okresami trendu; w ciagu dnia most Browna.
    Akcje notowane 13:30-20:00 UTC w dni robocze, krypto 24/7.
    """

    def _daily_levels(self, symbol, n_days):
        rng = np.random.default_rng(_seed(symbol, "daily"))
        crypto = is_crypto(symbol)
        sigma = 0.035 if crypto else 0.017
        drift = np.zeros(n_days)
        i = 0
        while i < n_days:                                    # okresy trendu 20-60 dni
            length = int(rng.integers(20, 60))
            drift[i:i + length] = rng.normal(0.0003, sigma * 0.08)
            i += length
        rets = drift + rng.normal(0, sigma, n_days)
        base = {"BTC/USD": 60000, "ETH/USD": 3000, "SOL/USD": 150, "SPY": 450}.get(symbol, 20 + _seed(symbol) % 300)
        return np.log(base) + np.concatenate([[0], np.cumsum(rets)])

    def _minute_path(self, symbol, day_idx, levels):
        rng = np.random.default_rng(_seed(symbol, "intraday", day_idx))
        sigma_m = (0.035 if is_crypto(symbol) else 0.017) / np.sqrt(1440) * 1.2
        w = np.cumsum(rng.normal(0, sigma_m, 1440))
        t = np.arange(1, 1441) / 1440
        bridge = w - t * w[-1]
        path = levels[day_idx] + t * (levels[day_idx + 1] - levels[day_idx]) + bridge
        vol = rng.lognormal(7, 0.6, 1440)
        return np.exp(path), vol

    def minutes(self, symbol, start, end):
        start = max(start, EPOCH)
        d0 = (start.date() - EPOCH.date()).days
        d1 = (end.date() - EPOCH.date()).days
        levels = self._daily_levels(symbol, d1 + 2)
        prices, vols = [], []
        for d in range(d0, d1 + 1):
            p, v = self._minute_path(symbol, d, levels)
            prices.append(p)
            vols.append(v)
        idx = pd.date_range(EPOCH + timedelta(days=d0), periods=1440 * (d1 - d0 + 1), freq="1min")
        s = pd.DataFrame({"price": np.concatenate(prices), "volume": np.concatenate(vols)}, index=idx)
        s = s[(s.index >= start) & (s.index < end)]
        if not is_crypto(symbol):
            mins = s.index.hour * 60 + s.index.minute
            s = s[(s.index.dayofweek < 5) & (mins >= 13 * 60 + 30) & (mins < 20 * 60)]
        return s

    def bars(self, symbols, timeframe, start, end=None):
        end = end or datetime.now(timezone.utc)
        out = {}
        m = TIMEFRAME_MINUTES[timeframe]
        for sym in symbols:
            s = self.minutes(sym, start, end)
            if s.empty:
                continue
            rule = f"{m}min" if m < 1440 else "1D"
            g = s.resample(rule, label="left", closed="left")
            df = pd.DataFrame({"open": g["price"].first(), "high": g["price"].max(),
                               "low": g["price"].min(), "close": g["price"].last(),
                               "volume": g["volume"].sum()}).dropna()
            out[sym] = df
        return out

    SIM_COINS = ["BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "AVAX", "LINK", "DOT", "LTC", "UNI", "ATOM", "NEAR",
                 "AAVE", "ARB", "OP", "SUI", "APT", "INJ", "FIL"]

    def universe(self, quote="USD"):
        """Rynek syntetyczny: staly zestaw monet z deterministycznym 'obrotem'."""
        return sorted([(f"{c}/{quote}", float(_seed(c, "vol") % 50_000_000 + 1_000_000)) for c in self.SIM_COINS],
                      key=lambda x: -x[1])

    def price(self, symbol, at=None):
        at = at or datetime.now(timezone.utc)
        s = self.minutes(symbol, at - timedelta(days=4), at + timedelta(minutes=1))
        return float(s["price"].iloc[-1]) if not s.empty else None


# ------------------------------------------------------------------ cache dla backtestow
def cached_bars(provider, symbols, timeframe, start, end, tag):
    """Backtest na danych z przeszlosci - zapisujemy je, zeby kolejne testy byly szybkie."""
    out, missing = {}, []
    for s in symbols:
        key = hashlib.md5(f"{tag}|{s}|{timeframe}|{start.date()}|{end.date()}|adj".encode()).hexdigest()
        path = os.path.join(CACHE_DIR, key + ".pkl")
        if os.path.exists(path):
            with open(path, "rb") as f:
                out[s] = pickle.load(f)
        else:
            missing.append((s, path))
    if missing:
        fetched = provider.bars([s for s, _ in missing], timeframe, start, end)
        for s, path in missing:
            if s in fetched:
                out[s] = fetched[s]
                with open(path, "wb") as f:
                    pickle.dump(fetched[s], f)
    return out
