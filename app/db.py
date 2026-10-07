"""Warstwa SQLite. Jedno polaczenie wspoldzielone przez watki, chronione blokada."""

import json
import sqlite3
import threading
from datetime import datetime, timezone

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS bots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    account     TEXT NOT NULL,
    market      TEXT NOT NULL CHECK (market IN ('stocks','crypto')),
    strategy    TEXT NOT NULL,
    params      TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'stopped',
    last_error  TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS trades (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    bot_id    INTEGER NOT NULL,
    ts        TEXT NOT NULL,
    symbol    TEXT NOT NULL,
    side      TEXT NOT NULL,
    qty       REAL NOT NULL,
    price     REAL NOT NULL,
    value     REAL NOT NULL,
    pnl       REAL,
    pnl_pct   REAL,
    reason    TEXT
);
CREATE INDEX IF NOT EXISTS trades_bot ON trades(bot_id, ts);
CREATE TABLE IF NOT EXISTS positions_state (
    bot_id    INTEGER NOT NULL,
    symbol    TEXT NOT NULL,
    entry     REAL NOT NULL,
    qty       REAL NOT NULL,
    sl        REAL,
    tp        REAL,
    opened_at TEXT NOT NULL,
    PRIMARY KEY (bot_id, symbol)
);
CREATE TABLE IF NOT EXISTS equity (
    ts          TEXT NOT NULL,
    scope       TEXT NOT NULL,
    ref         TEXT NOT NULL,
    equity      REAL,
    cash        REAL,
    exposure    REAL,
    realized    REAL,
    unrealized  REAL
);
CREATE INDEX IF NOT EXISTS equity_ref ON equity(scope, ref, ts);
CREATE TABLE IF NOT EXISTS logs (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    bot_id  INTEGER,
    ts      TEXT NOT NULL,
    level   TEXT NOT NULL,
    msg     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS logs_bot ON logs(bot_id, id);
CREATE TABLE IF NOT EXISTS bot_reports (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    bot_id      INTEGER NOT NULL,
    bot_name    TEXT,
    kind        TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    period_from TEXT NOT NULL,
    period_to   TEXT NOT NULL,
    summary     TEXT,
    data        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS bot_reports_bot ON bot_reports(bot_id, created_at);
CREATE TABLE IF NOT EXISTS ml_models (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    bot_id      INTEGER NOT NULL,
    bot_name    TEXT,
    created_at  TEXT NOT NULL,
    status      TEXT NOT NULL,
    kind        TEXT,
    signature   TEXT,
    path        TEXT,
    progress    REAL DEFAULT 0,
    summary     TEXT,
    error       TEXT,
    decided_at  TEXT
);
CREATE INDEX IF NOT EXISTS ml_models_bot ON ml_models(bot_id, id);
CREATE TABLE IF NOT EXISTS radar_scans (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, provider TEXT NOT NULL, quote TEXT NOT NULL, data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS radar_seen (
    provider TEXT NOT NULL, symbol TEXT NOT NULL, first_seen TEXT NOT NULL, baseline INTEGER DEFAULT 0,
    PRIMARY KEY (provider, symbol)
);
CREATE TABLE IF NOT EXISTS lab_variants (
    id INTEGER PRIMARY KEY AUTOINCREMENT, bot_id INTEGER NOT NULL, key TEXT NOT NULL, label TEXT NOT NULL,
    kind TEXT NOT NULL, changes TEXT, model_id INTEGER, created_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active',
    state TEXT
);
CREATE INDEX IF NOT EXISTS lab_variants_bot ON lab_variants(bot_id, status);
CREATE TABLE IF NOT EXISTS lab_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT, variant_id INTEGER NOT NULL, bot_id INTEGER NOT NULL, symbol TEXT NOT NULL,
    t_in TEXT NOT NULL, t_out TEXT NOT NULL, entry REAL, exit REAL, pnl_pct REAL, weight REAL, reason TEXT
);
CREATE INDEX IF NOT EXISTS lab_trades_var ON lab_trades(variant_id, t_out);
CREATE TABLE IF NOT EXISTS lab_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, bot_id INTEGER NOT NULL, ts TEXT NOT NULL, kind TEXT NOT NULL,
    status TEXT NOT NULL, text TEXT NOT NULL, data TEXT
);
CREATE TABLE IF NOT EXISTS backtests (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  TEXT NOT NULL,
    status      TEXT NOT NULL,
    progress    REAL DEFAULT 0,
    config      TEXT NOT NULL,
    result      TEXT,
    error       TEXT
);
CREATE TABLE IF NOT EXISTS bot_state (
    bot_id    INTEGER NOT NULL,
    symbol    TEXT NOT NULL,
    data      TEXT NOT NULL,
    PRIMARY KEY (bot_id, symbol)
);
CREATE TABLE IF NOT EXISTS manual_orders (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT NOT NULL,
    account   TEXT NOT NULL,
    symbol    TEXT NOT NULL,
    side      TEXT NOT NULL,
    qty       REAL NOT NULL,
    price     REAL,
    value     REAL,
    status    TEXT NOT NULL,
    note      TEXT
);
CREATE TABLE IF NOT EXISTS tv_alerts (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    bot_id    INTEGER NOT NULL,
    ts        TEXT NOT NULL,
    symbol    TEXT NOT NULL,
    action    TEXT NOT NULL,
    price     REAL,
    raw       TEXT,
    status    TEXT NOT NULL DEFAULT 'new',
    note      TEXT
);
CREATE INDEX IF NOT EXISTS tv_alerts_bot ON tv_alerts(bot_id, id);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class DB:
    def __init__(self, path=DB_PATH):
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.executescript(SCHEMA)
            bcols = {r[1] for r in self.conn.execute("PRAGMA table_info(bots)")}
            if "started_at" not in bcols:
                self.conn.execute("ALTER TABLE bots ADD COLUMN started_at TEXT")
            pcols = {r[1] for r in self.conn.execute("PRAGMA table_info(positions_state)")}
            if "peak" not in pcols:
                self.conn.execute("ALTER TABLE positions_state ADD COLUMN peak REAL")
            if "side" not in pcols:                     # 'long' / 'short' (gra na spadek)
                self.conn.execute("ALTER TABLE positions_state ADD COLUMN side TEXT DEFAULT 'long'")
            if "meta" not in pcols:                     # np. spolka bazowa dla ETF-u lewarowanego
                self.conn.execute("ALTER TABLE positions_state ADD COLUMN meta TEXT")
            cols = {r[1] for r in self.conn.execute("PRAGMA table_info(backtests)")}
            for col, typ in (("opt_status", "TEXT"), ("opt_progress", "REAL"), ("opt_result", "TEXT"),
                             ("opt_error", "TEXT")):
                if col not in cols:                     # migracja starszej bazy
                    self.conn.execute(f"ALTER TABLE backtests ADD COLUMN {col} {typ}")

    def execute(self, sql, args=()):
        with self.lock:
            cur = self.conn.execute(sql, args)
            return cur.lastrowid

    def all(self, sql, args=()):
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def one(self, sql, args=()):
        with self.lock:
            r = self.conn.execute(sql, args).fetchone()
            return dict(r) if r else None

    # ------------------------------------------------------------ boty
    def bots(self):
        rows = self.all("SELECT * FROM bots ORDER BY id")
        for r in rows:
            r["params"] = json.loads(r["params"])
        return rows

    def bot(self, bot_id):
        r = self.one("SELECT * FROM bots WHERE id=?", (bot_id,))
        if r:
            r["params"] = json.loads(r["params"])
        return r

    def create_bot(self, name, account, market, strategy, params):
        ts = now_iso()
        return self.execute(
            "INSERT INTO bots(name, account, market, strategy, params, status, created_at, updated_at) "
            "VALUES (?,?,?,?,?,'stopped',?,?)",
            (name, account, market, strategy, json.dumps(params), ts, ts))

    def update_bot(self, bot_id, **fields):
        if "params" in fields:
            fields["params"] = json.dumps(fields["params"])
        fields["updated_at"] = now_iso()
        cols = ", ".join(f"{k}=?" for k in fields)
        self.execute(f"UPDATE bots SET {cols} WHERE id=?", (*fields.values(), bot_id))

    def delete_bot(self, bot_id):
        for table in ("bots", "trades", "positions_state", "logs", "ml_models", "bot_state", "tv_alerts"):
            col = "id" if table == "bots" else "bot_id"
            self.execute(f"DELETE FROM {table} WHERE {col}=?", (bot_id,))
        self.execute("DELETE FROM equity WHERE scope='bot' AND ref=?", (str(bot_id),))

    # ------------------------------------------------------------ transakcje i stan
    def add_trade(self, bot_id, symbol, side, qty, price, reason, pnl=None, pnl_pct=None):
        self.execute(
            "INSERT INTO trades(bot_id, ts, symbol, side, qty, price, value, pnl, pnl_pct, reason) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (bot_id, now_iso(), symbol, side, qty, price, qty * price, pnl, pnl_pct, reason))
        if self.on_trade:
            try:
                self.on_trade(bot_id, symbol, side, qty, price, reason, pnl, pnl_pct)
            except Exception:
                pass

    on_trade = None                             # powiadomienia (ustawiane w main.py)

    def trades(self, bot_id=None, limit=500):
        if bot_id is None:
            return self.all("SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,))
        return self.all("SELECT * FROM trades WHERE bot_id=? ORDER BY id DESC LIMIT ?", (bot_id, limit))

    def realized_pnl(self, bot_id):
        r = self.one("SELECT COALESCE(SUM(pnl),0) AS s, COUNT(pnl) AS n, "
                     "SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END) AS wins "
                     "FROM trades WHERE bot_id=? AND pnl IS NOT NULL", (bot_id,))
        return r["s"], r["n"], r["wins"] or 0

    def pos_states(self, bot_id):
        return {r["symbol"]: r for r in self.all("SELECT * FROM positions_state WHERE bot_id=?", (bot_id,))}

    def set_pos_state(self, bot_id, symbol, entry, qty, sl, tp, side="long", meta=None):
        self.execute(
            "INSERT OR REPLACE INTO positions_state(bot_id, symbol, entry, qty, sl, tp, side, meta, opened_at) "
            "VALUES (?,?,?,?,?,?,?,?,COALESCE((SELECT opened_at FROM positions_state "
            "WHERE bot_id=? AND symbol=?), ?))",
            (bot_id, symbol, entry, qty, sl, tp, side, meta, bot_id, symbol, now_iso()))

    def set_peak(self, bot_id, symbol, peak):
        self.execute("UPDATE positions_state SET peak=? WHERE bot_id=? AND symbol=?", (peak, bot_id, symbol))

    # ------------------------------------------------------------ stan botow specjalnych (siatka, DCA)
    def get_state(self, bot_id, symbol):
        r = self.one("SELECT data FROM bot_state WHERE bot_id=? AND symbol=?", (bot_id, symbol))
        return json.loads(r["data"]) if r else None

    def set_state(self, bot_id, symbol, data):
        self.execute("INSERT OR REPLACE INTO bot_state(bot_id, symbol, data) VALUES (?,?,?)",
                     (bot_id, symbol, json.dumps(data)))

    def del_state(self, bot_id, symbol=None):
        if symbol is None:
            self.execute("DELETE FROM bot_state WHERE bot_id=?", (bot_id,))
        else:
            self.execute("DELETE FROM bot_state WHERE bot_id=? AND symbol=?", (bot_id, symbol))

    def del_pos_state(self, bot_id, symbol):
        self.execute("DELETE FROM positions_state WHERE bot_id=? AND symbol=?", (bot_id, symbol))

    # ------------------------------------------------------------ equity i logi
    def add_equity(self, scope, ref, equity=None, cash=None, exposure=None, realized=None, unrealized=None):
        self.execute("INSERT INTO equity VALUES (?,?,?,?,?,?,?,?)",
                     (now_iso(), scope, str(ref), equity, cash, exposure, realized, unrealized))

    def equity_series(self, scope, ref, since=None):
        if since:
            return self.all("SELECT * FROM equity WHERE scope=? AND ref=? AND ts>=? ORDER BY ts",
                            (scope, str(ref), since))
        return self.all("SELECT * FROM equity WHERE scope=? AND ref=? ORDER BY ts", (scope, str(ref)))

    def log(self, bot_id, level, msg):
        self.execute("INSERT INTO logs(bot_id, ts, level, msg) VALUES (?,?,?,?)",
                     (bot_id, now_iso(), level, msg))

    def logs(self, bot_id=None, after_id=0, limit=300):
        if bot_id is None:
            rows = self.all("SELECT * FROM logs WHERE id>? ORDER BY id DESC LIMIT ?", (after_id, limit))
        else:
            rows = self.all("SELECT * FROM logs WHERE bot_id=? AND id>? ORDER BY id DESC LIMIT ?",
                            (bot_id, after_id, limit))
        return list(reversed(rows))

    def prune_logs(self, keep=20000):
        self.execute("DELETE FROM logs WHERE id < (SELECT COALESCE(MAX(id),0) - ? FROM logs)", (keep,))

    # ------------------------------------------------------------ backtesty
    def create_backtest(self, config):
        return self.execute("INSERT INTO backtests(created_at, status, config) VALUES (?,?,?)",
                            (now_iso(), "queued", json.dumps(config)))

    def update_backtest(self, bt_id, **fields):
        for k in ("result", "opt_result"):
            if k in fields and fields[k] is not None and not isinstance(fields[k], str):
                fields[k] = json.dumps(fields[k])
        cols = ", ".join(f"{k}=?" for k in fields)
        self.execute(f"UPDATE backtests SET {cols} WHERE id=?", (*fields.values(), bt_id))

    def backtests(self, limit=50):
        rows = self.all("SELECT id, created_at, status, progress, config, error, opt_status, "
                        "json_extract(result, '$.metrics') AS metrics FROM backtests "
                        "ORDER BY id DESC LIMIT ?", (limit,))
        for r in rows:
            r["config"] = json.loads(r["config"])
            r["metrics"] = json.loads(r["metrics"]) if r["metrics"] else None
        return rows

    def backtest(self, bt_id):
        r = self.one("SELECT * FROM backtests WHERE id=?", (bt_id,))
        if r:
            r["config"] = json.loads(r["config"])
            r["result"] = json.loads(r["result"]) if r["result"] else None
            r["opt_result"] = json.loads(r["opt_result"]) if r["opt_result"] else None
        return r

    # ------------------------------------------------------------ raporty z dzialania botow
    def add_report(self, bot_id, bot_name, kind, period_from, period_to, summary, data):
        return self.execute(
            "INSERT INTO bot_reports(bot_id, bot_name, kind, created_at, period_from, period_to, summary, data) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (bot_id, bot_name, kind, now_iso(), period_from, period_to, json.dumps(summary), json.dumps(data)))

    def reports(self, bot_id=None, limit=200):
        sql = "SELECT id, bot_id, bot_name, kind, created_at, period_from, period_to, summary FROM bot_reports"
        rows = self.all(sql + (" WHERE bot_id=?" if bot_id else "") + " ORDER BY id DESC LIMIT ?",
                        ((bot_id, limit) if bot_id else (limit,)))
        for r in rows:
            r["summary"] = json.loads(r["summary"]) if r["summary"] else {}
        return rows

    def report(self, report_id):
        r = self.one("SELECT * FROM bot_reports WHERE id=?", (report_id,))
        if r:
            r["summary"] = json.loads(r["summary"]) if r["summary"] else {}
            r["data"] = json.loads(r["data"])
        return r

    def last_report(self, bot_id, kind):
        return self.one("SELECT id, created_at, period_to FROM bot_reports WHERE bot_id=? AND kind=? "
                        "ORDER BY id DESC LIMIT 1", (bot_id, kind))

    def trades_between(self, bot_id, since, until):
        return self.all("SELECT * FROM trades WHERE bot_id=? AND ts>=? AND ts<=? ORDER BY id", (bot_id, since, until))

    def logs_between(self, bot_id, since, until):
        return self.all("SELECT * FROM logs WHERE bot_id=? AND ts>=? AND ts<=? ORDER BY id", (bot_id, since, until))

    # ------------------------------------------------------------ modele ML
    def add_model(self, bot_id, bot_name, kind, signature):
        return self.execute("INSERT INTO ml_models(bot_id, bot_name, created_at, status, kind, signature, progress) "
                            "VALUES (?,?,?,?,?,?,0)", (bot_id, bot_name, now_iso(), "training", kind, signature))

    def update_model(self, model_id, **fields):
        if "summary" in fields and not isinstance(fields["summary"], (str, type(None))):
            fields["summary"] = json.dumps(fields["summary"])
        cols = ", ".join(f"{k}=?" for k in fields)
        self.execute(f"UPDATE ml_models SET {cols} WHERE id=?", (*fields.values(), model_id))

    def models(self, bot_id=None, limit=200):
        sql = ("SELECT id, bot_id, bot_name, created_at, status, kind, signature, progress, error, decided_at, "
               "summary FROM ml_models")
        rows = self.all(sql + (" WHERE bot_id=?" if bot_id else "") + " ORDER BY id DESC LIMIT ?",
                        ((bot_id, limit) if bot_id else (limit,)))
        for r in rows:
            r["summary"] = json.loads(r["summary"]) if r["summary"] else None
        return rows

    def model(self, model_id):
        r = self.one("SELECT * FROM ml_models WHERE id=?", (model_id,))
        if r:
            r["summary"] = json.loads(r["summary"]) if r["summary"] else None
        return r
