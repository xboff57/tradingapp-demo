"""
Nauka dźwigni na historii (zwykle w nocy) — przygotowanie dla laboratorium.

Dla każdego wybranego bota (konto papierowe / symulator, strategia z sygnałami, bez ML i kopiowania funduszy):
  1. backtest obecnych ustawień i każdego wariantu dźwigni możliwego na tym koncie (ETF 2×, margin, gra na spadki,
     a dla bota z dźwignią — bez niej / niższa / tylko na wzrost), na prawdziwych danych z kosztami i odsetkami;
  2. okres dzielony na część „do nauki” (pierwsze 65%) i „sprawdzian” (ostatnie 35%, dane, których wybór nie widział);
  3. wariant przechodzi, gdy w sprawdzianie: obecne ustawienia same zarabiają, wariant zarabia więcej (> 1 pp),
     stosunek zysku do obsunięcia (Calmar) najwyżej o 10% gorszy, obsunięcie nie większe niż 30%, a w części do nauki
     nie jest wyraźnie gorszy. Warianty zmniejszające dźwignię (bot, który już jej używa) przechodzą, gdy wyraźnie
     poprawiają stosunek zysku do obsunięcia;
  4. najlepsze (maks. 2) trafiają do laboratorium bota jako pretendenci. Tam grają na żywo „na niby” i przejmują
     bota dopiero, gdy wygrają także na nowych danych (konto papierowe: automatycznie, prawdziwe: po Twojej zgodzie).

Nic tu nie handluje i nie zmienia ustawień bota poza włączeniem laboratorium („tylko dźwignię”, gdy było wyłączone).
"""

import json
import os
import threading
import time
from datetime import date, datetime, timedelta, timezone

from . import lab as L
from .config import ACCOUNTS, DATA_DIR
from .strategies import full_params, validate

PATH = os.path.join(DATA_DIR, "levstudy.json")
PLAN = os.path.join(DATA_DIR, "levstudy_plan.json")          # harmonogram: {"next_at", "weekly", "bot_ids"}
NIGHT_HOUR = 1                                               # 01:00 czasu polskiego: rynki zamknięte, NAS wolny
_lock = threading.Lock()
_state = {"thread": None}

ELIGIBLE = {"rules", "sma_cross", "mean_reversion", "breakout", "rsi_reversion", "donchian"}


def load():
    try:
        return json.load(open(PATH, encoding="utf-8"))
    except (OSError, ValueError):
        return {"status": "idle", "bots": []}


def save(d):
    with _lock:
        tmp = PATH + ".tmp"
        json.dump(d, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, default=str)
        os.replace(tmp, PATH)


def eligible(bot):
    acc = ACCOUNTS.get(bot["account"])
    if not acc:
        return False, "konto nie istnieje"
    p = full_params(bot["market"], bot["strategy"], bot["params"])
    if bot["strategy"] not in ELIGIBLE:
        return False, "strategia bez sygnałów do odwrócenia (ML, kopiowanie, siatka, DCA, alerty)"
    if p.get("ml_filter") or p.get("ml_sizing"):
        return False, "bot używa ML — dźwignia nie łączy się z ML"
    if not L.lev_variants(bot["strategy"], bot["market"], p, acc):
        return False, ("to konto nie pozwala na dźwignię ani grę na spadki (np. krypto na Alpace)"
                       if p.get("leverage_mode", "off") == "off" else "brak wariantów")
    return True, ""


def plan(bot):
    """Okres i źródło danych testu dla bota."""
    acc = ACCOUNTS[bot["account"]]
    p = full_params(bot["market"], bot["strategy"], bot["params"])
    end = date.today() - timedelta(days=1)
    tf = p["timeframe"]
    start = {"1Day": date(2021, 1, 4), "4Hour": date(2023, 1, 2), "1Hour": date(2023, 1, 2),
             "30Min": end - timedelta(days=700), "15Min": end - timedelta(days=700)}.get(tf, end - timedelta(days=200))
    src, params = "auto", dict(p)
    if acc.type == "ibkr":
        src = "ibkr"
    elif acc.type == "sim":
        src = "sim"
    elif acc.type in ("kraken", "gielda"):
        # długą historię krypto bierzemy z Alpaki (pary w USD) — Kraken daje tylko ~720 świec
        src = "auto"
        fx = lambda s: s.split("/")[0] + "/USD" if "/" in s else s
        params["symbols"] = [fx(s) for s in p["symbols"]]
        if params.get("regime_symbol"):
            params["regime_symbol"] = fx(params["regime_symbol"])
    return {"start": start.isoformat(), "end": end.isoformat(), "source": src, "params": params}


def segment(eq, a, b):
    """Zwrot, maks. obsunięcie i Calmar dla fragmentu krzywej kapitału [(iso, wartość)]."""
    pts = [v for t, v in eq if a <= t[:10] <= b]
    if len(pts) < 5:
        return None
    peak, dd = pts[0], 0.0
    for v in pts:
        peak = max(peak, v)
        dd = min(dd, v / peak - 1)
    ret = (pts[-1] / pts[0] - 1) * 100
    return {"ret": round(ret, 2), "dd": round(dd * 100, 2), "calmar": round(ret / max(abs(dd * 100), 1.0), 3)}


def judge(base, v):
    """Czy wariant dźwigni zasługuje na test na żywo? Zwraca listę powodów odrzucenia (pusta = tak).
    Sprawdzian (ostatnie 35%) według tych samych reguł co laboratorium (lab.lev_verdict, limit obsunięcia 30%),
    a w części do nauki wariant nie może być wyraźnie gorszy (inaczej wynik sprawdzianu to przypadek)."""
    bo, vo, bi, vi = base["oos"], v["oos"], base["is"], v["is"]
    if not (bo and vo and bi and vi):
        return ["za mało danych"]
    if not base.get("trades") or base["trades"] < 10:
        return ["za mało transakcji obecnych ustawień (< 10), żeby wyciągać wnioski"]
    why = [w.replace("nie zarabia", "w sprawdzianie nie zarabia").replace("stosunek", "w sprawdzianie stosunek")
           if not w.startswith("obecne") else w.replace("obecne ustawienia", "obecne ustawienia w sprawdzianie")
           for w in L.lev_verdict(v["key"], bo["ret"], bo["dd"], vo["ret"], vo["dd"], 30)]
    if v["key"] not in L.DELEVER and vi["calmar"] < bi["calmar"] - max(abs(bi["calmar"]) * 0.25, 0.1):
        why.append("w części do nauki wyraźnie gorszy")
    return why


def seed(db, manager, b, acc, base, winners, losers, rec):
    """Najlepsze warianty -> pretendenci w laboratorium; te, które przestały przechodzić -> wycofane.
    Bot restartuje się najwyżej raz (laboratorium czyta warianty przy starcie)."""
    changed = False
    fresh = db.bot(b["id"])
    bp = full_params(fresh["market"], fresh["strategy"], fresh["params"])
    if winners and not bp.get("lab_enabled"):
        bp.update(lab_enabled=True, lab_scope="leverage")
        db.update_bot(b["id"], params=bp)
        db.log(b["id"], "INFO", "Nauka dźwigni: włączono laboratorium (tylko warianty dźwigni sprawdzone na historii).")
        fresh = db.bot(b["id"])
        changed = True
    for row in winners:
        note = (f"Nauka dźwigni: „{row['label']}” przeszedł test na historii (sprawdzian {row['oos']['ret']:+.1f}% / "
                f"obsunięcie {row['oos']['dd']:.1f}% vs obecne {base['oos']['ret']:+.1f}% / {base['oos']['dd']:.1f}%). "
                "Gra teraz na żywo, na niby.")
        if L.add_variant(db, fresh, acc, row["key"], row["label"] + " ✓ historia", row["changes"], note=note):
            changed = True
            db.log(b["id"], "INFO", f"Nauka dźwigni: pretendent „{row['label']}” dodany do laboratorium.")
            rec["seeded"].append(row["label"])
        else:
            rec.setdefault("kept", []).append(row["label"])
    for row in losers:
        if L.retire_variant(db, b["id"], row["key"], f"Nauka dźwigni: „{row['label']}” nie przechodzi już testu na "
                                                     f"historii ({', '.join(row.get('why') or [])}) — wycofany."):
            changed = True
            rec.setdefault("retired", []).append(row["label"])
    if changed and manager.status(b["id"]) == "running":
        manager.stop(b["id"], report=False)
        manager.start(b["id"], resume=True)


def run(db, manager, provider_for, run_backtest, bot_ids=None, notify=None):
    from .backtest import period_error
    bots = [b for b in db.bots() if (not bot_ids or b["id"] in bot_ids)]
    out = {"status": "running", "started": datetime.now(timezone.utc).isoformat(timespec="seconds"), "bots": [],
           "progress": 0.0}
    save(out)
    todo = []
    for b in bots:
        ok, why = eligible(b)
        if not ok:
            if bot_ids:
                out["bots"].append({"id": b["id"], "name": b["name"], "skipped": why})
            continue
        todo.append(b)
    total = sum(1 + len(L.lev_variants(b["strategy"], b["market"],
                                        full_params(b["market"], b["strategy"], b["params"]), ACCOUNTS[b["account"]]))
                for b in todo) or 1
    done = 0
    for b in todo:
        acc = ACCOUNTS[b["account"]]
        pl = plan(b)
        p = pl["params"]
        rec = {"id": b["id"], "name": b["name"], "account": b["account"], "period": [pl["start"], pl["end"]],
               "source": pl["source"], "variants": [], "seeded": []}
        out["bots"].append(rec)
        s, e = date.fromisoformat(pl["start"]), date.fromisoformat(pl["end"])
        split = (s + (e - s) * 0.65).isoformat()
        rec["split"] = split
        err = period_error(p["timeframe"], datetime.combine(s, datetime.min.time()), datetime.combine(e, datetime.min.time()))
        if err:
            rec["error"] = err
            continue
        try:
            provider, tag = provider_for(pl["source"])
        except Exception as ex:
            rec["error"] = f"brak danych: {getattr(ex, 'detail', ex)}"
            continue
        cands = [("base", "Obecne ustawienia", {})] + L.lev_variants(b["strategy"], b["market"], p, acc)
        base = None
        for key, label, ch in cands:
            vp = full_params(b["market"], b["strategy"], dict(p, **ch))
            row = {"key": key, "label": label, "changes": ch}
            errs = validate(b["market"], b["strategy"], vp)
            if errs:
                row["error"] = "; ".join(errs)
            else:
                cfg = {"name": f"Nauka dźwigni: {b['name']} — {label}", "market": b["market"], "strategy": b["strategy"],
                       "params": vp, "start": pl["start"], "end": pl["end"], "initial_capital": 100000,
                       "data_source": tag,
                       "cost_pct": 0.001 if tag == "ibkr" else 0.0025 if b["market"] == "crypto" else 0.0005}
                try:
                    res = run_backtest(provider, cfg, tag)
                    m = res["metrics"]
                    eq = res["equity"]
                    row.update(total=round(m["total_return_pct"], 2), dd=round(m["max_drawdown_pct"], 2),
                               trades=m["trades"], sharpe=round(m.get("sharpe") or 0, 2),
                               **{"is": segment(eq, pl["start"], split), "oos": segment(eq, split, pl["end"])})
                except Exception as ex:
                    row["error"] = str(ex)[:200]
            if key == "base":
                base = row
            rec["variants"].append(row)
            done += 1
            out["progress"] = round(done / total, 3)
            save(out)
        if not base or base.get("error"):
            rec["error"] = rec.get("error") or "test obecnych ustawień nieudany"
            continue
        passed = []
        for row in rec["variants"][1:]:
            if row.get("error"):
                continue
            row["why"] = judge(base, row)
            if not row["why"]:
                passed.append(row)
        passed.sort(key=lambda r: r["oos"]["calmar"], reverse=True)
        seed(db, manager, b, acc, base, passed[:2],
             [r for r in rec["variants"][1:] if r.get("why")], rec)
        save(out)
    out["status"] = "done"
    out["progress"] = 1.0
    out["finished"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    save(out)
    if notify:
        lines = []
        for r in out["bots"]:
            if r.get("skipped") or r.get("error"):
                continue
            parts = []
            if r["seeded"]:
                parts.append(", ".join(r["seeded"]) + " → laboratorium")
            if r.get("kept"):
                parts.append(", ".join(r["kept"]) + " — dalej w grze")
            if r.get("retired"):
                parts.append(", ".join(r["retired"]) + " — wycofane")
            lines.append(f"• {r['name']}: " + ("; ".join(parts) or "dźwignia nie poprawia wyniku — bez zmian"))
        notify("🧠 Nauka dźwigni zakończona.\n" + ("\n".join(lines) or "Brak botów do nauki."))
    return out


# ------------------------------------------------------------------ harmonogram (noc)
def _tz():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(os.environ.get("TZ_PANEL", "Europe/Warsaw"))
    except Exception:
        return timezone(timedelta(hours=2))


def next_night(after=None, weekly=False):
    """Najbliższa 01:00 czasu polskiego (przy powtarzaniu co tydzień: w nocy z soboty na niedzielę)."""
    now = (after or datetime.now(timezone.utc)).astimezone(_tz())
    t = now.replace(hour=NIGHT_HOUR, minute=0, second=0, microsecond=0)
    if t <= now:
        t += timedelta(days=1)
    if weekly:
        while t.weekday() != 6:                      # niedziela 01:00 = noc z soboty na niedzielę
            t += timedelta(days=1)
    return t.astimezone(timezone.utc)


def plan_get():
    try:
        return json.load(open(PLAN, encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def plan_set(next_at=None, weekly=False, bot_ids=None):
    d = {"next_at": next_at.isoformat(timespec="seconds") if next_at else None, "weekly": bool(weekly),
         "bot_ids": list(bot_ids or [])}
    with _lock:
        json.dump(d, open(PLAN + ".tmp", "w", encoding="utf-8"))
        os.replace(PLAN + ".tmp", PLAN)
    return d


def schedule(weekly=False, bot_ids=None):
    return plan_set(next_night(weekly=False), weekly, bot_ids)     # pierwszy raz: najbliższa noc


def unschedule():
    return plan_set(None, False, [])


def loop(db, manager, provider_for, run_backtest, notify=None, stop=None):
    """Wątek w tle: o zaplanowanej porze uruchamia naukę; przy „co tydzień” planuje następną noc z soboty na niedzielę."""
    while not (stop and stop.is_set()):
        try:
            pl = plan_get()
            at = pl.get("next_at")
            if at and datetime.fromisoformat(at) <= datetime.now(timezone.utc):
                plan_set(next_night(weekly=True) if pl.get("weekly") else None, pl.get("weekly"), pl.get("bot_ids"))
                t = _state.get("thread")
                if not (t and t.is_alive()):
                    start(db, manager, provider_for, run_backtest, pl.get("bot_ids") or None, notify)
        except Exception as ex:
            import logging
            logging.getLogger("tradingapp").warning(f"Nauka dźwigni (harmonogram): {ex}")
        time.sleep(30)


def start_loop(db, manager, provider_for, run_backtest, notify=None):
    threading.Thread(target=loop, args=(db, manager, provider_for, run_backtest, notify), daemon=True,
                     name="levstudy-plan").start()


def start(db, manager, provider_for, run_backtest, bot_ids=None, notify=None):
    t = _state.get("thread")
    if t and t.is_alive():
        raise ValueError("Nauka dźwigni już trwa.")

    def work():
        try:
            run(db, manager, provider_for, run_backtest, bot_ids, notify)
        except Exception as ex:
            d = load()
            d.update(status="error", error=str(ex)[:300])
            save(d)
            raise
    t = threading.Thread(target=work, daemon=True, name="levstudy")
    _state["thread"] = t
    t.start()
