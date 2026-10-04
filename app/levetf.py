"""
ETF-y lewarowane i odwrotne — „dźwignia bez kredytu”.

Bot liczy sygnał na spółce albo indeksie (np. NVDA), a kupuje ETF, który porusza się 2× tak jak ona (na wzrost)
albo odwrotnie do niej (na spadek). Kupujesz zwykły ETF za własne pieniądze: nie ma pożyczki, odsetek ani wezwania
do dopłaty, a strata jest ograniczona do włożonej kwoty.

Uwaga: te ETF-y odnawiają dźwignię codziennie. Przy długim trzymaniu w rynku bez trendu tracą na wartości
(„erozja zmienności”) — nadają się do krótszych ruchów, nie na lata.

Lista poniżej to kandydaci; bot przed użyciem sprawdza u brokera, czy dany ETF istnieje i jest w obrocie
(verify), i bierze pierwszego dostępnego. Własne pary: ustawienie bota „Własne pary ETF”.
"""

import json
import os
import time

from .config import CACHE_DIR

# spółka / indeks -> (kandydaci na wzrost [(ETF, dźwignia)], kandydaci na spadek [(ETF, dźwignia)])
PAIRS = {
    # --- indeksy i sektory USA
    "SPY": ([("SSO", 2), ("UPRO", 3)], [("SH", -1), ("SDS", -2), ("SPXU", -3)]),
    "QQQ": ([("QLD", 2), ("TQQQ", 3)], [("PSQ", -1), ("QID", -2), ("SQQQ", -3)]),
    "IWM": ([("UWM", 2), ("TNA", 3)], [("RWM", -1), ("TWM", -2), ("TZA", -3)]),
    "DIA": ([("DDM", 2), ("UDOW", 3)], [("DOG", -1), ("DXD", -2), ("SDOW", -3)]),
    "SMH": ([("USD", 2), ("SOXL", 3)], [("SSG", -2), ("SOXS", -3)]),
    "SOXX": ([("USD", 2), ("SOXL", 3)], [("SSG", -2), ("SOXS", -3)]),
    "XLF": ([("UYG", 2), ("FAS", 3)], [("SKF", -2), ("FAZ", -3)]),
    "XLE": ([("ERX", 2)], [("ERY", -2)]),
    "XLK": ([("ROM", 2), ("TECL", 3)], [("REW", -2), ("TECS", -3)]),
    "XBI": ([("LABU", 3)], [("LABD", -3)]),
    "GLD": ([("UGL", 2)], [("GLL", -2)]),
    "TLT": ([("UBT", 2), ("TMF", 3)], [("TBT", -2), ("TMV", -3)]),
    "FXI": ([("YINN", 3)], [("YANG", -3)]),
    "EEM": ([("EDC", 3)], [("EDZ", -3)]),
    # --- pojedyncze spółki USA (ETF-y jednej spółki)
    "NVDA": ([("NVDL", 2), ("NVDU", 2)], [("NVDD", -1), ("NVDS", -1.5)]),
    "TSLA": ([("TSLL", 2), ("TSLR", 2), ("TSLT", 2)], [("TSLS", -1), ("TSDD", -2), ("TSLQ", -2)]),
    "AAPL": ([("AAPU", 2), ("AAPB", 2)], [("AAPD", -1)]),
    "MSFT": ([("MSFU", 2)], [("MSFD", -1)]),
    "AMZN": ([("AMZU", 2)], [("AMZD", -1)]),
    "GOOGL": ([("GGLL", 2)], [("GGLS", -1)]),
    "META": ([("METU", 2), ("FBL", 2)], [("METD", -1)]),
    "AMD": ([("AMDL", 2)], [("AMDD", -1)]),
    "AVGO": ([("AVL", 2)], [("AVS", -1)]),
    "NFLX": ([("NFXL", 2)], [("NFXS", -1)]),
    "PLTR": ([("PLTU", 2), ("PTIR", 2)], [("PLTD", -1)]),
    "COIN": ([("CONL", 2)], [("CONI", -2)]),
    "MSTR": ([("MSTU", 2), ("MSTX", 2)], [("MSTZ", -2)]),
    "MU": ([("MUU", 2)], [("MUD", -1)]),
    "TSM": ([("TSMX", 2)], [("TSMZ", -1)]),
    "BA": ([("BOEU", 2)], [("BOED", -1)]),
    "XOM": ([("XOMX", 2)], [("XOMZ", -1)]),
    "LLY": ([("ELIL", 2)], [("ELIS", -1)]),
    "UBER": ([("UBRL", 2)], []),
    "BABA": ([("BABX", 2)], []),
    "SMCI": ([("SMCX", 2)], []),
    "HOOD": ([("HOOX", 2), ("ROBN", 2)], []),
    # --- GPW (Beta ETF, przez IBKR)
    "ETFBW20TR.WSE": ([("ETFBW20LV.WSE", 2)], [("ETFBW20ST.WSE", -1)]),
}
VERIFY_PATH = os.path.join(CACHE_DIR, "levetf_verify.json")
VERIFY_TTL = 7 * 86400


def parse_custom(items):
    """['NVDA:NVDL:NVDD', 'TSLA:TSLL*2:TSLS*-1'] -> {baza: ([(etf, lev)], [(etf, lev)])}."""
    out = {}
    for it in items or []:
        parts = [x.strip().upper() for x in str(it).split(":")]
        if len(parts) < 2 or not parts[0]:
            continue

        def one(x, default):
            if not x:
                return []
            if "*" in x:
                sym, lev = x.split("*", 1)
                try:
                    return [(sym.strip(), float(lev))]
                except ValueError:
                    return [(sym.strip(), default)]
            return [(x, default)]
        out[parts[0]] = (one(parts[1], 2.0), one(parts[2] if len(parts) > 2 else "", -1.0))
    return out


def verified():
    try:
        with open(VERIFY_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def pick(base, custom=None, check=None):
    """Para (ETF na wzrost, ETF na spadek) dla spółki: najpierw własne pary, potem wbudowana lista.
    check(etf) -> True/False/None (None = nie wiadomo, bierzemy). Zwraca {"bull": (etf, lev)|None, "bear": ...}."""
    cand = (custom or {}).get(base.upper()) or PAIRS.get(base.upper())
    if not cand:
        return {"bull": None, "bear": None}
    v = verified().get("etfs", {})

    def first(lst):
        for etf, lev in lst:
            ok = v.get(etf, {}).get("ok") if check is None else check(etf)
            if ok is not False:
                return (etf, float(lev))
        return None
    return {"bull": first(cand[0]), "bear": first(cand[1])}


def verify(alpaca_client=None, ibkr_broker=None):
    """Sprawdza u brokera, które ETF-y z listy istnieją i są w obrocie. Wynik w pamięci podręcznej na tydzień."""
    etfs = sorted({e for bulls, bears in PAIRS.values() for e, _ in bulls + bears})
    res = {}
    for e in etfs:
        if e.endswith(".WSE"):
            if not ibkr_broker:
                continue
            try:
                d = ibkr_broker.details(e)
                res[e] = {"ok": True, "name": getattr(d, "longName", "") or e, "broker": "IBKR"}
            except Exception as ex:
                res[e] = {"ok": False, "why": str(ex)[:120], "broker": "IBKR"}
            continue
        if not alpaca_client:
            continue
        try:
            a = alpaca_client.get_asset(e)
            st = str(getattr(a.status, "value", a.status)).lower()
            ok = bool(a.tradable) and st == "active"
            res[e] = {"ok": ok, "name": a.name, "fractionable": bool(getattr(a, "fractionable", False)),
                      "broker": "Alpaca", "why": None if ok else f"status {st}, tradable={a.tradable}"}
        except Exception as ex:
            res[e] = {"ok": False, "why": str(ex)[:120], "broker": "Alpaca"}
    data = {"t": time.time(), "etfs": res}
    tmp = VERIFY_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, VERIFY_PATH)
    return data


def table():
    """Lista par do zakładki Ryzyko."""
    v = verified().get("etfs", {})
    rows = []
    for base, (bulls, bears) in PAIRS.items():
        p = pick(base)
        rows.append({"base": base,
                     "bull": [{"etf": e, "lev": l, **v.get(e, {})} for e, l in bulls],
                     "bear": [{"etf": e, "lev": l, **v.get(e, {})} for e, l in bears],
                     "use_bull": p["bull"], "use_bear": p["bear"]})
    return {"rows": rows, "checked": verified().get("t")}
