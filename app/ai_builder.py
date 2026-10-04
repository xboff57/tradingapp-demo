"""
Strategia z opisu słownego: opis po polsku -> ustawienia bota (konstruktor zasad albo gotowa strategia).

Korzysta z API Claude (Anthropic) — potrzebny własny klucz ANTHROPIC_API_KEY w .env (płatny za użycie,
jedno tłumaczenie to ułamek centa). Model zwraca JSON w formacie naszego konstruktora; panel go sprawdza
(te same reguły co przy zapisie bota), pokazuje do akceptacji i dopiero Ty decydujesz: backtest albo bot.
Nic nie jest uruchamiane automatycznie.
"""

import json
import logging
import os

from . import rules as R
from .strategies import COMMON_PARAMS, STRATEGIES, TIMEFRAMES, full_params, validate

log = logging.getLogger("tradingapp")
API = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com").strip().rstrip("/") + "/v1/messages"
MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5").strip()


def enabled():
    return bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())


def _catalog():
    out = []
    for r in R.catalog():
        ps = ", ".join(f"{p['key']} ({p['type']}, domyślnie {p.get('default')}, zakres {p.get('min')}–{p.get('max')})"
                       for p in r.get("params", []))
        out.append(f"- {r['id']} [{r['side']}]: {r['label']}" + (f" — parametry: {ps}" if ps else ""))
    return "\n".join(out)


def _params_doc():
    keys = ["symbols", "timeframe", "allocation_pct", "risk_per_trade_pct", "stop_loss_pct", "take_profit_pct",
            "max_positions", "regime_filter", "regime_symbol", "regime_sma_days", "trail_atr_mult",
            "breakeven_after_pct", "max_hold_days", "leverage_mode", "direction", "leverage", "earnings_blackout_days",
            "fractional_shares"]
    by = {c["key"]: c for c in COMMON_PARAMS}
    lines = []
    for k in keys:
        c = by.get(k)
        if c:
            extra = f" opcje: {c['options']}" if c.get("options") else ""
            lines.append(f"- {k} ({c['type']}{', ułamek: 0.05 = 5%' if c['type'] == 'pct' else ''}): {c['label']}{extra}")
    for key in ("grid", "dca", "sma_cross", "mean_reversion"):
        st = STRATEGIES[key]
        lines.append(f"Strategia '{key}' ({st.name}) — własne parametry: " +
                     ", ".join(f"{p['key']} ({p['type']})" for p in st.params))
    return "\n".join(lines)


SYSTEM = """Jesteś tłumaczem opisu strategii inwestycyjnej (po polsku) na ustawienia bota w aplikacji TradingApp.
Zwróć WYŁĄCZNIE jeden obiekt JSON, bez komentarzy i bez bloku markdown:
{{"name": "krótka nazwa bota", "market": "stocks" | "crypto", "strategy": "rules" | "sma_cross" | "mean_reversion" | "grid" | "dca",
 "params": {{...}}, "explanation": "2-4 zdania po polsku: co bot robi, kiedy kupuje i sprzedaje",
 "assumptions": ["założenia, które przyjąłeś, bo opis ich nie podawał"], "warnings": ["ryzyka, np. dźwignia, mało symboli"]}}

Zasady:
- Domyślnie użyj strategii "rules" (konstruktor) z polami entry_rules, exit_rules (listy obiektów {{"id": ..., parametry}}),
  entry_mode "all" (wszystkie zasady naraz) albo "any" (dowolna).
- Symbole: akcje USA jak "AAPL", GPW z końcówką ".WSE" (np. "PKO.WSE"), ETF-y USA jak "SPY", krypto jako para "BTC/USD"
  (na Krakenie "BTC/EUR"). Gdy opis podaje indeks/grupę (np. "duże spółki z WIG20"), wpisz 8-15 najpłynniejszych.
- Interwał "timeframe": jeden z {timeframes}. Bez wskazówek: "1Day" dla akcji, "4Hour" dla krypto.
- Procenty jako ułamki (5% = 0.05). Zawsze ustaw rozsądny stop_loss_pct i take_profit_pct.
- Filtr rynku: regime_filter true, regime_symbol "SPY" (USA), "ETFBW20TR.WSE" (GPW), "BTC/USD" (krypto), gdy opis mówi
  o kupowaniu tylko w hossie / gdy rynek rośnie.
- Gra na spadki / dźwignia tylko, gdy opis o nią prosi: leverage_mode "etf" albo "margin", direction "short"/"both".
- Regularne kupowanie co tydzień / uśrednianie -> strategy "dca"; handel w przedziale cen -> "grid" (tylko krypto).
- Nie wymyślaj zasad spoza katalogu. Gdy czegoś nie da się zrobić, przybliż i opisz to w warnings.

Katalog zasad konstruktora (id [kupno/sprzedaż]):
{catalog}

Pozostałe ustawienia:
{params}
"""


def _extract_json(text):
    text = text.strip()
    i, j = text.find("{"), text.rfind("}")
    if i < 0 or j < 0:
        raise ValueError("Model nie zwrócił ustawień w formacie JSON.")
    return json.loads(text[i:j + 1])


def _call(messages):
    import requests
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    system = SYSTEM.format(timeframes=", ".join(TIMEFRAMES), catalog=_catalog(), params=_params_doc())
    r = requests.post(API, timeout=90, headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                                                "content-type": "application/json"},
                      json={"model": MODEL, "max_tokens": 2500, "system": system, "messages": messages})
    if r.status_code != 200:
        try:
            msg = r.json().get("error", {}).get("message", "")
        except ValueError:
            msg = r.text[:200]
        raise ValueError(f"API Claude: HTTP {r.status_code} {msg[:200]}")
    return "".join(b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text")


def build(text, market_hint=None, account_type=None):
    if not enabled():
        raise ValueError("Brak klucza ANTHROPIC_API_KEY w .env — dopisz go (console.anthropic.com) i zrestartuj aplikację.")
    text = (text or "").strip()
    if len(text) < 10:
        raise ValueError("Opisz strategię co najmniej jednym zdaniem.")
    ctx = []
    if market_hint:
        ctx.append(f"Rynek: {'akcje' if market_hint == 'stocks' else 'krypto'}.")
    if account_type:
        ctx.append(f"Konto: {account_type} (kraken = pary w EUR, ibkr = GPW/USA w całych akcjach, alpaca = USA).")
    msgs = [{"role": "user", "content": (" ".join(ctx) + "\n\n" if ctx else "") + "Opis strategii:\n" + text[:3000]}]
    out, errors, p = None, [], None
    for attempt in range(2):
        raw = _call(msgs)
        try:
            out = _extract_json(raw)
        except ValueError as e:
            errors = [str(e)]
            msgs += [{"role": "assistant", "content": raw}, {"role": "user", "content": "Zwróć tylko poprawny JSON."}]
            continue
        market = out.get("market") if out.get("market") in ("stocks", "crypto") else (market_hint or "stocks")
        strategy = out.get("strategy") if out.get("strategy") in STRATEGIES else "rules"
        try:
            p = full_params(market, strategy, out.get("params") or {})
            errors = validate(market, strategy, p)
        except Exception as e:
            p, errors = None, [f"nieprawidłowe ustawienia: {e}"]
        if p is not None:
            out.update(market=market, strategy=strategy, params=p)
        if not errors:
            break
        msgs += [{"role": "assistant", "content": raw},
                 {"role": "user", "content": "Panel odrzucił te ustawienia: " + "; ".join(errors) +
                  ". Popraw je i zwróć cały JSON jeszcze raz."}]
    if out is None:
        raise ValueError("; ".join(errors) or "Nie udało się zamienić opisu na strategię.")
    out["errors"] = errors
    out.setdefault("assumptions", [])
    out.setdefault("warnings", [])
    return out
