"""
Sygnaly zewnetrzne - "drugie zdanie" dla bota.

  Insiderzy (SEC Form 4): zakupy i sprzedaze akcji wlasnej spolki przez zarzad i dyrektorow.
      Najmocniejszy sygnal: kilku insiderow kupuje na rynku (kod P) w krotkim czasie.
  Fundusze (SEC 13F): zmiany pozycji wybranych zarzadzajacych (domyslnie Druckenmiller,
      Tepper, Ackman) - kto dokupil / otworzyl, a kto sprzedal / zamknal.

Zasada bez zagladania w przyszlosc: dzien D widzi tylko zgloszenia ZLOZONE przed dniem D
(liczy sie data zlozenia w SEC, nie data transakcji).

Dane z SEC sa zapisywane w bazie - kolejne testy i cykle botow nie pobieraja ich ponownie.
Konto demo / dane symulowane uzywaja deterministycznych zdarzen syntetycznych.
"""

import hashlib
import json
import os
import re
import threading
import time
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd

from .config import DATA_DIR

SEC_USER_AGENT = os.environ.get("SEC_USER_AGENT", "").strip()
OPENFIGI_API_KEY = os.environ.get("OPENFIGI_API_KEY", "").strip()
FUND_MANAGERS = os.environ.get("FUND_MANAGERS", "Druckenmiller:1536411,Tepper:1656456,Ackman:1336528")

SCHEMA = """
CREATE TABLE IF NOT EXISTS insider_filings (
    accession   TEXT PRIMARY KEY,
    ticker      TEXT NOT NULL,
    filing_date TEXT NOT NULL,
    tx          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS insider_ticker ON insider_filings(ticker, filing_date);
CREATE TABLE IF NOT EXISTS insider_sync (
    ticker      TEXT PRIMARY KEY,
    cik         INTEGER,
    synced_from TEXT,
    synced_at   TEXT
);
CREATE TABLE IF NOT EXISTS fund_filings (
    accession   TEXT PRIMARY KEY,
    manager     TEXT NOT NULL,
    cik         INTEGER NOT NULL,
    filing_date TEXT NOT NULL,
    period      TEXT,
    holdings    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS fund_mgr ON fund_filings(cik, filing_date);
CREATE TABLE IF NOT EXISTS cusip_map (
    cusip   TEXT PRIMARY KEY,
    ticker  TEXT,
    name    TEXT
);
"""

MODES = ["off", "veto", "confirm"]
STALE_DAYS = 200             # raport starszy niz ok. 2 kwartaly = fundusz przestal raportowac, nie liczymy go


# Superinwestorzy do kopiowania (bot "Kopiowanie funduszy"): skoncentrowane portfele, raporty 13F co kwartal.
COPY_MANAGERS = ("Berkshire (Buffett):1067983,Pershing Square (Ackman):1336528,Duquesne (Druckenmiller):1536411,"
                 "Appaloosa (Tepper):1656456,Himalaya (Li Lu):1709323,Baupost (Klarman):1061768,"
                 "Third Point (Loeb):1040273,Lone Pine:1061165,"
                 "Viking Global:1103804,Tiger Global:1167483,Coatue:1135730,Akre:1112520")


def parse_managers(raw=None):
    raw = FUND_MANAGERS if raw is None else raw
    out = []
    for part in (raw or "").split(","):
        if ":" in part:
            name, cik = part.rsplit(":", 1)
            out.append((name.strip(), int(cik.strip())))
    return out


# ------------------------------------------------------------------ parsery dokumentow SEC
def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _val(node, path):
    """Wartosc z Form 4: <transactionShares><value>100</value></transactionShares>."""
    cur = node
    for part in path.split("/"):
        nxt = None
        for ch in cur:
            if _local(ch.tag) == part:
                nxt = ch
                break
        if nxt is None:
            return None
        cur = nxt
    for ch in cur:
        if _local(ch.tag) == "value":
            return (ch.text or "").strip()
    return (cur.text or "").strip()


def parse_form4(xml_bytes):
    """Transakcje z Form 4: [{owner, role, code, shares, price, value, date}]. Tylko akcje (nie opcje)."""
    root = ET.fromstring(xml_bytes)
    owners, roles = [], []
    for ro in root.iter():
        if _local(ro.tag) != "reportingOwner":
            continue
        name = _val(ro, "reportingOwnerId/rptOwnerName") or "?"
        rel = {}
        for ch in ro.iter():
            if _local(ch.tag) in ("isDirector", "isOfficer", "isTenPercentOwner", "officerTitle"):
                rel[_local(ch.tag)] = (ch.text or "").strip()
        role = rel.get("officerTitle") or ("Dyrektor" if rel.get("isDirector") in ("1", "true") else
                                           "Wlasciciel 10%" if rel.get("isTenPercentOwner") in ("1", "true") else "")
        owners.append(name)
        roles.append(role)
    owner = " / ".join(owners) or "?"
    role = " / ".join(r for r in roles if r)
    out = []
    for tx in root.iter():
        if _local(tx.tag) != "nonDerivativeTransaction":
            continue
        code = _val(tx, "transactionCoding/transactionCode")
        if code not in ("P", "S"):          # tylko zakupy/sprzedaze na rynku (bez opcji, darowizn, grantow)
            continue
        try:
            shares = float(_val(tx, "transactionAmounts/transactionShares") or 0)
            price = float(_val(tx, "transactionAmounts/transactionPricePerShare") or 0)
        except ValueError:
            continue
        out.append({"owner": owner, "role": role, "code": code, "shares": shares, "price": price,
                    "value": shares * price, "date": _val(tx, "transactionDate")})
    return out


def parse_13f(xml_bytes):
    """{cusip: {'name', 'shares', 'value'}} - tylko akcje (bez opcji put/call)."""
    root = ET.fromstring(xml_bytes)
    out = {}
    for el in root.iter():
        if _local(el.tag) != "infoTable":
            continue
        f = {_local(ch.tag): (ch.text or "").strip() for ch in el.iter()}
        if f.get("putCall"):
            continue
        cusip = f.get("cusip", "").upper()
        h = out.setdefault(cusip, {"name": f.get("nameOfIssuer", ""), "shares": 0.0, "value": 0.0})
        h["shares"] += float(f.get("sshPrnamt") or 0)
        h["value"] += float(f.get("value") or 0)
    return out


def diff_13f(prev, curr, threshold=0.10):
    """{cusip: +1 (nowa / zwiekszona >=10%) albo -1 (zamknieta / zmniejszona >=10%)}."""
    out = {}
    for c, h in curr.items():
        p = prev.get(c)
        if not p or p["shares"] == 0:
            out[c] = 1
        elif (h["shares"] - p["shares"]) / p["shares"] >= threshold:
            out[c] = 1
        elif (h["shares"] - p["shares"]) / p["shares"] <= -threshold:
            out[c] = -1
    for c in prev:
        if c not in curr:
            out[c] = -1
    return out


def norm_name(name):
    name = re.sub(r"[^A-Z0-9 ]", " ", (name or "").upper())
    stop = {"INC", "CORP", "CORPORATION", "CO", "LTD", "PLC", "HOLDINGS", "HLDGS", "GROUP", "CLASS", "CL",
            "COM", "THE", "NEW", "SA", "NV", "AG", "A", "B", "C", "DEL", "ADR", "SPONSORED"}
    return " ".join(w for w in name.split() if w not in stop)


# ------------------------------------------------------------------ SEC (prawdziwe dane)
class SecClient:
    def __init__(self):
        import requests
        if not SEC_USER_AGENT:
            raise ValueError("Brak SEC_USER_AGENT w .env (np. 'Jan Kowalski jan@example.com') - "
                             "SEC wymaga przedstawienia sie przy pobieraniu danych.")
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": SEC_USER_AGENT, "Accept-Encoding": "gzip, deflate"})
        self.last = 0.0
        self.lock = threading.Lock()

    def get(self, url, as_json=False):
        for attempt in range(4):
            with self.lock:                       # SEC: max 10 zapytan/s - trzymamy sie ~7/s
                wait = 0.15 - (time.time() - self.last)
                if wait > 0:
                    time.sleep(wait)
                self.last = time.time()
            r = self.s.get(url, timeout=30)
            if r.status_code == 200:
                return r.json() if as_json else r.content
            if r.status_code in (429, 503):
                time.sleep(3 * (attempt + 1))
                continue
            if r.status_code == 404:
                return None
            r.raise_for_status()
        return None

    def tickers(self):
        path = os.path.join(DATA_DIR, "sec_tickers.json")
        if os.path.exists(path) and time.time() - os.path.getmtime(path) < 7 * 86400:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        data = self.get("https://www.sec.gov/files/company_tickers.json", as_json=True) or {}
        out = {v["ticker"].upper(): {"cik": int(v["cik_str"]), "title": v["title"]} for v in data.values()}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(out, f)
        return out

    def filings(self, cik, forms, since):
        """Zgloszenia danego typu od daty 'since' (lacznie ze starszymi stronami archiwum)."""
        sub = self.get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json", as_json=True)
        if not sub:
            return []
        out = []

        def take(block):
            n = len(block.get("form", []))
            for i in range(n):
                if block["form"][i] in forms and block["filingDate"][i] >= since:
                    out.append({"accession": block["accessionNumber"][i], "form": block["form"][i],
                                "filing_date": block["filingDate"][i],
                                "period": (block.get("reportDate") or [""] * n)[i],
                                "doc": block["primaryDocument"][i]})

        take(sub.get("filings", {}).get("recent", {}))
        for extra in sub.get("filings", {}).get("files", []):
            if extra.get("filingTo", "9999") < since:
                continue
            page = self.get(f"https://data.sec.gov/submissions/{extra['name']}", as_json=True)
            if page:
                take(page)
        return out

    def doc(self, cik, accession, doc):
        doc = doc.split("/")[-1]                   # 'xslF345X05/form4.xml' -> surowy XML 'form4.xml'
        return self.get(f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace('-', '')}/{doc}")

    def infotable(self, cik, accession):
        base = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace('-', '')}/"
        idx = self.get(base + "index.json", as_json=True)
        if not idx:
            return None
        for it in idx["directory"]["item"]:
            name = it["name"]
            if name.lower().endswith(".xml") and name.lower() != "primary_doc.xml":
                content = self.get(base + name)
                if content and b"informationTable" in content:
                    return content
        return None


def openfigi_map(cusips):
    """CUSIP -> ticker przez OpenFIGI (darmowe; z kluczem szybciej). Zwraca {cusip: ticker}."""
    import requests
    out = {}
    batch = 100 if OPENFIGI_API_KEY else 10
    headers = {"Content-Type": "application/json"}
    if OPENFIGI_API_KEY:
        headers["X-OPENFIGI-APIKEY"] = OPENFIGI_API_KEY
    for i in range(0, len(cusips), batch):
        chunk = cusips[i:i + batch]
        body = [{"idType": "ID_CUSIP", "idValue": c, "exchCode": "US"} for c in chunk]
        for attempt in range(3):
            r = requests.post("https://api.openfigi.com/v3/mapping", json=body, headers=headers, timeout=30)
            if r.status_code == 429:
                time.sleep(7 if not OPENFIGI_API_KEY else 1)
                continue
            r.raise_for_status()
            for c, res in zip(chunk, r.json()):
                data = res.get("data") or []
                if data:
                    out[c] = data[0].get("ticker", "").upper().replace("/", ".")
            break
        time.sleep(2.5 if not OPENFIGI_API_KEY else 0.3)
    return out


class SecSignals:
    """Dane z SEC z cache w bazie. Wspolne dla backtestow i botow."""

    def __init__(self, db, log=lambda msg: None):
        self.db = db
        self.log = log
        with db.lock:
            db.conn.executescript(SCHEMA)
        self._sec = None
        self.lock = threading.Lock()

    @property
    def sec(self):
        if self._sec is None:
            self._sec = SecClient()
        return self._sec

    # --- insiderzy
    def sync_insiders(self, ticker, since, progress=None):
        with self.lock:
            st = self.db.one("SELECT * FROM insider_sync WHERE ticker=?", (ticker,))
            fresh = st and st["synced_at"] and \
                datetime.fromisoformat(st["synced_at"]) > datetime.now(timezone.utc) - timedelta(hours=12)
            if fresh and st["synced_from"] <= since:
                return
            tick = self.sec.tickers().get(ticker.replace(".", "-")) or self.sec.tickers().get(ticker)
            if not tick:
                self.db.execute("INSERT OR REPLACE INTO insider_sync VALUES (?,?,?,?)",
                                (ticker, None, since, datetime.now(timezone.utc).isoformat()))
                return
            cik = tick["cik"]
            have = {r["accession"] for r in self.db.all("SELECT accession FROM insider_filings WHERE ticker=?", (ticker,))}
            todo = [f for f in self.sec.filings(cik, {"4"}, since) if f["accession"] not in have]
            if todo:
                self.log(f"Insiderzy {ticker}: pobieram {len(todo)} zgloszen Form 4 z SEC...")
            for i, f in enumerate(todo):
                if progress and i % 20 == 0:
                    progress(i / len(todo))
                try:
                    content = self.sec.doc(cik, f["accession"], f["doc"])
                    tx = parse_form4(content) if content else []
                except Exception:
                    tx = []
                self.db.execute("INSERT OR REPLACE INTO insider_filings VALUES (?,?,?,?)",
                                (f["accession"], ticker, f["filing_date"], json.dumps(tx)))
            new_from = min(since, st["synced_from"]) if st and st["synced_from"] else since
            self.db.execute("INSERT OR REPLACE INTO insider_sync VALUES (?,?,?,?)",
                            (ticker, cik, new_from, datetime.now(timezone.utc).isoformat()))

    def insider_events(self, ticker, since, until):
        rows = self.db.all("SELECT filing_date, tx FROM insider_filings WHERE ticker=? AND filing_date>=? "
                           "AND filing_date<=? ORDER BY filing_date", (ticker, since, until))
        out = []
        for r in rows:
            for t in json.loads(r["tx"]):
                out.append(dict(t, filed=r["filing_date"]))
        return out

    # --- fundusze
    def sync_funds(self, since, managers=None):
        managers = managers or parse_managers()
        with self.lock:
            done = getattr(self, "_funds_synced", {})
            todo_m = [(n, c) for n, c in managers
                      if not (c in done and done[c][0] <= since and time.time() - done[c][1] < 12 * 3600)]
            if not todo_m:
                return
            for name, cik in todo_m:
                have = {r["accession"] for r in self.db.all("SELECT accession FROM fund_filings WHERE cik=?", (cik,))}
                todo = [f for f in self.sec.filings(cik, {"13F-HR"}, since) if f["accession"] not in have]
                for f in todo:
                    self.log(f"Fundusze: pobieram 13F {name} za {f['period']}...")
                    content = self.sec.infotable(cik, f["accession"])
                    holdings = parse_13f(content) if content else {}
                    self.db.execute("INSERT OR REPLACE INTO fund_filings VALUES (?,?,?,?,?,?)",
                                    (f["accession"], name, cik, f["filing_date"], f["period"], json.dumps(holdings)))
            self._map_cusips()
            for _, cik in todo_m:
                done[cik] = (since, time.time())
            self._funds_synced = done

    def _map_cusips(self):
        known = {r["cusip"] for r in self.db.all("SELECT cusip FROM cusip_map")}
        names = {}
        for r in self.db.all("SELECT holdings FROM fund_filings"):
            for c, h in json.loads(r["holdings"]).items():
                if c not in known:
                    names[c] = h["name"]
        if not names:
            return
        mapped = {}
        try:
            mapped = openfigi_map(sorted(names))
        except Exception as e:
            self.log(f"OpenFIGI niedostepne ({e}) - dopasowuje po nazwie spolki")
        by_name = {}
        try:
            for t, v in self.sec.tickers().items():
                by_name.setdefault(norm_name(v["title"]), t)
        except Exception:
            pass
        for c, name in names.items():
            ticker = mapped.get(c) or by_name.get(norm_name(name))
            self.db.execute("INSERT OR REPLACE INTO cusip_map VALUES (?,?,?)", (c, ticker, name))

    def fund_changes(self, since, until, managers=None):
        """[(data_zlozenia, zarzadzajacy, {ticker: +1/-1})] - zmiana wzgledem poprzedniego 13F."""
        cmap = {r["cusip"]: r["ticker"] for r in self.db.all("SELECT cusip, ticker FROM cusip_map")}
        out = []
        for name, cik in managers or parse_managers():
            rows = self.db.all("SELECT filing_date, period, holdings FROM fund_filings WHERE cik=? "
                               "ORDER BY period, filing_date", (cik,))
            for prev, cur in zip(rows, rows[1:]):
                if not (since <= cur["filing_date"] <= until):
                    continue
                d = diff_13f(json.loads(prev["holdings"]), json.loads(cur["holdings"]))
                ch = {}
                for c, v in d.items():
                    t = cmap.get(c)
                    if t:
                        ch[t] = v
                out.append((cur["filing_date"], name, ch, cur["period"]))
        return out


# ------------------------------------------------------------------ dane syntetyczne (demo)
class SimSignals:
    """Deterministyczne zdarzenia: ok. 3 zgloszenia insiderow miesiecznie, 13F co kwartal."""

    def sync_insiders(self, ticker, since, progress=None):
        pass

    def sync_funds(self, since, managers=None):
        pass

    def insider_events(self, ticker, since, until):
        d0, d1 = date.fromisoformat(since), date.fromisoformat(until)
        out = []
        d = d0
        while d <= d1:
            rng = np.random.default_rng(int(hashlib.md5(f"{ticker}|{d}".encode()).hexdigest()[:8], 16))
            if rng.random() < 0.1:
                buy = rng.random() < 0.25
                who = f"Insider {int(rng.integers(1, 9))}"
                shares = float(rng.integers(1, 50)) * 1000
                out.append({"owner": who, "role": "Dyrektor" if rng.random() < .5 else "CFO",
                            "code": "P" if buy else "S", "shares": shares, "price": 100.0,
                            "value": shares * 100.0, "date": d.isoformat(), "filed": d.isoformat()})
            d += timedelta(days=1)
        return out

    def fund_changes(self, since, until, managers=None):
        out = []
        d0, d1 = date.fromisoformat(since), date.fromisoformat(until)
        for y in range(d0.year, d1.year + 1):
            for m, day in ((2, 14), (5, 15), (8, 14), (11, 14)):
                fd = date(y, m, day)
                if not (d0 <= fd <= d1):
                    continue
                for name, _ in managers or parse_managers():
                    rng = np.random.default_rng(int(hashlib.md5(f"{name}|{fd}".encode()).hexdigest()[:8], 16))
                    ch = {"*": int(rng.choice([-1, 1]))}
                    if managers:                        # tryb kopiowania: kilka "spolek" z listy demo
                        ch = {t: int(rng.choice([-1, 1, 1])) for t in SIM_COPY_TICKERS if rng.random() < 0.35}
                    out.append((fd.isoformat(), name, ch, ""))
        return out


SIM_COPY_TICKERS = ["AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "JPM", "V", "UNH", "HD", "COST", "LLY"]


# ------------------------------------------------------------------ wybor zrodla
_state = {"db": None, "log": lambda m: None, "sec": None}


def set_db(db, log=lambda m: None):
    _state["db"], _state["log"] = db, log


def source_for(tag):
    """'sim' -> dane syntetyczne; inaczej SEC z cache w bazie aplikacji."""
    if tag == "sim":
        return SimSignals()
    if _state["sec"] is None:
        if _state["db"] is None:
            from .db import DB
            _state["db"] = DB()
        _state["sec"] = SecSignals(_state["db"], _state["log"])
    return _state["sec"]


EXT_KEYS = ["insider_mode", "insider_days", "insider_min_buyers", "insider_min_sellers",
            "funds_mode", "funds_min_bulls", "funds_min_bears"]


# ------------------------------------------------------------------ dzienne serie sygnalow
def insider_daily(events, days, start, end):
    """DataFrame po dniach: kupujacy, sprzedajacy, wartosc zakupow w oknie [D-days, D)."""
    idx = pd.date_range(start.date(), end.date(), freq="D")
    buyers = np.zeros(len(idx), dtype=int)
    sellers = np.zeros(len(idx), dtype=int)
    buy_value = np.zeros(len(idx))
    evs = [(date.fromisoformat(e["filed"]), e) for e in events]
    for i, d in enumerate(idx.date):
        lo = d - timedelta(days=days)
        win = [e for fd, e in evs if lo <= fd < d]
        buyers[i] = len({e["owner"] for e in win if e["code"] == "P"})
        sellers[i] = len({e["owner"] for e in win if e["code"] == "S"})
        buy_value[i] = sum(e["value"] for e in win if e["code"] == "P")
    return pd.DataFrame({"buyers": buyers, "sellers": sellers, "buy_value": buy_value}, index=idx.date)


def fund_daily(changes, symbol, start, end):
    """Po dniach: ilu zarzadzajacych w ostatnim znanym 13F dokupilo (+) / sprzedalo (-) dany symbol."""
    idx = pd.date_range(start.date(), end.date(), freq="D")
    latest = {}                                   # zarzadzajacy -> (data zlozenia, zmiana dla symbolu)
    by_date = sorted(changes, key=lambda x: x[0])
    bulls, bears = np.zeros(len(idx), dtype=int), np.zeros(len(idx), dtype=int)
    j = 0
    for i, d in enumerate(idx.date):
        while j < len(by_date) and date.fromisoformat(by_date[j][0]) < d:
            fd, mgr, ch, _ = by_date[j]
            v = ch.get(symbol, ch.get("*", 0) if "*" in ch else 0)
            latest[mgr] = (date.fromisoformat(fd), v)
            j += 1
        live = [v for fd, v in latest.values() if (d - fd).days <= STALE_DAYS]   # fundusz przestal raportowac
        bulls[i] = sum(1 for v in live if v > 0)
        bears[i] = sum(1 for v in live if v < 0)
    return pd.DataFrame({"bulls": bulls, "bears": bears}, index=idx.date)


def allowed_days(p, ins, fund):
    """Seria bool po dniach - czy sygnaly zewnetrzne pozwalaja na wejscie."""
    ok = pd.Series(True, index=(ins if ins is not None else fund).index)
    im, fm = p.get("insider_mode", "off"), p.get("funds_mode", "off")
    if ins is not None and im == "veto":
        ok &= ~((ins["sellers"] >= p["insider_min_sellers"]) & (ins["buyers"] == 0))
    elif ins is not None and im == "confirm":
        ok &= ins["buyers"] >= p["insider_min_buyers"]
    if fund is not None and fm == "veto":
        ok &= ~((fund["bears"] >= p["funds_min_bears"]) & (fund["bulls"] == 0))
    elif fund is not None and fm == "confirm":
        ok &= fund["bulls"] >= p["funds_min_bulls"]
    return ok


def active(p):
    return p.get("insider_mode", "off") != "off" or p.get("funds_mode", "off") != "off"


def build_filter(source, symbols, p, start, end, progress=None):
    """{symbol: Series[date -> bool]} dla okresu testu / dzialania bota."""
    if not active(p):
        return None
    since = (start - timedelta(days=max(p.get("insider_days", 30), 1) + 5)).date().isoformat()
    until = end.date().isoformat()
    out, stats, funds = {}, {}, {}
    fund_changes = None
    if p.get("funds_mode", "off") != "off":
        managers = managers_for(p)
        source.sync_funds((start - timedelta(days=200)).date().isoformat(), managers)
        fund_changes = source.fund_changes((start - timedelta(days=200)).date().isoformat(), until, managers)
    for k, sym in enumerate(symbols):
        ins = fund = None
        n_ins = 0
        if p.get("insider_mode", "off") != "off":
            source.sync_insiders(sym, since, progress=(lambda x, k=k: progress((k + x) / len(symbols)))
                                 if progress else None)
            events = source.insider_events(sym, since, until)
            n_ins = len(events)
            ins = insider_daily(events, p.get("insider_days", 30), start, end)
        if fund_changes is not None:
            fund = fund_daily(fund_changes, sym, start, end)
            funds[sym] = fund
        out[sym] = allowed_days(p, ins, fund)
        stats[sym] = {"insider_events": n_ins,
                      "fund_mentions": sum(1 for c in (fund_changes or []) if sym in c[2] or "*" in c[2])}
    return {"allowed": out, "stats": stats, "funds": funds}


def apply_filter(df, flt, symbol):
    """Zeruje sygnaly wejscia w dniach, w ktorych sygnaly zewnetrzne nie pozwalaja kupic."""
    if not flt or symbol not in flt["allowed"]:
        return df, 0
    allow = flt["allowed"][symbol]
    days = pd.Index(df.index.date)
    mask = allow.reindex(days).fillna(True).to_numpy(dtype=bool)
    before = int(df["entry"].sum())
    df = df.copy()
    df["entry"] = df["entry"].to_numpy() & mask
    return df, before - int(df["entry"].sum())


# ------------------------------------------------------------------ kopiowanie funduszy (strategia copy_funds)
TICKER_RE = re.compile(r"^[A-Z]{1,5}(\.[A-Z])?$")


def managers_for(p):
    """Lista zarzadzajacych: wlasna lista bota kopiujacego albo globalna z .env (FUND_MANAGERS)."""
    raw = (p.get("copy_managers") or "").strip()
    return parse_managers(raw) if raw else None


def copy_state(changes, until):
    """{ticker: (kupujacy, sprzedajacy)} wg ostatniego znanego 13F kazdego zarzadzajacego (zlozonego przed 'until')."""
    latest = {}
    oldest = (date.fromisoformat(until) - timedelta(days=STALE_DAYS)).isoformat()
    for fd, mgr, ch, _ in sorted(changes, key=lambda x: x[0]):
        if oldest <= fd < until:
            latest[mgr] = ch
    out = {}
    for ch in latest.values():
        for t, v in ch.items():
            if t == "*" or not TICKER_RE.match(t or ""):
                continue
            b, s_ = out.get(t, (0, 0))
            out[t] = (b + (v > 0), s_ + (v < 0))
    return out


def copy_picks(changes, until, min_bulls, top, exclude=()):
    """Spolki, ktore dokupilo/otworzylo najwiecej sledzonych funduszy (ostatnie raporty) - do listy bota na zywo."""
    st = copy_state(changes, until)
    ranked = sorted(((t, b) for t, (b, _) in st.items() if b >= min_bulls and t not in exclude),
                    key=lambda x: (-x[1], x[0]))
    return [t for t, _ in ranked[:top]]


def copy_pool(changes):
    """Wszystkie spolki, ktore ktorykolwiek fundusz dokupil w okresie - kandydaci do backtestu."""
    seen = {}
    for fd, mgr, ch, _ in changes:
        for t, v in ch.items():
            if v > 0 and t != "*" and TICKER_RE.match(t or ""):
                seen[t] = seen.get(t, 0) + 1
    return sorted(seen, key=lambda t: (-seen[t], t))


def apply_copy(df, flt, symbol, p):
    """Wejscie: fundusze dokupily (>= copy_min_bulls) i jest trend, a sygnal jest swiezy (raport z ostatnich
    copy_fresh_days dni) albo cena wlasnie przebila srednia. Wyjscie: fundusze sprzedaja, a nikt nie dokupuje."""
    df = df.copy()
    fund = (flt or {}).get("funds", {}).get(symbol)
    if fund is None or fund.empty:
        df["entry"] = False
        df["prio"] = 0
        return df
    fund = fund.copy()
    fund.index = pd.DatetimeIndex(fund.index)
    allow = fund["bulls"] >= int(p.get("copy_min_bulls", 1))
    flip = allow & ~allow.shift(1, fill_value=True)
    n = max(int(p.get("copy_fresh_days", 10)), 1)
    fresh = flip.astype(int).rolling(n, min_periods=1).max().astype(bool)
    sell = (fund["bears"] >= 1) & (fund["bulls"] == 0)
    days = pd.DatetimeIndex(pd.to_datetime(df.index.date))
    a = allow.reindex(days).fillna(False).to_numpy(dtype=bool)
    f = fresh.reindex(days).fillna(False).to_numpy(dtype=bool)
    cross = df["cross"].to_numpy(dtype=bool) if "cross" in df else np.zeros(len(df), dtype=bool)
    df["entry"] = df["entry"].to_numpy(dtype=bool) & a & (f | cross)
    if p.get("copy_exit_on_sell", True):
        sl = sell.reindex(days).fillna(False).to_numpy(dtype=bool)
        df["exit"] = df["exit"].to_numpy(dtype=bool) | sl
    df["prio"] = fund["bulls"].reindex(days).fillna(0).to_numpy()
    return df
