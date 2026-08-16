"""Helpers shared by verified AMC adapters."""

from __future__ import annotations

import ast
import html as html_module
import json
from collections.abc import Iterator
from typing import Any, Callable
from urllib.parse import urljoin

from core.discovery import Document, document_from_link
from core.http import fetch_text
from core.parsing import extract_json_script, next_payload_text, recursive_records, walk


def string_values(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from string_values(child)
    elif isinstance(value, (list, tuple, set)):
        for child in value:
            yield from string_values(child)


def parse_jsonish(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = html_module.unescape(value).strip()
    for _ in range(2):
        text = text.replace("\\/", "/").replace("\\u002F", "/")
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError:
            try:
                decoded = ast.literal_eval(text)
            except (ValueError, SyntaxError):
                break
        if decoded == text:
            break
        if not isinstance(decoded, str):
            return decoded
        text = decoded
    return value


def next_data(html: str) -> Any:
    return extract_json_script(html, script_id="__NEXT_DATA__")


def fetch_first_html(session, urls: tuple[str, ...] | list[str]) -> tuple[str, str]:
    errors: list[str] = []
    for url in urls:
        try:
            return fetch_text(session, url), url
        except Exception as exc:
            errors.append(f"{url}: {exc}")
    raise RuntimeError("All page aliases failed: " + " | ".join(errors))


def docs_from_json_records(
    payload: Any,
    *,
    amc: str,
    period: str,
    page_url: str,
    predicate: Callable[[dict, str, str], bool] | None = None,
) -> list[Document]:
    documents: list[Document] = []
    for record in recursive_records(payload):
        text = " ".join(string_values(record))
        urls = []
        for value in string_values(record):
            if value.lower().split("?", 1)[0].endswith((".xls", ".xlsx", ".xlsm", ".zip")):
                urls.append(urljoin(page_url, value))
        for url in dict.fromkeys(urls):
            label = text[:500]
            if predicate and not predicate(record, label, url):
                continue
            documents.append(document_from_link(amc=amc, period=period, source_page_url=page_url, link=url, label=label))
    return documents


def decoded_page_text(html: str) -> str:
    return next_payload_text(html)


def aes_cbc_decrypt(ciphertext: bytes, key: bytes, iv: bytes) -> bytes:
    """Decrypt AES-CBC/PKCS7 ciphertext using a key lifted from a site's own JS bundle."""

    from Crypto.Cipher import AES
    from Crypto.Util.Padding import unpad

    return unpad(AES.new(key, AES.MODE_CBC, iv).decrypt(ciphertext), AES.block_size)


def aes_cbc_encrypt(plaintext: bytes, key: bytes, iv: bytes) -> bytes:
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import pad

    return AES.new(key, AES.MODE_CBC, iv).encrypt(pad(plaintext, AES.block_size))

