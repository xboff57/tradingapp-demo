"""
Podpisane wydania - aplikacja instaluje tylko paczki podpisane kluczem autora.

Paczka .zip zawiera (poza kodem) dwa pliki:
    tradingapp/MANIFEST       - lista "sha256  sciezka" wszystkich plikow paczki
    tradingapp/MANIFEST.sig   - podpis Ed25519 pliku MANIFEST (base64)

Sprawdzenie przy wgrywaniu:
  1. podpis MANIFEST pasuje do klucza publicznego ZAINSTALOWANEJ wersji (app/release_pubkey.pem),
     a nie do klucza z nowej paczki - inaczej kazdy moglby dolaczyc wlasny klucz,
  2. kazdy plik w paczce jest w MANIFEST i ma zgodna sume sha256, i nic nie brakuje.

Klucz prywatny zostaje TYLKO u autora (poza repozytorium). Podpisywanie: tools/podpisz_wydanie.py.
Wlasna wersja aplikacji (fork): podmien app/release_pubkey.pem na swoj klucz publiczny,
albo ustaw ALLOW_UNSIGNED_UPDATES=true w .env (niezalecane).
"""

import base64
import hashlib
import io
import os
import zipfile

PUBKEY_PATH = os.path.join(os.path.dirname(__file__), "release_pubkey.pem")
PREFIX = "tradingapp/"
MANIFEST = PREFIX + "MANIFEST"
SIGNATURE = PREFIX + "MANIFEST.sig"


class ReleaseError(Exception):
    pass


def manifest_for(files):
    """files: {sciezka: bajty} -> tekst MANIFEST (posortowany, bez samego MANIFEST i podpisu)."""
    lines = [f"{hashlib.sha256(data).hexdigest()}  {name}" for name, data in sorted(files.items())
             if name not in (MANIFEST, SIGNATURE) and not name.endswith("/")]
    return ("\n".join(lines) + "\n").encode()


def fingerprint(pem_bytes):
    return hashlib.sha256(pem_bytes).hexdigest()[:16]


def public_key_info():
    if not os.path.exists(PUBKEY_PATH):
        return None
    return {"fingerprint": fingerprint(open(PUBKEY_PATH, "rb").read())}


def verify(zip_bytes, pubkey_pem=None):
    """Zwraca wersje z paczki albo rzuca ReleaseError z opisem po polsku."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.serialization import load_pem_public_key
    if pubkey_pem is None:
        if not os.path.exists(PUBKEY_PATH):
            raise ReleaseError("Brak klucza publicznego wydań (app/release_pubkey.pem) - nie mogę sprawdzić podpisu.")
        pubkey_pem = open(PUBKEY_PATH, "rb").read()
    z = zipfile.ZipFile(io.BytesIO(zip_bytes))
    names = [n for n in z.namelist() if not n.endswith("/")]
    if MANIFEST not in names or SIGNATURE not in names:
        raise ReleaseError("Paczka nie jest podpisana (brak MANIFEST / MANIFEST.sig). Instaluję tylko oficjalne, "
                           "podpisane wydania.")
    manifest = z.read(MANIFEST)
    try:
        sig = base64.b64decode(z.read(SIGNATURE).strip())
        load_pem_public_key(pubkey_pem).verify(sig, manifest)
    except (InvalidSignature, ValueError):
        raise ReleaseError("Podpis paczki jest nieprawidłowy - paczka nie pochodzi od autora albo została zmieniona.")
    expected = {}
    for line in manifest.decode().splitlines():
        if line.strip():
            digest, name = line.split("  ", 1)
            expected[name] = digest
    actual = {n for n in names if n not in (MANIFEST, SIGNATURE)}
    extra, missing = actual - set(expected), set(expected) - actual
    if extra or missing:
        raise ReleaseError("Zawartość paczki nie zgadza się z podpisaną listą plików"
                           + (f" (dodatkowe: {', '.join(sorted(extra)[:3])})" if extra else "")
                           + (f" (brakuje: {', '.join(sorted(missing)[:3])})" if missing else "") + ".")
    for name, digest in expected.items():
        if hashlib.sha256(z.read(name)).hexdigest() != digest:
            raise ReleaseError(f"Plik {name} został zmieniony po podpisaniu.")
    try:
        return z.read(PREFIX + "VERSION").decode().strip()
    except KeyError:
        return "?"
