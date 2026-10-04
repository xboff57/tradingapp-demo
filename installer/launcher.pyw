"""
TradingApp — uruchamianie wersji z instalatora Windows.

  pythonw launcher.pyw                 uruchom (albo tylko otwórz panel, jeśli już działa)
  pythonw launcher.pyw --tlo           uruchom w tle, bez otwierania przeglądarki (autostart)
  pythonw launcher.pyw --nie-usypiaj   komputer nie zaśnie, dopóki działa TradingApp
  pythonw launcher.pyw --demo          wersja demonstracyjna (osobne dane, port 8765)
  python  launcher.pyw --stop [--demo] zatrzymaj
  python  launcher.pyw --reset-logowania   zapomniane hasło: kasuje login, hasło i 2FA panelu (boty, konta
                                           i historia zostają); przy następnym starcie kreator ustawi nowe

Dane, hasło, konta i ustawienia (.env) leżą w %LOCALAPPDATA%\\TradingApp\\dane — odinstalowanie
ani aktualizacja programu ich nie ruszają. Launcher pilnuje aplikacji: gdy się wyłączy przez błąd,
uruchamia ją ponownie (maks. 5 razy w ciągu 10 minut).
"""
import ctypes
import os
import socket
import subprocess
import sys
import time
import webbrowser
from datetime import datetime

APP = os.path.dirname(os.path.abspath(__file__))
ARGS = sys.argv[1:]
DEMO = "--demo" in ARGS
LOCAL = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
DATA = os.path.join(LOCAL, "TradingApp", "demo" if DEMO else "dane")
os.makedirs(DATA, exist_ok=True)
ENV = os.path.join(DATA, ".env")
LOG = os.path.join(DATA, "launcher.log")
PIDS = os.path.join(DATA, "launcher.pid")
STOP = os.path.join(DATA, "launcher.stop")
LOCK = os.path.join(DATA, "launcher.lock")
PY = os.path.join(APP, "python", "python.exe")
NO_WINDOW = 0x08000000

DEFAULT_ENV = """# TradingApp — ustawienia (wersja z instalatora).
# Po zmianie zatrzymaj i uruchom aplikację ponownie (menu Start -> TradingApp -> Zatrzymaj, potem TradingApp).
# Konta brokerów, hasło, 2FA i powiadomienia ustawiasz w panelu — tu zwykle nic nie trzeba zmieniać.

# Panel tylko na tym komputerze. Dostęp z telefonu: zainstaluj Tailscale i zmień na 0.0.0.0
# (NIGDY nie przekierowuj portu na routerze).
PANEL_HOST=127.0.0.1
PANEL_PORT=8420

# Wbudowane konto symulowane (bez prawdziwych pieniędzy). Prawdziwe konta dodajesz w panelu: Konta.
ACCOUNTS=demo
ACCOUNT_DEMO_START=100000

# Dane o insiderach i funduszach z SEC: wpisz "Imię Nazwisko twoj@email" (wymóg SEC).
SEC_USER_AGENT=

# Alerty z TradingView (opcjonalnie, patrz README): odbiornik tylko lokalnie, do internetu przez Tailscale Funnel.
TV_WEBHOOK_HOST=127.0.0.1
TV_WEBHOOK_URL=

# Strategia z opisu słownego (opcjonalnie): własny klucz z console.anthropic.com
ANTHROPIC_API_KEY=
"""


def log(msg):
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n")
    except OSError:
        pass


def read_env():
    out = {}
    try:
        for line in open(ENV, encoding="utf-8-sig"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def port():
    return 8765 if DEMO else int(read_env().get("PANEL_PORT") or 8420)


def up(p):
    try:
        socket.create_connection(("127.0.0.1", p), timeout=1).close()
        return True
    except OSError:
        return False


def kill(pid):
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], creationflags=NO_WINDOW,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def stop():
    open(STOP, "w").close()
    try:
        pids = [int(x) for x in open(PIDS).read().split()]
    except (OSError, ValueError):
        pids = []
    for pid in reversed(pids):           # najpierw aplikacja, potem launcher
        kill(pid)
    for _ in range(20):
        if not up(port()):
            break
        time.sleep(0.5)
    log("Zatrzymano (--stop).")


def single_instance():
    import msvcrt
    f = open(LOCK, "a+")
    try:
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        return None
    return f


def keep_awake(on):
    # tylko na czas działania programu; ustawienia zasilania Windows zostają bez zmian
    ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
    try:
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if on else 0))
    except Exception:
        pass


def open_when_ready(p, wait=120):
    for _ in range(wait * 2):
        if up(p):
            webbrowser.open(f"http://127.0.0.1:{p}/")
            return
        time.sleep(0.5)


def reset_login():
    import sqlite3
    stop()
    db = sqlite3.connect(os.path.join(DATA, "tradingapp.sqlite"))
    db.execute("DELETE FROM panel_user")
    db.commit()
    db.close()
    for f in ("sesje.json", "haslo_panelu.txt"):
        try:
            os.remove(os.path.join(DATA, f))
        except OSError:
            pass
    log("Zresetowano login panelu (--reset-logowania).")
    print("Login, hasło i 2FA panelu skasowane. Uruchom TradingApp — kreator poprosi o nowe.")


def main():
    if "--stop" in ARGS:
        return stop()
    if "--reset-logowania" in ARGS:
        return reset_login()
    p = port()
    if up(p):
        if "--tlo" not in ARGS:
            webbrowser.open(f"http://127.0.0.1:{p}/")
        return
    lock = single_instance()
    if lock is None:                     # inny launcher właśnie startuje
        if "--tlo" not in ARGS:
            open_when_ready(p, 60)
        return
    if os.path.exists(STOP):
        os.remove(STOP)
    if not DEMO and not os.path.exists(ENV):
        with open(ENV, "w", encoding="utf-8") as f:
            f.write(DEFAULT_ENV)
        log(f"Utworzono {ENV}")
    env = dict(os.environ, DATA_DIR=DATA, PYTHONUTF8="1", PYTHONIOENCODING="utf-8", TRADINGAPP_INSTALLED="1")
    if DEMO:
        env.update(DEMO="1", PANEL_HOST="127.0.0.1", PANEL_PORT="8765")
        env.pop("ENV_FILE", None)
    else:
        env["ENV_FILE"] = ENV
        env.setdefault("PANEL_HOST", read_env().get("PANEL_HOST") or "127.0.0.1")
        env.setdefault("TV_WEBHOOK_HOST", read_env().get("TV_WEBHOOK_HOST") or "127.0.0.1")
    if "--nie-usypiaj" in ARGS:
        keep_awake(True)
    if "--tlo" not in ARGS:
        import threading
        threading.Thread(target=open_when_ready, args=(p,), daemon=True).start()
    crashes = []
    while True:
        out = open(os.path.join(DATA, "aplikacja_konsola.log"), "a", encoding="utf-8")
        log(f"Start aplikacji ({'demo' if DEMO else 'pełna'}, port {p})")
        proc = subprocess.Popen([PY, "-m", "app.main"], cwd=APP, env=env, creationflags=NO_WINDOW,
                                stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT)
        with open(PIDS, "w") as f:
            f.write(f"{os.getpid()} {proc.pid}")
        code = proc.wait()
        out.close()
        if os.path.exists(STOP):
            break
        log(f"Aplikacja zakończyła się (kod {code}).")
        now = time.time()
        crashes = [t for t in crashes if now - t < 600] + [now]
        if len(crashes) > 5:
            log("Za dużo awarii w 10 minut — przerywam. Szczegóły: aplikacja_konsola.log i tradingapp.log.")
            ctypes.windll.user32.MessageBoxW(0, "TradingApp kilka razy z rzędu zakończył się błędem.\n\n"
                                             f"Szczegóły w folderze:\n{DATA}\n(pliki tradingapp.log i aplikacja_konsola.log)",
                                             "TradingApp", 0x10)
            break
        time.sleep(10)
    keep_awake(False)
    try:
        os.remove(PIDS)
    except OSError:
        pass


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"Błąd launchera: {e!r}")
        raise
