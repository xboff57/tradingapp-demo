"""
Logowanie do panelu: własny login i hasło + kod 2FA z aplikacji na telefonie (TOTP), klucze dla skryptów.

- Dopóki nie ustawisz własnego loginu (zakładka „Bezpieczeństwo”), działa hasło PANEL_PASSWORD z .env.
  Po ustawieniu loginu hasło z .env przestaje otwierać panel.
- Hasło jest zapisane jako skrót scrypt (sól + koszt), nie jawnie.
- Sekret 2FA jest w bazie zaszyfrowany (ten sam klucz co klucze API kont). Kod jednorazowy z aplikacji
  (Google Authenticator, Authy, 1Password…) zmienia się co 30 s; ten sam kod nie zadziała drugi raz.
- Kody awaryjne (10 sztuk, każdy jednorazowy) — na wypadek utraty telefonu.
- Klucze dla skryptów: nagłówek „Authorization: Bearer tapp_…”, w bazie tylko skrót.
- Awaryjnie (zgubiony telefon i kody): na NAS-ie
      sudo docker exec tradingapp python -m app.auth reset
  usuwa login i 2FA — znowu działa hasło z .env.
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
import struct
import sys
import threading
import time
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS panel_user (
    id            INTEGER PRIMARY KEY CHECK (id = 1),
    username      TEXT NOT NULL,
    pw_hash       TEXT NOT NULL,
    totp_enc      TEXT,
    totp_on       INTEGER NOT NULL DEFAULT 0,
    totp_last     INTEGER NOT NULL DEFAULT 0,
    recovery      TEXT NOT NULL DEFAULT '[]',
    changed_at    TEXT
);
CREATE TABLE IF NOT EXISTS api_tokens (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    last_used  TEXT
);
"""
ISSUER = "TradingApp"
_db = None
_lock = threading.Lock()
_pending = {}            # sekret 2FA w trakcie wlaczania (do potwierdzenia kodem)


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def init(db):
    global _db
    _db = db
    with db.lock:
        db.conn.executescript(SCHEMA)


def user():
    return _db.one("SELECT * FROM panel_user WHERE id=1") if _db else None


# ------------------------------------------------------------------ hasla
def hash_pw(pw):
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(h).decode()


def check_pw(pw, stored):
    try:
        _, s, h = stored.split("$")
        got = hashlib.scrypt((pw or "").encode(), salt=base64.b64decode(s), n=2 ** 14, r=8, p=1, dklen=32)
        return hmac.compare_digest(got, base64.b64decode(h))
    except Exception:
        return False


def password_ok(pw, env_password):
    """Haslo panelu: wlasne (gdy ustawione) albo z .env."""
    u = user()
    if u:
        return check_pw(pw, u["pw_hash"])
    return hmac.compare_digest((pw or "").encode(), (env_password or "").encode())


def strength_error(pw, username=""):
    if len(pw) < 10:
        return "Hasło musi mieć co najmniej 10 znaków."
    if pw.lower() == (username or "").lower():
        return "Hasło nie może być takie samo jak login."
    if len(set(pw)) < 5:
        return "Hasło jest za proste."
    return None


# ------------------------------------------------------------------ TOTP (RFC 6238)
def _totp(secret_b32, step):
    key = base64.b32decode(secret_b32 + "=" * (-len(secret_b32) % 8))
    d = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    o = d[-1] & 0x0F
    return f"{(struct.unpack('>I', d[o:o + 4])[0] & 0x7FFFFFFF) % 1_000_000:06d}"


def _match_totp(secret_b32, code, last_step=0):
    """Krok czasu pasujacego kodu (okno ±30 s) albo None. Kod juz uzyty (krok <= last_step) nie przechodzi."""
    code = "".join(c for c in (code or "") if c.isdigit())
    if len(code) != 6:
        return None
    cur = int(time.time() // 30)
    for st in (cur - 1, cur, cur + 1):
        if st > last_step and hmac.compare_digest(_totp(secret_b32, st), code):
            return st
    return None


def _enc(s):
    from .accounts import _enc as enc
    return enc(s)


def _dec(s):
    from .accounts import _dec as dec
    return dec(s)


def totp_on():
    u = user()
    return bool(u and u["totp_on"])


def check_code(code):
    """Kod z aplikacji albo kod awaryjny. Zwraca 'totp' / 'recovery' / None."""
    with _lock:
        u = user()
        if not u or not u["totp_on"]:
            return None
        st = _match_totp(_dec(u["totp_enc"]), code, u["totp_last"])
        if st:
            _db.execute("UPDATE panel_user SET totp_last=? WHERE id=1", (st,))
            return "totp"
        norm = "".join(c for c in (code or "").lower() if c.isalnum())
        rec = json.loads(u["recovery"] or "[]")
        h = hashlib.sha256(norm.encode()).hexdigest()
        if norm and h in rec:
            rec.remove(h)
            _db.execute("UPDATE panel_user SET recovery=? WHERE id=1", (json.dumps(rec),))
            return "recovery"
    return None


def recovery_left():
    u = user()
    return len(json.loads(u["recovery"] or "[]")) if u else 0


# ------------------------------------------------------------------ zmiany
def set_login(username, new_pw):
    username = (username or "").strip()
    if not (3 <= len(username) <= 32) or not all(c.isalnum() or c in "._-@" for c in username):
        raise ValueError("Login: 3–32 znaki (litery, cyfry, . _ - @).")
    err = strength_error(new_pw or "", username)
    if err:
        raise ValueError(err)
    with _lock:
        if user():
            _db.execute("UPDATE panel_user SET username=?, pw_hash=?, changed_at=? WHERE id=1",
                        (username, hash_pw(new_pw), now()))
        else:
            _db.execute("INSERT INTO panel_user(id, username, pw_hash, changed_at) VALUES (1,?,?,?)",
                        (username, hash_pw(new_pw), now()))


def username_ok(name):
    u = user()
    return True if not u else hmac.compare_digest((name or "").strip().lower().encode(), u["username"].lower().encode())


def totp_begin():
    if not user():
        raise ValueError("Najpierw ustaw własny login i hasło.")
    secret = base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")
    u = user()
    _pending["secret"] = (secret, time.time())
    label = f"{ISSUER}:{u['username']}"
    from urllib.parse import quote
    uri = f"otpauth://totp/{quote(label)}?secret={secret}&issuer={ISSUER}&digits=6&period=30"
    return {"secret": " ".join(secret[i:i + 4] for i in range(0, len(secret), 4)), "uri": uri}


def totp_confirm(code):
    p = _pending.get("secret")
    if not p or time.time() - p[1] > 600:
        raise ValueError("Zacznij od nowa — kod QR był ważny 10 minut.")
    st = _match_totp(p[0], code)
    if not st:
        raise ValueError("Kod się nie zgadza. Sprawdź, czy zegar w telefonie jest ustawiony automatycznie.")
    codes = [secrets.token_hex(5) for _ in range(10)]
    with _lock:
        _db.execute("UPDATE panel_user SET totp_enc=?, totp_on=1, totp_last=?, recovery=? WHERE id=1",
                    (_enc(p[0]), st, json.dumps([hashlib.sha256(c.encode()).hexdigest() for c in codes])))
    _pending.clear()
    return [c[:5] + "-" + c[5:] for c in codes]


def totp_disable():
    with _lock:
        _db.execute("UPDATE panel_user SET totp_enc=NULL, totp_on=0, totp_last=0, recovery='[]' WHERE id=1")


def new_recovery():
    codes = [secrets.token_hex(5) for _ in range(10)]
    _db.execute("UPDATE panel_user SET recovery=? WHERE id=1",
                (json.dumps([hashlib.sha256(c.encode()).hexdigest() for c in codes]),))
    return [c[:5] + "-" + c[5:] for c in codes]


# ------------------------------------------------------------------ klucze dla skryptow
def token_create(name):
    name = (name or "").strip()[:40] or "skrypt"
    tok = "tapp_" + secrets.token_urlsafe(32)
    _db.execute("INSERT INTO api_tokens(name, token_hash, created_at) VALUES (?,?,?)",
                (name, hashlib.sha256(tok.encode()).hexdigest(), now()))
    return tok


def token_check(tok):
    if not tok or not tok.startswith("tapp_"):
        return False
    h = hashlib.sha256(tok.encode()).hexdigest()
    r = _db.one("SELECT id, last_used FROM api_tokens WHERE token_hash=?", (h,))
    if not r:
        return False
    if not r["last_used"] or r["last_used"][:13] != now()[:13]:          # zapis najwyzej raz na godzine
        _db.execute("UPDATE api_tokens SET last_used=? WHERE id=?", (now(), r["id"]))
    return True


def tokens():
    return _db.all("SELECT id, name, created_at, last_used FROM api_tokens ORDER BY id")


def token_delete(tid):
    _db.execute("DELETE FROM api_tokens WHERE id=?", (int(tid),))


def status():
    u = user()
    return {"custom_login": bool(u), "username": u["username"] if u else None,
            "totp": bool(u and u["totp_on"]), "recovery_left": recovery_left(),
            "changed_at": u["changed_at"] if u else None, "tokens": tokens()}


# ------------------------------------------------------------------ awaryjny reset (z konsoli NAS-a)
if __name__ == "__main__":
    if sys.argv[1:] == ["reset"]:
        from .config import DB_PATH
        import sqlite3
        c = sqlite3.connect(DB_PATH)
        c.executescript(SCHEMA)
        c.execute("DELETE FROM panel_user")
        c.commit()
        print("Usunięto własny login i 2FA. Zaloguj się hasłem PANEL_PASSWORD z .env i ustaw je od nowa.")
    else:
        print("Użycie: python -m app.auth reset")
