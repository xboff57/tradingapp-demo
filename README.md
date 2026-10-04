# TradingApp

Panel do automatycznych botów inwestycyjnych: akcje USA i GPW, ETF-y, kryptowaluty. Działa **na Twoim komputerze** — klucze, hasło i historia nie trafiają na żaden cudzy serwer.

![Pulpit](docs/pulpit.png)

## Pobierz

| | Dla kogo | Plik |
|---|---|---|
| **Pełna wersja** | chcesz podłączyć własne konto (najpierw papierowe) i uruchomić boty | **[TradingApp-Setup.exe](https://github.com/xboff57/tradingapp-demo/releases/latest)** — instalator |
| **Wersja demo** | chcesz tylko obejrzeć program na przykładowych danych | **[TradingApp-Demo.zip](https://github.com/xboff57/tradingapp-demo/releases/latest/download/TradingApp-Demo.zip)** — bez instalacji |

Wszystkie wersje są w zakładce [Releases](https://github.com/xboff57/tradingapp-demo/releases). Działa na Windows 10 i 11 (64-bit). Python i biblioteki są w środku — nic więcej nie trzeba instalować.

> **📘 Pełna instrukcja krok po kroku: [INSTRUKCJA.md](installer/INSTRUKCJA.md)**
>
> Instrukcja obejmuje:
> - instalację,
> - pierwsze logowanie,
> - podłączenie darmowego konta papierowego,
> - pierwszego bota,
> - powiadomienia na telefon,
> - dostęp z telefonu przez Tailscale,
> - aktualizacje i kopie zapasowe,
> - rozwiązywanie problemów.

## Pełna wersja — instalacja w skrócie

1. Pobierz `TradingApp-Setup-X.Y.Z.exe` z [Releases](https://github.com/xboff57/tradingapp-demo/releases/latest) i uruchom go.
   - Jeśli pojawi się „System Windows ochronił ten komputer”, kliknij **Więcej informacji → Uruchom mimo to**.
   - Instalator nie ma płatnego certyfikatu. Buduje go GitHub z kodu w tym repozytorium, a suma SHA256 jest w opisie wydania.
2. Zaakceptuj ostrzeżenia o ryzyku i zostaw zaznaczone opcje:
   - **praca w tle po zalogowaniu**,
   - **nie usypiaj komputera, gdy działa TradingApp**.
   
   Uprawnienia administratora nie są potrzebne.
3. Panel otworzy się w przeglądarce (http://127.0.0.1:8420). **Ustaw login i hasło**, a potem włącz 2FA w zakładce Logowanie.
4. **Konta → + Podłącz platformę**:
   - **Alpaca** w trybie „Na niby” — darmowe konto papierowe na alpaca.markets, akcje USA,
   - albo **Kraken** „Na niby” — krypto, bez kluczy.
5. **Galeria strategii** → wybierz strategię → **Backtest na moich danych** → **Utwórz bota** → **Start**.

Program działa w tle (Menu Start → TradingApp / Zatrzymaj TradingApp).
- **Dane:** `%LOCALAPPDATA%\TradingApp\dane` — aktualizacja i odinstalowanie ich nie ruszają.
- **Aktualizacja:** uruchom nowszy instalator.

## Co potrafi

- **Boty:**
  - konstruktor zasad (wybicia, średnie, RSI, wolumen…),
  - powrót do średniej, trend,
  - **siatka (grid)** i **uśrednianie (DCA)**,
  - kopiowanie funduszy (SEC 13F),
  - **alerty z TradingView** (webhook),
  - uczenie maszynowe.
- **Galeria strategii** z wynikami backtestów na prawdziwych danych. Jednym kliknięciem backtest albo bot.
- **Strategia z opisu słownego (AI):** opisz pomysł po polsku, panel ułoży z niego bota. Wymaga własnego klucza API Claude.
- **Backtesty** z kosztami transakcji, raportem w czasie i propozycjami poprawek sprawdzanymi poza próbą.
- **Platformy:**
  - Alpaca (USA),
  - Kraken i inne giełdy krypto,
  - Interactive Brokers (GPW, USA, waluty).
  
  Wszystkie z trybem papierowym.
- **Ryzyko:**
  - stop-loss u brokera,
  - dzienny limit straty,
  - wyłącznik awaryjny,
  - dźwignia tylko po osobnej zgodzie (hasło i 2FA).
- **Wykresy:** formacje, kalendarz raportów i dywidend, dane finansowe spółek.
- **Powiadomienia na telefon:** ntfy / Telegram oraz strażnik z zewnątrz (healthchecks.io).

![Wykres z formacjami i planem](docs/wykres.png)

## Wersja demo

1. Rozpakuj `TradingApp-Demo.zip` do zwykłego folderu, np. `Dokumenty\TradingApp-Demo`. Nie uruchamiaj programu z wnętrza ZIP-a.
2. Kliknij dwa razy **`Uruchom TradingApp Demo.bat`**.
3. Panel otworzy się pod http://127.0.0.1:8765. Za pierwszym razem przygotowanie przykładowych danych trwa 1–3 minuty.
4. Aby wyłączyć program, zamknij czarne okno. Aby go usunąć, skasuj folder.

Wersja demo ma ograniczenia:
- Działa na koncie **symulowanym** ze 100 000 USD.
- Ceny są generowane przez program. Nazwy spółek to tylko etykiety, a nie prawdziwe notowania.
- Podłączanie kont, powiadomienia, logowanie i aktualizacje są wyłączone.

Demo jest też w pełnej wersji: Menu Start → **TradingApp — wersja demo**.

## Bezpieczeństwo i ryzyko

- To narzędzie, nie porada inwestycyjna. Wyniki z przeszłości nie gwarantują przyszłych. Handel wiąże się z ryzykiem utraty pieniędzy, a z dźwignią — także większych niż wkład.
- Zaczynaj na koncie papierowym. Klucze API twórz **bez prawa wypłaty**.
- Panel słucha tylko na tym komputerze (127.0.0.1). Dostęp z telefonu: **Tailscale**, nigdy przekierowanie portu na routerze.

## Dla zaawansowanych

- **Budowanie:** instalator i paczkę demo buduje i sprawdza GitHub Actions (`.github/workflows/paczka-demo.yml`). Każde wydanie przechodzi automatyczną próbę: instalacja, kreator hasła, panel, zatrzymanie i odinstalowanie bez utraty danych.
- **Uruchomienie bez instalatora:** `pip install -r requirements.txt`, a potem `python -m app.main`. Dla demo dodaj `DEMO=1`.
- **Serwer / NAS (Docker, 24/7):** w przygotowaniu — patrz instrukcja, punkt 14.
- **Opis wszystkich funkcji:** [docs/FUNKCJE.md](docs/FUNKCJE.md).

Biblioteki zewnętrzne i ich licencje: [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
