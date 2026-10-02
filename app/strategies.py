"""
Strategie - JEDEN kod dla handlu na zywo i dla backtestow.

Kazda strategia dostaje DataFrame swiec (open/high/low/close/volume) i dopisuje
kolumny 'entry' i 'exit' (bool) liczone WYLACZNIE na zamknietych swiecach.
Bot na zywo patrzy na ostatni wiersz, backtest przechodzi po wszystkich.

Nowa strategia = nowa klasa + wpis w STRATEGIES.
"""

import numpy as np
import pandas as pd

from . import rules as R
from .signals import COPY_MANAGERS

TIMEFRAMES = ["1Min", "5Min", "15Min", "30Min", "1Hour", "4Hour", "1Day"]
TIMEFRAME_MINUTES = {"1Min": 1, "5Min": 5, "15Min": 15, "30Min": 30, "1Hour": 60, "4Hour": 240, "1Day": 1440}


def rsi(close: pd.Series, period: int) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50)


# ------------------------------------------------------------------ parametry wspolne
# typy: int, float, pct (ulamek, w panelu jako %), bool, list (symbole), choice, str
COMMON_PARAMS = [
    {"key": "symbols", "label": "Symbole", "type": "list",
     "help": "Akcje: NVDA,TSLA. Krypto: BTC/USD,ETH/USD"},
    {"key": "timeframe", "label": "Interwal swiec", "type": "choice", "options": TIMEFRAMES},
    {"key": "allocation_pct", "label": "Budzet bota (% kapitalu konta)", "type": "pct", "min": 0.01, "max": 1},
    {"key": "risk_per_trade_pct", "label": "Ryzyko na transakcje", "type": "pct", "min": 0.001, "max": 0.1},
    {"key": "stop_loss_pct", "label": "Stop-loss", "type": "pct", "min": 0.002, "max": 0.5},
    {"key": "take_profit_pct", "label": "Take-profit", "type": "pct", "min": 0.002, "max": 2},
    {"key": "max_positions", "label": "Max otwartych pozycji", "type": "int", "min": 1, "max": 20},
    {"key": "fractional_shares", "label": "Ułamki akcji — kupuj za kwotę", "type": "bool", "market": "stocks",
     "help": "Bot kupuje akcje za wyliczoną kwotę, nawet gdy nie starcza na całą akcję (np. 100 $ akcji po 300 $). "
             "Tylko Alpaca (akcje i ETF-y z USA, które Alpaca dopuszcza do ułamków). Stop-loss i take-profit "
             "pilnuje wtedy bot co cykl. GPW i IBKR nie obsługują ułamków."},
    {"key": "regime_filter", "label": "Filtr rezimu rynku", "type": "bool",
     "help": "Nowe wejscia tylko gdy symbol rezimu jest powyzej swojej sredniej dziennej"},
    {"key": "regime_symbol", "label": "Symbol rezimu", "type": "str"},
    {"key": "regime_sma_days", "label": "Srednia rezimu (dni)", "type": "int", "min": 5, "max": 250},
    {"key": "regime2_symbol", "label": "Drugi termometr: symbol (puste = brak)", "type": "str",
     "help": "Np. SPY dla rynku światowego. Nowe wejścia tylko, gdy także on jest nad swoją średnią."},
    {"key": "regime2_sma_days", "label": "Drugi termometr: średnia (dni)", "type": "int", "min": 5, "max": 250},
    {"key": "breadth_filter", "label": "Filtr szerokości rynku", "type": "bool",
     "help": "Nowe wejścia tylko, gdy wystarczająco dużo spółek z listy bota jest nad swoją średnią "
             "(min. 5 spółek z pełną historią, inaczej filtr nie blokuje)."},
    {"key": "breadth_min", "label": "Szerokość: min. odsetek spółek nad średnią", "type": "pct", "min": 0.1, "max": 0.9},
    {"key": "breadth_sma_days", "label": "Szerokość: średnia (dni)", "type": "int", "min": 20, "max": 250},
    {"key": "poll_seconds", "label": "Co ile sekund cykl", "type": "int", "min": 15, "max": 3600},
    {"key": "flatten_before_close_min", "label": "Zamknij pozycje X min przed koncem sesji (0 = trzymaj)",
     "type": "int", "min": 0, "max": 120, "market": "stocks"},
    {"key": "no_entry_after_open_min", "label": "Bez wejsc przez X min po otwarciu", "type": "int",
     "min": 0, "max": 120, "market": "stocks"},
    {"key": "stop_limit_slippage_pct", "label": "Limit ponizej stopu (krypto)", "type": "pct",
     "min": 0.001, "max": 0.05, "market": "crypto"},
    # --- sygnaly zewnetrzne (tylko akcje pojedynczych spolek; ETF-y nie maja insiderow)
    {"key": "insider_mode", "label": "Insiderzy (SEC Form 4)", "type": "choice", "options": ["off", "veto", "confirm"],
     "labels": {"off": "wyłączone", "veto": "weto", "confirm": "potwierdzenie"},
     "market": "stocks", "group": "ext",
     "help": "weto = nie kupuj, gdy kilku insiderów sprzedaje, a nikt nie kupuje · "
             "potwierdzenie = kupuj tylko, gdy insiderzy ostatnio kupowali"},
    {"key": "insider_days", "label": "Okno insiderów (dni)", "type": "int", "min": 5, "max": 180,
     "market": "stocks", "group": "ext"},
    {"key": "insider_min_buyers", "label": "Min. kupujących insiderów (potwierdzenie)", "type": "int", "min": 1, "max": 10,
     "market": "stocks", "group": "ext"},
    {"key": "insider_min_sellers", "label": "Min. sprzedających insiderów (weto)", "type": "int", "min": 1, "max": 20,
     "market": "stocks", "group": "ext"},
    {"key": "funds_mode", "label": "Fundusze (SEC 13F)", "type": "choice", "options": ["off", "veto", "confirm"],
     "labels": {"off": "wyłączone", "veto": "weto", "confirm": "potwierdzenie"},
     "market": "stocks", "group": "ext",
     "help": "Ostatni znany raport 13F wybranych zarządzających (opóźnienie do 45 dni). weto = nie kupuj, gdy "
             "sprzedają · potwierdzenie = kupuj tylko, gdy dokupili"},
    {"key": "funds_min_bulls", "label": "Min. dokupujących funduszy (potwierdzenie)", "type": "int", "min": 1, "max": 10,
     "market": "stocks", "group": "ext"},
    {"key": "funds_min_bears", "label": "Min. sprzedających funduszy (weto)", "type": "int", "min": 1, "max": 10,
     "market": "stocks", "group": "ext"},
    # --- wyjscia z pozycji (dzialaja dla kazdej strategii, obok SL/TP)
    {"key": "trail_atr_mult", "label": "Stop kroczący: X × ATR pod szczytem (0 = wyłączony)", "type": "float",
     "min": 0, "max": 10, "group": "exits",
     "help": "Stop podąża za najwyższą ceną od wejścia w odległości X średnich zasięgów świecy (ATR 14). "
             "Pilnuje go bot; twardy stop-loss zostaje na serwerze."},
    {"key": "breakeven_after_pct", "label": "Po zysku X% przesuń stop na cenę wejścia (0 = wyłączone)", "type": "pct",
     "min": 0, "max": 0.5, "group": "exits"},
    {"key": "max_hold_days", "label": "Maks. czas trzymania pozycji (dni, 0 = bez limitu)", "type": "int",
     "min": 0, "max": 365, "group": "exits"},
    # --- radar altcoinow (tylko krypto)
    {"key": "universe_mode", "label": "Wybór monet", "type": "choice", "options": ["fixed", "radar"],
     "labels": {"fixed": "stała lista symboli", "radar": "radar: najmocniejsze monety z giełdy (codziennie)"},
     "market": "crypto", "group": "radar",
     "help": "W trybie radaru pole „Symbole” to monety zawsze uwzględniane (może być puste)."},
    {"key": "radar_top", "label": "Ile najlepszych monet z radaru", "type": "int", "min": 1, "max": 30,
     "market": "crypto", "group": "radar"},
    {"key": "radar_min_volume", "label": "Min. obrót z doby (w walucie konta)", "type": "float", "min": 0,
     "max": 1e10, "market": "crypto", "group": "radar"},
    # --- okazje do recznego kopiowania (powiadomienia na telefon)
    {"key": "copy_signals", "label": "Powiadamiaj o okazjach do ręcznego kopiowania", "type": "bool", "group": "signals",
     "help": "Przy każdym kupnie i sprzedaży bota przyjdzie powiadomienie na telefon (ntfy/Telegram) z ceną, "
             "stop-lossem i kwotą dla Ciebie — żeby kupić te akcje na swoim koncie."},
    {"key": "manual_capital", "label": "Twój kapitał do ręcznego kopiowania (0 = bez wyliczeń)", "type": "float",
     "min": 0, "max": 1e9, "group": "signals",
     "help": "W walucie konta bota (np. zł dla GPW). Powiadomienie poda kwotę i liczbę akcji w tej samej proporcji, "
             "w jakiej bot kupił ze swojego budżetu."},
    # --- laboratorium wariantow (app/lab.py)
    {"key": "lab_enabled", "label": "Laboratorium: testuj warianty na żywo (na niby)", "type": "bool", "group": "lab",
     "help": "Obok bota działa kilka wirtualnych wersji z innymi ustawieniami; zwycięzca może przejąć bota."},
    {"key": "lab_min_trades", "label": "Min. transakcji, by wariant mógł wygrać", "type": "int", "min": 10, "max": 500,
     "group": "lab"},
    {"key": "lab_min_days", "label": "Min. dni testu, by wariant mógł wygrać", "type": "int", "min": 3, "max": 180,
     "group": "lab"},
    # --- uczenie maszynowe (app/ml.py)
    {"key": "ml_filter", "label": "Filtr ML - odrzucaj słabe wejścia", "type": "bool", "group": "ml",
     "not_strategy": ["ml_model"],
     "help": "Model uczony na historii ocenia szansę, że wejście trafi take-profit przed stop-lossem. "
             "Wejścia poniżej progu opłacalności są odrzucane."},
    {"key": "ml_sizing", "label": "Wielkość pozycji wg pewności modelu", "type": "bool", "group": "ml",
     "help": "Przy progu pół pozycji, przy pewności o 10 pp wyższej pełna. Nigdy więcej niż bez ML."},
    {"key": "ml_confidence", "label": "Wymagana pewność modelu", "type": "choice", "options": ["low", "mid", "high"],
     "labels": {"low": "niska (próg opłacalności)", "mid": "średnia (+5 pp)", "high": "wysoka (+10 pp)"},
     "group": "ml",
     "help": "Próg opłacalności = (SL + koszty) / (TP + SL) - szansa, przy której wejście wychodzi na zero."},
    {"key": "ml_horizon", "label": "Horyzont oceny wejścia (świece)", "type": "int", "min": 4, "max": 200,
     "group": "ml", "help": "Ile świec model 'czeka' na TP albo SL, zanim oceni wejście po cenie końcowej."},
    {"key": "ml_train_days", "label": "Okno uczenia (dni)", "type": "int", "min": 60, "max": 1500, "group": "ml",
     "help": "Na ilu ostatnich dniach model się uczy. Dla 1Min max 30, 5Min 90, 15Min 180 dni."},
    {"key": "ml_exit_drop", "label": "Wyjście, gdy pewność spadnie o (pp poniżej progu opłacalności)",
     "type": "pct", "min": 0, "max": 0.3, "group": "ml", "only_strategy": ["ml_model"]},
]

CONTEXT_DEFAULTS = {"regime2_symbol": "", "regime2_sma_days": 200, "breadth_filter": False,
                    "breadth_min": 0.5, "breadth_sma_days": 200}
SIGNAL_DEFAULTS = {"copy_signals": False, "manual_capital": 0.0}
LAB_DEFAULTS = {"lab_enabled": False, "lab_min_trades": 30, "lab_min_days": 14}
EXIT_DEFAULTS = {"trail_atr_mult": 0.0, "breakeven_after_pct": 0.0, "max_hold_days": 0}
ML_DEFAULTS = {"ml_filter": False, "ml_sizing": False, "ml_confidence": "mid", "ml_horizon": 24,
               "ml_train_days": 365, "ml_exit_drop": 0.05}

MARKET_DEFAULTS = {
    "stocks": {"timeframe": "1Hour", "allocation_pct": 1.0, "risk_per_trade_pct": 0.01, "stop_loss_pct": 0.03,
               "take_profit_pct": 0.06, "max_positions": 4, "regime_filter": True, "regime_symbol": "SPY",
               "regime_sma_days": 50, "poll_seconds": 60, "flatten_before_close_min": 0,
               "no_entry_after_open_min": 5, "insider_mode": "off", "insider_days": 30,
               "insider_min_buyers": 1, "insider_min_sellers": 3, "funds_mode": "off", "funds_min_bulls": 1,
               "funds_min_bears": 2, "fractional_shares": False},
    "crypto": {"timeframe": "1Hour", "allocation_pct": 1.0, "risk_per_trade_pct": 0.005, "stop_loss_pct": 0.04,
               "take_profit_pct": 0.08, "max_positions": 3, "regime_filter": True, "regime_symbol": "BTC/USD",
               "regime_sma_days": 50, "poll_seconds": 60, "stop_limit_slippage_pct": 0.01},
}
for _m in MARKET_DEFAULTS.values():
    _m.update(ML_DEFAULTS)
    _m.update(EXIT_DEFAULTS)
    _m.update(LAB_DEFAULTS)
    _m.update(SIGNAL_DEFAULTS)
    _m.update(CONTEXT_DEFAULTS)
MARKET_DEFAULTS["crypto"].update({"universe_mode": "fixed", "radar_top": 5, "radar_min_volume": 250000.0})


# ------------------------------------------------------------------ strategie
class Strategy:
    key = ""
    name = ""
    description = ""
    params = []           # parametry wlasne strategii
    defaults = {}

    @classmethod
    def warmup(cls, p) -> int:
        """Ile swiec potrzeba, zeby sygnaly mialy sens."""
        return 50

    @classmethod
    def compute(cls, df: pd.DataFrame, p: dict) -> pd.DataFrame:
        raise NotImplementedError


class SmaCross(Strategy):
    key = "sma_cross"
    name = "Przeciecie srednich (trend)"
    description = ("Kupno gdy szybka SMA przecina wolna od dolu, RSI nie jest wykupiony i (opcjonalnie) "
                   "wolumen jest powyzej sredniej. Wyjscie gdy szybka spada pod wolna.")
    params = [
        {"key": "sma_fast", "label": "SMA szybka", "type": "int", "min": 2, "max": 200},
        {"key": "sma_slow", "label": "SMA wolna", "type": "int", "min": 3, "max": 400},
        {"key": "rsi_period", "label": "Okres RSI", "type": "int", "min": 2, "max": 50},
        {"key": "rsi_max", "label": "RSI max przy wejsciu", "type": "float", "min": 50, "max": 100},
        {"key": "use_volume", "label": "Potwierdzenie wolumenem", "type": "bool"},
        {"key": "volume_lookback", "label": "Srednia wolumenu (swiece)", "type": "int", "min": 5, "max": 200},
    ]
    defaults = {"sma_fast": 10, "sma_slow": 30, "rsi_period": 14, "rsi_max": 70,
                "use_volume": True, "volume_lookback": 20}

    @classmethod
    def warmup(cls, p):
        return max(p["sma_slow"], p["rsi_period"], p["volume_lookback"]) + 2

    @classmethod
    def compute(cls, df, p):
        df = df.copy()
        fast = df["close"].rolling(p["sma_fast"]).mean()
        slow = df["close"].rolling(p["sma_slow"]).mean()
        r = rsi(df["close"], p["rsi_period"])
        crossed_up = (fast.shift(1) <= slow.shift(1)) & (fast > slow)
        vol_ok = df["volume"] > df["volume"].rolling(p["volume_lookback"]).mean() if p["use_volume"] else True
        df["entry"] = (crossed_up & (r < p["rsi_max"]) & vol_ok).fillna(False).astype(bool)
        df["exit"] = (fast < slow).fillna(False).astype(bool)
        df["ind_fast"], df["ind_slow"], df["ind_rsi"] = fast, slow, r
        return df


class MeanReversion(Strategy):
    key = "mean_reversion"
    name = "Powrot do sredniej"
    description = ("Kupno gdy RSI jest wyprzedany, a cena lezy co najmniej X% pod srednia. "
                   "Wyjscie gdy cena wroci do sredniej albo RSI przekroczy prog wyjscia. "
                   "W Twoich backtestach najlepsza na ETF-ach sektorowych.")
    params = [
        {"key": "sma_period", "label": "Srednia (swiece)", "type": "int", "min": 5, "max": 400},
        {"key": "rsi_period", "label": "Okres RSI", "type": "int", "min": 2, "max": 50},
        {"key": "rsi_oversold", "label": "RSI wyprzedania (wejscie ponizej)", "type": "float", "min": 5, "max": 50},
        {"key": "pct_below_sma", "label": "Min. odchylenie pod srednia", "type": "pct", "min": 0, "max": 0.3},
        {"key": "rsi_exit", "label": "RSI wyjscia (powyzej)", "type": "float", "min": 30, "max": 90},
    ]
    defaults = {"sma_period": 20, "rsi_period": 14, "rsi_oversold": 30, "pct_below_sma": 0.02, "rsi_exit": 55}

    @classmethod
    def warmup(cls, p):
        return max(p["sma_period"], p["rsi_period"]) + 2

    @classmethod
    def compute(cls, df, p):
        df = df.copy()
        sma = df["close"].rolling(p["sma_period"]).mean()
        r = rsi(df["close"], p["rsi_period"])
        below = (sma - df["close"]) / sma
        df["entry"] = ((r < p["rsi_oversold"]) & (below >= p["pct_below_sma"])).fillna(False).astype(bool)
        df["exit"] = ((df["close"] >= sma) | (r > p["rsi_exit"])).fillna(False).astype(bool)
        df["ind_fast"], df["ind_slow"], df["ind_rsi"] = sma, sma, r
        return df


class MlModel(Strategy):
    key = "ml_model"
    name = "Model ML (uczenie maszynowe)"
    description = ("Wejścia wybiera model uczony na historii: kupno, gdy szansa trafienia take-profitu przed "
                   "stop-lossem jest powyżej progu opłacalności (+ wybrany margines). Wyjście: SL/TP albo spadek "
                   "pewności modelu. Model uczy się krocząco w backteście, a dla bota na żywo co tydzień - "
                   "nowa wersja czeka na akceptację w zakładce ML.")
    params = []
    defaults = {}

    @classmethod
    def warmup(cls, p):
        return 110

    @classmethod
    def compute(cls, df, p):
        # decyzje podejmuje ml.signal_frame (potrzebuje prawdopodobienstw modelu); bez modelu - brak wejsc
        df = df.copy()
        df["entry"] = False
        df["exit"] = False
        sma = df["close"].rolling(20).mean()
        df["ind_fast"], df["ind_slow"], df["ind_rsi"] = sma, df["close"].rolling(50).mean(), rsi(df["close"], 14)
        return df


class RuleBuilder(Strategy):
    key = "rules"
    name = "Konstruktor zasad"
    description = ("Własna strategia z gotowych zasad: trend, wybicia, momentum, wolumen, zmienność. Kupno, gdy "
                   "spełnione są wszystkie wybrane zasady kupna (albo dowolna z nich), sprzedaż, gdy zadziała "
                   "którakolwiek zasada sprzedaży — obok stop-lossa, take-profitu i stopu kroczącego.")
    params = [
        {"key": "entry_rules", "label": "Zasady kupna", "type": "rules", "side": "entry"},
        {"key": "entry_mode", "label": "Kupuj, gdy spełnione są", "type": "choice", "options": ["all", "any"],
         "labels": {"all": "wszystkie zasady kupna naraz (i)", "any": "dowolna z zasad kupna (lub)"}},
        {"key": "exit_rules", "label": "Zasady sprzedaży (wystarczy dowolna)", "type": "rules", "side": "exit",
         "help": "Bez zasad sprzedaży pozycję zamyka tylko SL, TP, stop kroczący albo limit czasu."},
    ]
    defaults = {"entry_rules": [{"id": "price_above_sma", "n": 200}, {"id": "breakout_high", "n": 20, "within": 1}],
                "entry_mode": "all", "exit_rules": [{"id": "breakdown_low", "n": 10}]}

    @classmethod
    def warmup(cls, p):
        return R.warmup(p["entry_rules"] + p["exit_rules"])

    @classmethod
    def compute(cls, df, p):
        df = df.copy()
        df["entry"] = R.evaluate(df, p["entry_rules"], p.get("entry_mode", "all")).astype(bool)
        df["exit"] = R.evaluate(df, p["exit_rules"], "any").astype(bool)
        df["ind_fast"] = df["close"].rolling(20).mean()
        df["ind_slow"] = df["close"].rolling(50).mean()
        df["ind_rsi"] = rsi(df["close"], 14)
        return df


class CopyFunds(Strategy):
    key = "copy_funds"
    name = "Kopiowanie funduszy (13F superinwestorów)"
    description = ("Copy trading najlepszych zarządzających: bot co dzień czyta ich raporty 13F w SEC i kupuje "
                   "spółki, które fundusze ostatnio dokupiły lub otworzyły (najwięcej zgodnych funduszy = "
                   "pierwszeństwo), gdy cena jest w trendzie. Sprzedaje, gdy fundusze zaczynają sprzedawać, "
                   "albo na stopie. Raporty 13F mają do 45 dni opóźnienia - to kopiowanie pomysłów, nie "
                   "codziennych transakcji. Pole „Symbole” to spółki zawsze brane pod uwagę (może być puste).")
    params = [
        {"key": "copy_managers", "label": "Kopiowani zarządzający (Nazwa:CIK, po przecinku)", "type": "str",
         "help": "CIK funduszu znajdziesz na sec.gov (EDGAR). Puste = lista z .env (FUND_MANAGERS)."},
        {"key": "copy_min_bulls", "label": "Min. funduszy, które dokupiły spółkę", "type": "int", "min": 1, "max": 10},
        {"key": "copy_top", "label": "Ile spółek z raportów śledzić na raz", "type": "int", "min": 3, "max": 60},
        {"key": "copy_fresh_days", "label": "Kupuj do X dni po nowym raporcie funduszu", "type": "int",
         "min": 1, "max": 120,
         "help": "Później kupno tylko wtedy, gdy cena przebije średnią od dołu (nowy impuls trendu)."},
        {"key": "copy_trend_sma", "label": "Filtr trendu: cena nad SMA (dni, 0 = bez filtra)", "type": "int",
         "min": 0, "max": 250},
        {"key": "copy_exit_on_sell", "label": "Sprzedaj, gdy fundusze sprzedają (a żaden nie dokupuje)",
         "type": "bool"},
    ]
    defaults = {"copy_managers": COPY_MANAGERS, "copy_min_bulls": 2, "copy_top": 20, "copy_fresh_days": 45,
                "copy_trend_sma": 200, "copy_exit_on_sell": True}

    @classmethod
    def warmup(cls, p):
        return max(int(p.get("copy_trend_sma", 50)), 20) + 2

    @classmethod
    def compute(cls, df, p):
        # decyzje funduszy dokleja signals.apply_copy (potrzebuje danych z SEC); tu tylko trend ceny
        df = df.copy()
        n = int(p.get("copy_trend_sma", 50))
        if n > 0:
            sma = df["close"].rolling(n).mean()
            trend = (df["close"] > sma).fillna(False)
            df["cross"] = (trend & ~trend.shift(1, fill_value=True)).astype(bool)
        else:
            sma = df["close"].rolling(50).mean()
            trend = pd.Series(True, index=df.index)
            df["cross"] = False
        df["entry"] = trend.astype(bool)
        df["exit"] = False
        df["ind_fast"], df["ind_slow"], df["ind_rsi"] = df["close"].rolling(20).mean(), sma, rsi(df["close"], 14)
        return df


STRATEGIES = {s.key: s for s in (SmaCross, MeanReversion, MlModel, RuleBuilder, CopyFunds)}


# ------------------------------------------------------------------ szablony botow
TEMPLATES = [
    {"id": "aggr_stocks", "name": "Agresywny - akcje (1 min)", "market": "stocks", "strategy": "sma_cross",
     "params": {"symbols": ["NVDA", "TSLA", "AMD", "META", "MU", "COIN", "PLTR"], "timeframe": "1Min",
                "sma_fast": 5, "sma_slow": 15, "rsi_max": 75, "use_volume": True, "allocation_pct": 0.7,
                "risk_per_trade_pct": 0.01, "stop_loss_pct": 0.015, "take_profit_pct": 0.025,
                "max_positions": 2, "flatten_before_close_min": 10}},
    {"id": "aggr_crypto", "name": "Agresywny - krypto (15 min)", "market": "crypto", "strategy": "sma_cross",
     "params": {"symbols": ["BTC/USD", "ETH/USD", "SOL/USD", "XRP/USD", "DOGE/USD"], "timeframe": "15Min",
                "sma_fast": 10, "sma_slow": 30, "rsi_max": 70, "use_volume": False, "allocation_pct": 0.3,
                "risk_per_trade_pct": 0.005, "stop_loss_pct": 0.03, "take_profit_pct": 0.06, "max_positions": 2}},
    {"id": "hybrid_etf", "name": "Hybryda - ETF sektorowe (powrot do sredniej)", "market": "stocks",
     "strategy": "mean_reversion",
     "params": {"symbols": ["XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLP", "XLU", "XLB", "XLRE", "XLC",
                            "SMH", "IWM"], "timeframe": "1Hour", "allocation_pct": 0.6,
                "risk_per_trade_pct": 0.02, "stop_loss_pct": 0.04, "take_profit_pct": 0.08,
                "max_positions": 4, "flatten_before_close_min": 0}},
    {"id": "hybrid_crypto", "name": "Hybryda - krypto (trend, 1 h)", "market": "crypto", "strategy": "sma_cross",
     "params": {"symbols": ["BTC/USD", "ETH/USD", "SOL/USD", "XRP/USD", "DOGE/USD", "ADA/USD", "AVAX/USD",
                            "LINK/USD", "DOT/USD", "LTC/USD"], "timeframe": "1Hour", "sma_fast": 10,
                "sma_slow": 30, "use_volume": True, "allocation_pct": 0.4, "risk_per_trade_pct": 0.01,
                "stop_loss_pct": 0.05, "take_profit_pct": 0.12, "max_positions": 4}},
    {"id": "rules_trend", "name": "Konstruktor - trend + wybicie (akcje, 1 dzień)", "market": "stocks",
     "strategy": "rules",
     "params": {"symbols": ["AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "AVGO", "JPM", "LLY", "COST"],
                "timeframe": "1Day", "allocation_pct": 0.5, "risk_per_trade_pct": 0.01, "stop_loss_pct": 0.08,
                "take_profit_pct": 0.4, "max_positions": 5, "trail_atr_mult": 3.0,
                "entry_rules": [{"id": "price_above_sma", "n": 200}, {"id": "breakout_high", "n": 50, "within": 1},
                                {"id": "volume_spike", "n": 20, "mult": 1.3}],
                "entry_mode": "all", "exit_rules": [{"id": "close_below_sma", "n": 50}]}},
    {"id": "rules_pullback", "name": "Konstruktor - korekta w trendzie (ETF, 1 h)", "market": "stocks",
     "strategy": "rules",
     "params": {"symbols": ["XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLP", "XLU", "SMH", "IWM", "SPY"],
                "timeframe": "1Hour", "allocation_pct": 0.5, "risk_per_trade_pct": 0.01, "stop_loss_pct": 0.03,
                "take_profit_pct": 0.05, "max_positions": 4, "max_hold_days": 10,
                "entry_rules": [{"id": "sma_fast_above_slow", "fast": 50, "slow": 200},
                                {"id": "rsi_below", "n": 14, "level": 35}],
                "entry_mode": "all", "exit_rules": [{"id": "back_to_sma", "n": 20}]}},
    {"id": "radar_trend", "name": "Radar altcoinów - trend + wybicie (krypto, 4 h)", "market": "crypto",
     "strategy": "rules",
     "params": {"symbols": [], "universe_mode": "radar", "radar_top": 5, "radar_min_volume": 250000,
                "timeframe": "4Hour", "allocation_pct": 0.5, "risk_per_trade_pct": 0.01, "stop_loss_pct": 0.08,
                "take_profit_pct": 0.3, "max_positions": 4, "trail_atr_mult": 2.5, "lab_enabled": True,
                "entry_rules": [{"id": "price_above_sma", "n": 50}, {"id": "breakout_high", "n": 20, "within": 2}],
                "entry_mode": "all", "exit_rules": [{"id": "close_below_sma", "n": 20}]}},
    {"id": "radar_ml", "name": "Radar altcoinów + ML z laboratorium (krypto, 1 h)", "market": "crypto",
     "strategy": "ml_model",
     "params": {"symbols": [], "universe_mode": "radar", "radar_top": 6, "radar_min_volume": 250000,
                "timeframe": "1Hour", "allocation_pct": 0.5, "risk_per_trade_pct": 0.01, "stop_loss_pct": 0.04,
                "take_profit_pct": 0.08, "max_positions": 4, "ml_confidence": "mid", "ml_horizon": 48,
                "ml_sizing": True, "lab_enabled": True}},
    {"id": "trend_follow", "name": "Trend following - ETF z całego świata (1 dzień)", "market": "stocks",
     "strategy": "rules",
     "params": {"symbols": ["SPY", "QQQ", "DIA", "EFA", "EEM", "EWJ", "TLT", "IEF", "GLD", "SLV", "DBC", "USO",
                            "VNQ", "UUP", "HYG"], "timeframe": "1Day", "allocation_pct": 0.1,
                "risk_per_trade_pct": 0.02, "stop_loss_pct": 0.08, "take_profit_pct": 1.0, "max_positions": 5,
                "regime_filter": False, "trail_atr_mult": 0.0, "poll_seconds": 300,
                "entry_rules": [{"id": "price_above_sma", "n": 200}, {"id": "breakout_high", "n": 50, "within": 1}],
                "entry_mode": "all", "exit_rules": [{"id": "breakdown_low", "n": 20}]}},
    {"id": "copy_funds", "name": "Copy trading - superinwestorzy z raportów 13F (1 dzień)", "market": "stocks",
     "strategy": "copy_funds",
     "params": {"symbols": [], "timeframe": "1Day", "allocation_pct": 0.1, "risk_per_trade_pct": 0.05,
                "stop_loss_pct": 0.35, "take_profit_pct": 2.0, "max_positions": 8, "regime_filter": True,
                "regime_sma_days": 200, "trail_atr_mult": 0.0, "max_hold_days": 0, "poll_seconds": 300,
                "copy_min_bulls": 2, "copy_top": 20, "copy_fresh_days": 45, "copy_trend_sma": 200,
                "copy_exit_on_sell": True}},
    {"id": "ml_etf", "name": "Model ML - ETF sektorowe (1 h)", "market": "stocks", "strategy": "ml_model",
     "params": {"symbols": ["XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLP", "XLU", "XLB", "SMH", "IWM", "SPY"],
                "timeframe": "1Hour", "allocation_pct": 0.5, "risk_per_trade_pct": 0.01, "stop_loss_pct": 0.03,
                "take_profit_pct": 0.06, "max_positions": 4, "ml_confidence": "mid", "ml_horizon": 35,
                "ml_sizing": True}},
]


def full_params(market: str, strategy: str, params: dict) -> dict:
    """Uzupelnia parametry domyslnymi i rzutuje typy."""
    strat = STRATEGIES[strategy]
    out = {"symbols": []}
    out.update(MARKET_DEFAULTS[market])
    out.update(strat.defaults)
    out.update(params or {})
    specs = {s["key"]: s for s in COMMON_PARAMS + strat.params}
    for k, spec in specs.items():
        if k not in out:
            continue
        v = out[k]
        t = spec["type"]
        if t == "int":
            v = int(v)
        elif t in ("float", "pct"):
            v = float(v)
        elif t == "bool":
            v = v if isinstance(v, bool) else str(v).lower() in ("1", "true", "tak", "on")
        elif t == "list":
            v = [s.strip().upper() for s in (v.split(",") if isinstance(v, str) else v) if str(s).strip()]
        elif t == "rules":
            v = R.normalize(v, spec["side"])
        out[k] = v
    if strategy == "copy_funds":             # kopiowanie = filtr funduszy w trybie potwierdzenia
        out["funds_mode"] = "confirm"
        out["funds_min_bulls"] = out["copy_min_bulls"]
        out["insider_mode"] = out.get("insider_mode", "off")
    return out


def validate(market: str, strategy: str, p: dict) -> list:
    errors = []
    if strategy not in STRATEGIES:
        return [f"Nieznana strategia: {strategy}"]
    specs = COMMON_PARAMS + STRATEGIES[strategy].params
    for s in specs:
        v = p.get(s["key"])
        if s["type"] in ("int", "float", "pct") and v is not None:
            if "min" in s and v < s["min"] or "max" in s and v > s["max"]:
                errors.append(f"{s['label']}: wartosc {v} poza zakresem {s.get('min')}-{s.get('max')}")
    if strategy == "copy_funds" and market != "stocks":
        errors.append("Kopiowanie funduszy działa tylko na akcjach (raporty 13F dotyczą spółek z USA).")
    if not p.get("symbols") and not (market == "crypto" and p.get("universe_mode") == "radar") \
            and strategy != "copy_funds":
        errors.append("Podaj co najmniej jeden symbol (albo włącz radar altcoinów dla krypto).")
    is_crypto = [("/" in s) for s in p.get("symbols", [])]
    if market == "crypto" and not all(is_crypto):
        errors.append("Bot krypto przyjmuje tylko pary w formacie BTC/USD.")
    if market == "stocks" and any(is_crypto):
        errors.append("Bot akcyjny nie moze miec par krypto - zaloz osobnego bota krypto.")
    if p.get("timeframe") not in TIMEFRAMES:
        errors.append("Nieprawidlowy interwal.")
    if strategy == "rules":
        if not p.get("entry_rules"):
            errors.append("Dodaj co najmniej jedną zasadę kupna.")
        errors += R.errors(p.get("entry_rules", []), "Kupno") + R.errors(p.get("exit_rules", []), "Sprzedaż")
    if strategy == "sma_cross" and p.get("sma_fast", 0) >= p.get("sma_slow", 0):
        errors.append("SMA szybka musi byc mniejsza od wolnej.")
    return errors


def leverage_warning(p: dict):
    ratio = p["risk_per_trade_pct"] / p["stop_loss_pct"]
    if ratio > 1:
        return (f"Ryzyko/stop = {ratio:.2f}. Pozycja z samego ryzyka bylaby wieksza niz budzet - "
                f"zostanie przycieta limitem budzet/max pozycji, wiec realne ryzyko bedzie nizsze.")
    return None


def sized_notional(budget, p, buying_power):
    """Wartosc pozycji bez ukrytej dzwigni: min(z ryzyka, budzet/max pozycji, sila nabywcza)."""
    by_risk = budget * p["risk_per_trade_pct"] / p["stop_loss_pct"]
    cap = budget / p["max_positions"] * 0.98
    return max(0.0, min(by_risk, cap, buying_power * 0.98))


def schema():
    return {
        "common": COMMON_PARAMS,
        "market_defaults": MARKET_DEFAULTS,
        "strategies": [{"key": s.key, "name": s.name, "description": s.description,
                        "params": s.params, "defaults": s.defaults} for s in STRATEGIES.values()],
        "templates": TEMPLATES,
        "rules": R.catalog(),
        "rule_groups": R.GROUPS,
        "timeframes": TIMEFRAMES,
    }
