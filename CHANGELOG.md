# Historia wersji

Numer wersji to **MAJOR.MINOR.PATCH**: MINOR rośnie przy nowych funkcjach, PATCH przy poprawkach, MAJOR przy
zmianach, które wymagają czegoś od Ciebie (np. nowych ustawień w `.env`). Podpisana paczka każdej wersji leży
w folderze `wydania/` jako `tradingapp-X.Y.Z.zip` — tą paczką możesz też **wrócić do starszej wersji**
(System → Wgraj).

Wersje do 1.3.1 miały numery z datą (np. `2026.09.30-11`) — w paczkach tych wersji taki numer nadal widać w panelu.

## 1.27.2
- **Konta → Portfel:** podgląd, co jest na koncie — gotówka, pozycje z wyceną i udziałem, kto je prowadzi (bot albo Ty).
  Przy pozycjach spoza botów jest przycisk „Sprzedaj…” (otwiera zlecenie ręczne z wpisaną ilością).

## 1.27.1
- Zlecenie ręczne: godzina otwarcia sesji pokazywana w czasie polskim (wcześniej Alpaca dawała czas nowojorski z dopiskiem UTC).

## 1.27.0
- **Zlecenie ręczne:** kupno albo sprzedaż kilku akcji lub monet bez bota.
  - Gdzie: przycisk „Kup / sprzedaj” na wykresie i „Zlecenie ręczne” w zakładce Transakcje.
  - Panel pokazuje cenę, koszt, gotówkę i posiadane sztuki.
  - Zlecenie idzie po rynku, bez dźwigni. Akcje: tylko w trakcie sesji, na IBKR w całych sztukach, na Alpace także ułamki.
  - Symbol, którym handluje bot na tym koncie, jest zablokowany, żeby bot nie przejął ręcznie kupionych akcji.
  - Konto z prawdziwymi pieniędzmi: hasło i kod 2FA przy każdym zleceniu.
  - Historia zleceń jest w zakładce Transakcje, a każde wykonane zlecenie przychodzi powiadomieniem.
- **Bot na koncie giełdy krypto** (Binance, Kraken i inne przez CCXT):
  - po wybraniu konta formularz sam przełącza rynek na krypto i zamienia pary na walutę konta (BTC/USD → BTC/USDT);
  - pokazuje budżet bota i kwotę na jedną pozycję oraz ostrzega, gdy wyjdzie poniżej minimum giełdy.
- Start bota na koncie z prawdziwymi pieniędzmi wymaga potwierdzenia.

## 1.26.2
- Szybszy panel na wolnym łączu, np. Tailscale przez przekaźnik, gdy sieć blokuje UDP:
  - kompresja gzip — skrypt panelu 261 → 77 KB, style 36 → 8 KB;
  - pliki z numerem wersji przeglądarka trzyma u siebie do następnej aktualizacji, bez pytania serwera przy każdym otwarciu.

## 1.26.1
- Paski zakładek (Radar, backtest, wykresy…) nie pokazują już na Windowsie zbędnych strzałek przewijania. Na telefonie zakładki nadal przesuwa się palcem.

## 1.26.0
- **Nauka dźwigni** (zakładka Laboratorium, karta „Nauka dźwigni na historii”):
  - Dla każdego bota z sygnałami panel testuje na prawdziwej historii jego wersje z dźwignią i grą na spadki: ETF 2×, margin, spadki. Bot, który już ma dźwignię, testuje też niższą dźwignię albo jej brak.
  - Dane dzielone są na część do nauki (65%) i sprawdzian (35%, niewidziany przy wyborze).
- **Do laboratorium trafiają tylko warianty, które przeszły sprawdzian** (maks. 2 na bota). Tam grają na żywo, na niby.
  - Bota przejmują dopiero po wygranej także na nowych danych: na koncie papierowym same, na prawdziwych po zgodzie.
  - Dźwignia na prawdziwym koncie dodatkowo wymaga zgody w zakładce Ryzyko.
- **Reguły dla dźwigni:**
  - obecna strategia musi sama zarabiać,
  - wariant musi zarabiać więcej,
  - stosunek zysku do obsunięcia może spaść najwyżej o 10%,
  - obsunięcie: do 30% na historii i do 25% na żywo.
  
  Zmniejszenie dźwigni wygrywa, gdy wyraźnie poprawia stosunek zysku do obsunięcia.
- **Harmonogram:** nauka o 01:00 w nocy, jednorazowo albo co tydzień (noc z soboty na niedzielę); wynik przychodzi powiadomieniem.
  - Kolejne nauki nie zerują wariantów, które już grają, a wycofują te, które przestały przechodzić.
- Laboratorium obsługuje boty z dźwignią i wirtualne pozycje na spadek: wynik liczony od własnego kapitału, z prowizjami i odsetkami.
- Warianty dźwigni nie wchodzą już do laboratorium bez sprawdzianu na historii.

## 1.25.1
- **Galeria strategii z wynikami:** 9 backtestów na prawdziwych danych (IBKR, Alpaca, 2021–2026, z kosztami), z wykresem kapitału i porównaniem z „kup i trzymaj”.
- **Instalator Windows** (pełna wersja, w repozytorium tradingapp-demo):
  - kreator loginu i hasła przy pierwszym uruchomieniu,
  - praca w tle z autostartem,
  - opcja „nie usypiaj komputera”,
  - pełna instrukcja (`installer/INSTRUKCJA.md`).
- Pulpit: karta „Pierwsze kroki”, gdy nie ma jeszcze botów.
- Raport backtestu siatki i DCA bez wskazówek o stop-lossie i take-proficie. „Propozycje poprawek” są dla nich wyłączone.
- Siatka buduje się od nowa po zmianie ustawień (gdy nie trzyma porcji). Ustawienia AI z błędem walidacji pokazują pełne parametry.
- `ENV_FILE` — ścieżka do pliku `.env` (używana przez instalator).

## 1.25.0
- **Siatka (grid)** dla krypto i **uśrednianie (DCA)**: regularne albo na spadkach. Działają na żywo i w backteście, a na stronie bota widać stan siatki lub średniej.
- **Alerty z TradingView:**
  - nowa strategia „Alerty z TradingView (webhook)”,
  - osobny odbiornik na porcie 8421 (do wystawienia przez Tailscale Funnel),
  - adres i treść alertu do skopiowania, tabela ostatnich alertów.
- **Galeria strategii:** 9 gotowych ustawień z wynikami backtestów; jednym kliknięciem backtest albo bot.
- **Strategia z opisu słownego (AI):** opis po polsku zamieniany na ustawienia bota (wymaga własnego `ANTHROPIC_API_KEY`).
- Formularz bota dla siatki i DCA pokazuje tylko potrzebne pola. Okno edycji zamyka się przy przejściu na inną stronę.
- `docker-compose.yml`: nowy port 8421, nowe opcje w `.env.example`.

## 1.24.1
- ETF-y: poprawiona dźwignia TSLQ i CONI (−2×, sprawdzone u brokera). Na liście jest 100 ETF-ów potwierdzonych w Alpace i IBKR.

## 1.24.0
- **Dźwignia i gra na spadki:**
  - tryb ETF-ów lewarowanych i odwrotnych (sygnał na spółce, zakup ETF-u 2× albo odwrotnego),
  - margin z krótką sprzedażą akcji (Alpaca, IBKR, symulator),
  - krypto z dźwignią na Krakenie (na żywo i na niby, z opłatami Krakena).
- Kierunek gry: wzrost, spadek albo oba. Sygnał spadku to lustrzane odbicie zasad strategii.
- Backtest liczy krótkie pozycje, dźwignię, koszty pożyczki i ETF-y odtworzone ze spółki.
- **Nowa zakładka „Ryzyko”:**
  - wyłącznik nowych wejść,
  - zamknięcie wszystkich pozycji z dźwignią,
  - dzienny limit straty na konto,
  - zgoda na dźwignię dla kont z prawdziwymi pieniędzmi (hasło i 2FA),
  - limit dźwigni na konto,
  - lista ETF-ów ze sprawdzaniem u brokera.
- Transakcje i wykresy rozróżniają sprzedaż krótką i odkupienie. Powiadomienia mówią o grze na spadek.

## 1.23.0
- **Kalendarz raportów i dywidend:**
  - nowa zakładka „Kalendarz” z danymi z Yahoo Finance; historia raportów USA pochodzi z SEC,
  - znaczniki R / D na wykresie,
  - data najbliższego raportu i dywidendy w karcie „Finanse”,
  - powiadomienie dzień wcześniej o spółkach, które trzymają boty.
- **Blokada zakupów przed raportem:** ustawienie bota „Bez zakupów przed raportem (dni)”, domyślnie wyłączone.
- **Strażnik z zewnątrz (healthchecks.io):** znak życia co 5 min, a przy problemach ping `/fail` z opisem.
- Karta „Finanse”: poprawiony okres ostatniego raportu SEC.

## 1.22.0
- **Karta „Finanse” na wykresie:** publiczne dane finansowe spółki lub kryptowaluty.
  - Wycena: kapitalizacja, C/Z, C/WK, C/P, marża, ROE, dług, FCF, dywidenda.
  - Ostatnie 8 kwartałów (przychody i zysk netto) oraz wyniki roczne z 5 lat.
  - Źródła:
    - akcje USA: SEC EDGAR, czyli raporty 10-K / 10-Q,
    - GPW, ETF-y i inne giełdy: Yahoo Finance,
    - krypto: CoinGecko.
  - Dane są trzymane 12 h w pamięci podręcznej, a przycisk „Odśwież” pobiera je od nowa.
- Nowa biblioteka: `yfinance` (aktualizacja przebuduje kontener, co potrwa kilka minut dłużej).

## 1.21.1
- Bramka IBKR w weekend (pt 23:00 – pn 06:00):
  - bez automatycznych restartów i bez powiadomień, ani o bramce, ani o błędach botów na IBKR,
  - zostaje tylko wpis w historii,
  - przełącznik „Wstrzymaj w weekend” w zakładce Bramka.
  
  Jeśli w poniedziałek o 6:00 połączenia nadal nie ma, następuje zwykły restart przed sesją.

## 1.21.0
- **Wersja demonstracyjna** (`DEMO=1`), żeby pokazać aplikację innym osobom:
  - konto symulowane i przykładowe boty, transakcje, backtesty oraz radary,
  - pasek „Wersja demonstracyjna”,
  - zablokowane klucze, bramka, powiadomienia, logowanie i aktualizacje,
  - przycisk „Przywróć dane demo”.
- Przenośna paczka demo dla Windows: `tools/zbuduj_demo.ps1` i folder `demo/` (uruchamiany jednym kliknięciem).

## 1.20.0
- Formacje: dodane potrójny szczyt / dno, spodek, kliny (rosnący i opadający), prostokąt, flagi i chorągiewki,
  filiżanka z uszkiem, luki cenowe oraz świece: wisielec, harami hossy / bessy, trzech białych żołnierzy, trzy czarne wrony.
- **„Wybierz formacje”**: każdą formację, wsparcia i opory, linie trendu i wskaźniki można włączyć lub wyłączyć
  (przyciski Wszystkie / Żadne / Domyślne); wybór jest zapamiętywany, a ocena przewagi liczy tylko włączone sygnały.

## 1.19.0
- **Formacje i sygnały na wykresie** (przełącznik „Formacje i poziomy”): wsparcia i opory z liczbą dotknięć, linie trendu,
  formacje cenowe (podwójny szczyt / dno, głowa z ramionami i odwrócona, trójkąty, wybicie oporu / przebicie wsparcia)
  z potwierdzeniem i zasięgiem, formacje świecowe z ostatnich sesji (doji, młot, spadająca gwiazda, objęcia, gwiazdy
  poranna / wieczorna) ze skutecznością na danym symbolu, wskaźniki (RSI, dywergencja, SMA 200, złoty krzyż, wolumen)
  i podsumowanie: przewaga wzrostowa / spadkowa / mieszana.

## 1.18.0
- **Plan i przykładowe ruchy na wykresie**: wybierz strategię (domyślnie „Wybicie 34 dni + SMA 200” jak bot GPW albo
  ustawienia dowolnego bota). Wykres pokazuje, gdzie strategia kupiłaby (● W) i sprzedała (● z wynikiem %), a karta planu
  — co dziś: sygnał kupna z poziomami stop-loss i take-profit, poziom wybicia, na który czeka, albo poziom wyjścia
  z przykładowej pozycji. Do tego statystyka przykładowych ruchów na danym symbolu.

## 1.17.0
- **Radar spółek**: zakładka Radar ma podstrony Przegląd, Altcoiny, GPW i USA. Ranking spółek (GPW ok. 90 spółek z danych
  IBKR, USA ok. 100 dużych spółek z Alpaki): siła względem indeksu, momentum, obrót, bliskość szczytu z 52 tygodni, trend.
  Oznaczenie „sygnał” = wybicie nad maksimum z 34 dni przy cenie nad SMA 200. Mało płynne spółki są pomijane.
- **Przegląd** zbiera najlepsze propozycje z trzech radarów i dzisiejsze sygnały wybicia.
- Skan spółek odbywa się sam po każdej sesji (GPW ok. 17:30, USA ok. 22:30) albo przyciskiem „Skanuj teraz”.
- Bot GPW agresywny: lista rozszerzona z 44 do 69 spółek (backtest: podobny zysk, mniejsze obsunięcia).

## 1.16.0
- Konstruktor zasad: nowa zasada kupna **„Płynność: średni obrót co najmniej X”** — pomija małe, mało płynne spółki
  (np. przy szerokiej liście GPW).

## 1.15.0
- **Okazje na telefon** (opcja bota „Powiadamiaj o okazjach do ręcznego kopiowania”): przy każdym kupnie i sprzedaży bota
  przychodzi powiadomienie z ceną, stop-lossem i — po podaniu „Twojego kapitału” — kwotą i liczbą akcji dla Ciebie
  (w tej samej proporcji, w jakiej bot kupił ze swojego budżetu).
- Nowy kanał powiadomień **ntfy** (darmowa aplikacja na iPhone i Androida, bez konta): System → Powiadomienia → „Włącz ntfy”,
  kod QR i prywatny temat. Telegram działa jak dotąd; oba kanały mogą działać jednocześnie.

## 1.14.0
- **Ułamki akcji** (opcja bota „Ułamki akcji — kupuj za kwotę”): na Alpace bot kupuje akcje i ETF-y z USA za wyliczoną
  kwotę, także gdy nie starcza na całą akcję (np. 100 $ akcji po 300 $). Stop-loss i take-profit takich pozycji pilnuje bot.
  Backtest liczy wtedy ułamki. Bez tej opcji dziennik podpowiada ją, gdy budżet nie wystarcza na 1 akcję.

## 1.13.0
- Nowa zakładka **Wykresy**: świece dowolnej spółki, ETF-u, waluty albo kryptowaluty (15 min, 1 h, 4 h, 1 dzień)
  z wolumenem i średnimi SMA 20/50/200. Na świecach strzałki zakupów i sprzedaży botów (z wynikiem w %), a dla
  otwartej pozycji linie wejścia, stop-lossa i take-profitu. Źródło danych dobierane po symbolu (GPW/Xetra/waluty → IBKR,
  USA → Alpaca, krypto → Kraken/giełda konta) albo wybrane ręcznie.
- Symbol w tabelach transakcji otwiera jego wykres.

## 1.12.0
- Nowa szata graficzna „terminal tradera”: ciemny granat, bursztyn dla przycisków i aktywnego menu, zieleń i czerwień
  tylko dla zysku i straty. Kroje IBM Plex Sans i IBM Plex Mono (cyfry), wbudowane w aplikację (działa bez internetu).
- Menu z boku, pogrupowane: Handel, Analiza, Ustawienia; na telefonie rozwijane przyciskiem „Menu”.
- Pulpit: karty kont z dziennym wynikiem w zł/$ i %, strzałką kierunku i mini-wykresem kapitału.
- Wykresy, tabele i formularze w nowym stylu; kwoty w tabelach w czcionce o stałej szerokości.

## 1.11.1
- Dziennik bramki: maskowanie wszystkich numerów kont IBKR (także kont papierowych z literami przed cyframi).

## 1.11.0
- Nowa zakładka **Bramka**: stan IB Gateway (kontener, połączenie aplikacji, połączenie z serwerami IBKR), restart /
  zatrzymanie / uruchomienie z panelu, historia zdarzeń i dziennik bramki (z zamaskowanymi numerami kont).
- Automatyczny restart bramki po dłuższej utracie połączenia (domyślnie 15 min, z limitami) + powiadomienie.
- Updater wykonuje polecenia dla bramki (aplikacja nadal nie ma dostępu do Dockera).

## 1.10.0
- Nowa zakładka **Logowanie**: własny login i hasło (zamiast hasła z `.env`), kod z telefonu (2FA, TOTP) z kodem QR
  i 10 kodami awaryjnymi, klucze dla skryptów (`PANEL_API_TOKEN`).
- Logowanie w dwóch krokach, gdy 2FA jest włączone. Zmiana hasła wylogowuje inne urządzenia.
- Awaryjny reset z konsoli NAS-a: `python -m app.auth reset`.

## 1.9.1
- Zakładka Konta od nowa: konta jako karty, czytelny wybór platformy, tryb konta do kliknięcia (na niby / sieć testowa /
  prawdziwe pieniądze), pola kluczy tylko tam, gdzie są potrzebne, krótsze opisy i podpowiedzi pod polami.

## 1.9.0
- Konta: nowa platforma **„Inna giełda krypto (CCXT)”** — Binance, Bybit, OKX, Coinbase, KuCoin, Bitget, Gate,
  Bitstamp i każda inna giełda z biblioteki CCXT. Tryb na niby (bez kluczy), sieć testowa giełdy albo prawdziwe konto.
  Passphrase (OKX, KuCoin, Bitget) zapisywana zaszyfrowana.
- Giełdy bez zleceń stop: stop-loss pilnuje bot co cykl.
- Backtesty i radar altcoinów na danych podłączonych giełd.
- Pulpit i dziennik pokazują walutę konta (np. PLN na IBKR, EUR na Krakenie) zamiast zawsze USD.

## 1.8.0
- Termometry rynku: filtr **szerokości rynku** (odsetek spółek z listy bota nad średnią, np. 200-dniową) i **drugi
  symbol rynku** (np. SPY obok ETF-u na WIG20). Działają w backteście, laboratorium i u bota na żywo.
- Analiza poprawek proponuje włączenie / złagodzenie / zaostrzenie filtra szerokości.

## 1.7.1
- IBKR: zlecenie odrzucone przez IB (np. bramka w trybie tylko do odczytu) jest zgłaszane jako błąd, a nie zapisywane jako transakcja.
- Bot nie uznaje pozycji za zamkniętą przez stop, dopóki jego zlecenie kupna czeka na realizację.

## 1.7.0
- IBKR: giełda niemiecka Xetra (symbole `SAP.DE`, `ALV.DE`, w EUR).
- IBKR: kursy walut do przeliczeń pobierane w konwencji IB (mniej błędów w dzienniku).

## 1.6.5
- IBKR: poprawka cyklu bota (część zapytań IB zwraca Future, nie korutynę) — bot na GPW działa.
- Analiza poprawek nie proponuje filtrów SEC (insiderzy, 13F) dla spółek spoza USA.

## 1.6.4
- ML: odporność na cechy bez zmienności (np. krótka historia indeksu rynku) — model nie przerywa backtestu.
- IBKR: równoległe i powtórzone zapytania o historię łączone w jedno (mniej blokad po stronie IB).

## 1.6.3
- IBKR: symbol nieznany w IBKR jest pomijany (reszta listy działa), zamiast przerywać backtest lub cykl bota.

## 1.6.2
- IBKR: osobny cache danych dla ML (nie miesza się z Alpaką), koszt transakcji 0,1% w backtestach i ML.

## 1.6.1
- Test konta IBKR pokazuje walutę bazową konta.

## 1.6.0
- **Interactive Brokers**: nowy broker przez IB Gateway — akcje z GPW (`PKN.WSE`), waluty (`EUR.USD`) i akcje z USA.
  Zlecenia z SL/TP po stronie IBKR, godziny sesji z kalendarza IBKR (ze świętami), wielkość pozycji przeliczana
  na walutę konta, backtesty na danych IBKR. Konto dodajesz w zakładce Konta.

## 1.5.0
- Nowa zakładka **Konta**: podłączanie platform z panelu (Alpaca, Kraken, symulator; Interactive Brokers w przygotowaniu)
  zamiast dopisywania kluczy do `.env`. Test połączenia przed zapisem, klucze szyfrowane w bazie, panel nigdy ich nie
  pokazuje, zmiany kont wymagają hasła do panelu.

## 1.4.1
- Modele ML i laboratorium działają po przeniesieniu aplikacji na inny komputer (np. z Windowsa na NAS).
- Aktualizator na NAS-ie sprawdza panel także przy sieci hosta (`network_mode: host`).

## 1.4.0
- Numeracja wersji MAJOR.MINOR.PATCH zamiast daty.
- W folderze `wydania/` zostają paczki wszystkich wersji (nazwane numerem wersji), a nie tylko najnowsza.
- Panel ostrzega, gdy wgrywana paczka to starsza wersja (powrót), i zapisuje to w dzienniku.

## 1.3.1 (dawniej 2026.09.30-11)
- Copy trading jako „klon funduszy”: min. 2 fundusze, filtr SMA200, trzymanie do sprzedaży przez fundusze.
- Pomijanie raportów 13F starszych niż ok. 200 dni (fundusz przestał raportować).

## 1.3.0 (dawniej 2026.09.30-10)
- Nowa strategia **Kopiowanie funduszy (13F superinwestorów)** — copy trading.
- Szablon **Trend following - ETF z całego świata**.
- Ceny akcji z Alpaki skorygowane o splity i dywidendy (wcześniej split wyglądał jak krach).

## 1.2.0 (dawniej 2026.09.30-9)
- Niezawodność: ponawianie zamiast zatrzymania bota, strażnik Windows, powiadomienia Telegram, czyszczenie cache.

## 1.1.0 (dawniej 2026.09.30-8)
- Podpisane wydania (Ed25519): aplikacja instaluje tylko paczki podpisane kluczem autora.

## 1.0.0 (dawniej 2026.09.30-7)
- Pierwsza wersja w repozytorium: panel botów, backtesty, ML, laboratorium, Kraken, radar altcoinów.
