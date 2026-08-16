from __future__ import annotations

import base64
import hashlib
import hmac
import json
import sys
import time
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.cli import run_cli
from core.config import settings
from core.discovery import document_from_link, dedupe_documents, only_period
from core.periods import period_conflicts, period_matches

AMC = "edelweiss"
PAGE_URL = "https://www.edelweissmf.com/statutory/portfolio-of-schemes"
API_URL = "https://api.edelweissmf.com/edelweissmf/api/v1/mf/statutory-menus/single"
# The Angular app's PRE_LOGIN secret/hashKey pair, lifted from its public JS
# bundle (main.<hash>.js) and confirmed live via GET
# https://api.edelweissmf.com/virat_eks_api/api/v1/auth/encryption-key. The
# response-decryption key also folds in an "x-ip-address"/"x-timestamp" pair
# that the *client* chooses and sends as headers -- the server just echoes
# the same values back into its own key derivation, so any self-consistent
# values work; there is no real secret held server-side.
SECRET = "5b6714126d3149fbab994747b2633287"
HASHKEY = "r4vcos0ejvndsow95n"
CLIENT_IP = "103.0.123.175"


def _curl_cffi_session():
    # Both this API and the file host behind it sit behind an Akamai bot
    # check that a plain requests.Session fails outright (403, even on a
    # known-good file URL); curl_cffi replays a real Chrome TLS/HTTP2
    # fingerprint the same way the project's HDFC adapter does.
    from curl_cffi import requests as curl_requests

    config = settings()
    session = curl_requests.Session(impersonate="chrome124")
    session.default_timeout = (config.connect_timeout, config.read_timeout)
    session.headers.update({"Referer": "https://www.edelweissmf.com/", "Origin": "https://www.edelweissmf.com"})
    return session


def _openssl_kdf(passphrase: bytes, salt: bytes, key_len: int = 32, iv_len: int = 16) -> tuple[bytes, bytes]:
    derived = block = b""
    while len(derived) < key_len + iv_len:
        block = hashlib.md5(block + passphrase + salt).digest()
        derived += block
    return derived[:key_len], derived[key_len : key_len + iv_len]


def _decrypt(b64_ciphertext: str, key_hex: str) -> dict:
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import unpad

    raw = base64.b64decode(b64_ciphertext)
    if not raw.startswith(b"Salted__"):
        raise RuntimeError("Edelweiss response was not CryptoJS-salted ciphertext; the encryption scheme has changed")
    salt, ciphertext = raw[8:16], raw[16:]
    key, iv = _openssl_kdf(key_hex.encode("utf-8"), salt)
    plaintext = unpad(AES.new(key, AES.MODE_CBC, iv).decrypt(ciphertext), AES.block_size)
    return json.loads(plaintext)


def _fetch_menu(session) -> dict:
    timestamp = str(int(time.time() * 1000))
    key_hex = hmac.new(HASHKEY.encode("utf-8"), (SECRET + CLIENT_IP + timestamp).encode("utf-8"), hashlib.sha256).hexdigest()
    response = session.get(
        API_URL,
        params={"type": "Statutory", "fundType": "MF", "menuName": "Portfolio of scheme(s)"},
        headers={"x-timestamp": timestamp, "x-ip-address": CLIENT_IP},
    )
    response.raise_for_status()
    return _decrypt(response.json()["body"], key_hex)


def discover(period: str, session=None):
    payload = _fetch_menu(session)
    documents = []
    for record in payload.get("files", []):
        if record.get("subMenuName") != "Monthly Portfolio and Risk-o-Meter":
            continue
        url = record.get("filePath") or record.get("downloadFile")
        if not url:
            continue
        evidence = f"{record.get('fileTitle', '')} {record.get('month', '')} {record.get('year', '')}"
        if period_conflicts(evidence, period) or not period_matches(evidence, period):
            continue
        documents.append(document_from_link(amc=AMC, period=period, source_page_url=PAGE_URL, link=url, label=record.get("fileTitle", ""), primary=True))
    documents = only_period(dedupe_documents(documents), period)
    if not documents:
        raise RuntimeError(f"Edelweiss Monthly Portfolio and Risk-o-Meter menu has no workbook for {period}")
    return documents


if __name__ == "__main__":
    raise SystemExit(run_cli(amc=AMC, discover=discover, description="Download Edelweiss monthly portfolio disclosure", session=_curl_cffi_session()))
