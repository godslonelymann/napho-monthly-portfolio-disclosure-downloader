from __future__ import annotations

import re
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.cli import run_cli
from core.config import settings
from core.discovery import document_from_link, dedupe_documents, only_period
from core.periods import period_conflicts, period_matches

AMC = "franklin_templeton"
PAGE_URL = "https://www.franklintempletonindia.com/reports"


def discover(period: str, session=None):
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("Franklin Templeton discovery requires Playwright; install requirements and Chromium") from exc
    links: list[dict[str, str]] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=settings().headless)
        page = browser.new_page()
        try:
            page.goto(PAGE_URL, wait_until="domcontentloaded", timeout=settings().read_timeout * 1000)
            page.wait_for_timeout(2000)
            # The tab list is filtered/reflowed by category; find the exact
            # "Monthly Portfolio Disclosure" tab rather than a partial-text
            # match, since "Monthly Average Assets under Management" and
            # "Monthly Dashboard" sit right next to it in the same list.
            tab = page.get_by_role("tab", name="Monthly Portfolio Disclosure", exact=True).first
            tab.scroll_into_view_if_needed()
            tab.click()
            page.wait_for_timeout(1500)
            links = page.locator("a[href]").evaluate_all(
                "els => els.map(a => ({href: a.href, text: (a.innerText || a.textContent || '')}))"
            )
        except PlaywrightTimeoutError as exc:
            raise RuntimeError(f"Franklin Templeton reports page did not load: {exc}") from exc
        finally:
            browser.close()
    documents = []
    for item in links:
        url = str(item.get("href") or "")
        label = str(item.get("text") or "")
        evidence = f"{label} {url}"
        if not re.search(r"\.(?:xlsx|xls)(?:[?#]|$)", url, re.I) or "monthly-portfolio-dsclr" not in url.lower():
            continue
        if period_conflicts(evidence, period) or not period_matches(evidence, period):
            continue
        documents.append(document_from_link(amc=AMC, period=period, source_page_url=PAGE_URL, link=url, label=label, primary=True))
    documents = only_period(dedupe_documents(documents), period)
    if not documents:
        raise RuntimeError(f"Franklin Templeton Monthly Portfolio Disclosure tab has no workbook for {period}")
    return documents


if __name__ == "__main__":
    raise SystemExit(run_cli(amc=AMC, discover=discover, description="Download Franklin Templeton monthly portfolio disclosure"))
