# TradingApp — instalacja i pierwsze kroki (Windows)

TradingApp to panel do automatycznego handlu (boty) działający **na Twoim komputerze**.
- Klucze do brokerów, hasło i historia zostają u Ciebie — nic nie trafia na cudzy serwer.
- Ta instrukcja prowadzi od pobrania do pierwszego bota na koncie papierowym (na niby).
- Całość zajmie około 15 minut.

> **Najpierw na papierze.** Każdą strategię testuj kilka tygodni na koncie papierowym, zanim użyjesz
> prawdziwych pieniędzy.
> - Wyniki z przeszłości nie gwarantują przyszłych.
> - TradingApp to narzędzie, nie porada inwestycyjna.

---

## 1. Wymagania

- Windows 10 lub 11, 64-bitowy.
- Około 600 MB miejsca na dysku.
- Stały internet.
- Komputer, który może być włączony wtedy, gdy mają działać boty. Boty działają tylko, gdy działa komputer.
  - Na 24/7 lepszy jest stacjonarny komputer, mini-PC albo serwer NAS — patrz [punkt 14](#14-alternatywa-nas-albo-serwer-docker).

Nie trzeba instalować Pythona ani niczego innego. Instalator ma wszystko w sobie.

## 2. Pobranie

1. Wejdź na **https://github.com/xboff57/tradingapp-demo/releases/latest**.
2. W sekcji **Assets** kliknij **`TradingApp-Setup-X.Y.Z.exe`** (X.Y.Z to numer wersji).
3. *(Opcjonalnie)* sprawdź, czy plik nie został podmieniony. W PowerShellu, w folderze Pobrane, wpisz:
   ```
   Get-FileHash .\TradingApp-Setup-X.Y.Z.exe
   ```
   Wynik porównaj z sumą **SHA256** w opisie wydania na GitHubie.

## 3. Ostrzeżenie Windows SmartScreen

Instalator nie ma płatnego certyfikatu podpisu kodu, więc Windows może pokazać okno
**„System Windows ochronił ten komputer”**.

Kliknij **Więcej informacji → Uruchom mimo to**.

Ostrzeżenie oznacza tylko „nieznany wydawca”. Kod programu jest publiczny w tym repozytorium, a instalator buduje się automatycznie na serwerach GitHuba z tego kodu (zakładka **Actions**).

## 4. Instalacja

Instalator nie wymaga uprawnień administratora. Program trafia do `%LOCALAPPDATA%\Programs\TradingApp`.

1. **Przeczytaj ostrzeżenia o ryzyku** i kliknij „Akceptuję”.
2. **Opcje:**
   - **Uruchamiaj w tle po zalogowaniu do Windows** (zalecane) — boty startują same po włączeniu komputera, bez otwierania panelu.
   - **Nie usypiaj komputera, gdy działa TradingApp** (zalecane) — komputer nie zaśnie, dopóki działa program. Ekran może się wygaszać. Ustawienia zasilania Windows nie są zmieniane, a po zamknięciu programu komputer usypia normalnie.
   - **Skrót na pulpicie.**
3. **Zainstaluj → Zakończ.** Panel otworzy się w przeglądarce pod adresem **http://127.0.0.1:8420**.

**Laptop:** zamknięcie pokrywy zwykle usypia komputer, nawet z tą opcją. Jeśli boty mają działać przy zamkniętej pokrywie, ustaw to sam: Panel sterowania → Opcje zasilania → „Wybierz, co ma robić zamknięcie pokrywy” → „Nic nie rób” (przy zasilaniu z sieci).

## 5. Pierwsze uruchomienie: login i hasło

Przy pierwszym otwarciu panel poprosi o **utworzenie loginu i hasła**:
- login: 3–32 znaki,
- hasło: co najmniej 10 znaków.

Zapisz je w menedżerze haseł. Kreator działa tylko z tego komputera.

Zaraz potem włącz **logowanie dwuetapowe**: zakładka **Logowanie → Kod z aplikacji (2FA)**. Zeskanuj kod QR w Google Authenticator, Microsoft Authenticator albo Aegis. **Zapisz kody awaryjne.**

## 6. Podłączenie konta (na niby)

Na start masz wbudowane konto **demo** — symulację ze 100 000 $ wirtualnej gotówki. Do prawdziwych cen polecamy darmowe konta papierowe.

### Alpaca Paper — akcje i ETF-y z USA, za darmo, bez wpłaty

1. Załóż konto na **https://alpaca.markets** (Sign up). Wystarczy e-mail.
2. Po zalogowaniu przełącz się na **Paper Trading** (lewy górny róg).
3. Po prawej **API Keys → Generate New Keys**. Skopiuj **Key** i **Secret** — Secret widać tylko raz.
4. W TradingApp: **Konta → + Podłącz platformę → Alpaca**:
   - nazwa, np. `alpaca`,
   - tryb **Na niby**,
   - wklej klucze,
   - **Sprawdź połączenie → Zapisz konto**.

### Kraken — kryptowaluty, tryb na niby bez kluczy

1. **Konta → + Podłącz platformę → Kraken**.
2. Wybierz tryb **Na niby**, ustaw walutę (EUR) i kapitał na start.

Bot handluje po prawdziwych cenach Krakena, a transakcje są tylko symulowane.

### Konto z prawdziwymi pieniędzmi (później)

Klucze API giełd i brokerów twórz **zawsze bez prawa wypłaty** (bez „Withdraw”) i najlepiej z ograniczeniem do Twojego adresu IP.
- Klucze są zapisane tylko na tym komputerze, zaszyfrowane.
- Dźwignia na prawdziwym koncie wymaga osobnej zgody (hasło i 2FA) w zakładce **Ryzyko**.
- Interactive Brokers (GPW) wymaga dodatkowo programu IB Gateway (zalogowanego na konto) — [opis funkcji](https://github.com/xboff57/tradingapp-demo/blob/main/docs/FUNKCJE.md), rozdział 5e.

## 7. Pierwszy bot z galerii

1. **Analiza → Galeria strategii.** Każda strategia ma opis, poziom ryzyka i wynik testu na danych historycznych (zwrot, maksymalne obsunięcie, porównanie z „kup i trzymaj”).
2. **Backtest na moich danych** — sprawdź strategię w swoim okresie i na swoim źródle danych.
3. **Utwórz bota** → wybierz konto papierowe → **Utwórz bota** → na stronie bota **Start**.
4. Bot sprawdza rynek co kilka minut.
   - Akcje USA handlują od 15:30 do 22:00 czasu polskiego, w dni robocze.
   - Krypto handluje całą dobę.
   - Na stronie bota widać dziennik, pozycje i transakcje.

**Masz własny pomysł?** Wpisz go w polu **„Opisz strategię słowami”** w Galerii, np. „kupuj spółki z S&P 500, gdy kurs przebije maksimum z 50 dni, stop 8%”. Wymaga własnego klucza API Claude — patrz [punkt 10](#10-ustawienia-env).

Inne rodzaje botów:
- siatka i uśrednianie (DCA) — [opis funkcji](https://github.com/xboff57/tradingapp-demo/blob/main/docs/FUNKCJE.md), rozdział 5k,
- alerty z TradingView — [opis funkcji](https://github.com/xboff57/tradingapp-demo/blob/main/docs/FUNKCJE.md), rozdział 5l.

## 8. Codzienna obsługa

Wszystkie skróty są w **Menu Start → TradingApp**:

| Skrót | Do czego |
|---|---|
| **TradingApp** | uruchamia program i otwiera panel; jeśli już działa — tylko otwiera panel |
| **Zatrzymaj TradingApp** | wyłącza program i boty; pozycje u brokera zostają ze stop-lossami |
| **TradingApp — wersja demo** | osobna wersja z przykładowymi danymi (port 8765), do obejrzenia funkcji |
| **Folder danych i ustawień** | baza, logi, plik `.env` |
| **Instrukcja** | ten dokument |

- Program pracuje w tle i nie ma okna. Zamknięcie przeglądarki **nie** zatrzymuje botów.
- Jeśli program wyłączy się przez błąd, uruchamia się ponownie sam (do 5 razy w ciągu 10 minut).

## 9. Powiadomienia na telefon

1. **System → Powiadomienia → Włącz ntfy**.
2. Zainstaluj aplikację **ntfy** (Android / iOS) i zasubskrybuj temat pokazany w panelu. Zamiast tego możesz podłączyć Telegram.
3. Dostaniesz powiadomienia o transakcjach, błędach i dziennych podsumowaniach.

**Strażnik z zewnątrz:** jeśli komputer się wyłączy, sam program nie da znać. Darmowy **healthchecks.io** wyśle Ci SMS albo e-mail, gdy panel przestanie dawać znak życia. Konfiguracja: **System → Strażnik z zewnątrz**.

## 10. Ustawienia (.env)

Plik **`.env`** jest w folderze danych (Menu Start → **Folder danych i ustawień**). Zwykle nic tu nie trzeba zmieniać. Po zmianie: **Zatrzymaj TradingApp**, potem **TradingApp**.

| Ustawienie | Do czego |
|---|---|
| `SEC_USER_AGENT=Imię Nazwisko email` | dane o insiderach i funduszach z SEC (zakładka Sygnały) |
| `ANTHROPIC_API_KEY=` | strategia z opisu słownego (klucz z console.anthropic.com, płatny za użycie, ułamek centa za opis; ustaw tam limit wydatków) |
| `TV_WEBHOOK_URL=` | alerty z TradingView (opis funkcji, rozdział 5l) |
| `PANEL_PORT=8420` | inny port, jeśli 8420 jest zajęty |

## 11. Dostęp z telefonu (bezpiecznie)

**Nigdy nie przekierowuj portu 8420 na routerze.** Użyj Tailscale — prywatnej sieci VPN, darmowej do użytku osobistego:

1. Zainstaluj Tailscale na komputerze i na telefonie, zaloguj się na to samo konto.
2. W panelu administracyjnym Tailscale (login.tailscale.com → **DNS**) włącz **HTTPS Certificates**.
3. Na komputerze, w PowerShellu:
   ```
   tailscale serve --bg 8420
   ```
4. Polecenie pokaże adres w rodzaju `https://twoj-komputer.tail1234.ts.net`. Otwórz go na telefonie.

Działa tylko na Twoich urządzeniach, z certyfikatem HTTPS. Panel nadal słucha tylko lokalnie, więc nie trzeba zmieniać `PANEL_HOST`.

## 12. Aktualizacja, kopia zapasowa, przeniesienie

- **Aktualizacja:**
  1. Pobierz nowszy `TradingApp-Setup-…exe` z Releases i uruchom go.
  2. Instalator sam zatrzyma starą wersję.
  3. Boty, konta, hasło i historia zostają.
  4. Lista zmian: `CHANGELOG.md`.
- **Kopia zapasowa:** skopiuj cały folder `%LOCALAPPDATA%\TradingApp\dane`, najlepiej po zatrzymaniu programu.
- **Przeniesienie na inny komputer:**
  1. Zainstaluj TradingApp na nowym komputerze.
  2. Zatrzymaj go.
  3. Wklej skopiowany folder `dane`.
  4. Uruchom.
- **Odinstalowanie:** Ustawienia Windows → Aplikacje → TradingApp → Odinstaluj. Folder danych zostaje — usuń go ręcznie, jeśli chcesz wszystko skasować.

## 13. Rozwiązywanie problemów

| Problem | Co zrobić |
|---|---|
| Panel się nie otwiera | Menu Start → **TradingApp**. Poczekaj 20 s i odśwież http://127.0.0.1:8420. Logi: folder danych → `tradingapp.log`, `aplikacja_konsola.log`, `launcher.log` |
| „Port zajęty” / inny program na 8420 | w `.env` zmień `PANEL_PORT`, np. na `8430`, i uruchom ponownie |
| Zapomniane hasło lub zgubiony telefon z 2FA | najpierw spróbuj kodu awaryjnego. Jeśli go nie masz — patrz pod tabelą |
| Bot nic nie robi | sprawdź dziennik bota. Najczęściej: rynek zamknięty, brak sygnału albo budżety botów na koncie przekraczają 100% |
| Komputer usypia mimo opcji | laptop z zamkniętą pokrywą — patrz punkt 4. Sprawdź, czy TradingApp działa (Menu Start → TradingApp) |

**Reset hasła i 2FA:** w PowerShellu wpisz:
```
& "$env:LOCALAPPDATA\Programs\TradingApp\python\python.exe" "$env:LOCALAPPDATA\Programs\TradingApp\launcher.pyw" --reset-logowania
```
Potem uruchom TradingApp — kreator poprosi o nowy login i hasło. Boty, konta i historia zostają.

Błąd w programie? Zgłoś go w zakładce **Issues** na GitHubie. Dołącz fragment `tradingapp.log`, ale **bez kluczy API**.

## 14. Alternatywa: NAS albo serwer (Docker)

Jeśli masz serwer NAS (Synology, QNAP, Asustor) albo VPS, TradingApp może działać tam 24/7 w Dockerze. Komputer domowy nie musi być wtedy włączony.
- Gotowa paczka dla Dockera jest w przygotowaniu. Jeśli Cię interesuje, napisz w zakładce **Issues**.
- Dostęp: przez Tailscale.

## 15. Bezpieczeństwo w skrócie

- Hasło panelu i 2FA włączone.
- Klucze API bez prawa wypłaty.
- Panel niewystawiony do internetu — tylko Tailscale.
- Najpierw papier, potem małe kwoty.
- Dzienny limit straty i wyłącznik awaryjny: zakładka **Ryzyko**.
