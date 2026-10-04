"""
Bezpiecznik konta (zakładka „Ryzyko”) — wspólne zasady dla wszystkich botów.

- Wyłącznik: „wstrzymaj nowe wejścia” dla wszystkich botów naraz (pozycje zostają ze swoimi stopami).
- Dzienny limit straty na konto: gdy kapitał konta spadnie dziś o więcej niż X% od wczorajszego zamknięcia,
  boty tego konta do końca dnia nie otwierają nowych pozycji (opcjonalnie: zamykają pozycje z dźwignią).
- Zgoda na dźwignię: na koncie z PRAWDZIWYMI pieniędzmi bot z dźwignią / grą na spadki wystartuje dopiero, gdy
  włączysz zgodę dla tego konta (z hasłem i kodem 2FA). Konta papierowe i symulator nie wymagają zgody.
- Limit dźwigni na konto: bot nie użyje większej dźwigni niż limit konta (nawet jeśli ma wyższą w ustawieniach).

Stan w data/risk.json.
"""

import json
import os
import threading
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .config import DATA_DIR

PATH = os.path.join(DATA_DIR, "risk.json")
PL = ZoneInfo("Europe/Warsaw")
DEFAULTS = {"halt": False, "halt_reason": "", "daily_loss_pct": 0.0, "close_leveraged_on_limit": False,
            "accounts": {}}            # nazwa konta -> {"leverage_ok": bool, "max_leverage": float, "since": iso}
_lock = threading.Lock()
_tripped = {}                          # konto -> data (PL), gdy limit dzienny zadziałał


def load():
    try:
        with open(PATH, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        d = {}
    out = dict(DEFAULTS)
    out.update(d)
    out["accounts"] = dict(d.get("accounts") or {})
    return out


def save(d):
    with _lock:
        tmp = PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
        os.replace(tmp, PATH)


def update(**kw):
    d = load()
    d.update(kw)
    save(d)
    return d


def account_cfg(name):
    return load()["accounts"].get(name, {})


def set_account(name, **kw):
    d = load()
    a = dict(d["accounts"].get(name, {}))
    a.update(kw)
    if kw.get("leverage_ok"):
        a["since"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    d["accounts"][name] = a
    save(d)
    return a


def is_real(acc):
    """Konto z prawdziwymi pieniędzmi (nie papierowe, nie symulator, nie testnet)."""
    return not (acc.paper or acc.type == "sim" or acc.extra.get("sandbox"))


def leverage_allowed(acc):
    """(ok, powód) - czy na tym koncie wolno grać z dźwignią / na spadki."""
    if not is_real(acc):
        return True, ""
    if account_cfg(acc.name).get("leverage_ok"):
        return True, ""
    return False, (f"Konto „{acc.name}” to prawdziwe pieniądze — dźwignia i gra na spadki wymagają Twojej zgody "
                   "w zakładce „Ryzyko”.")


def max_leverage(acc, market):
    cap = float(account_cfg(acc.name).get("max_leverage") or 0)
    hard = 2.0 if market == "stocks" else 5.0
    return min(hard, cap) if cap >= 1 else hard


def entries_allowed(acc, acct):
    """(ok, powód) - czy boty tego konta mogą teraz otwierać nowe pozycje."""
    d = load()
    if d.get("halt"):
        return False, "wyłącznik w zakładce „Ryzyko” — nowe wejścia wstrzymane" + (
            f" ({d['halt_reason']})" if d.get("halt_reason") else "")
    lim = float(d.get("daily_loss_pct") or 0)
    if lim > 0:
        today = datetime.now(PL).date().isoformat()
        if _tripped.get(acc.name) == today:
            return False, f"dzienny limit straty konta ({lim:.1%}) — bez nowych wejść do jutra"
        last, eq = float(acct.get("last_equity") or 0), float(acct.get("equity") or 0)
        if last > 0 and eq < last * (1 - lim):
            _tripped[acc.name] = today
            from . import notify
            notify.send(f"🛑 Konto {acc.name}: dzienny limit straty — kapitał {eq:,.0f} wobec {last:,.0f} na otwarciu "
                        f"dnia ({(eq / last - 1) * 100:+.1f}%). Boty tego konta nie otwierają nowych pozycji do jutra.",
                        "errors", key=f"risk-{acc.name}", cooldown=6 * 3600, priority=5)
            return False, f"dzienny limit straty konta ({lim:.1%}) — bez nowych wejść do jutra"
    return True, ""


def tripped_today(acc_name):
    return _tripped.get(acc_name) == datetime.now(PL).date().isoformat()


def status(accounts, latest_equity):
    d = load()
    rows = []
    for acc in accounts:
        cfg = d["accounts"].get(acc.name, {})
        rows.append({"name": acc.name, "type": acc.type, "real": is_real(acc),
                     "leverage_ok": bool(cfg.get("leverage_ok")) or not is_real(acc),
                     "consent": bool(cfg.get("leverage_ok")), "since": cfg.get("since"),
                     "max_leverage": cfg.get("max_leverage") or None,
                     "tripped": tripped_today(acc.name), **(latest_equity.get(acc.name) or {})})
    return {"halt": d["halt"], "halt_reason": d["halt_reason"], "daily_loss_pct": d["daily_loss_pct"],
            "close_leveraged_on_limit": d["close_leveraged_on_limit"], "accounts": rows}
