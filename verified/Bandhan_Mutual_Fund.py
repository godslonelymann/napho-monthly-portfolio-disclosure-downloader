from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.cli import run_cli
from core.config import settings
from core.discovery import DiscoveryResult, PeriodUnavailable, document_from_link, dedupe_documents
from core.periods import month_name, period_conflicts, period_matches


AMC = "bandhan"
# The old "portfolio-summary/monthly" page only ever lists one consolidated
# debt-schemes workbook -- it structurally cannot cover equity/hybrid/index/
# ETF/FOF schemes. This disclosure page adds a third (scheme) dropdown and
# exposes every scheme individually, one file per scheme.
PAGE_URL = os.getenv(
    "BANDHAN_PAGE_URL",
    "https://bandhanmutual.com/statutory-disclosures/scheme-portfolios/monthly-half-yearly",
)
TAB_LABEL = "Monthly and Half Yearly"
# Rows no longer have a plain a[href]; a click fires a JS handler that calls
# this API with the real file location in a "filepath" query parameter.
DOWNLOAD_ENDPOINT_MARKER = "/investor/v1/dashboard/download-doc"
CMS_CALL_MARKER = "cms-call"
ROW_SELECTOR = "div.flex.items-center.px-2.py-3.border-b.w-full"
ROW_LABEL_SELECTOR = ".text-base.mx-1"
# The three dropdowns (year, month, scheme) are the only children of this
# container and it is otherwise unique on the page (verified against the
# live DOM). Indexing into it by position sidesteps the problem that a
# trigger button's own label is whatever value is currently selected (e.g.
# "July", or a scheme name), not a fixed marker we could match on.
DROPDOWN_CONTAINER_SELECTOR = "div.flex.flex-wrap.gap-3"

MAX_SELECT_ATTEMPTS = 3
MIN_FILE_BYTES = 1024


def _dropdown_wrappers(page):
    container = page.locator(DROPDOWN_CONTAINER_SELECTOR).first
    return container.locator(":scope > div > div.relative")


def _select_dropdown_option(page, wrapper, target: str, playwright_timeout_error, *, attempts: int = MAX_SELECT_ATTEMPTS):
    """Click a dropdown open, choose ``target``, and wait for its refresh fetch to land.

    Returns True if a cms-call response was observed to succeed, False if the
    site never responded after every retry (a real outage, not just "empty").
    Raises if ``target`` isn't even offered as an option -- that's a
    structural mismatch, not a timing issue, and retrying won't fix it.
    """
    option_pattern = re.compile(rf"^\s*{re.escape(target)}\s*$")
    for attempt in range(1, attempts + 1):
        wrapper.locator("button").first.click()
        try:
            # Opening the list is a React state update, not part of the click
            # itself -- reading options before it commits sees an empty list
            # and would otherwise be mistaken for "no such option".
            wrapper.locator("li").first.wait_for(state="visible", timeout=3_000)
        except playwright_timeout_error:
            if attempt < attempts:
                page.wait_for_timeout(400)
                continue
            raise RuntimeError(f"Bandhan dropdown never opened while selecting {target!r}")
        options = wrapper.locator("li", has_text=option_pattern)
        if options.count() == 0:
            raise PeriodUnavailable(f"Bandhan does not list {target!r} as a dropdown option")
        try:
            with page.expect_response(lambda response: CMS_CALL_MARKER in response.url, timeout=8_000) as response_info:
                options.first.click()
            if response_info.value.ok:
                page.wait_for_timeout(250)  # let the React re-render catch up with the response
                return True
        except playwright_timeout_error:
            pass
        if attempt < attempts:
            page.wait_for_timeout(500)
    return False


def _select_scheme(page, wrapper, name: str, period: str, playwright_timeout_error, *, timeout_ms: int = 1_200, poll_ms: int = 100):
    """Open the scheme dropdown, choose ``name``, and wait on the row's own
    text actually changing rather than on the site's network response.

    The listing API is fast (~0.2s in practice) but Playwright's response
    listener occasionally misses the event outright, and waiting on it then
    costs a full multi-second timeout for nothing -- across ~79 schemes a
    handful of these misses turned a ~1 minute job into a ~20 minute one.
    The row label is the thing this adapter actually needs to be true (it's
    already re-checked against staleness below), so polling it directly is
    both faster and no less safe: a stale row still fails the label/period
    check and is retried exactly as before.

    The site also occasionally drops the refresh fetch outright for a
    scheme change (the same "dropped fetch" behaviour documented in
    _pick_dropdown's history for year/month, observed here too): nothing
    renders, nothing errors, the previous scheme's row just sits there
    unchanged until the dropdown is reopened. ``timeout_ms`` is deliberately
    short (real responses land in ~0.2s) so that case is detected and handed
    back to the caller's retry loop quickly rather than burning a long wait
    on a fetch that was never going to arrive.

    Returns ``(good_rows, saw_any_row, responded)``: ``good_rows`` is a list
    of ``(row, label)`` pairs whose text names this scheme and period;
    ``saw_any_row`` records whether any row rendered at all (used to tell
    "confirmed empty" apart from "nothing rendered yet"); ``responded``
    records whether the site's own listing endpoint was observed to answer
    for this click (used to tell a dead dropdown entry apart from an outage).
    """
    option_pattern = re.compile(rf"^\s*{re.escape(name)}\s*$")
    wrapper.locator("button").first.click()
    try:
        wrapper.locator("li").first.wait_for(state="visible", timeout=3_000)
    except playwright_timeout_error:
        raise RuntimeError(f"Bandhan dropdown never opened while selecting {name!r}")
    options = wrapper.locator("li", has_text=option_pattern)
    if options.count() == 0:
        raise PeriodUnavailable(f"Bandhan does not list {name!r} as a dropdown option")

    responded = {"value": False}

    def _on_response(response) -> None:
        if CMS_CALL_MARKER in response.url and response.ok:
            responded["value"] = True

    page.on("response", _on_response)
    try:
        options.first.click()
        deadline = time.monotonic() + timeout_ms / 1000
        saw_any_row = False
        good_rows: list[tuple] = []
        while time.monotonic() < deadline:
            rows = page.locator(ROW_SELECTOR)
            row_count = rows.count()
            if row_count:
                saw_any_row = True
                candidates = []
                for index in range(row_count):
                    row = rows.nth(index)
                    label = row.locator(ROW_LABEL_SELECTOR).inner_text().strip()
                    if not _label_matches_scheme(label, name):
                        continue
                    evidence = f"{label} {name}"
                    if period_conflicts(evidence, period) or not period_matches(evidence, period):
                        continue
                    candidates.append((row, label))
                if candidates:
                    good_rows = candidates
                    break
            page.wait_for_timeout(poll_ms)
        return good_rows, saw_any_row, responded["value"]
    finally:
        page.remove_listener("response", _on_response)


def _select_period(page, period: str, playwright_timeout_error) -> None:
    year = period[:4]
    target_month = month_name(period)
    tab = page.get_by_role("button", name=TAB_LABEL, exact=True).first
    tab.click()
    wrappers = _dropdown_wrappers(page)
    if not _select_dropdown_option(page, wrappers.nth(0), year, playwright_timeout_error):
        raise RuntimeError(f"Bandhan disclosure page never confirmed the year={year} selection")
    # Selecting the year can reset the month dropdown to its default, so
    # month must be picked after, and the wrapper handles must be re-fetched
    # since a rerender can replace the underlying DOM nodes.
    wrappers = _dropdown_wrappers(page)
    if not _select_dropdown_option(page, wrappers.nth(1), target_month, playwright_timeout_error):
        raise RuntimeError(f"Bandhan disclosure page never confirmed the month={target_month} selection")
    wrappers = _dropdown_wrappers(page)
    trigger_year = wrappers.nth(0).locator("button").first.inner_text().strip()
    trigger_month = wrappers.nth(1).locator("button").first.inner_text().strip()
    if trigger_year != year or trigger_month != target_month:
        raise RuntimeError(
            f"Bandhan dropdowns show {trigger_year!r}/{trigger_month!r} after selecting {year!r}/{target_month!r}"
        )


def _wait_for_boot(page, playwright_timeout_error, *, timeout_ms: int = 20_000) -> None:
    """Wait past the page's initial load state where dropdowns read "Select Year" etc.

    Reading the DOM before this settles finds zero usable dropdowns and
    produces a confusing "no such option" error instead of an honest
    "the page never finished loading" one.
    """
    year_pattern = re.compile(r"^\s*20\d{2}\s*$")
    try:
        page.wait_for_function(
            """(sel) => {
                const container = document.querySelector(sel);
                if (!container) return false;
                const buttons = container.querySelectorAll('div.relative button');
                return buttons.length >= 3 && /^\\s*20\\d{2}\\s*$/.test(buttons[0].innerText);
            }""",
            arg=DROPDOWN_CONTAINER_SELECTOR,
            timeout=timeout_ms,
        )
    except playwright_timeout_error as exc:
        raise RuntimeError("Bandhan disclosure page dropdowns never finished loading") from exc


def _scheme_options(page) -> list[str]:
    wrapper = _dropdown_wrappers(page).nth(2)
    wrapper.locator("button").first.click()
    names = wrapper.locator("li").all_inner_texts()
    page.keyboard.press("Escape")
    names = [name.strip() for name in names if name.strip()]
    if not names:
        raise RuntimeError("Bandhan scheme dropdown listed no schemes")
    return names


# ICRA's Fund_Name never carries the site's plan-option suffix, and the
# site's own row label already drops it (e.g. dropdown option "Bandhan Fixed
# Term Plan - Series 179 - Growth" renders as row label "Bandhan Fixed Term
# Plan Series 179 (3652 days) ..."), so this is what the audit's manifest
# matching needs to see as the canonical scheme identity.
def _canonical_scheme_name(option_name: str) -> str:
    return re.sub(r"\s*-\s*growth\s*$", "", option_name, flags=re.I).strip()


_STOPWORDS = {"bandhan"}


def _significant_tokens(text: str) -> set[str]:
    text = re.sub(r"\s*-\s*growth\s*$", "", text, flags=re.I)
    return {token for token in re.findall(r"[a-z0-9]+", text.lower()) if token not in _STOPWORDS}


def _label_matches_scheme(label: str, scheme_name: str) -> bool:
    """A defensive check against stale rows: does the row's own text actually
    look like the scheme we just selected, not whatever was on screen before?

    Tolerates one missing token (the site drops "Growth" from row labels) but
    nothing more -- schemes with very similar names (Nifty 50 Index Fund vs
    Nifty 50 ETF) must not be allowed to satisfy each other.
    """
    scheme_tokens = _significant_tokens(scheme_name)
    if not scheme_tokens:
        return False
    label_tokens = _significant_tokens(label)
    missing = scheme_tokens - label_tokens
    return len(missing) <= 1


def _safe_filename(scheme_name: str, period: str) -> str:
    name = re.sub(r"^\s*bandhan\s+", "", scheme_name, flags=re.I)
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return f"bandhan_{slug}_{period}.xlsx"


MONTHLY_SUBDIR = "monthly"


def _report_path(period: str) -> Path:
    config = settings()
    slug = re.sub(r"[^a-z0-9._-]+", "_", AMC.lower()).strip("_")
    # Must match run_cli's own download destination (see the "subdir" call
    # below) so the discovery report sits next to the files it describes.
    directory = config.output_dir / slug / MONTHLY_SUBDIR / period
    directory.mkdir(parents=True, exist_ok=True)
    return directory / ".bandhan_discovery_report.json"


def _load_report(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _write_report(path: Path, report: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def _download_row(page, row, playwright_timeout_error, *, attempts: int = 2) -> str | None:
    button = row.locator("button").first
    for _ in range(attempts):
        try:
            with page.expect_request(lambda request: DOWNLOAD_ENDPOINT_MARKER in request.url, timeout=10_000) as request_info:
                button.click()
            query = parse_qs(urlsplit(request_info.value.url).query)
            filepath = (query.get("filepath") or [None])[0]
            if filepath:
                return filepath
        except playwright_timeout_error:
            continue
    return None


def _discovery_notes_summary(schemes_report: dict) -> dict:
    """A compact, audit-friendly digest of the per-scheme resume report.

    Full per-scheme detail (including which documents were found for a
    "found" scheme) already lives in .bandhan_discovery_report.json -- this
    is what's worth surfacing directly in .expected.json alongside the
    expected file list, without duplicating that whole file: how many
    schemes the dropdown offered in total, and which of them the site
    itself confirmed publish nothing this period (so a smaller expected
    count than "total schemes" reads as expected, not as a discovery bug).
    """
    status_counts: dict[str, int] = {}
    not_published: list[str] = []
    for name, entry in schemes_report.items():
        status = entry.get("status", "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
        if status in {"not_published", "unavailable_on_site"}:
            not_published.append(name)
    return {
        "total_schemes_offered": len(schemes_report),
        "status_counts": status_counts,
        "not_published": sorted(not_published),
    }


def discover(period: str, session=None):
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("Bandhan discovery requires Playwright; install requirements and Chromium") from exc

    report_path = _report_path(period)
    # Resume support: a scheme already resolved as "found" or "not_published"
    # in a prior run for this exact period is trusted and skipped, so a mid
    # -run browser crash doesn't force redownloading 70+ schemes that already
    # succeeded. Anything that previously "errored" is retried.
    report = _load_report(report_path)
    resolved: dict[str, dict] = {
        name: entry for name, entry in report.get("schemes", {}).items() if entry.get("status") in {"found", "not_published", "unavailable_on_site"}
    }

    documents: list = []
    for name, entry in resolved.items():
        if entry.get("status") == "found":
            for item in entry.get("documents", []):
                documents.append(
                    document_from_link(
                        amc=AMC,
                        period=period,
                        source_page_url=PAGE_URL,
                        link=item["url"],
                        label=item["label"],
                        filename=item["filename"],
                        scheme=item["scheme"],
                    )
                )

    schemes_report: dict[str, dict] = dict(resolved)
    errors: list[str] = []
    site_responded_at_least_once = False

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=settings().headless)
        page = browser.new_page()
        try:
            page.goto(PAGE_URL, wait_until="domcontentloaded", timeout=settings().read_timeout * 1000)
            _wait_for_boot(page, PlaywrightTimeoutError)
            _select_period(page, period, PlaywrightTimeoutError)
            # Reaching here means the year and month selections both got a
            # successful listing response, which is proof the site is
            # answering -- needed to tell a dead scheme entry apart from an
            # outage even on a resumed run that only retries one scheme.
            site_responded_at_least_once = True
            all_scheme_names = _scheme_options(page)
            pending = [name for name in all_scheme_names if name not in resolved]

            for name in pending:
                # A single scheme's own error (a dropdown that briefly won't
                # open, an option that transiently disappeared) must not
                # abort the other ~78 schemes -- record it and keep going.
                # PeriodUnavailable can legitimately mean "not published" for
                # a single scheme here (it's re-raised whole-page in
                # _select_period, which runs before this loop), so it's
                # folded into the per-scheme error bucket too.
                try:
                    canonical = _canonical_scheme_name(name)
                    matched_docs: list[tuple] = []
                    responded = False
                    saw_rows = False
                    mismatch_reason = None
                    for verify_attempt in range(MAX_SELECT_ATTEMPTS):
                        wrapper = _dropdown_wrappers(page).nth(2)
                        try:
                            good_rows, attempt_saw_rows, attempt_responded = _select_scheme(
                                page, wrapper, name, period, PlaywrightTimeoutError
                            )
                        except RuntimeError as exc:
                            # The dropdown itself briefly failed to open --
                            # transient UI hiccup, not a verdict on the
                            # scheme. Retry like any other missed attempt;
                            # only let it propagate if every attempt fails.
                            if verify_attempt == MAX_SELECT_ATTEMPTS - 1:
                                raise
                            mismatch_reason = str(exc)
                            page.wait_for_timeout(400)
                            continue
                        responded = responded or attempt_responded
                        # Unlike ``responded`` this must NOT accumulate across
                        # attempts: attempt 1 can trivially see the *previous*
                        # scheme's leftover row (that's the whole stale-row
                        # problem this loop exists to survive), and if that
                        # transient True stuck around it would permanently
                        # block a later attempt's honest "confirmed empty"
                        # reading from ever registering as not_published.
                        saw_rows = attempt_saw_rows
                        if attempt_responded:
                            site_responded_at_least_once = True
                        if good_rows:
                            matched_docs = good_rows
                            mismatch_reason = None
                            break
                        if not attempt_saw_rows:
                            # No row rendered at all -- either a genuinely
                            # empty result or the response hasn't landed yet.
                            # Stop retrying once the response is confirmed,
                            # otherwise give it one more pass.
                            if attempt_responded:
                                break
                            mismatch_reason = None
                            continue
                        # Row(s) present but don't look like this scheme/period
                        # yet -- classic stale-row race. Retry.
                        mismatch_reason = "row text did not match selected scheme/period"

                    if not matched_docs:
                        if responded and not saw_rows:
                            # The server answered and answered "empty" -- the
                            # scheme genuinely publishes nothing this period
                            # (e.g. a fund that matured before the month end).
                            schemes_report[name] = {"status": "not_published"}
                        elif not responded:
                            # Some dropdown entries are simply dead: selecting
                            # them fires no listing request at all, leaves the
                            # previous scheme's row on screen, and their
                            # download button yields nothing. That is
                            # indistinguishable from a site outage in
                            # isolation, so it is only downgraded to a benign
                            # "site has no file" after the loop, and only if
                            # this run proved the site was answering for
                            # other schemes.
                            schemes_report[name] = {"status": "no_response", "reason": "site fired no listing request"}
                        else:
                            errors.append(name)
                            schemes_report[name] = {"status": "error", "reason": mismatch_reason}
                        continue

                    doc_records = []
                    for row, label in matched_docs:
                        filepath = _download_row(page, row, PlaywrightTimeoutError)
                        if not filepath:
                            continue
                        filename = _safe_filename(canonical, period) if len(matched_docs) == 1 else _safe_filename(
                            f"{canonical} {label}", period
                        )
                        doc_records.append({"url": filepath, "label": label, "filename": filename, "scheme": canonical})
                        documents.append(
                            document_from_link(
                                amc=AMC,
                                period=period,
                                source_page_url=PAGE_URL,
                                link=filepath,
                                label=label,
                                filename=filename,
                                scheme=canonical,
                            )
                        )
                    if doc_records:
                        schemes_report[name] = {"status": "found", "documents": doc_records}
                    else:
                        errors.append(name)
                        schemes_report[name] = {"status": "error", "reason": "row matched but never fired a download request"}
                except (RuntimeError, PeriodUnavailable, PlaywrightTimeoutError) as exc:
                    errors.append(name)
                    schemes_report[name] = {"status": "error", "reason": str(exc)}

                # Persist after every scheme so a crash only costs the one
                # scheme in flight, not the whole run.
                _write_report(
                    report_path,
                    {"period": period, "total_schemes": len(all_scheme_names), "schemes": schemes_report},
                )
        finally:
            browser.close()

    # Resolve the provisional "no_response" schemes now that the whole run's
    # evidence is in. If the site answered for other schemes, it was up, so a
    # scheme it never answered for is a dead listing rather than an outage.
    for name, entry in schemes_report.items():
        if entry.get("status") != "no_response":
            continue
        if site_responded_at_least_once:
            entry["status"] = "unavailable_on_site"
        else:
            entry["status"] = "error"
            errors.append(name)

    _write_report(report_path, {"period": period, "total_schemes": len(schemes_report), "schemes": schemes_report})

    documents = dedupe_documents(documents)
    documents = [document for document in documents if document.period == period]

    if errors:
        raise RuntimeError(
            f"Bandhan discovery could not confirm {len(errors)} scheme(s) for {period} after retries: "
            f"{', '.join(sorted(errors))}. See {report_path} for details; re-run to retry only these."
        )
    if not documents:
        raise PeriodUnavailable(f"Bandhan lists no monthly portfolio documents for {period}")
    return DiscoveryResult(documents=documents, notes=_discovery_notes_summary(schemes_report))


if __name__ == "__main__":
    raise SystemExit(
        run_cli(
            amc=AMC,
            discover=discover,
            description="Download Bandhan monthly portfolio schemes",
            subdir=MONTHLY_SUBDIR,
        )
    )
