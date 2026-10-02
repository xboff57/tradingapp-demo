"""
Kontekst rynku ("termometry") - wspolne dla backtestu i bota na zywo.

- Symbol nad srednia (np. ETF na WIG20 nad SMA200, SPY nad SMA200).
- Szerokosc rynku: jaki odsetek spolek bota jest nad swoja srednia (np. 200-dniowa).
  Spolka liczy sie dopiero, gdy ma pelna historie do sredniej. Gdy policzalnych spolek jest < 5,
  wskaznik jest nieznany i filtr nie blokuje.

Wszystko liczone na zamknieciach dziennych; w backtescie dzien D widzi tylko zamkniecie z D-1.
"""

import pandas as pd

MIN_NAMES = 5


def above_sma(df, n):
    """Seria bool po dacie: zamkniecie nad SMA n (NaN, gdy za krotka historia)."""
    c = df["close"]
    sma = c.rolling(int(n)).mean()
    out = (c > sma).astype(float)
    out[sma.isna()] = float("nan")
    out.index = pd.DatetimeIndex(pd.to_datetime(out.index.date))
    return out[~out.index.duplicated(keep="last")]


def breadth(daily, n):
    """Seria po dacie: odsetek spolek nad SMA n (NaN, gdy policzalnych < MIN_NAMES)."""
    cols = {s: above_sma(df, n) for s, df in daily.items() if df is not None and len(df) > n}
    if not cols:
        return pd.Series(dtype=float)
    m = pd.DataFrame(cols).sort_index()
    m = m.ffill(limit=5)                                  # dni bez notowan pojedynczej spolki
    cnt = m.notna().sum(axis=1)
    val = m.sum(axis=1) / cnt.where(cnt > 0)
    val[cnt < MIN_NAMES] = float("nan")
    return val


def parts(p):
    """Lista termometrow wlaczonych w parametrach bota."""
    out = []
    if p.get("regime_filter") and p.get("regime_symbol"):
        out.append(("sym", p["regime_symbol"], int(p["regime_sma_days"])))
    if (p.get("regime2_symbol") or "").strip():
        out.append(("sym", p["regime2_symbol"].strip().upper(), int(p.get("regime2_sma_days", 200))))
    if p.get("breadth_filter"):
        out.append(("breadth", int(p.get("breadth_sma_days", 200)), float(p.get("breadth_min", 0.5))))
    return out


def allowed_by_day(p, daily_for):
    """{data: bool} - czy wejscia sa dozwolone danego dnia (wg zamkniecia dnia poprzedniego).
    daily_for(symbols, n) -> {symbol: DataFrame dzienny} z zapasem historii na srednia n."""
    ps = parts(p)
    if not ps:
        return None
    series = []
    for part in ps:
        if part[0] == "sym":
            _, sym, n = part
            df = daily_for([sym], n).get(sym)
            if df is None or len(df) < n:
                continue                                      # brak danych termometru - nie blokuje
            s = above_sma(df, n).shift(1)
            series.append(s.map(lambda v: True if v != v else bool(v)))
        else:
            _, n, mn = part
            b = breadth(daily_for(list(p["symbols"]), n), n).shift(1)
            series.append(b.map(lambda v: True if v != v else bool(v >= mn)))
    if not series:
        return None
    m = pd.concat(series, axis=1).sort_index().ffill().fillna(True)
    ok = m.all(axis=1)
    return {ts.date(): bool(v) for ts, v in ok.items()}


def describe_now(p, daily_for):
    """Stan termometrow na teraz (dla bota na zywo): (ok, opis)."""
    ok, bits = True, []
    for part in parts(p):
        if part[0] == "sym":
            _, sym, n = part
            df = daily_for([sym], n).get(sym)
            if df is None or len(df) < n:
                bits.append(f"{sym}: za mało danych (pominięty)")
                continue
            sma = df["close"].rolling(n).mean().iloc[-1]
            last = df["close"].iloc[-1]
            good = bool(last > sma)
            ok &= good
            bits.append(f"{sym} {last:,.2f} vs SMA{n} {sma:,.2f} {'OK' if good else 'SŁABY'}")
        else:
            _, n, mn = part
            b = breadth(daily_for(list(p["symbols"]), n), n)
            v = b.dropna()
            if v.empty:
                bits.append("szerokość rynku: za mało danych (pominięta)")
                continue
            good = bool(v.iloc[-1] >= mn)
            ok &= good
            bits.append(f"szerokość {v.iloc[-1]:.0%} spółek nad SMA{n} (min. {mn:.0%}) {'OK' if good else 'SŁABA'}")
    return ok, " | ".join(bits)
