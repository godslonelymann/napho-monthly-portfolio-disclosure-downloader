"""Small HTTP helpers shared by the verified downloaders."""

from __future__ import annotations

import json as json_module
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import settings


def create_session() -> requests.Session:
    config = settings()
    retry = Retry(
        total=config.retry_total,
        connect=config.retry_total,
        read=config.retry_total,
        status=config.retry_total,
        backoff_factor=config.retry_backoff,
        status_forcelist=(408, 425, 429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "HEAD", "OPTIONS", "POST"}),
        # A server-controlled Retry-After can otherwise exceed the runner's
        # per-AMC deadline and hide the useful failure diagnostic.
        respect_retry_after_header=False,
    )
    session = requests.Session()
    session.default_timeout = (config.connect_timeout, config.read_timeout)
    session.discovery_timeout = config.discovery_timeout
    session.retry_total = config.retry_total
    session.headers.update(
        {
            "User-Agent": config.user_agent,
            "Accept-Language": "en-IN,en;q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
        }
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.mount("http://", HTTPAdapter(max_retries=retry))
    return session


def _session(session):
    return session or create_session()


def _timeout(session) -> tuple[int, int]:
    return getattr(session, "default_timeout", (30, 120))


def fetch_response(session, url: str, *, headers: dict[str, str] | None = None, **kwargs):
    active = _session(session)
    phase = kwargs.pop("phase", "request")
    kwargs.setdefault("timeout", _timeout(active))
    try:
        response = active.get(url, headers=headers, **kwargs)
        response.raise_for_status()
        return response
    except requests.Timeout as exc:
        attempts = getattr(active, "retry_total", 0) + 1
        raise RuntimeError(f"Timeout: url={url} phase={phase} attempts<={attempts}: {exc}") from exc
    except requests.RequestException as exc:
        raise RuntimeError(f"Request failed: url={url} phase={phase}: {exc}") from exc


def fetch_text(session, url: str, *, headers: dict[str, str] | None = None, **kwargs) -> str:
    return fetch_response(session, url, headers=headers, **kwargs).text


def fetch_json(session, url: str, *, headers: dict[str, str] | None = None, **kwargs) -> Any:
    response = fetch_response(session, url, headers=headers, **kwargs)
    try:
        return response.json()
    except (ValueError, json_module.JSONDecodeError) as exc:
        preview = response.text[:300].replace("\n", " ")
        raise RuntimeError(f"Expected JSON from {url}, got: {preview!r}") from exc


def fetch_bytes(session, url: str, *, headers: dict[str, str] | None = None, **kwargs) -> bytes:
    return fetch_response(session, url, headers=headers, **kwargs).content


def post_response(session, url: str, *, json: Any = None, data: Any = None, headers: dict[str, str] | None = None, **kwargs):
    active = _session(session)
    phase = kwargs.pop("phase", "request")
    kwargs.setdefault("timeout", _timeout(active))
    try:
        response = active.post(url, json=json, data=data, headers=headers, **kwargs)
        response.raise_for_status()
        return response
    except requests.Timeout as exc:
        attempts = getattr(active, "retry_total", 0) + 1
        raise RuntimeError(f"Timeout: url={url} phase={phase} attempts<={attempts}: {exc}") from exc
    except requests.RequestException as exc:
        raise RuntimeError(f"Request failed: url={url} phase={phase}: {exc}") from exc


def post_json(session, url: str, *, json: Any = None, data: Any = None, headers: dict[str, str] | None = None, **kwargs) -> Any:
    response = post_response(session, url, json=json, data=data, headers=headers, **kwargs)
    try:
        return response.json()
    except (ValueError, json_module.JSONDecodeError) as exc:
        preview = response.text[:300].replace("\n", " ")
        raise RuntimeError(f"Expected JSON from {url}, got: {preview!r}") from exc


def post_text(session, url: str, *, json: Any = None, data: Any = None, headers: dict[str, str] | None = None, **kwargs) -> str:
    return post_response(session, url, json=json, data=data, headers=headers, **kwargs).text
