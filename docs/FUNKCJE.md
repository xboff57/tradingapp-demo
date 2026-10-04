# TradingApp — opis funkcji

Szczegółowy opis zakładek i strategii. Instalacja i pierwsze kroki: [INSTRUKCJA.md](../installer/INSTRUKCJA.md).

## 3. Jak działają boty

- **Budżet**: każdy bot ma % kapitału konta. Suma budżetów działających botów na jednym koncie ≤ 100%.
- **Bez wspólnych symboli**: dwa działające boty na tym samym koncie nie mogą handlować tym samym symbolem.
- **Wielkość pozycji**: `min(budżet × ryzyko / stop, budżet / max pozycji, dostępna gotówka)` —
  bez ukrytej dźwigni (to był błąd starego scalpera).
- **Ochrona na serwerze**:
  - akcje — zlecenie *bracket* (stop-loss i take-profit leżą u brokera),
  - krypto — *stop-limit* u brokera; take-profit i wyjście z sygnału pilnuje bot.
  Wyłączenie aplikacji nie zostawia pozycji bez stopu.
- **Restart**: boty, które działały, wracają same po ponownym uruchomieniu aplikacji.
- **Stop** zostawia pozycje (ze stopami). **Stop i zamknij pozycje** sprzedaje je po rynku.
- Po 10 błędach z rzędu bot sam się zatrzymuje (status „błąd”).

## 4. Backtesty, raport i propozycje poprawek

Każdy backtest ma trzy zakładki:

- **Wyniki** — zwrot, obsunięcie, Sharpe, skuteczność, wykres vs „kup i trzymaj”, wyniki per symbol, transakcje.
- **Raport w czasie** — czy wynik jest powtarzalny, a nie zasługą jednego okresu:
  wynik w kolejnych okresach (ok. kwartały) obok „kup i trzymaj”, tabela zwrotów miesięcznych,
  porównanie 1. i 2. połowy testu, wykres obsunięcia i czas „pod wodą”, koszty vs zysk brutto,
  sposób zamykania pozycji, czas trzymania, dzień tygodnia i godzina wejścia, symbole w obu połowach.
  Na górze **wnioski** z liczbami i sugestią, co sprawdzić.
- **Propozycje poprawek** — ok. 15–20 wariantów, każdy zmienia **jedną** rzecz (stop, TP, liczba pozycji,
  filtr reżimu, interwał, parametry strategii, usunięcie stratnych symboli). Każdy jest sprawdzany w czasie:
  - **próba** — pierwsze 70% okresu: tu wariant musi być lepszy,
  - **poza próbą** — ostatnie 30%, których nie użyto do wyboru: tu musi się utrzymać,
  - **okresy** — musi być lepszy w co najmniej 60% kolejnych okresów,
  - obsunięcie nie gorsze o więcej niż 25%, min. 10 transakcji.
  Spełnia wszystko → **potwierdzona**. Na koniec potwierdzone zmiany z różnych grup są łączone i sprawdzane tak samo.
  Jednym kliknięciem uruchamiasz pełny backtest z poprawką albo tworzysz z niej bota.
  Symbole „stratne” do usunięcia wybierane są tylko z próby — dane poza próbą zostają nietknięte.

Raport drukujesz albo zapisujesz jako PDF przyciskiem **Drukuj / PDF** (drukuje otwartą zakładkę).

Założenia symulacji: wejście po zamknięciu świecy z sygnałem, SL/TP sprawdzane na high/low, SL wygrywa, gdy
obie granice w jednej świecy; koszt 0,05% (akcje) / 0,25% (krypto) na stronę; filtr reżimu z dnia poprzedniego.
Dane z Alpaca są zapisywane w `data/cache`. Limity okresu: 1 min — 60 dni, 5 min — 240 dni, 15 min — 2 lata.
Dla wiarygodnych propozycji testuj co najmniej rok (daje ok. 4 okresy kwartalne).

## 4b. Konstruktor zasad i wyjścia z pozycji

Strategia **„Konstruktor zasad”** to własna strategia złożona z gotowych zasad, bez pisania kodu. Zasady wybierasz
w ustawieniach bota albo backtestu („+ dodaj zasadę…”), a każdą z nich możesz dostroić.

**Zasady kupna** (łączone: *wszystkie naraz* albo *dowolna*):

- trend: cena nad SMA, szybka SMA nad wolną, złoty krzyż, rosnąca EMA, ADX > 25 z +DI > −DI,
- wybicie: zamknięcie nad maksimum z N świec, blisko maksimum 52-tygodniowego,
- momentum: przecięcie MACD, MACD > 0, RSI poniżej / powyżej progu, wzrost o X% w N świec,
- wolumen i zmienność: skok wolumenu, zwężenie wstęg Bollingera, cena pod dolną wstęgą,
- cena: cofnięcie pod krótką średnią, świeca wzrostowa nad maksimum poprzedniej.

**Zasady sprzedaży** (wystarczy dowolna):

- cena pod SMA,
- szybka SMA pod wolną,
- spadek EMA,
- słabnący trend (ADX),
- MACD w dół,
- zamknięcie pod minimum z N świec,
- RSI wykupiony / słabnący,
- górna wstęga Bollingera,
- powrót do średniej.

Zasady „zdarzeniowe” (przecięcie, wybicie) mają parametr „w ciągu ostatnich X świec”. Dzięki temu da się je łączyć
z warunkami trendu. Wszystkie zasady liczą się wyłącznie z zamkniętych świec, bez zaglądania w przyszłość (sprawdzone
testem). Szablony na start:

- „Konstruktor – trend + wybicie (akcje, 1 dzień)”,
- „Konstruktor – korekta w trendzie (ETF, 1 h)”.

Propozycje poprawek dla konstruktora same sprawdzają w czasie trzy rodzaje zmian:

- usunięcie każdej zasady,
- zmianę jej głównego parametru o ±50%,
- dołożenie typowych filtrów (SMA 200, wolumen, ADX).

**Wyjścia z pozycji (dla każdej strategii, sekcja „Wyjścia z pozycji”):**

- **stop kroczący** X × ATR pod najwyższą ceną od wejścia,
- **stop na wejściu** po zysku X%,
- **limit czasu** trzymania pozycji (dni).

Pilnuje ich bot w każdym cyklu. Twardy stop-loss zostaje na serwerze brokera jako zabezpieczenie na wypadek
wyłączonego komputera. W backteście stop liczony jest z danych do poprzedniej świecy i sprawdzany na bieżącej.

## 4a. Raporty z działania botów (zakładka „Raporty”)

Raporty powstają automatycznie:
- **dzienny** — codziennie po zamknięciu sesji (22:30 czasu PL), dla botów aktywnych w ciągu dnia,
- **tygodniowy** — w sobotę rano, za ostatnie 7 dni,
- **podsumowanie działania** — przy każdym zatrzymaniu bota, od jego uruchomienia
  (zmiana ustawień działającego bota nie tworzy raportu),
- **na żądanie** — przycisk „Generuj raport” (24 h, 7 dni, 30 dni, od uruchomienia albo wybrane daty).

Każdy raport: wynik w $ i w % budżetu, porównanie z „kup i trzymaj” tych samych symboli, skuteczność, średni zysk/strata,
profit factor, obsunięcie, czas w rynku; wykresy wyniku, rynku, dziennego P/L, wyniku per symbol i zaangażowania;
sposób zamykania pozycji, otwarte pozycje, błędy i blokady z dziennika, lista transakcji oraz **zgodność z backtestem**
(ten sam okres i ustawienia przepuszczone przez symulację — czy bot na żywo robi to samo). Na górze wnioski.
Raport drukujesz lub zapisujesz jako PDF przyciskiem „Drukuj / PDF”.

## 5. Sygnały zewnętrzne — „drugie zdanie” dla bota

Bot akcyjny może łączyć swój sygnał techniczny z sygnałami z SEC (sekcja „Sygnały zewnętrzne” w ustawieniach bota):

- **Insiderzy (Form 4)** — zakupy i sprzedaże akcji własnej spółki przez zarząd i dyrektorów (tylko transakcje rynkowe,
  bez opcji i grantów). Najmocniejszy sygnał: kilku insiderów kupuje w krótkim czasie.
- **Fundusze (13F)** — czy wybrani zarządzający (domyślnie Druckenmiller, Tepper, Ackman; zmiana w `FUND_MANAGERS`)
  w ostatnim znanym raporcie dokupili, czy sprzedali daną spółkę. Opóźnienie do 45 dni.

Tryby: **wyłączone**, **weto** (nie kupuj, gdy źródło mówi „nie”), **potwierdzenie** (kupuj tylko, gdy źródło mówi „tak”).
Liczy się data złożenia zgłoszenia w SEC — backtest nie „widzi” przyszłości. Dane są zapisywane w bazie; pierwsze
pobranie dużej spółki (np. NVDA) trwa do minuty, kolejne są natychmiastowe.

Działa tylko dla akcji pojedynczych spółek z USA — **ETF-y nie mają insiderów**. Raport backtestu pokazuje, ile sygnałów
filtr zablokował, a zakładka „Propozycje poprawek” sprawdza w czasie, czy filtr faktycznie pomaga (warianty z filtrem i bez).
Podgląd danych: strona **Sygnały** w panelu.

Wymaga w `.env`: `SEC_USER_AGENT=Imię Nazwisko email` (SEC blokuje zapytania bez tego). Opcjonalnie `OPENFIGI_API_KEY`
(darmowy, szybsze dopasowanie spółek z raportów 13F). Bez `SEC_USER_AGENT` panel pokazuje dane demo.

## 5a. Uczenie maszynowe (zakładka „ML”)

Model nie handluje „z głowy”. Uczy się na historii odpowiedzi na jedno pytanie: *jeśli kupię na zamknięciu tej świecy,
z tym stop-lossem i tym take-profitem, to czy trafię TP, zanim trafię SL?* Patrzy na ok. 20 cech liczonych z zamkniętych
świec (zmiany ceny, zmienność, RSI, odległość od średnich, wolumen, godzina, stan rynku z dnia poprzedniego).
Silnik: drzewa decyzyjne (scikit-learn, HistGradientBoosting). Działają na zwykłym procesorze, na Windowsie i na NAS-ie.

Trzy sposoby użycia (sekcja „Uczenie maszynowe (ML)” w ustawieniach bota):

- **Filtr ML** — strategia proponuje wejście, a model odrzuca te, które mają za małą szansę.
- **Strategia „Model ML”** — model sam wybiera wejścia i wychodzi, gdy jego pewność spada (plus SL/TP).
  Szablon: „Model ML – ETF sektorowe”.
- **Wielkość pozycji wg pewności** — przy progu pół pozycji, przy pewności o 10 pp wyższej pełna.
  Nigdy więcej niż bez ML, bo limity ryzyka zostają te same.

**Próg wejścia nie jest „na oko”.** Próg opłacalności p* = (SL + 2 × koszt) / (TP + SL) to szansa, przy której wejście
wychodzi na zero (np. SL 3%, TP 6% → ok. 34%). Do tego dodawany jest margines: niska pewność = +0 pp, średnia = +5 pp,
wysoka = +10 pp.

**Uczciwość testów.** W backteście model uczy się **krocząco**: każdy miesiąc ocenia model uczony wyłącznie na danych
sprzed tego miesiąca. Przykłady, których wynik nie był jeszcze znany w chwili nauki, są wyrzucane. Wyniki backtestu
z ML są więc wynikami „poza próbą”. Karta „Model ML w tym teście” pokazuje:

- **AUC** — 0,50 to rzut monetą, od ok. 0,55 model realnie coś rozróżnia,
- trafność wejść powyżej progu w porównaniu ze średnią,
- czy przewidywane szanse się sprawdzały,
- na które cechy model patrzy najbardziej.

Propozycje poprawek porównują warianty z filtrem ML i bez niego oraz z inną wymaganą pewnością.

**Bot na żywo — model czeka na Twoją akceptację:**

1. Pierwszy model dla bota z ML uczy się sam kilka minut po zapisaniu. Nowy model powstaje też po zmianie SL, TP,
   interwału albo horyzontu, bo stary uczył się na innych ustawieniach.
2. Co niedzielę od 7:00 działające boty dostają nowy model uczony na świeżych danych.
3. Każdy nowy model przechodzi **test w czasie** na ostatnich ~180 dniach: bot z ML kontra bot bez ML. Model musi być
   lepszy w próbie i poza próbą, w ≥60% okresów, bez obsunięcia większego o ponad 25%, i mieć AUC ≥ 0,52.
   Strategia „Model ML” musi po prostu zarabiać w tych samych warunkach.
4. Zakładka **ML** pokazuje kandydatów z pełnym porównaniem. Aktywny model zmienia się **tylko po kliknięciu „Zatwierdź”**.
   Model, który nie przeszedł testu, możesz zatwierdzić „mimo to”, ale panel o tym ostrzeże.
5. Bez zatwierdzonego modelu bot z filtrem ML albo strategią ML **nie otwiera nowych pozycji**, a otwarte chroni SL/TP.
   Sama „wielkość pozycji wg pewności” działa wtedy po staremu.

Uczenie idzie w tle, jeden model naraz; na procesorze trwa zwykle od kilkunastu sekund do kilku minut. Pliki modeli
leżą w `data/models/` (10 ostatnich na bota + aktywny). Na danych symulowanych (konto demo) model **nie ma czego się
nauczyć** — rynek syntetyczny jest losowy, więc AUC wyjdzie ok. 0,5, a modele zostaną odrzucone. To dobry dowód, że
testy działają. Realną wartość ML sprawdzisz dopiero backtestem na danych Alpaca.

## 5b. Kraken, radar altcoinów i laboratorium

**Kraken (przez CCXT).** W `.env` dopisz konto (wzór jest w `.env.example`):

```
ACCOUNTS=demo,win,kraken
ACCOUNT_KRAKEN_TYPE=kraken
ACCOUNT_KRAKEN_KEY=...
ACCOUNT_KRAKEN_SECRET=...
ACCOUNT_KRAKEN_PAPER=true        # true = na niby po prawdziwych cenach; false = prawdziwe pieniądze
ACCOUNT_KRAKEN_QUOTE=EUR         # bot handluje parami XXX/EUR
ACCOUNT_KRAKEN_START=10000       # kapitał trybu na niby
```

- Kraken nie ma konta demo dla rynku spot, więc tryb „na niby” liczy aplikacja: prawdziwe ceny Krakena i prowizja 0,40%.
  Klucze nie są do tego potrzebne.
- **Klucz API: tylko handel, bez wypłat**, najlepiej z ograniczeniem do Twojego IP. Uprawnienia:
  - Query Funds,
  - Query Open Orders & Trades,
  - Create & Modify Orders,
  - Cancel/Close Orders.
- Bot na Krakenie zarządza **tylko pozycjami, które sam otworzył**. Twoje własne monety na giełdzie zostają w spokoju.
  Stop-loss leży na giełdzie (stop-loss-limit), a take-profit i stop kroczący pilnuje bot.
- **Historia świec jest krótka:** 720 ostatnich świec na interwał (1 h ≈ 30 dni, 4 h ≈ 120 dni, 1 dzień ≈ 2 lata).
  Aplikacja dokleja kolejne świece, więc historia rośnie z czasem. Model ML dla bota Krakena uzupełnia brakującą
  historię z Alpaki (ta sama moneta w parze /USD), jeśli jest.
- Panel pokazuje kwoty ze znakiem „$”. Dla konta Kraken /EUR są to euro.

**Radar altcoinów (zakładka „Radar”).** Codzienny ranking ~40 najpłynniejszych monet giełdy, bez stablecoinów.
Wynik 0–100 składa się z czterech części:

- 40% — siła względem BTC z 30 dni,
- 30% — momentum z 7 dni,
- 20% — wzrost obrotu,
- 10% — cena nad SMA 50.

Bot krypto z opcją **„Wybór monet: radar”** handluje N najlepszymi z ostatniego skanu i zawsze pilnuje monet, które już ma.
Przy każdej monecie radar pokazuje **trend**:

- wzrostowy — cena nad SMA 50, SMA 20 nad SMA 50 i dodatnie nachylenie z 30 dni,
- spadkowy — odwrotnie,
- boczny — pozostałe przypadki.

Obok jest **statystyka „co bywało dalej”**: jak często i o ile moneta rosła w ciągu 7 i 30 dni w przeszłości, gdy była
w takim samym trendzie jak dziś (z ok. 2 lat notowań), w porównaniu z wynikiem bez względu na trend. Na górze widać
podsumowanie rynku (ile monet w którym trendzie, trend BTC). Kliknięcie monety pokazuje wykres z **lejkiem możliwych
cen na 30 dni**: środek to mediana historyczna, pasma to 1 i 2 odchylenia wg zmienności z 30 dni. To mapa ryzyka,
a nie prognoza ceny. Jeśli wynik „w trendzie” jest podobny do „bez względu na trend”, trend niewiele mówi o przyszłości.
W backteście ranking liczony jest co tydzień, tylko z danych sprzed danego dnia. Lista kandydatów to jednak monety
notowane dziś, więc wynik jest nieco zawyżony, bo pomija monety, które zniknęły z giełdy. Radar zapisuje też
**nowe pary na giełdzie**; do handlu wchodzą dopiero po 35 dniach notowań.

**Laboratorium (zakładka „Laboratorium”).** Włączasz je w ustawieniach bota. Obok bota grają „na niby” jego warianty
z jedną zmianą: inny próg pewności ML, stop kroczący, SL/TP, zasady. Botom ML dochodzi model douczany co noc
(od 2:00). Pretendent wygrywa, gdy spełni cztery warunki:

- ma co najmniej `lab_min_trades` transakcji (domyślnie 30),
- test trwa co najmniej `lab_min_days` dni (domyślnie 14),
- ma wynik lepszy od mistrza o ponad 1 pp,
- nie ma większego obsunięcia.

Na koncie papierowym (Alpaca paper, Kraken „na niby”, demo) zwycięzca przejmuje bota sam. Na koncie z prawdziwymi
pieniędzmi pojawia się propozycja do zatwierdzenia. Po każdej zmianie mistrza, a także po ręcznej zmianie ustawień
bota, porównanie zaczyna się od nowa. Boty z laboratorium nie mają cotygodniowego douczania, bo robi to laboratorium
co noc.

## 5c. Copy trading (kopiowanie funduszy) i trend following

**Copy trading — strategia „Kopiowanie funduszy (13F superinwestorów)”.** Brokerzy (Alpaca, Kraken) nie
udostępniają kopiowania cudzych kont, więc bot kopiuje to, co da się sprawdzić i co jest publiczne: kwartalne
raporty 13F dużych zarządzających (domyślnie 12 funduszy, m.in. Buffett, Ackman, Druckenmiller, Tepper, Li Lu, Klarman).

- Raz dziennie bot czyta raporty z SEC i bierze spółki, które **co najmniej 2 fundusze dokupiły albo otworzyły**
  w ostatnim raporcie (najwięcej zgodnych funduszy = pierwszeństwo), do 20 spółek.
- Kupuje do 45 dni po raporcie (albo później, gdy cena przebije SMA200 od dołu), tylko gdy cena jest nad SMA200,
  a nowe wejścia wstrzymuje, gdy SPY jest pod swoją SMA200.
- Trzyma jak fundusz — sprzedaje, gdy fundusze zaczynają sprzedawać (a żaden nie dokupuje), albo na awaryjnym stopie 35%.
- Zarządza tylko pozycjami, które sam otworzył, i omija symbole innych botów na koncie.
- Wymaga w `.env`: `SEC_USER_AGENT=Imię Nazwisko email` (SEC wymaga przedstawienia się).
- Raporty 13F mają do 45 dni opóźnienia — to kopiowanie pomysłów, nie codziennych transakcji. Backtest liczy
  wszystko w czasie: danego dnia bot zna tylko raporty złożone wcześniej. Raporty starsze niż ok. 200 dni
  (fundusz przestał raportować) są pomijane.
- Backtest (dane IEX z Alpaki są od połowy 2020, więc pierwsze transakcje od maja 2021): +156% do września 2026,
  maks. obsunięcie −31%, Sharpe 0,8; rok 2022 na minusie (ok. −23% budżetu), najlepsze lata 2024–2026.
  Wersja z krótkimi stopami i trendem SMA50 dawała 6% rocznie przy obsunięciu −41% — kopiowanie działa jako
  „trzymaj z funduszami”, nie jako krótki trading. Uwaga: spółki usunięte z giełdy nie mają danych, więc wynik jest
  trochę zawyżony (efekt przetrwania).

**Trend following — szablon „Trend following - ETF z całego świata”.** Konstruktor zasad na dziennych świecach
koszyka 15 ETF (akcje USA i świata, obligacje, złoto, srebro, surowce, ropa, nieruchomości, dolar): kupno przy wybiciu
50-dniowego szczytu powyżej SMA200, sprzedaż przy zamknięciu pod 20-dniowym dołkiem albo na stopie 15%.
Backtest maj 2021 – wrzesień 2026: +80%, maks. obsunięcie −10%, Sharpe 0,95 — ale ok. 2/3 zysku przyniósł rok 2026
(złoto, srebro); w latach 2021–2025 było to ok. 5% rocznie. To strategia na duże trendy: długo nudna, zarabia rzadko, ale dużo. Stop kroczący 3× ATR wyraźnie pogarszał
wynik (wycinał trendy za wcześnie), dlatego jest wyłączony; analiza poprawek nie znalazła zmiany lepszej w czasie.

**Ceny skorygowane.** Od wersji 1.3.0 dane akcji z Alpaki są korygowane o splity i dywidendy
(wcześniej split np. 10:1 wyglądał jak spadek o 90%). Cache backtestów liczy się od nowa.

## 5c+. Termometry rynku (kontekst)

Obok klasycznego filtra reżimu (symbol nad średnią) bot może patrzeć na dwa dodatkowe „termometry”:

- **Drugi termometr** (`regime2_symbol`, np. `SPY`) — nowe wejścia tylko, gdy także ten symbol jest nad swoją średnią.
- **Szerokość rynku** (`breadth_filter`) — odsetek spółek z listy bota, które są nad swoją średnią (domyślnie 200 dni).
  Gdy spada poniżej progu (`breadth_min`, np. 50%), bot nie otwiera nowych pozycji. Liczone są tylko spółki z pełną
  historią; gdy jest ich mniej niż 5, filtr nie blokuje.

Wszystkie termometry muszą być „OK”, żeby bot otworzył pozycję. W backteście dzień D widzi tylko zamknięcie z D-1.
Otwarte pozycje są dalej pilnowane przez SL/TP i reguły wyjścia.

## 5d. Konta i platformy (zakładka „Konta”)

Nowe konta brokerskie podłączasz w panelu: **Konta → Podłącz platformę** → wybierasz platformę (Alpaca, Kraken,
Interactive Brokers, inna giełda krypto, symulator) → wpisujesz klucze według instrukcji przy formularzu →
**Sprawdź połączenie** → **Zapisz konto** (z hasłem do panelu). Konto od razu pojawia się przy tworzeniu bota.

- Klucze są w bazie **zaszyfrowane** (Fernet). Klucz szyfrujący leży w `data/.accounts_key` (uprawnienia 600) albo
  — bezpieczniej — w `.env` jako `ACCOUNTS_MASTER_KEY=...`. Rób jego kopię razem z bazą: bez niego zapisanych kluczy
  API nie da się odczytać.
- Panel nigdy nie odsyła kluczy — pokazuje tylko końcówkę identyfikatora. Przy edycji puste pole = bez zmian.
- Dodanie, zmiana i usunięcie konta wymagają hasła do panelu (z limitem prób jak przy logowaniu). Konta nie da się
  usunąć, dopóki korzystają z niego boty; zmiana kluczy wymaga zatrzymania botów tego konta.
- Konta z `.env` nadal działają i są w panelu tylko do odczytu.
- Nowa platforma w kodzie = wpis w `PLATFORMS` (`app/accounts.py`) + klasa brokera w `app/brokers.py`.
  Kod nowych platform przychodzi tylko w podpisanych wydaniach — aplikacja nie wczytuje „wtyczek” z dysku
  (obcy kod miałby dostęp do kluczy API).

### Inna giełda krypto (CCXT)

Platforma **„Inna giełda krypto (CCXT)”** podłącza dowolną giełdę obsługiwaną przez bibliotekę CCXT (ok. 100 giełd):
na liście są Binance, Bybit, OKX, Coinbase, KuCoin, Bitget, Gate, Bitstamp, Bitfinex, MEXC, HTX, Crypto.com, Bitvavo
i Gemini, a każdą inną wpisujesz identyfikatorem CCXT (np. `bitvavo`).

- **Tryb „na niby”** (domyślny): bez kluczy, prawdziwe ceny i prowizja giełdy, wirtualny kapitał.
- **Sieć testowa** (testnet, np. Binance, Bybit, OKX): osobne konto testowe i klucze z testnetu.
- **Prawdziwe konto**: klucz tylko do odczytu i handlu spot, **bez wypłat**; OKX/KuCoin/Bitget wymagają też Passphrase
  (w bazie zaszyfrowana jak klucze).
- Pary w walucie konta: `BTC/USDT`, `SOL/USDT`. Bot zarządza tylko pozycjami, które sam otworzył.
- Stop-loss: bot próbuje postawić stop-limit na giełdzie; gdy giełda go nie przyjmie, stop pilnuje bot co cykl
  (w dzienniku: „SL … (bot)”).
- Backtest: w polu „Dane” pojawia się giełda z Twojego konta. Giełdy dają ograniczoną historię (np. Binance 1000,
  OKX/Coinbase 300 ostatnich świec na interwał); aplikacja zbiera kolejne świece z czasem, jak przy Krakenie.
- Radar altcoinów działa też na tych giełdach.
- XTB nie ma już publicznego API — nie da się go podłączyć.

## 5e. Interactive Brokers (GPW, waluty, USA)

> **Wersja z instalatora (Windows):** zainstaluj **IB Gateway** ze strony interactivebrokers.com, zaloguj się na konto papierowe, w ustawieniach API włącz *Enable ActiveX and Socket Clients*, wyłącz *Read-Only API*, port **4002**. W panelu: Konta → + Podłącz platformę → Interactive Brokers (adres 127.0.0.1, port 4002). Bramka musi być zalogowana, gdy działają boty.

- Konto w aplikacji: **Konta → Podłącz platformę → Interactive Brokers** (adres 127.0.0.1, port 4002).
- Symbole: `PKN.WSE`, `CDR.WSE` (GPW, PLN) · `SAP.DE`, `ALV.DE` (Xetra, EUR) · `EUR.USD`, `EUR.PLN` (waluty; ilość = jednostki waluty bazowej pary) ·
  `AAPL`, `SPY` (USA). Jeden bot = jeden rynek, bo sesje mają inne godziny.
- Godziny handlu i święta bot bierze z kalendarza IBKR. Wielkość pozycji liczy w walucie konta (kursy z IBKR).
- Dane z GPW i USA wymagają subskrypcji w IBKR (bez niej są opóźnione o 15 min); waluty są bez dopłat.
- Backtest: w polu „Dane” wybierz „IBKR”.

### 5g. Wykresy (zakładka „Wykresy”)

Wpisz symbol (GPW `PKN.WSE`, USA `AAPL`, krypto `BTC/USD` / `BTC/EUR`, waluty `EUR.USD`, Xetra `SAP.DE`) albo kliknij
pozycję bota. Świece 15 min / 1 h / 4 h / 1 dzień, wolumen, SMA 20/50/200, strzałki zakupów i sprzedaży botów,
linie wejścia, stop-lossa i take-profitu otwartej pozycji. Źródło danych dobiera się po symbolu albo wybierasz je ręcznie.
Kliknięcie symbolu w tabeli transakcji otwiera jego wykres. Wykresy: TradingView Lightweight Charts (Apache 2.0).

**Karta „Finanse”** (przełącznik „Finanse” nad wykresem) pokazuje publicznie dostępne dane o spółce albo kryptowalucie.

| Rynek | Źródło | Uwagi |
|---|---|---|
| Akcje USA | **SEC EDGAR** — dane z raportów 10-K / 10-Q, prosto od regulatora | Wymaga `SEC_USER_AGENT` w `.env`. Bez niego używane jest Yahoo. |
| GPW, ETF-y, inne giełdy | **Yahoo Finance** (biblioteka `yfinance`) | Nieoficjalne źródło, czasem z brakami. Symbol `PKO.WSE` jest tłumaczony na `PKO.WA`. |
| Kryptowaluty | **CoinGecko** | Opcjonalnie własny darmowy klucz `COINGECKO_API_KEY` w `.env`, gdy publiczny limit jest za mały. |

- **Wycena (kafelki):**
  - kapitalizacja,
  - C/Z, C/WK, C/P,
  - marża netto, ROE,
  - dług / kapitał, dług netto,
  - wolne przepływy,
  - stopa dywidendy.

  Wskaźniki zależne od ceny są przeliczane na ostatnią cenę z wykresu. Najechanie na kafelek pokazuje, co oznacza.
- **Ostatnie 8 kwartałów:** przychody i zysk netto na wykresie słupkowym. Czwarty kwartał spółek z USA jest liczony jako rok minus trzy kwartały.
- **Wyniki roczne (do 5 lat):**
  - przychody, zysk operacyjny i netto, marża, EPS,
  - przepływy i FCF,
  - aktywa, kapitał, dług, gotówka,
  - dywidenda na akcję,
  - zmiana r/r.
- **ETF-y:** aktywa funduszu, opłata, dywidenda, zwroty.
- **Kryptowaluty:**
  - kapitalizacja i miejsce w rankingu,
  - wycena przy pełnej podaży,
  - podaż w obiegu i maksymalna,
  - rekord wszech czasów i odległość od niego.
- **Pamięć podręczna:** dane są trzymane 12 h w `data/cache/fund`. Przycisk „Odśwież” pobiera je od nowa.
- Wersja demo nie pobiera danych finansowych.

### 5g+. Kalendarz raportów i dywidend (zakładka „Kalendarz”)

- **Źródła:**
  - nadchodzące raporty okresowe, dni odcięcia i wypłaty dywidend: Yahoo Finance (USA, GPW, inne giełdy),
  - historia raportów: dla USA daty złożenia 10-Q i 10-K w SEC, dla pozostałych Yahoo.
- **Pobieranie:** dane pobierają się w tle dla wszystkich spółek botów, około 40 na kwadrans, i odświeżają co około 20 h. Plik: `data/cache/calendar.json`.
- **Blokada zakupów przed raportem:** w ustawieniach bota „Bez zakupów przed raportem (dni)”, domyślnie 0 = wyłączone.
  - Bot nie otwiera nowej pozycji, gdy raport wypada dziś albo w ciągu tylu dni.
  - Otwartych pozycji nie zamyka, bo pilnują ich stop-loss i take-profit.
  - Backtest tej blokady nie uwzględnia, bo nie ma historycznych kalendarzy.
- **Zakładka Kalendarz:** raporty i dywidendy w najbliższych 7–60 dniach. Pokazuje, który bot trzyma spółkę i czy zakupy są wstrzymane.
- **Wykres:** znaczniki **R** (dzień raportu) i **D** (odcięcie dywidendy). W karcie „Finanse” jest data najbliższego raportu i dywidendy.
- **Powiadomienie** (rodzaj „Kalendarz”, po 17:00): jutro raport albo odcięcie dywidendy spółki, którą trzyma bot.

### 5h. Ułamki akcji (małe kwoty)

Opcja bota **„Ułamki akcji — kupuj za kwotę”** (sekcja Ryzyko): bot kupuje za kwotę wyliczoną z budżetu i ryzyka, nawet
gdy to mniej niż cena jednej akcji. Działa tylko na **Alpace** (akcje i ETF-y z USA, które Alpaca dopuszcza do ułamków;
minimum 1 $). Alpaca nie przyjmuje zleceń bracket na ułamki, więc stop-loss i take-profit sprawdza bot przy każdym
cyklu. GPW (IBKR) nie ma ułamków akcji. Krypto zawsze kupowane jest za kwotę.

### 5j. Dźwignia i gra na spadki (dla bardziej ryzykownych)

W ustawieniach bota jest sekcja „Dźwignia i gra na spadki”. Ma trzy tryby:

| Tryb | Jak działa | Gdzie | Ryzyko |
|---|---|---|---|
| **wyłączone** (domyślnie) | tylko kupno za własne pieniądze | wszędzie | do wkładu |
| **ETF-y lewarowane i odwrotne** | sygnał liczony na spółce (np. NVDA), bot kupuje ETF 2× na wzrost (NVDL) albo odwrotny na spadek (NVDD) | Alpaca (USA), IBKR (WIG20: Beta ETF WIG20lev / WIG20short) | do wkładu, bez pożyczki |
| **margin i krótka sprzedaż** | pożyczone pieniądze i akcje lub monety u brokera; dźwignia 1–2× dla akcji, 1–5× dla krypto | Alpaca, IBKR, Kraken (na żywo i na niby), symulator | strata może przekroczyć wkład, do tego odsetki |

**Kierunek:** tylko wzrost, tylko spadek albo oba.
- Sygnał spadku to lustrzane odbicie zasad strategii, czyli te same zasady na odwróconych świecach (1/cena). Wybicie maksimum zamienia się w przebicie minimum, a „cena nad średnią” w „cenę pod średnią”.
- Filtr reżimu rynku działa odwrotnie dla spadków: krótkie pozycje otwierają się, gdy rynek jest słaby.

**ETF-y:**
- Stop-loss i take-profit są przeliczane na ETF (przy 2× ruch spółki o 5% to około 10% ETF-u).
- Lista par jest w zakładce **Ryzyko**, a przycisk „Sprawdź dostępność u brokera” zaznacza, które ETF-y są w obrocie.
- Własne pary dopisujesz w ustawieniach bota: `NVDA:NVDL:NVDD` albo z dźwignią `NVDA:NVDL*2:NVDD*-1`.
- Te ETF-y codziennie odnawiają dźwignię, więc przy długim trzymaniu w rynku bez trendu tracą wartość.

**Margin:**
- **Alpaca:** krótka sprzedaż tylko w całych akcjach i tylko spółek „shortable / easy to borrow”, na koncie z marginesem.
- **IBKR:** na GPW krótka sprzedaż zwykle nie jest dostępna, więc IBKR odrzuci zlecenie z opisem.
- **Kraken:** pozycja z parametrem dźwigni (2–5×, zależnie od pary). Stop stawiany jest na giełdzie, a jeśli giełda go nie przyjmie, pilnuje go bot. Na koncie „na niby” liczone są opłaty Krakena: 0,02% przy otwarciu i 0,02% co 4 h.

**Backtest uwzględnia:**
- krótkie pozycje,
- dźwignię i koszty pożyczki (akcje long: 7% rocznie od pożyczonej części, short: 1% rocznie, krypto: opłaty Krakena),
- ETF-y odtworzone ze spółki bazowej (dźwignia na każdej świecy i opłata ETF-u 0,95% rocznie).

**Wyłączenia:** nie łączy się z ML, kopiowaniem funduszy ani laboratorium. Stop-loss × dźwignia nie może przekroczyć 50%.

**Zakładka „Ryzyko”** (bezpiecznik dla wszystkich botów):
- **Wyłącznik:** wstrzymuje nowe wejścia wszystkich botów. Przycisk „Zamknij pozycje z dźwignią” wymaga hasła.
- **Dzienny limit straty:** gdy kapitał konta spadnie dziś o X% od wczorajszego zamknięcia, boty tego konta do jutra nie otwierają pozycji. Opcjonalnie zamykają też pozycje z dźwignią.
- **Zgoda na dźwignię dla kont z prawdziwymi pieniędzmi:** wymaga hasła i kodu 2FA, a bez niej bot z dźwignią nie wystartuje. Konta papierowe i symulator zgody nie wymagają.
- **Limit dźwigni na konto:** obcina ustawienie bota.

### 5k. Siatka (grid) i uśrednianie (DCA)

Dwie strategie, które nie czekają na sygnał ze świec, tylko pilnują ceny w każdym cyklu.

**Siatka** (tylko krypto) dzieli przedział ± X% wokół ceny startowej na poziomy.
- Gdy cena spada przez poziom, bot dokupuje porcję. Gdy wraca o poziom wyżej, sprzedaje ją z małym zyskiem.
- Na starcie kupuje porcje dla poziomów nad ceną, żeby miał co sprzedawać, gdy cena rośnie.
- Najlepiej działa, gdy cena chodzi w bok. Przy silnym spadku zostajesz z monetami kupionymi drożej, dlatego jest stop X% pod dolną krawędzią. Przy silnym wzroście siatka wszystko sprzeda i czeka.
- „Przesuwaj siatkę” buduje nową siatkę wokół aktualnej ceny, gdy cena z niej wyjdzie (w górę bez pozycji albo po stopie).
- Krok między poziomami musi być co najmniej 0,6%, żeby zysk z porcji pokrył prowizje.
- Zmiana ustawień siatki wchodzi, gdy bot nie trzyma porcji. Inaczej po ich sprzedaży albo po „Stop i zamknij pozycje”.

**Uśrednianie (DCA)** ma dwa tryby:
- **regularne** — zakup za stałą część budżetu co N godzin, aż budżet się skończy. Opcjonalnie sprzedaż całości przy zysku Y% nad średnią.
- **na spadkach** — pierwszy zakup, potem dokupienia co X% spadku (każde × mnożnik, maks. N). Sprzedaż całości przy zysku Y% nad średnią i start od nowa. Opcjonalny stop pod średnią.

Wspólne zasady:
- Budżet bota jest dzielony po równo między symbole.
- Stop-loss na serwerze brokera nie jest stawiany — wyjścia pilnuje bot.
- Dźwignia, ML i laboratorium są wyłączone.
- Akcje kupowane są w ułamkach, jeśli konto to umożliwia, a na IBKR w całych sztukach.
- Na stronie bota widać stan: zajęte poziomy siatki albo średnią, liczbę dokupień i próg następnego.
- Backtest liczy obie strategie po cenach poziomów (maksimum i minimum świecy). Pozycje otwarte na koniec testu są zamykane po ostatniej cenie.

### 5l. Alerty z TradingView

Bot ze strategią **„Alerty z TradingView (webhook)”** handluje według alertów, które sam ustawisz w TradingView: ze wskaźnika, z linii albo ze strategii Pine. Stop-loss, take-profit, budżet, dźwignia i bezpiecznik konta działają jak w innych botach.

**Jak to działa:**
- Panel uruchamia osobny, minimalny odbiornik na porcie **8421**. Umie tylko przyjąć `POST /tv/<token>` i zapisać alert w kolejce bota.
- Token to losowe 32 znaki, osobne dla każdego bota.
- Panelu (8420) **nie** wystawiasz do internetu.

**Konfiguracja (raz):**
1. W panelu administracyjnym Tailscale włącz **HTTPS Certificates** i **Funnel** (DNS → HTTPS; Access controls → nodeAttrs `funnel`).
2. Na NAS-ie (SSH): `tailscale funnel --bg 8421`. Polecenie pokaże adres w rodzaju `https://nas.tail1234.ts.net`.
3. Wpisz go do `.env` jako `TV_WEBHOOK_URL=https://nas.tail1234.ts.net` i zrestartuj aplikację.
4. Utwórz bota z tą strategią i listą symboli. Na stronie bota pojawi się adres webhooka i gotowa treść alertu.
5. W TradingView: Alert → Powiadomienia → **Webhook URL** = adres z panelu. W **Message** wklej treść z panelu:
   ```
   {"symbol": "{{ticker}}", "action": "{{strategy.order.action}}", "price": {{close}}}
   ```
   W alertach ze wskaźnika zamiast `{{strategy.order.action}}` wpisz `buy` albo `sell`.

**Akcje:**

| Akcja | Działanie |
|---|---|
| `buy` / `long` | wejście |
| `sell` / `exit` | wyjście z pozycji długiej |
| `short` | wejście na spadek (gdy bot ma włączoną grę na spadki) |
| `cover` | zamknięcie krótkiej pozycji |
| `close` | zamknięcie wszystkiego |

Tekstowo też działa, np. `buy NVDA`.

**Symbole:** `NASDAQ:NVDA` → `NVDA`, `BTCUSD` / `KRAKEN:XBTEUR` → `BTC/USD` / `BTC/EUR`, `GPW:PKO` → `PKO.WSE`.

**Bezpieczeństwo i kolejka:**
- Adres webhooka działa jak hasło. „Nowy adres” na stronie bota unieważnia stary.
- Dodatkowo możesz ustawić **hasło w treści alertu** (`passphrase`) w ustawieniach bota.
- Limit: 60 alertów na minutę na bota, 2 KB treści.
- Alert czeka na najbliższy cykl bota. Starszy niż ustawiony limit (domyślnie 30 min) przepada, np. gdy rynek był zamknięty.
- Odrzucone alerty (złe hasło, nieznany symbol lub akcja) widać w tabeli na stronie bota.
- Backtestu tej strategii w panelu nie ma: testuj ją w TradingView (Strategy Tester).

### 5m. Galeria strategii i strategia z opisu słownego (AI)

**Galeria** (Analiza → Galeria strategii) to gotowe ustawienia z wynikami testów na prawdziwych danych historycznych, z kosztami transakcji:
- GPW,
- USA,
- ETF,
- dźwignia,
- uśrednianie,
- siatka,
- krypto.

Każdą możesz:
- przetestować na swoich danych i w swoim okresie („Backtest na moich danych”),
- od razu utworzyć z niej bota (zacznij od konta na papierze).

Wyniki z przeszłości nie gwarantują przyszłych.

**Opisz strategię słowami:**
- Wpisz np. „kupuj spółki z WIG20, gdy przebiją maksimum z 50 dni, tylko gdy WIG20 jest nad średnią 200 dni; stop 8%, zysk 20%”.
- Panel zamieni opis na ustawienia konstruktora zasad (albo siatkę lub DCA), sprawdzi je tymi samymi regułami co formularz bota i pokaże wyjaśnienie, przyjęte założenia i uwagi.
- Dalej decydujesz Ty: backtest albo bot. Nic nie uruchamia się samo.

Wymaga własnego klucza API Claude w `.env`:
- `ANTHROPIC_API_KEY=` z console.anthropic.com, płatny za użycie (jedno tłumaczenie to ułamek centa). Ustaw tam limit wydatków.
- Opcjonalnie `ANTHROPIC_MODEL=`.
- Do Anthropic wysyłany jest tylko opis strategii — żadne klucze, salda ani transakcje.
- Limit: 30 tłumaczeń na godzinę.

### 5i. Okazje na telefon (ręczne kopiowanie transakcji botów)

1. **System → Powiadomienia → Włącz ntfy** — zainstaluj aplikację ntfy i zasubskrybuj pokazany temat (albo podłącz Telegram).
2. W ustawieniach bota, sekcja **„Okazje na telefon”**: zaznacz „Powiadamiaj o okazjach…” i wpisz swój kapitał
   (w walucie konta bota, np. zł dla GPW).
3. Przy kupnie przyjdzie: symbol, cena, stop-loss, kwota i liczba akcji dla Ciebie; przy sprzedaży — cena, powód i wynik.

Temat ntfy działa jak hasło (kto go zna, widzi powiadomienia, ale nie ma dostępu do panelu). Własny serwer ntfy: `NTFY_SERVER` w `.env`.

## 6d. Strażnik z zewnątrz (healthchecks.io)

Gdy NAS padnie, straci prąd albo internet, sam nie wyśle żadnego powiadomienia. Robi to wtedy serwis z zewnątrz.

**Jak włączyć (System → Powiadomienia → „Strażnik z zewnątrz”):**
1. Załóż darmowe konto na healthchecks.io i dodaj „Check”: Period 5 min, Grace 10 min, a w Integrations e-mail, SMS, Telegram albo ntfy.
2. Wklej w panelu „Ping URL”.

**Jak działa:**
- Panel co 5 minut wysyła znak życia. Brak znaku przez 15 minut oznacza alarm.
- Gdy panel działa, ale coś jest nie tak, wysyła ping `/fail` z opisem i to też jest alarm. Chodzi o bota z błędem przez 3 cykle albo bramkę IBKR bez połączenia ponad 15 minut (poza weekendem).
- Adres pingu jest zapisany w `data/heartbeat.json` i działa jak hasło.

## 7. Bezpieczeństwo

> W wersji z instalatora zapomniane hasło resetuje polecenie z instrukcji (punkt 13).

- **Nie przekierowuj portu 8420 na routerze.** Dostęp z zewnątrz tylko przez VPN (Tailscale / WireGuard).
- Klucze API z `.env` panel tylko czyta; klucze dodane w zakładce Konta są w bazie zaszyfrowane.
- Logowanie: sesja w ciasteczku HttpOnly, blokada na 5 min po 5 błędnych próbach (hasło albo kod).

### Logowanie, 2FA i klucze dla skryptów (zakładka „Logowanie”)

1. **Własny login i hasło** (min. 10 znaków). Hasło zapisane jako skrót scrypt. Od tej chwili `PANEL_PASSWORD`
   z `.env` przestaje otwierać panel, a inne zalogowane urządzenia są wylogowane.
2. **Kod z telefonu (2FA, TOTP)**: zeskanuj kod QR w Google Authenticator / Microsoft Authenticator / Authy /
   menedżerze haseł i potwierdź kodem. Panel pokaże **10 kodów awaryjnych** (każdy jednorazowy) — zapisz je.
   Po włączeniu logowanie ma dwa kroki: login + hasło, potem 6-cyfrowy kod. Ten sam kod nie zadziała dwa razy.
   Zmiana hasła, wyłączenie 2FA i nowe klucze wymagają hasła i kodu.
3. **Klucze dla skryptów**: własne skrypty (np. backtesty) przy włączonym 2FA łączą się kluczem
   (nagłówek `Authorization: Bearer tapp_…`). Utwórz klucz w panelu i wpisz go do ustawień skryptu jako
   `PANEL_API_TOKEN=…`. W bazie zostaje tylko skrót klucza; klucz można w każdej chwili usunąć.

Awaryjnie (zgubiony telefon i kody awaryjne): reset logowania opisany w instrukcji, punkt 13.
- Klucze do kont z prawdziwymi pieniędzmi (`ACCOUNT_X_PAPER=false`) twórz bez uprawnień do wypłat.
