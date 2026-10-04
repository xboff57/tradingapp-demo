# Biblioteki zewnętrzne

- **TradingView Lightweight Charts™ 4.2.3** (`app/static/vendor/lightweight-charts.js`) — licencja Apache 2.0, © TradingView, Inc., https://www.tradingview.com/ (logo TradingView na wykresie to wymagane oznaczenie autora; licencja w `app/static/vendor/LICENSE-lightweight-charts.txt`)
- **IBM Plex Sans i IBM Plex Mono** (`app/static/fonts/`) — SIL Open Font License 1.1, © IBM Corp., pliki z pakietów @fontsource (licencja w `app/static/fonts/OFL-IBM-Plex.txt`)
- **qrcode-generator 1.4.4** (`app/static/qrcode.js`) — licencja MIT, © 2009 Kazuhiko Arase, https://github.com/kazuhikoarase/qrcode-generator
- **Chart.js 4.5.1** (`app/static/chart.umd.min.js`) — licencja MIT, © Chart.js Contributors, https://www.chartjs.org
- Biblioteki Pythona z `requirements.txt` (FastAPI, Uvicorn, alpaca-py, pandas, NumPy, python-dotenv, scikit-learn, CCXT, yfinance)
  są instalowane z PyPI na ich własnych licencjach (MIT / BSD / Apache 2.0).

## Źródła danych finansowych (karta „Finanse”)

- **SEC EDGAR** (data.sec.gov) to publiczne dane regulatora. Wymagany jest nagłówek User-Agent z danymi kontaktowymi (`SEC_USER_AGENT`), a limit wynosi 10 zapytań/s.
- **Yahoo Finance** przez bibliotekę **yfinance** (Apache 2.0, https://github.com/ranaroussi/yfinance). To nieoficjalny dostęp, przeznaczony do użytku osobistego zgodnie z warunkami Yahoo. Yahoo może go w każdej chwili zmienić lub ograniczyć.
- **CoinGecko API** (api.coingecko.com) korzysta z darmowego planu publicznego albo „Demo” z kluczem. Przy publikowaniu danych wymagane jest wskazanie źródła („Data provided by CoinGecko”). Karta pokazuje źródło z linkiem.
