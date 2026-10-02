"""
Silnik botow: kazdy uruchomiony bot to osobny watek (BotRunner) z wlasnym cyklem.

Ochrona pozycji zawsze lezy na serwerze brokera:
  - akcje: zlecenie bracket (stop-loss + take-profit)
  - krypto: stop-limit na serwerze; take-profit pilnuje bot
Dzieki temu restart aplikacji ani uspienie komputera nie zostawiaja pozycji bez stopu.
"""

import logging
import math
import os
import threading
import time
from datetime import datetime, timedelta, timezone

from .config import ACCOUNTS, EQUITY_SNAPSHOT_MINUTES, LOG_PATH
from .data import drop_unfinished, norm
from .brokers import BrokerError, get_broker
from . import signals as ext
from . import ml
from . import context
from .rules import atr as atr_series
from . import radar
from . import notify
import numpy as np
import pandas as pd
from .strategies import STRATEGIES, TIMEFRAME_MINUTES, full_params, sized_notional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_PATH, encoding="utf-8"), logging.StreamHandler()],
)
log = logging.getLogger("tradingapp")

MAX_CONSECUTIVE_ERRORS = 10                     # (historyczne - bot juz sie nie wylacza po bledach)
MAX_RETRY_SECONDS = 300
CACHE_MAX_AGE_DAYS = 14


def fp(x):
    """Czytelna cena: 102,301.52 / 3,715.11 / 0.123456."""
    x = float(x)
    return f"{x:,.2f}" if abs(x) >= 1 else f"{x:.6f}"


def lookback_start(p, market, warmup):
    """Od kiedy pobrac swiece, zeby miec 'warmup' zamknietych swiec (z zapasem na weekendy)."""
    m = TIMEFRAME_MINUTES[p["timeframe"]]
    per_day = 1440 if market == "crypto" else 390
    if m >= 1440:
        days = warmup * (1 if market == "crypto" else 1.5) + 10
    else:
        days = math.ceil(warmup * m / per_day * 1.6) + 4
    return datetime.now(timezone.utc) - timedelta(days=days)


class BotRunner(threading.Thread):
    def __init__(self, db, bot):
        super().__init__(daemon=True, name=f"bot-{bot['id']}")
        self.db = db
        self.bot = bot
        self.id = bot["id"]
        self.market = bot["market"]
        self.p = full_params(bot["market"], bot["strategy"], bot["params"])
        self.strategy = STRATEGIES[bot["strategy"]]
        self.symbols = self.p["symbols"]
        self.by_norm = {norm(s): s for s in self.symbols}
        self.broker = get_broker(ACCOUNTS[bot["account"]])
        self.stop_event = threading.Event()
        self.regime = (True, None)
        self.last_snapshot = None
        self.last_closed_log = None
        self.last_heartbeat = None
        self.ext_filter = None
        self.ext_day = None
        self.ext_logged = {}
        self.errors = 0
        self.use_ml = ml.uses_ml(bot["strategy"], self.p)
        self.ml = ml.LiveModel(db, self.id) if self.use_ml else None
        self.ml_state = None
        self.ml_logged = {}
        acc = ACCOUNTS[bot["account"]]
        self.acc = acc
        self.cost = ml.cost_for(self.market, acc.type)
        self.warmup = max(self.strategy.warmup(self.p), ml.ML_WARMUP if self.use_ml else 0)
        self.radar_mode = self.market == "crypto" and self.p.get("universe_mode") == "radar"
        # Kraken i radar: bot zarzadza TYLKO pozycjami, ktore sam otworzyl (Twoje wlasne monety na gieldzie zostaja w spokoju)
        self.copy_mode = self.market == "stocks" and bot["strategy"] == "copy_funds"
        self.owned_only = acc.type in ("kraken", "gielda") or self.radar_mode or self.copy_mode
        self.fixed_symbols = list(self.symbols)
        self.universe_day = None
        self.taken = lambda: set()
        self.cross = None
        from .lab import Lab
        self.lab = Lab(db, self) if self.p.get("lab_enabled") else None
        self.lab_err = None

    # ---------------------------------------------------------- pomocnicze
    def log(self, msg, level="INFO"):
        self.db.log(self.id, level, msg)
        log.log(getattr(logging, level), f"[{self.bot['name']}] {msg}")

    def bars(self):
        start = lookback_start(self.p, self.market, self.warmup)
        raw = self.broker.data.bars(self.symbols, self.p["timeframe"], start)
        return {s: drop_unfinished(df, self.p["timeframe"]) for s, df in raw.items()}

    def ml_status(self, mid, why):
        state = mid or why
        if state == self.ml_state:
            return
        self.ml_state = state
        if mid:
            self.log(f"ML: używam zatwierdzonego modelu #{mid}.")
        elif ml.needs_model(self.bot["strategy"], self.p):
            self.log(f"ML: {why} - nowe wejścia wstrzymane, dopóki nie zatwierdzisz modelu (zakładka ML).", "WARNING")
        else:
            self.log(f"ML: {why} - wielkość pozycji bez skalowania pewnością modelu.", "WARNING")

    def signals(self, df, sym=None):
        if len(df) < self.warmup:
            return None
        if self.use_ml:
            prob, mid, why = self.ml.prob(df, self.p, self.broker.data, self.cross)
            self.ml_status(mid, why)
            probs = pd.Series(np.nan, index=df.index)
            if prob is not None:
                probs.iloc[-1] = prob
            row = ml.signal_frame(self.strategy, df, self.p, probs, self.cost).iloc[-1].copy()
            if row["ml_blocked"] and prob is not None and self.ml_logged.get(sym) != df.index[-1]:
                self.ml_logged[sym] = df.index[-1]
                self.log(f"{sym}: sygnał kupna odrzucony przez model ML (pewność {prob:.0%} < próg "
                         f"{ml.threshold(self.p, self.cost):.0%})")
        elif self.copy_mode:
            d = self.strategy.compute(df, self.p)
            if self.ext_filter:
                row = ext.apply_copy(d, self.ext_filter, sym, self.p).iloc[-1].copy()
            else:
                row = d.iloc[-1].copy()
                row["entry"] = False
            row["ml_prob"] = np.nan
        else:
            row = self.strategy.compute(df, self.p).iloc[-1].copy()
            row["ml_prob"] = np.nan
        if row["entry"] and sym and not self.external_ok(sym):
            row["entry"] = False
            if self.ext_logged.get(sym) != df.index[-1]:
                self.ext_logged[sym] = df.index[-1]
                self.log(f"{sym}: sygnal kupna zablokowany przez filtr zewnetrzny ({self.ext_reason(sym)})")
        return row

    def refresh_external(self):
        """Raz dziennie: sygnaly insiderow / funduszy dla symboli bota (dane z SEC, cache w bazie)."""
        if self.market != "stocks" or not ext.active(self.p):
            self.ext_filter = None
            return
        today = datetime.now(timezone.utc).date()
        if self.ext_day == today:
            return
        now = datetime.now(timezone.utc)
        tag = "sim" if ACCOUNTS[self.bot["account"]].type == "sim" else "live"
        try:
            back = int(self.p.get("copy_fresh_days", 0)) + 5 if self.copy_mode else 3
            self.ext_filter = ext.build_filter(ext.source_for(tag), self.symbols, self.p,
                                               now - timedelta(days=back), now)
            self.ext_day = today
            blocked = [s for s in self.symbols if not self.external_ok(s)]
            self.log("Sygnaly zewnetrzne odswiezone. "
                     + (f"Dzis zablokowane wejscia: {', '.join(blocked)}." if blocked else "Brak blokad na dzis."))
            self.ext_failed = False
        except Exception as e:
            self.ext_filter = None
            self.ext_day = today
            self.ext_failed = True
            confirm = "confirm" in (self.p.get("insider_mode"), self.p.get("funds_mode"))
            self.log(f"Sygnaly zewnetrzne niedostepne ({e}) - " + (
                "tryb potwierdzenia: dzis bez nowych wejsc." if confirm else "weto nieaktywne do jutra."), "WARNING")

    def external_ok(self, sym):
        if getattr(self, "ext_failed", False):
            return "confirm" not in (self.p.get("insider_mode"), self.p.get("funds_mode"))
        if not self.ext_filter or sym not in self.ext_filter["allowed"]:
            return True
        s = self.ext_filter["allowed"][sym]
        return bool(s.iloc[-1]) if len(s) else True

    def ext_reason(self, sym):
        parts = []
        if self.p.get("insider_mode") != "off":
            parts.append(f"insiderzy: {self.p['insider_mode']}")
        if self.p.get("funds_mode") != "off":
            parts.append(f"fundusze: {self.p['funds_mode']}")
        return ", ".join(parts)

    def update_regime(self):
        if not context.parts(self.p):
            self.regime = (True, None)
            return
        today = datetime.now(timezone.utc).date()
        if self.regime[1] == today:
            return

        def daily_for(syms, n):
            raw = self.broker.data.bars(list(syms), "1Day", datetime.now(timezone.utc) - timedelta(days=n * 1.6 + 10))
            return {s: drop_unfinished(df, "1Day") for s, df in raw.items()}

        try:
            ok, desc = context.describe_now(self.p, daily_for)
        except Exception as e:
            self.log(f"Rezim: blad pobierania danych ({e}) - filtr pominiety", "WARNING")
            self.regime = (True, today)
            return
        self.regime = (ok, today)
        self.log(f"Rezim: {desc} -> "
                 f"{'OK, wejscia dozwolone' if ok else 'SLABY, brak nowych wejsc'}")

    def close(self, symbol, reason, pos=None):
        """Anuluje zlecenia ochronne i zamyka pozycje po rynku; zapisuje P/L."""
        for o in self.broker.open_orders(symbol):
            try:
                self.broker.cancel(o["id"])
            except Exception as e:
                self.log(f"{symbol}: nie udalo sie anulowac zlecenia ({e})", "WARNING")
        time.sleep(0.5)
        st = self.db.pos_states(self.id).get(norm(symbol))
        res = self.broker.close_position(symbol)
        if res:
            entry = st["entry"] if st else (pos or {}).get("avg_entry", res["price"])
            pnl = (res["price"] - entry) * res["qty"]
            pct = (res["price"] / entry - 1) * 100 if entry else None
            self.db.add_trade(self.id, symbol, "SELL", res["qty"], res["price"], reason, pnl, pct)
            self.log(f"SPRZEDAZ {symbol} x{res['qty']:g} ~{fp(res['price'])} ({reason}) | "
                     f"P/L {pnl:+,.2f} USD ({pct:+.2f}%)")
        self.db.del_pos_state(self.id, norm(symbol))

    def reconcile(self, positions):
        """Pozycja zniknela, a bot jej nie zamykal -> zamknal ja stop/TP na serwerze."""
        for s, st in self.db.pos_states(self.id).items():
            if s in positions:
                continue
            sym = self.by_norm.get(s, s)
            try:                                       # zlecenie kupna jeszcze czeka (np. rynek zamkniety) - to nie stop
                if any(o["side"] == "buy" for o in self.broker.open_orders(sym)):
                    continue
            except Exception:
                continue
            price_now = None
            try:
                price_now = self.broker.data.bars([sym], "1Min", datetime.now(timezone.utc)
                                                  - timedelta(days=3)).get(sym)["close"].iloc[-1]
            except Exception:
                pass
            hit_tp = st["tp"] and price_now and price_now >= st["tp"] * 0.995
            price = st["tp"] if hit_tp else (st["sl"] or st["entry"])
            reason = "take-profit (serwer)" if hit_tp else "stop-loss (serwer)"
            pnl = (price - st["entry"]) * st["qty"]
            self.db.add_trade(self.id, sym, "SELL", st["qty"], price, reason + ", cena szacunkowa",
                              pnl, (price / st["entry"] - 1) * 100)
            self.db.del_pos_state(self.id, s)
            self.log(f"{sym}: pozycja zamknieta przez {reason} ~{fp(price)} | P/L {pnl:+,.2f} USD")

    def snapshot(self, mine):
        now = datetime.now(timezone.utc)
        if self.last_snapshot and now - self.last_snapshot < timedelta(minutes=EQUITY_SNAPSHOT_MINUTES):
            return
        realized, _, _ = self.db.realized_pnl(self.id)
        unreal = sum(p["unrealized"] for p in mine.values())
        exposure = sum(p["market_value"] for p in mine.values())
        self.db.add_equity("bot", self.id, equity=realized + unreal, exposure=exposure,
                           realized=realized, unrealized=unreal)
        self.last_snapshot = now

    def mine(self, positions):
        if self.owned_only:
            owned = self.db.pos_states(self.id)
            return {s: p for s, p in positions.items() if s in owned}
        return {s: p for s, p in positions.items() if s in self.by_norm}

    def refresh_copy_universe(self, positions):
        """Kopiowanie funduszy: raz dziennie lista spolek z ostatnich raportow 13F (+ stale symbole i trzymane)."""
        today = datetime.now(timezone.utc).date()
        if self.universe_day == today and self.symbols:
            return
        owned = self.db.pos_states(self.id)
        held = [p["symbol"] for s, p in positions.items() if s in owned]
        tag = "sim" if self.acc.type == "sim" else "live"
        try:
            src = ext.source_for(tag)
            managers = ext.managers_for(self.p)
            since = (today - timedelta(days=400)).isoformat()
            src.sync_funds(since, managers)
            changes = src.fund_changes(since, (today + timedelta(days=1)).isoformat(), managers)
            picks = ext.copy_picks(changes, (today + timedelta(days=1)).isoformat(),
                                   int(self.p.get("copy_min_bulls", 1)), int(self.p.get("copy_top", 20)),
                                   exclude=self.taken())
        except Exception as e:
            self.universe_day = today
            if not self.symbols:
                self.symbols = list(dict.fromkeys(self.fixed_symbols + held))
                self.p["symbols"] = self.symbols
            self.log(f"Kopiowanie: raporty funduszy niedostępne ({e}) - dziś bez nowych spółek.", "WARNING")
            return
        new = list(dict.fromkeys(picks + self.fixed_symbols + held))
        if set(new) != set(self.symbols):
            added = [x for x in new if x not in self.symbols]
            gone = [x for x in self.symbols if x not in new]
            self.log("Kopiowanie: spółki z raportów funduszy: " + (", ".join(new) or "brak")
                     + (f" | nowe: {', '.join(added)}" if added and self.symbols else "")
                     + (f" | wypadły: {', '.join(gone)}" if gone else ""))
        self.symbols = new
        self.p["symbols"] = new
        for x in new:
            self.by_norm[norm(x)] = x
        self.universe_day = today
        self.ext_day = None                      # sygnaly funduszy od nowa dla nowej listy

    def refresh_universe(self, positions):
        """Tryb radaru: raz dziennie nowa lista monet (najlepsze z rankingu + te, ktore bot juz trzyma)."""
        if self.copy_mode:
            return self.refresh_copy_universe(positions)
        if not self.radar_mode:
            return
        today = datetime.now(timezone.utc).date()
        if self.universe_day == today and self.symbols:
            return
        pname = radar.provider_name(self.acc)
        quote = self.acc.extra.get("quote", "USD")
        res = radar.ensure(self.db, self.broker.data, pname, quote, log=self.log)
        picks = radar.pick(res, int(self.p.get("radar_top", 5)), exclude=self.taken(),
                           min_volume=float(self.p.get("radar_min_volume") or 0))
        owned = self.db.pos_states(self.id)
        held = [p["symbol"] for s, p in positions.items() if s in owned]
        new = list(dict.fromkeys(self.fixed_symbols + picks + held))
        if set(new) != set(self.symbols):
            added = [x for x in new if x not in self.symbols]
            gone = [x for x in self.symbols if x not in new]
            self.log("Radar: monety na dziś: " + (", ".join(new) or "brak")
                     + (f" | nowe: {', '.join(added)}" if added and self.symbols else "")
                     + (f" | wypadły: {', '.join(gone)}" if gone else ""))
        self.symbols = new
        self.p["symbols"] = new
        for x in new:
            self.by_norm[norm(x)] = x
        self.universe_day = today

    # ---------------------------------------------------------- cykl
    def cycle(self):
        acct = self.broker.account()
        positions = self.broker.positions()
        self.refresh_universe(positions)
        mine = self.mine(positions)
        self.reconcile(positions)
        budget = acct["equity"] * self.p["allocation_pct"]

        if self.market == "crypto":
            self.crypto_cycle(acct, positions, mine, budget)
        else:
            self.stock_cycle(acct, positions, mine, budget)

        mine = self.mine(self.broker.positions())
        self.snapshot(mine)
        now = datetime.now(timezone.utc)
        if not self.last_heartbeat or now - self.last_heartbeat >= timedelta(minutes=30):
            unreal = sum(p["unrealized"] for p in mine.values())
            cur = acct.get("currency") or self.acc.extra.get("quote", "USD")
            self.log(f"Cykl OK | pozycje: {', '.join(p['symbol'] for p in mine.values()) or 'brak'} | "
                     f"otwarte P/L {unreal:+,.2f} {cur} | kapital konta {acct['equity']:,.2f} {cur}")
            self.last_heartbeat = now

    def entries(self, bars, positions, mine, budget, buying_power, execute):
        if not self.regime[0]:
            return
        busy = set(positions) | {norm(o["symbol"]) for o in self.broker.open_orders()}
        slots = self.p["max_positions"] - len(mine)
        used = sum(p["market_value"] for p in mine.values())
        for sym in self.symbols:
            if slots <= 0:
                break
            if norm(sym) in busy or sym not in bars:
                continue
            sig = self.signals(bars[sym], sym)
            if sig is None or not sig["entry"] or sig["exit"]:
                continue                        # wejscie i wyjscie na tej samej swiecy = brak wejscia (bez "pily")
            available = min(buying_power, budget - used)
            notional = sized_notional(budget, self.p, available)
            prob = sig.get("ml_prob")
            prob = None if prob is None or pd.isna(prob) else float(prob)
            scale = ml.size_scale(prob, self.p, self.cost) if self.use_ml else 1.0
            notional *= scale
            price = float(sig["close"])
            try:
                spent = execute(sym, price, notional)
                if spent and prob is not None:
                    self.log(f"{sym}: pewność modelu ML {prob:.0%} (próg {ml.threshold(self.p, self.cost):.0%})"
                             + (f", pozycja {scale:.0%} standardowej" if self.p.get("ml_sizing") else ""))
                if spent:
                    slots -= 1
                    used += spent
                    buying_power -= spent
            except Exception as e:
                self.log(f"{sym}: zlecenie odrzucone ({e})", "WARNING")

    def position_exits(self, mine, bars):
        """Stop kroczacy, stop na wejsciu i limit czasu - pilnuje ich bot (twardy SL zostaje na serwerze).
        Zwraca symbole zamkniete w tym cyklu."""
        trail = self.p.get("trail_atr_mult") or 0
        be = self.p.get("breakeven_after_pct") or 0
        hold = self.p.get("max_hold_days") or 0
        if not (trail or be or hold):
            return set()
        closed = set()
        states = self.db.pos_states(self.id)
        now = datetime.now(timezone.utc)
        for s, pos in mine.items():
            st = states.get(s)
            if not st:
                continue
            sym = self.by_norm[s]
            price = float(pos["price"])
            peak = max(st.get("peak") or st["entry"], price)
            if peak > (st.get("peak") or 0):
                self.db.set_peak(self.id, s, peak)
            reason = None
            if trail and sym in bars and len(bars[sym]) > 15:
                a = float(atr_series(bars[sym], 14).iloc[-1])
                level = peak - trail * a
                if a == a and level > (st["sl"] or 0) and price <= level:
                    reason = f"stop kroczący ({fp(level)})"
            if not reason and be and peak >= st["entry"] * (1 + be) and price <= st["entry"] * (1 + 2 * self.cost):
                reason = "stop na wejściu"
            if not reason and hold and now - datetime.fromisoformat(st["opened_at"]) >= timedelta(days=hold):
                reason = f"limit czasu ({hold} dni)"
            if reason:
                self.close(sym, reason, pos)
                closed.add(s)
        return closed

    # --- akcje
    def stock_cycle(self, acct, positions, mine, budget):
        clock = self.broker.clock(self.symbols) if getattr(self.broker, "per_symbol_clock", False) else self.broker.clock()
        if not clock["is_open"]:
            now = datetime.now(timezone.utc)
            if not self.last_closed_log or now - self.last_closed_log > timedelta(minutes=60):
                mins = (clock["next_open"] - clock["now"]).total_seconds() / 60
                self.log(f"Rynek akcji zamkniety. Otwarcie za ok. {mins / 60:.1f} h.")
                self.last_closed_log = now
            return
        to_close = (clock["next_close"] - clock["now"]).total_seconds() / 60
        flatten = self.p.get("flatten_before_close_min", 0)
        if flatten and to_close <= flatten:
            for s, pos in mine.items():
                self.close(self.by_norm[s], "koniec sesji", pos)
            return

        self.update_regime()
        self.refresh_external()
        bars = self.bars()
        self.cross = ml.cross_section(bars) if self.use_ml else None
        done = self.guard_fractional(mine)
        done |= set(self.position_exits({s: p for s, p in mine.items() if s not in done}, bars) or ())
        for s, pos in mine.items():
            if s in done:
                continue
            sym = self.by_norm[s]
            sig = self.signals(bars[sym], sym) if sym in bars else None
            if sig is not None and sig["exit"]:
                self.close(sym, "sygnal wyjscia", pos)

        positions = self.broker.positions()
        mine = self.mine(positions)
        if clock.get("session_min", 390) - to_close < self.p.get("no_entry_after_open_min", 0):
            return
        overnight = not flatten

        def execute(sym, price, notional):
            fx = self.broker.fx_for(sym) if hasattr(self.broker, "fx_for") else 1.0   # cena w walucie konta
            if self.p.get("fractional_shares") and getattr(self.broker, "supports_fractional", False):
                return execute_fractional(sym, price, notional, fx)
            qty = int(notional // (price * fx))
            if qty < 1:
                hint = (" Włącz „Ułamki akcji”, żeby kupić część akcji za tę kwotę."
                        if getattr(self.broker, "supports_fractional", False) else "")
                self.log(f"{sym}: sygnal kupna, ale budzet ({notional:,.2f}) nie wystarcza na 1 akcje "
                         f"({price * fx:,.2f}).{hint}")
                return 0
            sl = price * (1 - self.p["stop_loss_pct"])
            tp = price * (1 + self.p["take_profit_pct"])
            self.broker.buy_bracket(sym, qty, sl, tp, overnight)
            self.db.set_pos_state(self.id, norm(sym), price, qty, sl, tp)
            self.db.add_trade(self.id, sym, "BUY", qty, price, "sygnal wejscia")
            cur = acct.get("currency", "USD")
            self.log(f"KUPNO {sym} x{qty} ~{price:,.4g} | SL {sl:,.4g} | TP {tp:,.4g} | ~{qty * price * fx:,.0f} {cur}")
            return qty * price * fx

        def execute_fractional(sym, price, notional, fx):
            """Zakup za kwote (ulamek akcji). Stop-loss i take-profit pilnuje bot (patrz guard_fractional)."""
            fill, qty = self.broker.buy_fractional(sym, notional / fx)
            sl = fill * (1 - self.p["stop_loss_pct"])
            tp = fill * (1 + self.p["take_profit_pct"])
            self.db.set_pos_state(self.id, norm(sym), fill, qty, sl, tp)
            self.db.add_trade(self.id, sym, "BUY", qty, fill, "sygnal wejscia (za kwotę)")
            cur = acct.get("currency", "USD")
            self.log(f"KUPNO {sym} x{qty:.6g} za ~{qty * fill * fx:,.2f} {cur} (ulamek akcji) ~{fill:,.4g} | "
                     f"SL {sl:,.4g} | TP {tp:,.4g} (pilnuje bot)")
            return qty * fill * fx

        self.entries(bars, positions, mine, budget, acct["buying_power"], execute)
        self.lab_step(bars)

    def guard_fractional(self, mine):
        """Pozycje akcji bez zlecen ochronnych na serwerze (ulamki) - stop-loss i take-profit sprawdza bot.
        Zwraca symbole (norm) zamkniete w tym cyklu."""
        done = set()
        states = self.db.pos_states(self.id)
        for s, pos in mine.items():
            st = states.get(s)
            if not st or abs(pos["qty"] - round(pos["qty"])) < 1e-9:
                continue                                     # cale akcje maja SL/TP na serwerze (bracket)
            sym = self.by_norm.get(s, pos["symbol"])
            if st["sl"] and pos["price"] <= st["sl"]:
                self.close(sym, "stop-loss (pilnowany przez bota)", pos)
                done.add(s)
            elif st["tp"] and pos["price"] >= st["tp"]:
                self.close(sym, "take-profit (pilnowany przez bota)", pos)
                done.add(s)
        return done

    # --- krypto
    def protect(self, positions, mine):
        """Kazda pozycja krypto bota musi miec stop-limit na serwerze."""
        states = self.db.pos_states(self.id)
        for s, pos in mine.items():
            sym = self.by_norm[s]
            has_stop = any(o["side"] == "sell" for o in self.broker.open_orders(sym))
            st = states.get(s)
            entry = st["entry"] if st else pos["avg_entry"]
            sl = entry * (1 - self.p["stop_loss_pct"])
            tp = entry * (1 + self.p["take_profit_pct"])
            if not has_stop and sym not in getattr(self.broker, "soft_stops", ()):
                qty = pos["qty_available"] or pos["qty"]
                try:
                    how = self.broker.sell_stop_limit(sym, qty, sl, sl * (1 - self.p["stop_limit_slippage_pct"]))
                    if how == "soft":
                        self.log(f"{sym}: giełda nie przyjęła stop-limitu - stop {fp(sl)} pilnuje bot co cykl", "WARNING")
                    else:
                        self.log(f"{sym}: postawiono stop-limit ochronny {fp(sl)}")
                except Exception as e:
                    self.log(f"{sym}: NIE udalo sie postawic stopu ({e})", "ERROR")
            if not st:
                self.db.set_pos_state(self.id, s, entry, pos["qty"], sl, tp)

    def crypto_cycle(self, acct, positions, mine, budget):
        self.protect(positions, mine)
        self.update_regime()
        bars = self.bars()
        self.cross = ml.cross_section(bars) if self.use_ml else None
        done = self.position_exits(mine, bars)
        states = self.db.pos_states(self.id)
        for s, pos in mine.items():
            if s in done:
                continue
            sym = self.by_norm[s]
            st = states.get(s)
            if st and st["sl"] and pos["price"] <= st["sl"] and sym in getattr(self.broker, "soft_stops", ()):
                self.close(sym, "stop-loss (pilnowany przez bota)", pos)
                continue
            if st and st["tp"] and pos["price"] >= st["tp"]:
                self.close(sym, "take-profit", pos)
                continue
            sig = self.signals(bars[sym], sym) if sym in bars else None
            if sig is not None and sig["exit"]:
                self.close(sym, "sygnal wyjscia", pos)

        positions = self.broker.positions()
        mine = self.mine(positions)

        def execute(sym, price, notional):
            if notional < 10:
                return 0
            fill, qty = self.broker.buy_notional(sym, notional)
            sl = fill * (1 - self.p["stop_loss_pct"])
            tp = fill * (1 + self.p["take_profit_pct"])
            self.db.set_pos_state(self.id, norm(sym), fill, qty, sl, tp)
            self.db.add_trade(self.id, sym, "BUY", qty, fill, "sygnal wejscia")
            try:
                how = self.broker.sell_stop_limit(sym, qty, sl, sl * (1 - self.p["stop_limit_slippage_pct"]))
            except Exception as e:
                how = "error"
                self.log(f"{sym}: NIE udalo sie postawic stopu ({e}) - ponowie w nastepnym cyklu", "ERROR")
            where = {"soft": "bot", "error": "BRAK"}.get(how, "serwer")
            self.log(f"KUPNO {sym} x{qty:.6g} ~{fp(fill)} | SL {fp(sl)} ({where}) | TP {fp(tp)} (bot) | "
                     f"~{qty * fill:,.0f} {getattr(self.broker, 'quote', 'USD')}")
            return qty * fill

        self.entries(bars, positions, mine, budget, acct["crypto_buying_power"], execute)
        self.lab_step(bars)

    def lab_step(self, bars):
        if not self.lab:
            return
        try:
            self.lab.step(bars, self.cross)
            self.lab_err = None
        except Exception as e:
            if str(e) != self.lab_err:
                self.lab_err = str(e)
                self.log(f"Laboratorium: błąd ({e}) - handel działa normalnie", "WARNING")
                log.exception("laboratorium")

    # ---------------------------------------------------------- petla
    def run(self):
        self.log(f"Start | konto {self.bot['account']} | {self.strategy.name} | {self.p['timeframe']} | "
                 f"{', '.join(self.symbols)} | budzet {self.p['allocation_pct']:.0%}")
        while not self.stop_event.is_set():
            try:
                self.cycle()
                if self.errors >= 3:
                    self.log(f"Połączenie wróciło po {self.errors} nieudanych cyklach - bot pracuje normalnie.")
                    self.db.update_bot(self.id, last_error=None)
                    notify.send(f"✅ {self.bot['name']}: znowu działa (po {self.errors} nieudanych próbach).",
                                "errors", key=f"err-{self.id}")
                self.errors = 0
            except Exception as e:
                # Bez trwalego zatrzymania: przerwa w internecie albo awaria API brokera nie wylacza bota.
                # Kolejne proby coraz rzadziej (do 15 min); pozycje i tak maja stopy u brokera.
                self.errors += 1
                wait = min(self.p["poll_seconds"] * 2 ** min(self.errors - 1, 6), MAX_RETRY_SECONDS)
                if self.errors <= 3 or self.errors % 10 == 0:
                    self.log(f"Blad cyklu (#{self.errors}): {e} - ponowię za {wait:.0f} s", "ERROR")
                    log.exception("szczegoly bledu")
                if self.errors == 3:
                    self.db.update_bot(self.id, last_error=str(e)[:300])
                    notify.send(f"⚠️ {self.bot['name']}: 3 nieudane cykle z rzędu ({str(e)[:150]}). "
                                f"Bot ponawia próby co kilka minut, pozycje mają stopy u brokera.",
                                "errors", key=f"err-{self.id}")
                self.stop_event.wait(wait)
                continue
            self.stop_event.wait(self.p["poll_seconds"])
        self.log("Zatrzymany.")


class Manager:
    def __init__(self, db):
        self.db = db
        self.runners = {}
        self.lock = threading.Lock()
        self.monitor_stop = threading.Event()

    def conflicts(self, bot):
        """Dwa dzialajace boty na tym samym koncie nie moga handlowac tym samym symbolem."""
        mine = {norm(s) for s in full_params(bot["market"], bot["strategy"], bot["params"])["symbols"]}
        out = []
        for other in self.db.bots():
            if other["id"] == bot["id"] or other["account"] != bot["account"] or other["id"] not in self.runners:
                continue
            theirs = {norm(s) for s in other["params"].get("symbols", [])}
            common = mine & theirs
            if common:
                out.append(f"{other['name']}: {', '.join(sorted(common))}")
        return out

    def budget_total(self, bot):
        total = 0.0
        for other in self.db.bots():
            if other["account"] == bot["account"] and (other["id"] in self.runners or other["id"] == bot["id"]):
                total += full_params(other["market"], other["strategy"], other["params"])["allocation_pct"]
        return total

    def start(self, bot_id, resume=False):
        with self.lock:
            bot = self.db.bot(bot_id)
            if not bot:
                raise ValueError("Nie ma takiego bota")
            if bot_id in self.runners and self.runners[bot_id].is_alive():
                return
            if bot["account"] not in ACCOUNTS:
                raise ValueError(f"Konto '{bot['account']}' nie istnieje w .env")
            clash = self.conflicts(bot)
            if clash:
                raise ValueError("Te symbole juz handluje inny bot na tym koncie - " + "; ".join(clash))
            if self.budget_total(bot) > 1.0001:
                raise ValueError("Suma budzetow dzialajacych botow na tym koncie przekroczylaby 100%.")
            try:
                runner = BotRunner(self.db, bot)
            except BrokerError as e:
                raise ValueError(str(e))
            runner.taken = lambda bid=bot_id, acc=bot["account"]: {
                x for rid, r in list(self.runners.items()) if rid != bid and r.bot["account"] == acc for x in r.symbols}
            self.runners[bot_id] = runner
            fields = {"status": "running", "last_error": None}
            if not resume or not bot.get("started_at"):
                fields["started_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            self.db.update_bot(bot_id, **fields)
            runner.start()

    def stop(self, bot_id, close_positions=False, report=True):
        with self.lock:
            runner = self.runners.pop(bot_id, None)
        if runner:
            runner.stop_event.set()
            runner.join(timeout=15)
            if close_positions:
                positions = runner.broker.positions()
                for s, pos in runner.mine(positions).items():
                    runner.close(runner.by_norm.get(s, pos["symbol"]), "zatrzymanie bota", pos)
        self.db.update_bot(bot_id, status="stopped")
        if runner and report:
            bot = self.db.bot(bot_id)
            started = bot.get("started_at")
            start = datetime.fromisoformat(started) if started else datetime.now(timezone.utc) - timedelta(days=1)
            threading.Thread(target=self.generate_report, args=(bot_id, "stop", start,
                             datetime.now(timezone.utc)), daemon=True).start()

    def status(self, bot_id):
        r = self.runners.get(bot_id)
        if r and not r.is_alive():
            self.runners.pop(bot_id, None)
            r = None
        return "running" if r else self.db.bot(bot_id)["status"]

    def resume(self):
        """Po restarcie aplikacji wznawiamy boty, ktore byly uruchomione."""
        for bot in self.db.bots():
            if bot["status"] == "running":
                try:
                    self.start(bot["id"], resume=True)
                except Exception as e:
                    self.db.update_bot(bot["id"], status="error", last_error=str(e))
                    log.error(f"Nie udalo sie wznowic bota {bot['name']}: {e}")

    def account_monitor(self):
        """Migawki equity wszystkich kont co EQUITY_SNAPSHOT_MINUTES. Blad konta logujemy raz, nie co 15 min."""
        acc_err = {}
        last_prune = 0
        while not self.monitor_stop.is_set():
            for acc in ACCOUNTS.values():
                try:
                    a = get_broker(acc).account()
                    pos = get_broker(acc).positions()
                    self.db.add_equity("account", acc.name, equity=a["equity"], cash=a["cash"],
                                       exposure=sum(p["market_value"] for p in pos.values()))
                    if acc_err.pop(acc.name, None):
                        log.info(f"Konto {acc.name}: połączenie przywrócone")
                except Exception as e:
                    if acc_err.get(acc.name) != str(e):
                        log.warning(f"Konto {acc.name}: brak migawki ({e}) - kolejne takie same błędy pomijam w logu")
                        acc_err[acc.name] = str(e)
            self.db.prune_logs()
            if time.time() - last_prune > 86400:
                self.prune_cache()
                last_prune = time.time()
            self.monitor_stop.wait(EQUITY_SNAPSHOT_MINUTES * 60)

    def prune_cache(self):
        """Pamiec podreczna swiec do backtestow/ML: pliki starsze niz 14 dni (historia Krakena zostaje)."""
        from .config import CACHE_DIR
        cut = time.time() - CACHE_MAX_AGE_DAYS * 86400
        n = size = 0
        for name in os.listdir(CACHE_DIR):
            p = os.path.join(CACHE_DIR, name)
            if name.endswith(".pkl") and os.path.isfile(p) and os.path.getmtime(p) < cut:
                size += os.path.getsize(p)
                try:
                    os.remove(p)
                    n += 1
                except OSError:
                    pass
        if n:
            log.info(f"Pamięć podręczna: usunięto {n} starych plików ({size / 1e6:.1f} MB)")

    def start_monitor(self):
        self.db.execute("UPDATE ml_models SET status='error', error='przerwane (restart aplikacji)' "
                        "WHERE status='training'")
        threading.Thread(target=self.account_monitor, daemon=True, name="account-monitor").start()
        threading.Thread(target=self.report_loop, daemon=True, name="report-scheduler").start()
        threading.Thread(target=self.ml_loop, daemon=True, name="ml-trainer").start()
        threading.Thread(target=self.lab_loop, daemon=True, name="lab").start()

    # ---------------------------------------------------------- laboratorium
    def lab_tick(self, bot, now, local):
        from . import lab as L
        p = full_params(bot["market"], bot["strategy"], bot["params"])
        if not p.get("lab_enabled") or bot["id"] not in self.runners:
            return
        # 1) nocny model dla pretendenta (od 2:00 PL, raz na dobe)
        if ml.uses_ml(bot["strategy"], p) and local.hour >= 2:
            last = self.db.one("SELECT created_at FROM ml_models WHERE bot_id=? AND kind='lab' "
                               "AND status IN ('lab','training','active','retired') ORDER BY id DESC LIMIT 1", (bot["id"],))
            if not last or datetime.fromisoformat(last["created_at"]).astimezone(local.tzinfo).date() < local.date():
                mid = self.train_model(bot["id"], "lab")
                m = self.db.one("SELECT status FROM ml_models WHERE id=?", (mid,)) if mid else None
                if m and m["status"] == "lab":
                    self.db.execute("UPDATE lab_variants SET model_id=? WHERE bot_id=? AND status='active' "
                                    "AND kind='challenger_model'", (mid, bot["id"]))
        # 2) czy pretendent pokonal mistrza
        winner, champ, _ = L.judge(self.db, bot, p)
        if winner:
            acc = ACCOUNTS[bot["account"]]
            if acc.paper or acc.type == "sim":
                L.apply_winner(self.db, self, bot, winner, auto=True)
            else:
                L.propose(self.db, bot, winner)

    def lab_loop(self):
        from zoneinfo import ZoneInfo
        pl = ZoneInfo("Europe/Warsaw")
        self.monitor_stop.wait(120)
        while not self.monitor_stop.is_set():
            now = datetime.now(timezone.utc)
            for bot in self.db.bots():
                try:
                    self.lab_tick(bot, now, now.astimezone(pl))
                except Exception:
                    log.exception(f"Laboratorium bota {bot['name']}")
            self.monitor_stop.wait(1800)

    # ---------------------------------------------------------- uczenie maszynowe
    ML_KIND = {"first": "pierwszy model", "weekly": "cotygodniowe douczanie", "manual": "ręcznie",
               "retry": "ponowna próba", "lab": "nocny model do laboratorium"}

    def ml_provider(self, bot, acc):
        """Dane do nauki: konto bota; dla Krakena z uzupelnieniem historii z Alpaki (gdy jest w .env)."""
        from .config import data_account
        data = get_broker(acc).data
        if acc.type in ("kraken", "gielda"):
            from .config import data_tag
            from .data import AlpacaData
            from .kraken import HistoryWithFallback
            da = data_account()
            return HistoryWithFallback(data, AlpacaData(da.key, da.secret, da.paper) if da else None), data_tag(acc) + "_ml"
        return data, {"sim": "sim", "ibkr": "ibkr"}.get(acc.type, "alpaca")

    def ml_bot(self, bot, acc):
        """Bot w trybie radaru uczy sie na 15 najlepszych monetach z ostatniego skanu (+ stale symbole)."""
        p = full_params(bot["market"], bot["strategy"], bot["params"])
        if bot["market"] == "crypto" and p.get("universe_mode") == "radar":
            pname = radar.provider_name(acc)
            res = radar.ensure(self.db, get_broker(acc).data, pname, acc.extra.get("quote", "USD"))
            syms = list(dict.fromkeys(p["symbols"] + radar.pick(res, 15)))
            p = dict(p, symbols=syms, universe_mode="fixed")
        return dict(bot, params=p)

    def train_model(self, bot_id, kind="manual"):
        """Uczy nowy model dla bota i zapisuje go jako kandydata (albo odrzucony). Jeden trening naraz."""
        bot = self.db.bot(bot_id)
        if not bot:
            return None
        p = full_params(bot["market"], bot["strategy"], bot["params"])
        mid = self.db.add_model(bot_id, bot["name"], kind, ml.signature(p))
        self.db.log(bot_id, "INFO", f"ML: uczenie modelu #{mid} ({self.ML_KIND.get(kind, kind)}) - "
                                    "test w czasie i trening mogą potrwać kilka minut.")
        with ml.training_lock():
            try:
                acc = ACCOUNTS[bot["account"]]
                provider, tag = self.ml_provider(bot, acc)
                status, path, summary = ml.train_for_bot(
                    self.ml_bot(bot, acc), provider, tag, acc_type=acc.type,
                    progress=lambda x: self.db.update_model(mid, progress=round(x, 3)))
                if getattr(provider, "used_fallback", None):
                    summary["train"]["fallback"] = sorted(provider.used_fallback)
                if kind == "lab":
                    status = "lab" if path else "error"
                    self.db.update_model(mid, status=status, path=path, summary=summary, progress=1,
                                         error=None if path else "brak modelu")
                    self.db.log(bot_id, "INFO", f"Laboratorium: nocny model #{mid} gotowy - gra na niby jako pretendent."
                                if path else f"Laboratorium: nocny model #{mid} nie powstał (za mało danych).")
                    self.prune_models(bot_id)
                    return mid
                if status == "candidate":
                    self.db.execute("UPDATE ml_models SET status='superseded' WHERE bot_id=? AND status='candidate' "
                                    "AND id<>?", (bot_id, mid))
                self.db.update_model(mid, status=status, path=path, summary=summary, progress=1)
                if status == "candidate":
                    self.db.log(bot_id, "INFO", f"ML: model #{mid} przeszedł test w czasie - czeka na Twoją "
                                                "akceptację w zakładce ML.")
                    notify.send(f"🧠 {bot['name']}: nowy model ML #{mid} przeszedł test w czasie - czeka na "
                                "akceptację w zakładce ML.", "lab")
                else:
                    self.db.log(bot_id, "WARNING", f"ML: model #{mid} nie przeszedł testu w czasie ("
                                + "; ".join(summary["reasons"]) + "). Nie zostanie użyty bez Twojej decyzji.")
            except Exception as e:
                log.exception("Uczenie modelu ML nieudane")
                self.db.update_model(mid, status="error", error=str(e), progress=1)
                self.db.log(bot_id, "WARNING", f"ML: uczenie modelu #{mid} nieudane: {e}")
        self.prune_models(bot_id)
        return mid

    def prune_models(self, bot_id, keep=10):
        rows = self.db.all("SELECT id, path FROM ml_models WHERE bot_id=? AND status NOT IN ('active','candidate') "
                           "AND path IS NOT NULL ORDER BY id DESC", (bot_id,))
        for r in rows[keep:]:
            try:
                if os.path.exists(r["path"]):
                    os.remove(r["path"])
            except OSError:
                pass
            self.db.update_model(r["id"], path=None)

    def ml_due(self, bot, now, local):
        p = full_params(bot["market"], bot["strategy"], bot["params"])
        if not ml.uses_ml(bot["strategy"], p):
            return None
        rows = self.db.all("SELECT id, status, created_at, signature FROM ml_models WHERE bot_id=? "
                           "ORDER BY id DESC LIMIT 30", (bot["id"],))
        if any(r["status"] == "training" for r in rows):
            return None
        same = [r for r in rows if r["signature"] == ml.signature(p)]
        if not same:
            return "first"
        age = now - datetime.fromisoformat(same[0]["created_at"])
        if same[0]["status"] == "error" and age > timedelta(hours=12):
            return "retry"
        running = bot["id"] in self.runners
        if p.get("lab_enabled"):
            return None                         # laboratorium douczy model co noc samo
        if running and local.weekday() == 6 and local.hour >= 7 and age > timedelta(days=6):
            return "weekly"
        return None

    def ml_loop(self):
        """Pierwszy model dla nowego bota ML i douczanie w niedziele (od 7:00 PL) dla dzialajacych botow."""
        from zoneinfo import ZoneInfo
        pl = ZoneInfo("Europe/Warsaw")
        self.monitor_stop.wait(60)
        while not self.monitor_stop.is_set():
            try:
                now = datetime.now(timezone.utc)
                for bot in self.db.bots():
                    kind = self.ml_due(bot, now, now.astimezone(pl))
                    if kind and not self.monitor_stop.is_set():
                        self.train_model(bot["id"], kind)
            except Exception:
                log.exception("Harmonogram ML")
            self.monitor_stop.wait(600)

    # ---------------------------------------------------------- raporty z dzialania
    def generate_report(self, bot_id, kind, start, end):
        from .botreport import build
        bot = self.db.bot(bot_id)
        if not bot:
            return None
        broker = None
        try:
            broker = get_broker(ACCOUNTS[bot["account"]])
        except Exception as e:
            log.warning(f"Raport {bot['name']}: brak polaczenia z kontem ({e}) - raport bez pozycji i rynku")
        try:
            summary, data = build(self.db, bot, kind, start, end, broker)
            rid = self.db.add_report(bot_id, bot["name"], kind, start.isoformat(timespec="seconds"),
                                     end.isoformat(timespec="seconds"), summary, data)
            self.db.log(bot_id, "INFO", f"Wygenerowano raport ({data['kind_label'].lower()}) #{rid}: "
                                        f"wynik {summary['pnl']:+,.2f} USD, {summary['closed_trades']} transakcji.")
            if kind in ("daily", "weekly", "stop"):
                notify.send(f"📊 {bot['name']} - {data['kind_label'].lower()}: wynik {summary['pnl']:+,.2f}, "
                            f"{summary['closed_trades']} zamkniętych transakcji.", "reports")
            return rid
        except Exception as e:
            log.exception(f"Raport {bot['name']} nieudany")
            self.db.log(bot_id, "WARNING", f"Nie udalo sie wygenerowac raportu: {e}")
            return None

    def _was_active(self, bot_id, start):
        s = start.isoformat(timespec="seconds")
        if bot_id in self.runners:
            return True
        return bool(self.db.one("SELECT 1 FROM trades WHERE bot_id=? AND ts>=? LIMIT 1", (bot_id, s)) or
                    self.db.one("SELECT 1 FROM equity WHERE scope='bot' AND ref=? AND ts>=? LIMIT 1", (str(bot_id), s)))

    def report_loop(self):
        """Raport dzienny po sesji (22:30 PL) i tygodniowy (sobota od 8:00) dla aktywnych botow."""
        from zoneinfo import ZoneInfo
        pl = ZoneInfo("Europe/Warsaw")
        while not self.monitor_stop.is_set():
            try:
                now = datetime.now(timezone.utc)
                local = now.astimezone(pl)
                for bot in self.db.bots():
                    bid = bot["id"]
                    if local.hour * 60 + local.minute >= 22 * 60 + 30:
                        last = self.db.last_report(bid, "daily")
                        done_today = last and datetime.fromisoformat(last["created_at"]).astimezone(pl).date() == local.date()
                        if not done_today:
                            start = max(datetime.fromisoformat(last["period_to"]), now - timedelta(days=3)) \
                                if last else now - timedelta(days=1)
                            if self._was_active(bid, start):
                                self.generate_report(bid, "daily", start, now)
                    if local.weekday() == 5 and local.hour >= 8:
                        last = self.db.last_report(bid, "weekly")
                        if not last or now - datetime.fromisoformat(last["created_at"]) > timedelta(days=6):
                            start = now - timedelta(days=7)
                            if self._was_active(bid, start):
                                self.generate_report(bid, "weekly", start, now)
            except Exception:
                log.exception("Harmonogram raportow")
            self.monitor_stop.wait(300)

    def shutdown(self):
        self.monitor_stop.set()
        for bot_id in list(self.runners):
            runner = self.runners.pop(bot_id)
            runner.stop_event.set()
            runner.join(timeout=10)
        # status 'running' zostaje w bazie, wiec po starcie boty wroca same
