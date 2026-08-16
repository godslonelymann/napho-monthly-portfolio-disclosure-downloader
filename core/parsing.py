"""HTML, JSON and React Server Components parsing helpers."""

from __future__ import annotations

import ast
import html as html_module
import json
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, Iterator
from urllib.parse import urljoin


@dataclass(frozen=True)
class Link:
    href: str
    text: str = ""
    searchable: str = ""


class _AnchorParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[Link] = []
        self._anchor: dict[str, Any] | None = None
        self._depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a" or self._anchor is not None:
            return
        attributes = {key.lower(): value or "" for key, value in attrs}
        href = attributes.get("href", "").strip()
        if not href:
            return
        self._anchor = {"href": html_module.unescape(href), "text": [], "attrs": attributes}
        self._depth = 1

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag.lower() == "a":
            self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if self._anchor is not None:
            self._anchor["text"].append(data)

    def handle_starttag_inside(self) -> None:
        if self._anchor is not None:
            self._depth += 1

    def handle_endtag(self, tag: str) -> None:
        if self._anchor is None:
            return
        if tag.lower() == "a":
            attrs = self._anchor["attrs"]
            text = " ".join(" ".join(self._anchor["text"]).split())
            searchable = " ".join(
                part
                for part in (
                    text,
                    self._anchor["href"],
                    attrs.get("title", ""),
                    attrs.get("aria-label", ""),
                    attrs.get("data-title", ""),
                )
                if part
            )
            self.links.append(Link(self._anchor["href"], text, searchable))
            self._anchor = None
            self._depth = 0


def extract_links(markup: str, base_url: str = "") -> list[Link]:
    parser = _AnchorParser()
    parser.feed(html_module.unescape(markup))
    result: list[Link] = []
    for link in parser.links:
        href = urljoin(base_url, link.href) if base_url else link.href
        result.append(Link(href=href, text=link.text, searchable=link.searchable))
    return result


# The opening quote is captured and back-referenced so only *that* quote ends
# the URL.  Excluding both quote characters instead would truncate any filename
# containing an apostrophe -- SBI serves
# "sbi-children's-benefit-fund-...xlsx", which used to be cut down to
# "s-benefit-fund-...xlsx" (a 404) because the apostrophe was read as the
# opening delimiter.
FILE_URL_RE = re.compile(
    r"([\"'])((?:(?!\1)[^<>]){4,300}?\.(?:xlsx|xls|xlsm|csv|zip)(?:\?(?:(?!\1)[^<>])*)?)(?=\1|[<>]|$)",
    re.I,
)


def extract_file_urls(markup: str, base_url: str = "") -> list[str]:
    """Extract file URLs, preserving literal spaces in filenames."""

    text = html_module.unescape(markup).replace("\\/", "/").replace("\\u002F", "/")
    urls: list[str] = []
    for match in FILE_URL_RE.finditer(text):
        value = match.group(2).strip()
        absolute = urljoin(base_url, value) if base_url else value
        if absolute not in urls:
            urls.append(absolute)
    return urls


def _script_body(markup: str, script_id: str) -> str | None:
    pattern = re.compile(
        rf"<script[^>]*\bid=[\"']{re.escape(script_id)}[\"'][^>]*>(.*?)</script>",
        re.I | re.S,
    )
    match = pattern.search(markup)
    return match.group(1).strip() if match else None


def _decode_next_f(markup: str) -> str:
    chunks: list[str] = []
    pattern = re.compile(r"self\.__next_f\.push\(\[\s*\d+\s*,\s*(\"(?:\\.|[^\"])*\")\s*\]\)", re.S)
    for match in pattern.finditer(markup):
        try:
            chunks.append(json.loads(match.group(1)))
        except json.JSONDecodeError:
            continue
    return "".join(chunks)


def next_payload_text(markup: str) -> str:
    """Return decoded text from either Next.js router when present."""

    body = _decode_next_f(markup)
    if body:
        return body
    return html_module.unescape(markup).replace("\\/", "/").replace("\\u002F", "/")


def _first_json(value: str) -> Any:
    value = value.strip()
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        pass
    for opening, closing in (("{", "}"), ("[", "]")):
        start = value.find(opening)
        end = value.rfind(closing)
        if start >= 0 and end > start:
            candidate = value[start : end + 1]
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                try:
                    return ast.literal_eval(candidate)
                except (ValueError, SyntaxError):
                    pass
    raise RuntimeError("No JSON object could be decoded from the page payload")


def extract_json_script(markup: str, script_id: str = "__NEXT_DATA__") -> Any:
    body = _script_body(markup, script_id)
    if body:
        return _first_json(html_module.unescape(body))
    if script_id == "__NEXT_DATA__":
        next_text = _decode_next_f(markup)
        if next_text:
            return _first_json(next_text)
    raise RuntimeError(f"JSON script {script_id!r} was not found")


def extract_js_json(markup: str, var_name: str) -> Any:
    """Decode the JSON literal assigned to a bare JS variable, e.g. ``const foo = {...};``.

    Unlike ``_first_json`` (which needs its input to be nothing but the JSON
    payload, since it locates the literal's bounds with ``str.find``/``rfind``
    over the whole string), this only needs the assignment to appear
    somewhere in a larger ``<script>`` body.  It decodes with
    ``json.JSONDecoder.raw_decode`` from the first ``{``/``[`` after the
    variable name, which stops at that literal's own balanced closing
    bracket and ignores any further JS statements following it in the same
    script -- necessary because ``rfind`` would otherwise grab the closing
    bracket of unrelated code later in the file instead of the variable's own.
    """

    match = re.search(rf"\b{re.escape(var_name)}\s*=\s*", markup)
    if not match:
        raise RuntimeError(f"JS variable {var_name!r} was not found")
    start = match.end()
    while start < len(markup) and markup[start] not in "{[":
        start += 1
    if start >= len(markup):
        raise RuntimeError(f"JS variable {var_name!r} has no JSON literal")
    value, _ = json.JSONDecoder().raw_decode(markup, start)
    return value


def recursive_records(value: Any) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from recursive_records(child)
    elif isinstance(value, list):
        for child in value:
            yield from recursive_records(child)


def walk(value: Any) -> Iterator[Any]:
    if isinstance(value, dict):
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)
    else:
        yield value
