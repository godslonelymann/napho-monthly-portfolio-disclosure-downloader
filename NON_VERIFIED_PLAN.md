# Non-Verified AMCs — Live Re-Probe & Build Plan

**Date of probe:** 2026-08-13
**Scope:** the 19 AMCs in `non-verified/`
**Source of truth:** `GET https://www.amfiindia.com/api/members` →
`AMFIMembers[].amc_monthly_portfolio_disclosure` (200, 88,056 bytes, no auth)

All 19 folder names matched an AMFI member record exactly. Every AMFI monthly URL below is the
**live** value pulled today, not the one cached in `amfi_members_raw.json`.

---

## 0. Blocker that gates everything

The 24 populated scripts in `verified/` import a shared framework that **does not exist**:

```
from core.cli import run_cli
from core.discovery import document_from_link, dedupe_documents, only_period
from core.http import fetch_json
from core.parsing import recursive_records
from core.periods import period_conflicts, period_matches, month_name
from amcs._shared import string_values
```

```
$ python3 "verified/Invesco Mutual Fund.py"
ModuleNotFoundError: No module named 'core'
```

`core/` and `amcs/` are absent from this repo and from both siblings
(`../AMFI-data-downloader`, `../amc-portfolio-harvester` — the latter has
`harvest/amcs/` but it contains only `__pycache__`).

Also note **10 of the 34 `verified/` files are 1-byte stubs**: Bandhan, Bank of India, HDFC,
IL&FS, JM Financial, PPFAS, Quantum, SBI, Sundaram, Union. "Verified" in that folder means
*the download method is known*, not *the script is written*.

**Nothing in `non-verified/` should be written until `core/` exists**, or all 19 new scripts
will be unrunnable in the same way.

---

## 1. What the live probe changed vs. the audit

| Finding | Audit said | Probe today |
|---|---|---|
| **HSBC** | `NOT_VERIFIED` — origin never responded, 3 timeouts | **200, 4.17 MB, 5,881 file URLs, download verified.** Fully solved. |
| **ITI** | `403 {"status":-100,"message":"Access denied."}` | **500 NullPointerException** — access is no longer the blocker, only the body shape |
| **Trust** | `GET → 405`, body shape unknown | **`POST → 204 No Content`** — endpoint accepts our POST |
| **ICICI** | blob URL verified, listing params unknown | **504 on `www`, connection-reset on `apimf`** — whole origin unreachable this pass |
| **Mirae** | POST body not captured | **200 + .NET error that names the missing parameter** |
| Nippon / Old Bridge / Samco | `PARTIALLY_VERIFIED` | **Downloads confirmed** — real XLSX/XLS bytes today |

Four AMCs are download-verified as of this probe. The audit's scoreboard understates the
current position by four.

---

## 2. Tiering

### Tier A — solved, plain HTTP, download confirmed today (4)

One adapter covers all four: fetch one server-rendered page, regex the file URLs, filter to
monthly, match the period. No JS, no auth, no params.

| AMC | AMFI monthly URL | Shape | Evidence |
|---|---|---|---|
| **HSBC** | `assetmanagement.hsbc.co.in/en/mutual-funds/investor-resources/information-library` | per-scheme, month = folder | 4.17 MB page · 5,881 unique files · 40 monthly folders · 45 files for Jul-2026 · **334,448 B PK** |
| **Nippon India** | `mf.nipponindiaim.com/investor-service/downloads/factsheet-portfolio-and-other-disclosures` | ONE consolidated `.xls`/month | 401 KB page · 336 unique files · 158 monthly-named · **1,206,358 B PK** |
| **Samco** | `www.samcomf.com/StatutoryDisclosure` | per-scheme `.xlsx` | 3.21 MB page · 6,429 unique files · 1,101 monthly-named · 303 for 2026 · **PK** |
| **Old Bridge** | `oldbridgemf.com/statutory-disclosures.html#v-pills-tabContent2` | per-scheme `.xlsx` | 735 KB page · 1,058 unique files · 34 monthly-named · **PK** |

**HSBC — the structure that makes it easy.** Monthly portfolios are addressed by folder date:

```
/-/media/files/attachments/india/mutual-funds/portfolios/document-31072026/
    hsbc-aggressive-hybrid-fund-31-jul-2026.xlsx
    hsbc-arbitrage-fund-31-jul-2026.xlsx          … 45 files
```

Traps, all real and all present in the data:

- The folder prefix **flips**: `documents-DDMMYYYY` for 2023–early-2024, `document-DDMMYYYY`
  after. Match `documents?-`.
- Three sibling disclosure types share the same page and must be excluded **by path**, not by
  filename: `fortnightly-debt-portfolio/`, `weekly-fund-portfolios/`, `half-yearly-portfolios/`.
- The legacy `portfolios/monthly-portfolio-<month>-<year>.xlsx` series is a **dead branch** — it
  stops at Aug-2021. Matching on the word "monthly" alone finds only that dead branch and misses
  every current file, because current filenames contain no month-word at all.

**Nippon** — irregular across years: `NIMF-MONTHLY-PORTFOLIO-31-July-26.xls`,
`MONTHLY-PORTFOLIO-APR-23.xls`, `31st-JULY-2022`, `Jan-13Monthly-portfolio.xls`. Some files are
joint `FORTNIGHTLY-MONTHLY-PORTFOLIO-*`. Fuzzy month matching required.

**Samco** — the typo **`Apirl`** for April is baked into 2026 filenames
(`IN_MF_MONTHLY_PORTFOLIO_Apirl_2026_SamcoELSSTaxSaverFund_1778319341.xlsx`). Two naming
families coexist: `IN_MF_MONTHLY_PORTFOLIO_*` and joint
`IN_MF_Portfolio_Monthly_Fortnightly_*`. Trailing `_<10-digit>` is an upload id.

**Old Bridge** — two-digit years (`March_26`, `Nov_25`) mixed with `Dec2025`, plus a Strapi
`_<10-hex>` hash suffix, so URLs are never constructible.

---

### Tier B — endpoint is live and unauthenticated; only the parameters are missing (7)

Every one of these answered without an auth wall. The remaining work is capturing one real
request in DevTools and replaying it — hours each, not days.

| AMC | Endpoint | Response today | What's missing |
|---|---|---|---|
| **Mirae** | `POST /AjaxService/GetDownloadsData` | `200` `ReturnCode:9999` — *"Specified argument was out of the range of valid values. **Parameter name: name**"* | The error **leaks the param name**. Filenames are hash-free (`mascf-july2026.xlsx`) — best-shaped target in this tier. |
| **LIC** | `POST /downloads/portfolio-filter-options` | `200`, returns `<option value=''>Month</option>` | Replay the cascade category→scheme→year→month, carry the cookie. `type=monthly_portfolio` already separates monthly from fortnightly. |
| **Franklin** | `GET /resourceapi/reports?first-load=true&segment=investor` | `200`, **413,985 B** BloomReach page-model JSON, no auth | Payload exposes the vocabulary: `MONTHLY-PORTFOLIO-DSCLR`, `SIF-MONTHLY-PORTFOLIO-DSCLR`, `UPDATES-<YYYY>-<M>`. Need the filtered follow-up call. |
| **PGIM** | `POST /api/v1/brochure/published/disclosure` | `200` `{"resultCode":"Failure","resultMsg":"Technical Error…"}` | Structured app-level failure, not a transport block. Body shape only. |
| **Trust** | `POST /api/api/Trust/GetData` | **`204 No Content`** | Accepts the POST. Needs the request body; page tabs confirm a Monthly tab exists. |
| **Choice** | `GET /api/monthly-portfolio-report/portfolio-website-list/` | `400 {"message":"invalid module"}` | A `module` param. Route name is explicitly monthly. |
| **ITI** | `POST /jeeth/api/v1/catalog/getPartnerDocumentByType` | `500 NullPointerException` | Body shape. **Previously 403** — the access block has lifted. |

---

### Tier C — real API behind a handshake (3)

| AMC | Endpoint | Response today | Handshake |
|---|---|---|---|
| **Navi** | `POST /wp-json/nv/v1/documents` | `403 {"code":"no_authorized","message":"Security verification required (nonce missing)"}` | WordPress nonce — normally printed into the page HTML. Scrape it, replay. Params already known: `financial_year=2026-2027&value=July&category=884&type=Monthly`. |
| **Bajaj Finserv** | `POST /wp-admin/admin-ajax.php` | `400`, body `0` | WP's signature for an unrecognised `action`. Recover the action string from the page bundle. Page carries 31 xlsx but **0 monthly-named** — the accordion is client-populated. |
| **Jio BlackRock** | `POST /v1/jiobr/api/auth/generateInvestorSession` | `400 {"userMsg":"Invalid Login Type"}` | Needs a login-type param, then a session token. Separately: addressing is **fiscal-year** (2026-2027), and the audit found August empty. |

---

### Tier D — browser-only or externally blocked (5)

| AMC | Blocker | Note |
|---|---|---|
| **Kotak** | Radware — serves `<title>Radware Captcha Page</title>` | Hard bot wall. Playwright. |
| **Edelweiss** | Edge `403 Access Denied` with reference # | Hard bot wall. Playwright. |
| **Mahindra Manulife** | Responses are an opaque encrypted `payload` blob — **even the 404 is encrypted** | Decryption key lives in the JS bundle. Same shape as Bandhan. Playwright. |
| **Tata** | 1.24 MB Next.js page, **2 file links, 0 "monthly portfolio" mentions** | AMFI links to the wrong sub-tab (`/schemes-related/scheme-factsheet`). Must first locate the real Portfolios tab. |
| **ICICI Prudential** | **504 on `www.icicipruamc.com`, connection-reset on `apimf.icicipruamc.com`** | Not a method problem. Origin was unreachable for every request this pass. Retest before assuming anything. |

**ICICI is the highest-value target in this tier** — one 25 MB ZIP per month covering all
schemes. The blob path is known-good from the prior audit:

```
/blob/downloads/Files/Monthly Portfolio Disclosures/2026/July/Monthly-Portfolio-Disclosure-July-2026.zip
```

but it is explicitly **not constructible** — `2026/March` and `2024/July` both 404. The listing
must be read. Requeue for retest, don't write it off.

---

## 3. Plan

### Phase 0 — build `core/` (blocking, ~1 day)

Extract the framework the 24 populated `verified/` scripts already assume, from their own import
surface: `core.cli.run_cli`, `core.discovery` (`document_from_link`, `dedupe_documents`,
`only_period`), `core.http.fetch_json`, `core.parsing.recursive_records`, `core.periods`
(`period_matches`, `period_conflicts`, `month_name`), `amcs._shared.string_values`.

Carry over the audit's five method rules, in particular:
- **Zero results is a hard error**, never a silent empty. This is the guard that would have
  caught both the Zerodha and Quantum misses.
- File-URL regex must **allow literal spaces** (`["']([^"'<>]{4,300}?\.(?:xlsx|xls|csv|zip))(?=["'?#])`).
- Decode **both** Next.js routers — `__NEXT_DATA__` and `self.__next_f`.
- Validate `kind` (monthly vs fortnightly) and `period` from the document **title/path**, never
  from the query you sent.

Exit criterion: one existing script — Invesco — runs end-to-end.

### Phase 1 — Tier A, four AMCs on one adapter (~1 day)

`ArchiveHtml`: HSBC, Nippon, Samco, Old Bridge. All four are download-verified; this is
transcription of known-good behaviour, not discovery. Encode the four traps above as per-AMC
filters, and add a fixture test per AMC pinned to a known month.

Expected result: **19 → 15** remaining, with the single hardest-looking name in the folder (HSBC)
cleared first.

### Phase 2 — Tier B, seven AMCs via parameter capture (~2–3 days)

Same loop for each: open the page in the browser, filter to a month, capture the one XHR that
returns the file list, replay it from `requests`, confirm bytes. Order by expected yield:

1. **Mirae** — the error names the missing param
2. **Franklin** — vocabulary already in hand from a no-auth 200
3. **LIC** — cascade replay, `type=monthly_portfolio` already isolated
4. **Trust** — 204 means the shape is close
5. **PGIM** — structured failure, app-level only
6. **Choice** — one `module` param
7. **ITI** — access block has lifted since the audit

Most of these land in the audit's proposed `FormApi` adapter; Franklin and Choice in `ApiJson`.

### Phase 3 — Tier C, three handshakes (~1–2 days)

Navi first — a WP nonce scraped from page HTML is the most reliably solvable of the three, and
its `type=Monthly` param is the cleanest monthly/fortnightly separator in the whole set. Bajaj
next (recover the `action` string). Jio BlackRock last — fiscal-year addressing plus an
apparently-empty current month means it may not repay the effort yet.

### Phase 4 — Tier D, five hard cases (~2–3 days)

- **ICICI first, and re-probe before any work** — today's 504 may well be transient, and it is
  the biggest single-file prize in the folder.
- **Tata** — a navigation problem before it is a scraping problem; find the real Portfolios tab.
- **Kotak, Edelweiss, Mahindra** — Playwright, accepted as browser-only. Budget them together
  and run them on a separate slower schedule from the HTTP path.

### Sequencing note

Phases 1–3 convert **14 of 19** AMCs using plain `requests` and no browser. Do not let the five
Tier-D cases set the architecture — they are a separate execution mode, and the audit already
scoped them that way.

---

## 4. Open questions

1. Where should `core/` live — this repo, or is it meant to come from `../amc-portfolio-harvester`
   (which has an empty `harvest/amcs/`)? This decides whether Phase 0 is a build or a wire-up.
2. Should the 10 one-byte `verified/` stubs be filled in the same pass, or tracked separately?
   They are "method known, script missing" and would otherwise be silently mistaken for done.
3. For per-scheme AMCs (HSBC 45 files/month, Samco, Old Bridge), is the deliverable every scheme
   file, or a consolidated per-AMC artifact?
