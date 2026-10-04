"""
Dane finansowe spółki / kryptowaluty do zakładki Wykresy (karta „Finanse”).

Źródła (wszystkie publiczne i darmowe):
- akcje USA: SEC EDGAR „company facts” (dane XBRL z raportów 10-K i 10-Q, prosto od regulatora; wymaga SEC_USER_AGENT),
- GPW, ETF-y, spółki spoza SEC (albo gdy brak SEC_USER_AGENT): Yahoo Finance (biblioteka yfinance),
- kryptowaluty: CoinGecko (kapitalizacja, podaż, rekord wszech czasów).

Wynik ma wspólny kształt: wycena (kafelki), tabela roczna (ostatnie lata), kwartały (przychody i zysk netto)
oraz źródło z datą ostatniego raportu. Wyniki są trzymane w pamięci podręcznej (data/cache/fund), żeby nie
odpytywać źródeł przy każdym otwarciu wykresu.
"""

import json
import logging
import math
import os
import re
import time
from datetime import date, datetime, timezone

from .config import CACHE_DIR, DEMO

log = logging.getLogger("tradingapp")
FDIR = os.path.join(CACHE_DIR, "fund")
os.makedirs(FDIR, exist_ok=True)
TTL_OK = 12 * 3600
TTL_ERR = 3600
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"

# sufiks IBKR -> sufiks Yahoo
YAHOO_SUFFIX = {"WSE": "WA", "LSE": "L", "AEB": "AS", "IBIS": "DE", "XETRA": "DE", "SBF": "PA", "BVME": "MI",
                "SWX": "SW", "EBS": "SW", "BM": "MC", "TSE": "TO", "SEHK": "HK", "ASX": "AX", "VSE": "VI", "PRA": "PR"}

ROWS = [  # klucz, etykieta, format
    ("revenue", "Przychody", "money"),
    ("operating_income", "Zysk operacyjny", "money"),
    ("net_income", "Zysk netto", "money"),
    ("net_margin", "Marża netto", "pct"),
    ("eps", "Zysk na akcję (EPS)", "eps"),
    ("ocf", "Przepływy operacyjne", "money"),
    ("fcf", "Wolne przepływy (FCF)", "money"),
    ("assets", "Aktywa", "money"),
    ("equity", "Kapitał własny", "money"),
    ("debt", "Dług", "money"),
    ("cash", "Gotówka", "money"),
    ("dps", "Dywidenda na akcję", "eps"),
]


# ------------------------------------------------------------------ pomocnicze
def _num(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _div(a, b):
    a, b = _num(a), _num(b)
    return a / b if a is not None and b not in (None, 0) else None


def _cache_path(key):
    return os.path.join(FDIR, re.sub(r"[^A-Za-z0-9._-]", "_", key) + ".json")


def _cache_get(key):
    try:
        with open(_cache_path(key), encoding="utf-8") as f:
            d = json.load(f)
        ttl = TTL_ERR if d.get("error") else TTL_OK
        if time.time() - d.get("_t", 0) < ttl:
            return d
    except (OSError, ValueError):
        pass
    return None


def _cache_put(key, d):
    d = dict(d, _t=time.time())
    tmp = _cache_path(key) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)
    os.replace(tmp, _cache_path(key))
    return d


def _kind(symbol):
    if "/" in symbol:
        return "crypto"
    return "stock"


# ------------------------------------------------------------------ SEC EDGAR (USA)
FLOW = {
    "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet",
                "RevenueFromContractWithCustomerIncludingAssessedTax", "RevenuesNetOfInterestExpense",
                "InterestAndDividendIncomeOperating"],
    "operating_income": ["OperatingIncomeLoss"],
    "net_income": ["NetIncomeLoss", "ProfitLoss", "NetIncomeLossAvailableToCommonStockholdersBasic"],
    "eps": ["EarningsPerShareDiluted", "EarningsPerShareBasic"],
    "ocf": ["NetCashProvidedByUsedInOperatingActivities",
            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"],
    "dps": ["CommonStockDividendsPerShareDeclared", "CommonStockDividendsPerShareCashPaid"],
}
INSTANT = {
    "assets": ["Assets"],
    "equity": ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
             "Cash"],
    "debt": ["LongTermDebt", "LongTermDebtNoncurrent", "LongTermDebtAndCapitalLeaseObligations", "DebtCurrent"],
}
PER_SHARE = {"eps", "dps"}


def _d(s):
    return date.fromisoformat(s)


def _entries(facts, concepts, per_share=False):
    """Wpisy XBRL dla pierwszego pasującego pojęcia (uzupełniane starszymi z kolejnych pojęć).
    Dla każdego okresu bierzemy wartość z najpóźniej złożonego raportu (korekty)."""
    unit = "USD/shares" if per_share else "USD"
    by_period = {}
    cands = []
    for c in concepts:
        node = facts.get("us-gaap", {}).get(c)
        vals = (node or {}).get("units", {}).get(unit)
        if vals:
            cands.append((max(v["end"] for v in vals), c, vals))
    cands.sort(key=lambda x: x[0], reverse=True)      # najpierw pojęcie z najświeższymi danymi
    for _, c, vals in cands:
        for v in vals:
            if v.get("form") not in ("10-K", "10-Q", "10-K/A", "10-Q/A", "20-F", "40-F"):
                continue
            key = (v.get("start"), v["end"])
            if key in by_period and by_period[key]["_c"] != c:
                continue                                # okres już pokryty lepszym pojęciem
            if key not in by_period or v.get("filed", "") > by_period[key].get("filed", ""):
                by_period[key] = dict(v, _c=c)
    return list(by_period.values())


def _flow_series(entries):
    """-> (roczne {koniec: (start, wartość)}, kwartały {koniec: (start, wartość)}) z wyliczonym Q4."""
    annual, quarter = {}, {}
    for v in entries:
        if not v.get("start"):
            continue
        days = (_d(v["end"]) - _d(v["start"])).days
        val = _num(v["val"])
        if val is None:
            continue
        if 350 <= days <= 380:
            annual[v["end"]] = (v["start"], val)
        elif 80 <= days <= 100:
            quarter[v["end"]] = (v["start"], val)
    for end, (start, val) in annual.items():          # Q4 = rok - (Q1+Q2+Q3)
        if end in quarter:
            continue
        inside = sorted((e, q) for e, q in quarter.items() if q[0] >= start and e < end)
        if len(inside) == 3:
            last_end = inside[-1][0]
            q4_start = date.fromordinal(_d(last_end).toordinal() + 1).isoformat()
            quarter[end] = (q4_start, val - sum(q[1] for _, q in inside))
    return annual, quarter


def _ttm(quarter):
    ends = sorted(quarter)[-4:]
    if len(ends) < 4:
        return None
    span = (_d(ends[-1]) - _d(quarter[ends[0]][0])).days
    if not 350 <= span <= 380:
        return None
    return sum(quarter[e][1] for e in ends)


def _instant(entries):
    out = {}
    for v in entries:
        val = _num(v["val"])
        if val is not None and not v.get("start"):
            if v["end"] not in out or v.get("filed", "") > out[v["end"]][1]:
                out[v["end"]] = (val, v.get("filed", ""))
    return {k: v[0] for k, v in out.items()}


def _at_or_before(series, end, max_days=10):
    """Wartość bilansowa na koniec roku obrotowego (dopuszczalne kilka dni różnicy)."""
    best = None
    for e, v in series.items():
        gap = (_d(end) - _d(e)).days
        if 0 <= gap <= max_days and (best is None or gap < best[0]):
            best = (gap, v)
    return best[1] if best else None


def _shares(facts):
    node = facts.get("dei", {}).get("EntityCommonStockSharesOutstanding", {}).get("units", {}).get("shares")
    if node:
        last = max(v["end"] for v in node)
        latest = [v for v in node if v["end"] == last]
        accn = max(v.get("filed", "") for v in latest)
        return sum(_num(v["val"]) or 0 for v in latest if v.get("filed", "") == accn) or None, last
    node = facts.get("us-gaap", {}).get("WeightedAverageNumberOfDilutedSharesOutstanding", {}).get("units", {}).get("shares")
    if node:
        v = max(node, key=lambda x: (x["end"], x.get("filed", "")))
        return _num(v["val"]), v["end"]
    return None, None


def from_sec(symbol, price):
    from .signals import SecClient
    sec = SecClient()
    tick = sec.tickers().get(symbol.upper().replace(".", "-")) or sec.tickers().get(symbol.upper())
    if not tick:
        return None
    cik = tick["cik"]
    cf = sec.get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json", as_json=True)
    if not cf or not cf.get("facts", {}).get("us-gaap"):
        return None
    facts = cf["facts"]
    flows, instants, last_filing = {}, {}, None
    for k, concepts in FLOW.items():
        ents = _entries(facts, concepts, per_share=k in PER_SHARE)
        flows[k] = _flow_series(ents)
        for v in ents:
            if not v.get("form", "").startswith(("10-K", "10-Q")):
                continue
            if not last_filing or v.get("filed", "") > last_filing["filed"]:
                last_filing = {"form": v["form"], "filed": v.get("filed", ""), "accn": v.get("accn", ""), "end": v["end"]}
            elif v.get("accn") == last_filing["accn"] and v["end"] > last_filing["end"]:
                last_filing["end"] = v["end"]           # okres raportu = najpóźniejszy koniec okresu w tym raporcie
    for k, concepts in INSTANT.items():
        instants[k] = _instant(_entries(facts, concepts))
    if not flows["revenue"][0] and not flows["net_income"][0]:
        return None

    # roczne: ostatnie 5 lat obrotowych (wg zysku netto albo przychodów)
    ann_ends = sorted(set(flows["net_income"][0]) | set(flows["revenue"][0]))[-5:]
    years, rows = [], {k: [] for k, _, _ in ROWS}
    for end in ann_ends:
        years.append(end[:4] if end[5:7] >= "06" else str(int(end[:4]) - 1))
        g = lambda k: (flows[k][0].get(end) or (None, None))[1]
        rev, ni, ocf, capex = g("revenue"), g("net_income"), g("ocf"), g("capex")
        rows["revenue"].append(rev)
        rows["operating_income"].append(g("operating_income"))
        rows["net_income"].append(ni)
        rows["net_margin"].append(_div(ni, rev) * 100 if _div(ni, rev) is not None else None)
        rows["eps"].append(g("eps"))
        rows["ocf"].append(ocf)
        rows["fcf"].append(ocf - capex if ocf is not None and capex is not None else None)
        rows["dps"].append(g("dps"))
        for k in ("assets", "equity", "cash", "debt"):
            rows[k].append(_at_or_before(instants[k], end))

    # kwartały: ostatnie 8
    qr, qn = flows["revenue"][1], flows["net_income"][1]
    q_ends = sorted(set(qr) | set(qn))[-8:]
    quarters = {"periods": [_qlabel(e) for e in q_ends], "revenue": [(qr.get(e) or (None, None))[1] for e in q_ends],
                "net_income": [(qn.get(e) or (None, None))[1] for e in q_ends]}

    ttm = {k: _ttm(flows[k][1]) for k in ("revenue", "net_income", "eps", "ocf", "capex", "dps")}
    for k in ("revenue", "net_income", "eps", "ocf", "capex"):     # brak pełnych 4 kwartałów -> ostatni rok
        if ttm[k] is None and flows[k][0]:
            ttm[k] = flows[k][0][max(flows[k][0])][1]
    if ttm["dps"] is None and flows["dps"][0]:
        last = max(flows["dps"][0])
        if (date.today() - _d(last)).days < 500:
            ttm["dps"] = flows["dps"][0][last][1]
    latest = {k: instants[k][max(instants[k])] if instants[k] else None for k in INSTANT}
    shares, _ = _shares(facts)
    mcap = shares * price if shares and price else None
    sub = sec.get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json", as_json=True) or {}
    accn = (last_filing or {}).get("accn", "")
    url = (f"https://www.sec.gov/Archives/edgar/data/{cik}/{accn.replace('-', '')}/{accn}-index.htm" if accn else
           f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}")
    res = {
        "kind": "stock", "symbol": symbol, "name": cf.get("entityName") or tick.get("title"),
        "sector": sub.get("sicDescription"), "currency": "USD",
        "source": "SEC EDGAR (raporty 10-K / 10-Q)", "source_url": url,
        "last_report": ({"form": last_filing["form"], "filed": last_filing["filed"], "period": last_filing["end"]}
                        if last_filing else None),
        "annual": {"years": years, "rows": [{"key": k, "label": l, "fmt": f, "values": rows[k]} for k, l, f in ROWS
                                            if any(v is not None for v in rows[k])]},
        "quarters": quarters,
    }
    res["valuation"] = _valuation(price, mcap, ttm.get("revenue"), ttm.get("net_income"), ttm.get("eps"),
                                  latest.get("equity"), latest.get("debt"), latest.get("cash"),
                                  (ttm["ocf"] - ttm["capex"]) if ttm.get("ocf") is not None and ttm.get("capex") is not None else None,
                                  ttm.get("dps"), "USD")
    return res


def _qlabel(end):
    """Kwartał kalendarzowy, w którym kończy się okres (np. 2025-12-27 -> 4Q25)."""
    d = date.fromordinal(_d(end).toordinal() - 10)
    return f"{(d.month - 1) // 3 + 1}Q{d.year % 100:02d}"


def _valuation(price, mcap, rev, ni, eps, equity, debt, cash, fcf, dps, cur):
    out = []

    def add(label, value, fmt, hint):
        if value is not None:
            out.append({"label": label, "value": value, "fmt": fmt, "hint": hint})

    add("Kapitalizacja", mcap, "money", "Wartość wszystkich akcji po obecnej cenie.")
    pe = _div(price, eps) if eps and eps > 0 else (_div(mcap, ni) if ni and ni > 0 else None)
    add("C/Z (P/E)", pe, "x", "Cena / zysk na akcję z ostatnich 12 miesięcy. Ile lat zysków płacisz za spółkę.")
    add("C/WK (P/B)", _div(mcap, equity) if equity and equity > 0 else None, "x",
        "Kapitalizacja / kapitał własny. Poniżej 1 = rynek wycenia spółkę taniej niż jej majątek księgowy.")
    add("C/P (P/S)", _div(mcap, rev), "x", "Kapitalizacja / przychody z 12 miesięcy.")
    add("Marża netto", _div(ni, rev) * 100 if _div(ni, rev) is not None else None, "pct",
        "Jaka część przychodów zostaje jako zysk netto (12 miesięcy).")
    add("ROE", _div(ni, equity) * 100 if equity and equity > 0 and ni is not None else None, "pct",
        "Zysk netto / kapitał własny — jak dobrze spółka pracuje na pieniądzach akcjonariuszy.")
    add("Dług / kapitał", _div(debt, equity) if equity and equity > 0 else None, "x",
        "Zadłużenie względem kapitału własnego. Powyżej 2 = wysoka dźwignia (w bankach to norma).")
    add("Dług netto", (debt - cash) if debt is not None and cash is not None else None, "money",
        "Dług minus gotówka. Ujemny = spółka ma więcej gotówki niż długu.")
    add("FCF (12 mies.)", fcf, "money", "Wolne przepływy pieniężne: gotówka z działalności minus inwestycje.")
    add("Stopa dywidendy", _div(dps, price) * 100 if dps and price else None, "pct",
        "Dywidenda na akcję z ostatnich 12 miesięcy / obecna cena.")
    add("Przychody (12 mies.)", rev, "money", "Suma przychodów z ostatnich 4 kwartałów.")
    add("Zysk netto (12 mies.)", ni, "money", "Suma zysku netto z ostatnich 4 kwartałów.")
    return out


# ------------------------------------------------------------------ Yahoo Finance (GPW, ETF-y, reszta świata)
def us_symbol(symbol):
    """AAPL, BRK.B (klasa akcji) -> spółka z USA; PKO.WSE -> nie."""
    return "/" not in symbol and ("." not in symbol or len(symbol.rsplit(".", 1)[1]) == 1)


def yahoo_symbol(symbol):
    if us_symbol(symbol):
        return symbol.replace(".", "-")
    if "." in symbol:
        base, ex = symbol.rsplit(".", 1)
        return f"{base}.{YAHOO_SUFFIX.get(ex.upper(), ex)}"
    return symbol.replace("/", "-")


def _frame_rows(df, keys):
    """DataFrame yfinance (wiersze = pozycje, kolumny = daty) -> {data: wartość} dla pierwszego pasującego klucza."""
    if df is None or getattr(df, "empty", True):
        return {}
    for k in keys:
        if k in df.index:
            s = df.loc[k]
            out = {str(c.date()) if hasattr(c, "date") else str(c)[:10]: _num(v) for c, v in s.items()}
            out = {d: v for d, v in out.items() if v is not None}
            if out:
                return out
    return {}


Y_IS = {"revenue": ["TotalRevenue", "OperatingRevenue"], "operating_income": ["OperatingIncome", "TotalOperatingIncomeAsReported"],
        "net_income": ["NetIncomeCommonStockholders", "NetIncome", "NetIncomeContinuousOperations"],
        "eps": ["DilutedEPS", "BasicEPS"]}
Y_BS = {"assets": ["TotalAssets"], "equity": ["StockholdersEquity", "CommonStockEquity", "TotalEquityGrossMinorityInterest"],
        "debt": ["TotalDebt", "LongTermDebt"], "cash": ["CashAndCashEquivalents", "CashCashEquivalentsAndShortTermInvestments",
                                                        "CashFinancial"], "shares": ["OrdinarySharesNumber", "ShareIssued"]}
Y_CF = {"ocf": ["OperatingCashFlow"], "capex": ["CapitalExpenditure"], "fcf": ["FreeCashFlow"],
        "div_paid": ["CashDividendsPaid", "CommonStockDividendPaid"]}


def from_yahoo(symbol, price):
    import yfinance as yf
    ysym = yahoo_symbol(symbol)
    t = yf.Ticker(ysym)
    try:
        info = t.get_info() or {}
    except Exception as e:
        log.info(f"Yahoo info {ysym}: {e}")
        info = {}
    qtype = (info.get("quoteType") or "").upper()
    if qtype in ("ETF", "MUTUALFUND"):
        return _yahoo_fund(symbol, ysym, info, price)

    def get(fn, freq):
        try:
            return fn(freq=freq)
        except Exception as e:
            log.info(f"Yahoo {ysym} {fn.__name__} {freq}: {e}")
            return None
    a_is, a_bs, a_cf = get(t.get_income_stmt, "yearly"), get(t.get_balance_sheet, "yearly"), get(t.get_cash_flow, "yearly")
    q_is = get(t.get_income_stmt, "quarterly")
    A = {k: _frame_rows(a_is, v) for k, v in Y_IS.items()}
    A.update({k: _frame_rows(a_bs, v) for k, v in Y_BS.items()})
    A.update({k: _frame_rows(a_cf, v) for k, v in Y_CF.items()})
    Q = {k: _frame_rows(q_is, v) for k, v in Y_IS.items()}
    if not A["revenue"] and not A["net_income"] and not info.get("marketCap"):
        return None
    ends = sorted(set(A["revenue"]) | set(A["net_income"]))[-5:]
    rows = {k: [] for k, _, _ in ROWS}
    for e in ends:
        rev, ni = A["revenue"].get(e), A["net_income"].get(e)
        rows["revenue"].append(rev)
        rows["operating_income"].append(A["operating_income"].get(e))
        rows["net_income"].append(ni)
        rows["net_margin"].append(_div(ni, rev) * 100 if _div(ni, rev) is not None else None)
        rows["eps"].append(A["eps"].get(e))
        rows["ocf"].append(A["ocf"].get(e))
        fcf = A["fcf"].get(e)
        if fcf is None and A["ocf"].get(e) is not None and A["capex"].get(e) is not None:
            fcf = A["ocf"][e] + A["capex"][e]          # capex w Yahoo jest ujemny
        rows["fcf"].append(fcf)
        for k in ("assets", "equity", "debt", "cash"):
            rows[k].append(A[k].get(e))
        sh, dp = A["shares"].get(e), A["div_paid"].get(e)
        rows["dps"].append(abs(dp) / sh if dp and sh else None)
    q_ends = sorted(set(Q["revenue"]) | set(Q["net_income"]))[-8:]
    quarters = {"periods": [_qlabel(e) for e in q_ends], "revenue": [Q["revenue"].get(e) for e in q_ends],
                "net_income": [Q["net_income"].get(e) for e in q_ends]}

    def ttm(k):
        ends4 = sorted(Q[k])[-4:]
        if len(ends4) == 4 and 250 <= (_d(ends4[-1]) - _d(ends4[0])).days <= 290:
            return sum(Q[k][e] for e in ends4)
        return A[k][max(A[k])] if A[k] else None

    cur = info.get("financialCurrency") or info.get("currency") or ("PLN" if symbol.endswith(".WSE") else "USD")
    price_cur = info.get("currency") or cur
    last = lambda k: A[k][max(A[k])] if A[k] else None
    shares = info.get("sharesOutstanding") or last("shares")
    mcap = info.get("marketCap") or (shares * price if shares and price else None)
    dps = info.get("trailingAnnualDividendRate") or info.get("dividendRate")
    val = _valuation(price, mcap, ttm("revenue"), ttm("net_income"), info.get("trailingEps") or ttm("eps"),
                     last("equity"), last("debt"), last("cash"),
                     info.get("freeCashflow") or (rows["fcf"][-1] if rows["fcf"] else None), dps, cur)
    for v in val:                                       # Yahoo podaje gotowe wskaźniki - lepsze niż nasze przybliżenia
        if v["label"] == "C/Z (P/E)" and _num(info.get("trailingPE")):
            v["value"] = _num(info["trailingPE"])
        if v["label"] == "C/WK (P/B)" and _num(info.get("priceToBook")):
            v["value"] = _num(info["priceToBook"])
    if price_cur != cur:
        for v in val:
            if v["label"] in ("Kapitalizacja",):
                v["hint"] += f" Kwota w {price_cur} (waluta notowań)."
    return {
        "kind": "stock", "symbol": symbol, "name": info.get("longName") or info.get("shortName") or symbol,
        "sector": " · ".join(x for x in (info.get("sector"), info.get("industry")) if x) or None,
        "currency": cur, "source": "Yahoo Finance", "source_url": f"https://finance.yahoo.com/quote/{ysym}/financials",
        "last_report": {"form": "raport kwartalny" if q_ends else "raport roczny",
                        "period": (q_ends or ends or [None])[-1], "filed": None},
        "about": (info.get("longBusinessSummary") or "")[:600] or None,
        "employees": info.get("fullTimeEmployees"), "website": info.get("website"),
        "annual": {"years": [e[:4] for e in ends],
                   "rows": [{"key": k, "label": l, "fmt": f, "values": rows[k]} for k, l, f in ROWS
                            if any(v is not None for v in rows[k])]},
        "quarters": quarters, "valuation": val,
    }


def _yahoo_fund(symbol, ysym, info, price):
    tiles = []

    def add(label, value, fmt, hint):
        if _num(value) is not None:
            tiles.append({"label": label, "value": _num(value), "fmt": fmt, "hint": hint})
    add("Aktywa funduszu", info.get("totalAssets"), "money", "Ile pieniędzy zarządza fundusz.")
    er = _num(info.get("netExpenseRatio"))             # Yahoo: netExpenseRatio w %, annualReportExpenseRatio jako ułamek
    if er is None and _num(info.get("annualReportExpenseRatio")) is not None:
        er = _num(info["annualReportExpenseRatio"]) * 100
    add("Opłata roczna", er, "pct", "Koszt funduszu rocznie (TER).")
    y = info.get("yield") or info.get("trailingAnnualDividendYield")
    add("Stopa dywidendy", y * 100 if y and y < 1 else y, "pct", "Wypłaty z ostatnich 12 miesięcy / cena.")
    add("Zwrot od początku roku", (info.get("ytdReturn") or 0) * (100 if abs(info.get("ytdReturn") or 0) < 2 else 1) or None,
        "pct", "Zmiana wartości od 1 stycznia.")
    add("Zwrot 3 lata (średnio rocznie)", (info.get("threeYearAverageReturn") or 0) * 100 or None, "pct", "")
    add("Zwrot 5 lat (średnio rocznie)", (info.get("fiveYearAverageReturn") or 0) * 100 or None, "pct", "")
    add("Beta (3 lata)", info.get("beta3Year"), "x", "Wrażliwość na ruchy rynku (1 = jak rynek).")
    return {"kind": "fund", "symbol": symbol, "name": info.get("longName") or info.get("shortName") or symbol,
            "sector": " · ".join(x for x in (info.get("fundFamily"), info.get("category")) if x) or "Fundusz ETF",
            "currency": info.get("currency") or "USD", "source": "Yahoo Finance",
            "source_url": f"https://finance.yahoo.com/quote/{ysym}", "last_report": None,
            "about": (info.get("longBusinessSummary") or "")[:600] or None, "valuation": tiles,
            "annual": None, "quarters": None}


# ------------------------------------------------------------------ CoinGecko (krypto)
def from_coingecko(symbol):
    import requests
    base, quote = symbol.split("/", 1)
    base = {"XBT": "BTC", "XDG": "DOGE"}.get(base.upper(), base.upper())
    vs = quote.lower() if quote.upper() in ("USD", "EUR", "PLN", "GBP", "USDT", "USDC") else "usd"
    vs = "usd" if vs in ("usdt", "usdc") else vs
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept": "application/json"})
    key = os.environ.get("COINGECKO_API_KEY", "").strip()
    if key:
        s.headers["x-cg-demo-api-key"] = key
    r = s.get("https://api.coingecko.com/api/v3/search", params={"query": base}, timeout=20)
    r.raise_for_status()
    coins = [c for c in r.json().get("coins", []) if (c.get("symbol") or "").upper() == base]
    if not coins:
        return None
    coins.sort(key=lambda c: c.get("market_cap_rank") or 10 ** 9)
    cid = coins[0]["id"]
    r = s.get(f"https://api.coingecko.com/api/v3/coins/{cid}", timeout=20,
              params={"localization": "false", "tickers": "false", "community_data": "false",
                      "developer_data": "false", "sparkline": "false"})
    r.raise_for_status()
    c = r.json()
    md = c.get("market_data") or {}
    pick = lambda k: _num((md.get(k) or {}).get(vs))
    tiles = []

    def add(label, value, fmt, hint):
        if value is not None:
            tiles.append({"label": label, "value": value, "fmt": fmt, "hint": hint})
    add("Kapitalizacja", pick("market_cap"), "money", "Cena × monety w obiegu.")
    add("Miejsce w rankingu", _num(c.get("market_cap_rank")), "rank", "Pozycja wg kapitalizacji na CoinGecko.")
    add("Wycena przy pełnej podaży", pick("fully_diluted_valuation"), "money",
        "Kapitalizacja, gdyby w obiegu były już wszystkie monety (FDV). Duża różnica = przyszła podaż może ciążyć cenie.")
    add("Obrót 24 h", pick("total_volume"), "money", "Wartość handlu ze wszystkich giełd z doby.")
    circ, mx, tot = _num(md.get("circulating_supply")), _num(md.get("max_supply")), _num(md.get("total_supply"))
    add("W obiegu", circ, "count", "Liczba monet w obiegu.")
    add("Podaż maksymalna", mx, "count", "Górny limit monet (brak = bez limitu).")
    if circ and (mx or tot):
        add("Wyemitowane z maks.", circ / (mx or tot) * 100, "pct", "Ile procent docelowej podaży już krąży.")
    add("Rekord wszech czasów (ATH)", pick("ath"), "price", "Najwyższa cena w historii.")
    add("Od rekordu", _num((md.get("ath_change_percentage") or {}).get(vs)), "pct", "Ile cena jest pod szczytem.")
    for k, lab in (("price_change_percentage_30d", "Zmiana 30 dni"), ("price_change_percentage_1y", "Zmiana 1 rok")):
        add(lab, _num(md.get(k)), "pct", "")
    desc = re.sub(r"<[^>]+>", "", (c.get("description") or {}).get("en") or "")[:600] or None
    return {"kind": "crypto", "symbol": symbol, "name": c.get("name") or base,
            "sector": ", ".join((c.get("categories") or [])[:3]) or None, "currency": vs.upper(),
            "source": "CoinGecko", "source_url": f"https://www.coingecko.com/en/coins/{cid}",
            "last_report": None, "about": desc, "genesis": c.get("genesis_date"),
            "valuation": tiles, "annual": None, "quarters": None}


# ------------------------------------------------------------------ wejście
def get(symbol, price=None, refresh=False):
    symbol = symbol.strip().upper()
    if DEMO:
        return {"error": "W wersji demonstracyjnej dane finansowe są wyłączone — ceny są symulowane, "
                         "a program nie łączy się z internetem."}
    if re.fullmatch(r"[A-Z]{3}\.[A-Z]{3}", symbol) and symbol[4:] not in YAHOO_SUFFIX:
        return {"error": "Para walutowa — waluty nie mają sprawozdań finansowych."}
    key = f"{symbol}"
    if not refresh:
        c = _cache_get(key)
        if c:
            return _reprice(c, price)
    kind = _kind(symbol)
    errors, res = [], None
    try:
        if kind == "crypto":
            res = from_coingecko(symbol)
        else:
            from .signals import SEC_USER_AGENT
            if us_symbol(symbol) and SEC_USER_AGENT:
                try:
                    res = from_sec(symbol, price)
                except Exception as e:
                    log.info(f"SEC {symbol}: {e}")
                    errors.append(f"SEC: {str(e)[:120]}")
            if res is None:
                res = from_yahoo(symbol, price)
    except Exception as e:
        log.warning(f"Dane finansowe {symbol}: {e}")
        errors.append(str(e)[:200])
    if res is None:
        msg = ("Nie znaleziono danych finansowych dla tego symbolu."
               + (f" ({'; '.join(errors)})" if errors else ""))
        _cache_put(key, {"error": msg})
        return {"error": msg}
    res["fetched"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    res["price_at_fetch"] = price
    return _reprice(_cache_put(key, res), price)


def _reprice(d, price):
    """Wskaźniki zależne od ceny (kapitalizacja, C/Z, C/WK, C/P, dywidenda) przeliczone na dzisiejszą cenę."""
    d = {k: v for k, v in d.items() if k != "_t"}
    p0 = d.get("price_at_fetch")
    if d.get("error") or not price or not p0 or d.get("kind") != "stock":
        return d
    f = price / p0
    if abs(f - 1) < 1e-9:
        return d
    vals = []
    for v in d.get("valuation", []):
        v = dict(v)
        if v["label"] in ("Kapitalizacja", "C/Z (P/E)", "C/WK (P/B)", "C/P (P/S)"):
            v["value"] = v["value"] * f
        elif v["label"] == "Stopa dywidendy":
            v["value"] = v["value"] / f
        vals.append(v)
    return dict(d, valuation=vals)
