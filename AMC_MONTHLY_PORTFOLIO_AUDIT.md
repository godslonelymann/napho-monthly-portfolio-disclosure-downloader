# AMC Monthly Portfolio Disclosure — Investigation Audit

**Status:** IN PROGRESS
**Started:** 2026-08-13
**Scope:** Monthly Portfolio Disclosure ONLY (not fortnightly, not half-yearly)
**Latest month expected available:** July 2026 (2026-07)

---

## Step 1 — AMFI Source of Truth (VERIFIED)

The AMFI page `https://www.amfiindia.com/online-center/portfolio-disclosure` is a Next.js
(React Server Components) app. The "Select Disclosure Type" dropdown does **not** trigger any
network request — the entire AMC list, with one URL per disclosure type, is embedded in the
page payload and the dropdown only switches which field is rendered.

### The field that matters

Each AMC record contains separate URLs per disclosure type. We want **exactly one**:

| Disclosure type in dropdown | JSON field |
|---|---|
| Fortnightly Portfolio Disclosure | `amc_fortnightly_portfolio_disclosure` |
| **Monthly Portfolio Disclosure** | **`amc_monthly_portfolio_disclosure`** ← our field |
| Half Yearly Portfolio Disclosure | `amc_halfYearly_portfolio_disclosure` |
| Scheme-wise Annual Report | `amc_schemewise_annual_report` |

### Machine-readable source (VERIFIED)

```text
Endpoint:       https://www.amfiindia.com/api/members
Method:         GET
Parameters:     none
Headers:        none required
Response type:  application/json
Shape:          {"AMFIMembers": [ {...}, ... ]}
Field wanted:   AMFIMembers[].amc_monthly_portfolio_disclosure
Name field:     AMFIMembers[].mf_name
```

Verified with a plain `curl` — no auth, no cookies, no referer needed. This is a far better
source of truth than scraping the HTML page, and it is the same data the page renders.

Fallback if the API is ever removed: the same JSON is embedded in the page HTML inside
`self.__next_f.push([1,"..."])` chunks; concatenate the decoded strings and locate `{"members":`.

### Counts (VERIFIED)

| Metric | Count |
|---|---|
| Total AMFI members returned | **57** |
| Members with a non-empty `amc_monthly_portfolio_disclosure` | **52** |
| Members with an empty monthly URL | **5** |

The 5 AMCs with **no** monthly portfolio URL published by AMFI (all newly registered AMCs):

1. ASK MUTUAL FUND
2. Carnelian Mutual Fund
3. Lakshya Mutual Fund
4. Monarch Mutual Fund
5. Nuvama Mutual Fund

These are classified `UNKNOWN` / `NOT_VERIFIED` and are out of automation scope until AMFI
publishes a URL for them.

### Step 1 Table — all 57 AMFI members

| # | AMC | AMFI Monthly Portfolio URL |
|---|---|---|
| 1 | 360 ONE Mutual Fund | https://www.360.one/asset/mutual-funds/downloads/ |
| 2 | Abakkus Mutual Fund | https://www.abakkusmf.com/statutory-disclosures.html# |
| 3 | Aditya Birla Sun Life Mutual Fund | https://mutualfund.adityabirlacapital.com/forms-and-downloads/portfolio |
| 4 | AlphaGrep Mutual Fund | https://www.alphagrepmf.ai/disclosures |
| 5 | Angel One Mutual Fund | https://www.angelonemf.com/downloads |
| 6 | ASK MUTUAL FUND | *(none published by AMFI)* |
| 7 | Axis Mutual Fund | https://www.axismf.com/statutory-disclosures |
| 8 | Bajaj Finserv Mutual Fund | https://www.bajajamc.com/downloads?statutory-disclosures= |
| 9 | Bandhan Mutual Fund | https://bandhanmutual.com/downloads/disclosures |
| 10 | Bank of India Mutual Fund | https://www.boimf.in/investor-corner#t2 |
| 11 | Baroda BNP Paribas Mutual Fund | https://www.barodabnpparibasmf.in/downloads/monthly-portfolio-scheme |
| 12 | Canara Robeco Mutual Fund | https://www.canararobeco.com/documents/statutory-disclosures/scheme-dashboard/scheme-monthly-portfolio/ |
| 13 | Capitalmind Mutual Fund | https://capitalmindmf.com/statutory-disclosures.html# |
| 14 | Carnelian Mutual Fund | *(none published by AMFI)* |
| 15 | Choice Mutual Fund | https://choicemf.com/disclosures/monthly-portfolio |
| 16 | DSP Mutual Fund | https://www.dspim.com/about-us/mandatory-disclosure/portfolio-disclosures |
| 17 | Edelweiss Mutual Fund | https://www.edelweissmf.com/statutory#Portfolio-of-Schemes |
| 18 | Franklin Templeton Mutual Fund | https://www.franklintempletonindia.com/investor/reports?firstFilter-12 |
| 19 | Groww Mutual Fund | https://growwmf.in/statutory-disclosure/portfolio |
| 20 | HDFC Mutual Fund | https://www.hdfcfund.com/statutory-disclosure/portfolio/monthly-portfolio |
| 21 | Helios Mutual Fund | https://www.heliosmf.in/portfolio-disclosure/ |
| 22 | HSBC Mutual Fund | https://www.assetmanagement.hsbc.co.in/en/mutual-funds/investor-resources/information-library#&accordion1446811090=2 |
| 23 | ICICI Prudential Mutual Fund | https://www.icicipruamc.com/news-and-media/downloads?currentTabFilter=Disclosures&&subCatTabFilter=MonthlyPortfolioDisclosures |
| 24 | IL&FS Mutual Fund (IDF) | http://www.ilfsinfrafund.com/other.php |
| 25 | Invesco Mutual Fund | https://invescomutualfund.com/literature-and-form?tab=Complete |
| 26 | ITI Mutual Fund | https://www.itiamc.com/statuory-disclosure |
| 27 | Jio BlackRock Mutual Fund | https://www.jioblackrockamc.com/statutory-disclosure/disclosures/monthly-portfolio-disclosure |
| 28 | JM Financial Mutual Fund | https://www.jmfinancialmf.com/downloads/Portfolio-Disclosure |
| 29 | Kotak Mahindra Mutual Fund | https://www.kotakmf.com/Information/forms-and-downloads |
| 30 | Lakshya Mutual Fund | *(none published by AMFI)* |
| 31 | LIC Mutual Fund | https://www.licmf.com/downloads/monthly-portfolio |
| 32 | Mahindra Manulife Mutual Fund | https://www.mahindramanulife.com/downloads#mandatory-disclosures |
| 33 | Mirae Asset Mutual Fund | https://www.miraeassetmf.co.in/downloads/portfolio |
| 34 | Monarch Mutual Fund | *(none published by AMFI)* |
| 35 | Motilal Oswal Mutual Fund | https://www.motilaloswalmf.com/downloads/scheme-portfolio-details |
| 36 | Navi Mutual Fund | https://navi.com/mutual-fund/downloads/portfolio |
| 37 | Nippon India Mutual Fund | https://mf.nipponindiaim.com/investor-service/downloads/factsheet-portfolio-and-other-disclosures |
| 38 | NJ Mutual Fund | https://downloads.njmutualfund.com/njmf_download.php?nme=127 |
| 39 | Nuvama Mutual Fund | *(none published by AMFI)* |
| 40 | Old Bridge Mutual Fund | https://oldbridgemf.com/statutory-disclosures.html#v-pills-tabContent2 |
| 41 | PGIM India Mutual Fund | https://www.pgimindia.com/mutual-funds/disclosures/Portfolios/Monthly-Portfolio |
| 42 | PPFAS Mutual Fund | https://amc.ppfas.com/downloads/portfolio-disclosure/ |
| 43 | quant Mutual Fund | https://quantmutual.com/statutory-disclosures |
| 44 | Quantum Mutual Fund | https://www.quantumamc.com/FileCDN/FactSheet/5f6c4b6e-5264-472c-b378-d54727d45369.xlsx |
| 45 | Samco Mutual Fund | https://www.samcomf.com/StatutoryDisclosure |
| 46 | SBI Mutual Fund | https://www.sbimf.com/portfolios |
| 47 | Shriram Mutual Fund | https://www.shriramamc.in/investor-statutory-disclosures |
| 48 | Sundaram Mutual Fund | https://www.sundarammutual.com/Monthly-Fortnightly-Adhoc-Portfolios |
| 49 | Tata Mutual Fund | https://www.tatamutualfund.com/schemes-related |
| 50 | Taurus Mutual Fund | https://taurusmutualfund.com/monthly-portfolio |
| 51 | Trust Mutual Fund | https://www.trustmf.com/disclosures?activeTab=portfolio-disclosures |
| 52 | Unifi Mutual Fund | https://unifimf.com/statutorydocuments/#monthly-portfolio-disclosure |
| 53 | Union Mutual Fund | https://unionmf.com/about-us/downloads/monthly-portfolio |
| 54 | UTI Mutual Fund | https://www.utimf.com/downloads/consolidate-all-portfolio-disclosure |
| 55 | The Wealth Company Mutual Fund | https://www.wealthcompanyamc.in/literature-forms/portfolio-documents/monthly/ |
| 56 | WhiteOak Capital Mutual Fund | https://mf.whiteoakamc.com/regulatory-disclosures/scheme-portfolios |
| 57 | Zerodha Mutual Fund | https://www.zerodhafundhouse.com/resources/disclosures?source=footer |

**Note on AMFI URL quality:** several of these URLs are already stale or point at a generic
downloads page rather than the monthly portfolio section (e.g. #44 Quantum points directly at a
single `.xlsx` file with an opaque GUID; #24 IL&FS points at a legacy PHP page). AMFI's URL is
the *entry point*, not necessarily the page that actually serves monthly files — Step 2 records
the real page per AMC.

---

## Steps 2–8 — Per-AMC Investigation

*(populated continuously below as each AMC is investigated)*


Legend for `Verification`:
- **VERIFIED** — I issued the request myself and got the real bytes back (checked file magic: `PK` = xlsx/zip, `\xd0\xcf\x11\xe0` = legacy xls, `%PDF`).
- **PARTIALLY_VERIFIED** — the listing/endpoint was confirmed, but not every claim (e.g. deep history) was tested.
- **NOT_VERIFIED** — could not confirm; treat as unknown.

---

### 2. Abakkus Mutual Fund

```text
AMC:                    Abakkus Mutual Fund
AMFI URL:               https://www.abakkusmf.com/statutory-disclosures.html#
Actual portfolio page:  same page (single page, all disclosure types in tabs)

Monthly selection:      NONE. The whole historical archive is rendered in one
                        server-side HTML page. Month is encoded in the filename only.

Download mechanism:     Plain <a href> to a static file. No JS, no XHR.

API/endpoint:           None needed. Site is Strapi-backed; files live under /uploads/
HTTP method:            GET
Parameters:             none

File formats:           .xls / .xlsx (mixed), some .pdf
One file or multiple:   ONE consolidated monthly file per month (AMC-wide)
Historical months:      Yes — many years present in the same page

Recommended method:     HTML_SCRAPE -> DIRECT_FILE
Difficulty:             Easy
Verification:           VERIFIED
```

Filename patterns are **inconsistent** across months — this is the key risk:
```text
/uploads/Final_Monthly_Portfolio_Jul_31_a313e9e6dd.xls          (Jul 2026)
/uploads/IN_MF_MONTHLY_PORTFOLIO_April_30_2026_48e9b6a58c.xls   (Apr 2026)
/uploads/ABK_MF_MONTHLY_PORTFOLIO_February_28_2026_a9bc6bf1b7.xls
/uploads/Abakkus_MF_MONTHLY_PORTFOLIO_31_12_2025_562986311e.xlsx
```
Note the trailing `_<10-hex>` — that is Strapi's upload hash. **URLs are not
constructible**; they must be scraped. Verified download:
`GET https://www.abakkusmf.com/uploads/Final_Monthly_Portfolio_Jul_31_a313e9e6dd.xls`
→ `200`, 215,476 bytes, `PK` (xlsx container).

---

### 5. Angel One Mutual Fund

```text
AMFI URL:               https://www.angelonemf.com/downloads
Actual portfolio page:  same page
Monthly selection:      NONE — full archive rendered in HTML
Download mechanism:     direct <a href> to WordPress uploads on cms.angelonemf.com
API/endpoint:           none required (WordPress "formidable" uploads dir)
File formats:           .xlsx
One file or multiple:   MULTIPLE — one file PER SCHEME per month
Historical months:      Yes
Recommended method:     HTML_SCRAPE -> DIRECT_FILE
Difficulty:             Easy
Verification:           PARTIALLY_VERIFIED (listing confirmed; download not re-tested)
```
```text
https://cms.angelonemf.com/amc-cms/wp-content/uploads/formidable/20/
    Monthly-Portfolio-April-2025-Angel-One-Nifty-Total-Market-ETF-1.xlsx
```
Watch out: duplicate variants (`-1.xlsx` and `-1-1.xlsx`) exist for the same scheme/month.

---

### 9. Bandhan Mutual Fund — hardest case found so far

```text
AMFI URL:               https://bandhanmutual.com/downloads/disclosures   (generic hub, NOT the portfolio page)
Actual portfolio page:  https://bandhanmutual.com/downloads/portfolio-summary/monthly

Monthly selection:      Tab (Monthly | Weekly) + Year dropdown + Month dropdown

Download mechanism:     React SPA. Selecting a month fires an XHR whose request body
                        AND response body are both AES-encrypted blobs.

API/endpoint:           POST https://pnservices.bandhanmutual.com/internal/
                             investorservices/encdec/investor/v1/dashboard/cms-call
HTTP method:            POST
Parameters:             encrypted opaque string (note the "encdec" path segment)
Response type:          encrypted opaque string
Where file URL appears: only AFTER client-side decryption, in React component props

File formats:           .xlsx
One file or multiple:   MULTIPLE possible; July 2026 listed 1 file (Bandhan Debt Fund)
Historical months:      Yes (year + month dropdowns)

Recommended method:     PLAYWRIGHT (no practical reproducible HTTP request)
Difficulty:             Hard
Verification:           VERIFIED (mechanism + final file)
```

The decrypted payload has this shape, recovered from the React fiber:
```json
{"data":[{"name":"Bandhan Debt Fund Portfolio as on 31-07-2026",
          "url":"https://storage.googleapis.com/nonprod-static-assets-121to59kaawfgfi7bol/2026/08/dca4dae3-bandhan-debt-fund-portfolio-as-on-31-07-2026.xlsx"}],
 "totalPages":1,"currentPage":1}
```
File URL verified: `200`, 1,306,567 bytes, `PK`. The GCS object name carries a random
hash (`dca4dae3-`) so it is **not** constructible. Because both directions are encrypted,
this is the one AMC where browser automation is genuinely justified rather than lazy.

---

### 11. Baroda BNP Paribas Mutual Fund

```text
AMFI URL:               https://www.barodabnpparibasmf.in/downloads/monthly-portfolio-scheme
Monthly selection:      server-rendered list on the page
Download mechanism:     direct <a href>
File formats:           .xls (actually xlsx container)
One file or multiple:   ONE consolidated file per month
Historical months:      Yes
Recommended method:     HTML_SCRAPE -> DIRECT_FILE
Difficulty:             Easy
Verification:           VERIFIED
```
```text
https://www.barodabnpparibasmf.in/assets/download_documents/BOBBNPMF_Monthly_Portfolio_31-07-2026_19490.xls
```
Month token is `DD-MM-YYYY`, but the trailing `_19490` is an opaque document id →
**scrape, do not construct**. Verified: `200`, 14,743,317 bytes, `PK`.

---

### 13. Capitalmind Mutual Fund

```text
AMFI URL:               https://capitalmindmf.com/statutory-disclosures.html#
Monthly selection:      NONE — full archive in one server-rendered page
Download mechanism:     direct <a href> (Strapi /uploads/ + hash suffix)
File formats:           .xlsx
One file or multiple:   MULTIPLE — per scheme (CMARB, CMFLEXI, ...) per month
Historical months:      Yes
Recommended method:     HTML_SCRAPE -> DIRECT_FILE
Difficulty:             Easy
Verification:           VERIFIED
```
```text
/uploads/CMARB_Monthly_Portfolio_Disclosure_July_31_2026_3e5349f8ff.xlsx
/uploads/CMARB_Monthly_Portfolio_Disclosure_June_2026_8a657cfe63.xlsx
/uploads/CMFLEXI_Capitalmind_Monthly_Portfolio_Disclosure_Dec_2025_162c5013e2.xlsx
```
Month token varies (`July_31_2026` vs `June_2026` vs `Dec_2025`). Verified: `200`, `PK`.

---

### 16. DSP Mutual Fund

```text
AMFI URL:               https://www.dspim.com/about-us/mandatory-disclosure/portfolio-disclosures
                        (redirects to https://www.dspim.com/mandatory-disclosures/portfolio-disclosures)
Monthly selection:      NONE — very deep archive rendered in one page (back to 2013)
Download mechanism:     direct <a href>
File formats:           .zip (a ZIP per month containing the scheme files)
One file or multiple:   ONE ZIP per month, MULTIPLE files inside
Historical months:      Yes — verified as far back as Sep 2013
Recommended method:     HTML_SCRAPE -> DIRECT_FILE
Difficulty:             Easy
Verification:           VERIFIED
```
```text
https://www.dspim.com/media/pages/mandatory-disclosures/portfolio-disclosures/
    06b9bd536e-1757771555/month-end-portfolio-august-2023.zip
    001294169e-1757771557/month_end_portfolio_disclosure_september-2013.zip
```
The `<10-hex>-<10-digit-epoch>/` directory segment is a Kirby CMS content hash →
**not constructible**. Naming drifts between `month_end_portfolio_disclosure_<month><year>`
and `month-end-portfolio-<month>-<year>`. Verified: `200`, 828,893 bytes, `PK` (zip).

---

### 19. Groww Mutual Fund

```text
AMFI URL:               https://growwmf.in/statutory-disclosure/portfolio
Monthly selection:      NONE — full archive in Next.js RSC payload / HTML
Download mechanism:     direct link to an asset host
File formats:           .xls / .xlsx (mixed)
One file or multiple:   ONE consolidated file per month
Historical months:      Yes — back to Mar 2023
Recommended method:     HTML_SCRAPE -> DIRECT_FILE
Difficulty:             Easy
Verification:           VERIFIED
```
```text
https://assets-netstorage.growwmf.in/compliance_docs/Statutory%20Disclosure/Portfolio/
    2023-%202024/Monthly%20Portfolio-%20Apr%2030,%202023.xls
```
Note the URL contains spaces, a comma, and an inconsistent financial-year folder
(`2023- 2024` with a stray space). Must be URL-encoded carefully.
Verified: `200`, 596,992 bytes, legacy OLE `.xls`.

---

### 21. Helios Mutual Fund

```text
AMFI URL:               https://www.heliosmf.in/portfolio-disclosure/
Monthly selection:      NONE — full archive in one WordPress page
Download mechanism:     direct <a href> to /wp-content/uploads/YYYY/MM/
File formats:           .xls
One file or multiple:   MULTIPLE — one per scheme per month
Historical months:      Yes — back to Oct 2023
Recommended method:     HTML_SCRAPE -> DIRECT_FILE
Difficulty:             Easy
Verification:           PARTIALLY_VERIFIED
```
```text
/wp-content/uploads/2024/01/Helios-Flexi-Cap-Fund-Monthly-Portfolio-as-on-31st-December-2023.xls
/wp-content/uploads/2024/01/Helios-Overnight-Fund-Monthly-Portfolio-as-on-31st-December-2023.xls
```
**Trap:** this page mixes *fortnightly* and *monthly* files in the same list, e.g.
`helios-overnight-fund-fortnightly-portfolio-as-on-31st-july-2026.xls`. The scraper must
filter on `monthly` in the filename or it will silently ingest fortnightly data.

---

### 24. IL&FS Mutual Fund (IDF)

```text
AMFI URL:               http://www.ilfsinfrafund.com/other.php   (plain HTTP, legacy PHP site)
Monthly selection:      NONE — full archive in one page
Download mechanism:     direct <a href>
File formats:           .xls / .xlsx
One file or multiple:   ONE combined file per month — but it is a COMBINED report
                        (portfolio + fortnightly + NAV + AAUM + investor complaints in one workbook)
Historical months:      Yes
Recommended method:     HTML_SCRAPE -> DIRECT_FILE
Difficulty:             Easy (fragile site)
Verification:           VERIFIED
```
```text
https://www.ilfsinfrafund.com/otherfile/ILFS_DASHBOARD_REPORT_R1 July 2026.xlsx
https://www.ilfsinfrafund.com/otherfile/December-2023-Portfolio-Fortnightly-Monthly-NAV-AAUM-Investor-complaint.xls
```
Filenames contain **literal spaces** and the naming scheme changed several times.
Verified: `200`, 53,181 bytes, `PK`. This is an infrastructure debt fund (IDF) — it is a
single-scheme AMC, so "monthly portfolio" is not comparable to the other AMCs.

---

### 37. Nippon India Mutual Fund

```text
AMFI URL:               https://mf.nipponindiaim.com/investor-service/downloads/factsheet-portfolio-and-other-disclosures
Monthly selection:      NONE — archive rendered in HTML
Download mechanism:     direct <a href> under /InvestorServices/FactsheetsDocuments/
File formats:           .xls
One file or multiple:   ONE consolidated file per month
Historical months:      Yes — back to Jan 2013
Recommended method:     HTML_SCRAPE -> DIRECT_FILE
Difficulty:             Easy
Verification:           PARTIALLY_VERIFIED
```
```text
/InvestorServices/FactsheetsDocuments/MONTHLY-PORTFOLIO-APR-23.xls
/InvestorServices/FactsheetsDocuments/MONTHLY-PORTFOLIO-31st-JULY-2022.xls
/InvestorServices/FactsheetsDocuments/FORTNIGHTLY-MONTHLY-PORTFOLIO-31-AUG-22.xls
/InvestorServices/FactsheetsDocuments/Jan-13Monthly-portfolio.xls
```
Naming is highly irregular across years (`APR-23`, `31st-JULY-2022`, `Jan-13`) and some
files are joint fortnightly+monthly. Scrape + fuzzy month match required.

---

### 38. NJ Mutual Fund

```text
AMFI URL:               https://downloads.njmutualfund.com/njmf_download.php?nme=127
Monthly selection:      NONE — archive listed on the page
Download mechanism:     indirect — links point at a PHP viewer, not the file
File formats:           .xlsx (served through viewfile.php)
One file or multiple:   ONE consolidated file per month
Historical months:      Yes — back to Dec 2022
Recommended method:     HTML_SCRAPE -> HTTP_REQUEST
Difficulty:             Easy/Medium
Verification:           PARTIALLY_VERIFIED
```
```text
viewfile.php?file=IN-MF-MONTHLY-PORTFOLIO---August-2025-20250908104712.xlsx
viewfile.php?file=IN-MF-MONTHLY-PORTFOLIO---April-2025-Final-With-Derivative-disclosure-20250508120818.xlsx
```
This is the "do not assume the visible link is the file" case the brief warns about: the
href is a **PHP indirection** and the trailing `-YYYYMMDDHHMMSS` upload timestamp makes the
name unconstructible.

---

### 40. Old Bridge Mutual Fund

```text
AMFI URL:               https://oldbridgemf.com/statutory-disclosures.html#v-pills-tabContent2
Monthly selection:      NONE — full archive in one page (Strapi /uploads/ + hash)
Download mechanism:     direct <a href>
File formats:           .xlsx
One file or multiple:   MULTIPLE — per scheme per month
Historical months:      Yes
Recommended method:     HTML_SCRAPE -> DIRECT_FILE
Difficulty:             Easy
Verification:           PARTIALLY_VERIFIED
```
```text
/uploads/Flexi_Cap_Monthly_Portfolio_March_26_1d35b5e950.xlsx
/uploads/Focused_Fund_Monthly_Portfolio_Nov_25_10a5645f6e.xlsx
/uploads/Monthly_Portfolio_AF_Dec2025_475d9b7f54.xlsx
```
Note the two-digit year (`March_26`, `Nov_25`) mixed with `Dec2025`.

---

### 42. PPFAS Mutual Fund — cleanest site in the entire list

```text
AMFI URL:               https://amc.ppfas.com/downloads/portfolio-disclosure/
Monthly selection:      NONE — full archive in one page, grouped by year
Download mechanism:     direct <a href>
File formats:           .xls
One file or multiple:   ONE consolidated file per month
Historical months:      Yes — back to Aug 2013
Recommended method:     HTML_SCRAPE -> DIRECT_FILE  (near-constructible)
Difficulty:             Easy
Verification:           VERIFIED
```
```text
https://amc.ppfas.com/downloads/portfolio-disclosure/2026/PPFAS_Monthly_Portfolio_Report_July_31_2026.xls
https://amc.ppfas.com/downloads/portfolio-disclosure/2020/Monthly-Portfolio-july-2020.xls
https://amc.ppfas.com/downloads/portfolio-disclosure/Monthly-Portfolio-August-2013.xls
```
No hash suffix at all — the only AMC whose recent URLs are essentially predictable
(`/YYYY/PPFAS_Monthly_Portfolio_Report_<Month>_<LastDay>_<YYYY>.xls`). Older files use a
different scheme and sit in the parent directory, so still scrape rather than construct.
Verified: `200`, 407,676 bytes, `PK`.

---

### 43. quant Mutual Fund — real JSON-ish API, fully reproducible

```text
AMC:                    quant Mutual Fund
AMFI URL:               https://quantmutual.com/statutory-disclosures
Actual portfolio page:  same page, accordion "MONTHLY PORTFOLIO - FUND - WISE"

Monthly selection:      two-step. Year tab -> month tab, both AJAX.

Download mechanism:     the month call returns an HTML fragment containing the real
                        <a href> to each scheme's .xlsx

API/endpoint:           ASP.NET page methods (2 calls)
HTTP method:            POST
Content-Type:           application/json; charset=utf-8
Auth/headers:           NONE required (no cookie, no token, no referer)
```

**Step 1 — list months available in a year**
```text
POST https://quantmutual.com/statutorydisclosures.aspx/displaydisclouser1
body: {id:'2026',cat:'MONTHLY PORTFOLIO - FUND - WISE'}
```
Returns `{"d":"<ul><li id='1'...>Jan</li>...</ul>"}` — month id is **1-12**.

**Step 2 — list files for a month**
```text
POST https://quantmutual.com/statutorydisclosures.aspx/displaydisclouser2
body: {id:'7',cat:'MONTHLY PORTFOLIO - FUND - WISE',tab:'2026'}
```
Returns `{"d":"<ul><li><a href='/Admin/disclouser/quant_Small_Cap_Fund_31_Jul_2026.xlsx'>...</ul>"}`

```text
Month parameter:        id = 1..12
Year parameter:         tab = 'YYYY'  (Step 2) / id = 'YYYY' (Step 1)
Response type:          JSON envelope {"d": "<html fragment>"}
Where file URL appears: href inside the returned fragment
File formats:           .xlsx
One file or multiple:   MULTIPLE — one per scheme (fund-wise)
Historical months:      Yes — year tabs 2021..2026
Recommended method:     API
Difficulty:             Easy
Verification:           VERIFIED
```
Verified end-to-end: the Jul-2026 call returned scheme files, and
`GET https://quantmutual.com/Admin/disclouser/quant_Small_Cap_Fund_31_Jul_2026.xlsx`
→ `200`, 120,739 bytes, `PK`.

---

### 45. Samco Mutual Fund

```text
AMFI URL:               https://www.samcomf.com/StatutoryDisclosure
Monthly selection:      NONE — entire archive in one 3.2 MB page (~7,000 file links)
Download mechanism:     direct <a href>
File formats:           .xlsx
One file or multiple:   MULTIPLE — one per scheme per month
Historical months:      Yes
Recommended method:     HTML_SCRAPE -> DIRECT_FILE
Difficulty:             Easy (heavy page)
Verification:           PARTIALLY_VERIFIED
```
```text
/amc-document-download/IN_MF_MONTHLY_PORTFOLIO_Apirl_2026_SamcoELSSTaxSaverFund_1778319341.xlsx
```
Note the **typo `Apirl` for April** baked into the filenames — a hard warning against
constructing URLs from month names. Trailing `_<10-digit>` is an upload id.

---

### 46. SBI Mutual Fund — best-documented API of all 52

```text
AMC:                    SBI Mutual Fund
AMFI URL:               https://www.sbimf.com/portfolios
Actual portfolio page:  same

Monthly selection:      Frequency dropdown (Monthly/Fortnightly/Half Yearly)
                        + Year dropdown + Month dropdown

Download mechanism:     the AJAX call returns an HTML table whose <a href> are the real files

API/endpoint:           https://www.sbimf.com/ajaxcall/CMS/GetSchemePortfolioSheets
HTTP method:            POST
Content-Type:           application/json;charset=utf-8
Required headers:       none (no token needed for this endpoint)
Parameters:             {"FundId":0,"PSYear":"2026","PSMonth":"July","PSFrequency":"Monthly"}
Month parameter:        PSMonth = full English month name ("July")
Year parameter:         PSYear  = "YYYY"
Frequency parameter:    PSFrequency = "Monthly"   <-- this is what keeps us on monthly only
Response type:          HTML fragment
Where file URL appears: <a href> in the fragment
```

Example request:
```bash
curl -s -X POST "https://www.sbimf.com/ajaxcall/CMS/GetSchemePortfolioSheets" \
  -H "Content-Type: application/json;charset=utf-8" \
  --data '{"FundId":0,"PSYear":"2026","PSMonth":"July","PSFrequency":"Monthly"}'
```

```text
File formats:           .xlsx
One file or multiple:   BOTH — one consolidated "all-schemes" workbook AND one file per scheme
Historical months:      Yes (year+month params); companion endpoints
                        /ajaxcall/CMS/GetMonthsByYear and /ajaxcall/CMS/GetYearsFromSubFolder
                        enumerate what exists
Recommended method:     API
Difficulty:             Easy
Verification:           VERIFIED
```
Returned 91,677 bytes / 242 file references for Jul 2026, including:
```text
https://www.sbimf.com/docs/default-source/scheme-portfolios/all-schemes-monthly-portfolio---as-on-31st-july-2026.xlsx?sfvrsn=94250cc6_2
https://www.sbimf.com/docs/default-source/scheme-portfolios/sbi-arbitrage-opportunities-fund-monthly-portfolio---july-2026.xlsx?sfvrsn=3675b99f_2
```
The consolidated file verified: `200`, 2,638,783 bytes, `PK`. The `?sfvrsn=` Sitefinity
version token appears required — keep the query string as returned.

---

### 48. Sundaram Mutual Fund — AjaxPro endpoint, reproducible with one gotcha

```text
AMC:                    Sundaram Mutual Fund
AMFI URL:               https://www.sundarammutual.com/Monthly-Fortnightly-Adhoc-Portfolios
Actual portfolio page:  same ("Portfolio Report")

Monthly selection:      Category dropdown (Monthly / Fortnightly / AdHoc) -> "View" button.
                        The response then contains ALL financial years at once, as tabs.

Download mechanism:     the AJAX response is an HTML fragment with real <a href>

API/endpoint:           POST /ajax/Modules_Disclosure_Monthly_Fortnightly_Adhoc_Portfolios,App_Web_<hash>.ashx
                             ?_method=GetCategory&_session=no
HTTP method:            POST
Content-Type:           text/plain; charset=UTF-8
Parameters:             body = `Catid=Monthly`      <-- NOTE: NO quotes around the value
Month parameter:        none — you get every month back in one call
Year parameter:         none — every financial year returned (2012-13 .. 2026-27)
Response type:          AjaxPro envelope containing an escaped HTML fragment
```

Two gotchas that cost real debugging time:
1. Sending `Catid="Monthly"` (JSON-quoted, which is what AjaxPro's own `enc()` looks like it
   should produce) returns
   `ajax_error('System.ArgumentException','Column 'finyear' does not belong to underlying table')`.
   The browser actually sends the **bare** `Catid=Monthly`.
2. The `App_Web_<hash>` segment of the `.ashx` **rotates between deployments** (observed
   `App_Web_bt1myd23` then `App_Web_2q2bc1yf`). It must be scraped from the page on every run.

Example request:
```bash
ASHX=$(curl -s "https://www.sundarammutual.com/Monthly-Fortnightly-Adhoc-Portfolios" \
  | grep -oE '/ajax/Modules_Disclosure_Monthly_Fortnightly_Adhoc_Portfolios,App_Web_[a-z0-9]+\.ashx' | head -1)
curl -s -X POST "https://www.sundarammutual.com${ASHX}?_method=GetCategory&_session=no" \
  -H "Content-Type: text/plain; charset=UTF-8" --data 'Catid=Monthly'
```

```text
File formats:           .xlsx
One file or multiple:   MULTIPLE — exactly 2 per month:
                          "Monthly Portfolio Disclosure Equity & Fund of Funds - Jul 2026"
                          "Monthly Portfolio Disclosure Fixed Income - Jul 2026"
Historical months:      Yes — financial years 2012-2013 through 2026-2027
Recommended method:     API
Difficulty:             Medium (the two gotchas above)
Verification:           VERIFIED
```
```text
/uploaddir/MonthlyPortfolio/monthlyportfolio_100826111829.xlsx   (Equity & FoF, Jul 2026)
/uploaddir/MonthlyPortfolio/monthlyportfolio_100826111919.xlsx   (Fixed Income,  Jul 2026)
```
Filenames are `monthlyportfolio_<DDMMYYHHMMSS>.xlsx` — an **upload timestamp, not the data
month**. The month is only in the link text, so the label must be parsed, not the filename.
Verified: `200`, 568,660 bytes, `PK`.

---

### 52. Unifi Mutual Fund

```text
AMFI URL:               https://unifimf.com/statutorydocuments/#monthly-portfolio-disclosure
Monthly selection:      NONE — archive in one WordPress page
Download mechanism:     direct <a href> to /wp-content/uploads/fund-sheets/
File formats:           .xlsx (portfolios) and .pdf (hosting notices)
One file or multiple:   ONE consolidated file per month
Historical months:      Yes
Recommended method:     HTML_SCRAPE -> DIRECT_FILE
Difficulty:             Easy
Verification:           PARTIALLY_VERIFIED
```
```text
https://unifimf.com/wp-content/uploads/fund-sheets/Monthly-Portfolio-April-25.xlsx
https://unifimf.com/wp-content/uploads/fund-sheets/Notice_Monthly-portfolio_07082026.pdf
```
**Trap:** the `Notice_Monthly-portfolio_*.pdf` files are newspaper hosting notices, *not*
portfolio data. Filter to `.xlsx` and exclude `Notice`.

---

### 55. The Wealth Company Mutual Fund

```text
AMFI URL:               https://www.wealthcompanyamc.in/literature-forms/portfolio-documents/monthly/
Monthly selection:      page is Next.js; the CURRENT month's files are embedded in the RSC payload
Download mechanism:     direct file URL once extracted from the RSC payload
API/endpoint:           none exposed; data ships inside the server-rendered payload
File formats:           .xlsx
One file or multiple:   MULTIPLE — one per scheme (Flexi Cap, Arbitrage, Gold ETF, Liquid, ...)
Historical months:      NOT_VERIFIED — only current month observed in the payload
Recommended method:     HTML_SCRAPE (RSC payload) -> DIRECT_FILE
Difficulty:             Medium
Verification:           VERIFIED (current month only)
```
```text
/uploads/Monthly_Portfolio_Flexi_Cap_05fb3f1328.xlsx
/uploads/Monthly_Portfolio_Gold_ETF_FOF_4210f6fb68.xlsx
```
Strapi hash suffix again. Note the filenames carry **no month at all** — the month is
implicit in the page. Two hashes exist per scheme name (e.g. `Flexi_Cap_05fb3f1328` and
`Flexi_Cap_ee10698f55`), so month attribution must come from the page structure, not the
filename. Verified: `200`, `PK`.


---

### 1. 360 ONE Mutual Fund

```text
AMFI URL:               https://www.360.one/asset/mutual-funds/downloads/
Monthly selection:      NONE — full archive embedded in the Next.js RSC payload
Download mechanism:     direct file URL on S3 (extracted from RSC payload, not from <a href>)
API/endpoint:           none exposed; data ships in the server-rendered payload
File formats:           .xls / .xlsx (mixed)
One file or multiple:   ONE consolidated file per month
Historical months:      Yes — 25 monthly files found, Nov 2023 .. Jul 2026
Recommended method:     HTML_SCRAPE (RSC payload) -> DIRECT_FILE
Difficulty:             Medium (must decode the RSC payload; links are NOT in the DOM)
Verification:           VERIFIED
```
```text
https://s3.ap-south-1.amazonaws.com/x-web-s3.360.one/360_ONE_MF_MONTHLY_PORTFOLIO_Nov2024_e300b9746e.xlsx
```
Verified: `200`, 181,865 bytes, `PK`.

**This AMC is the single best argument against constructing URLs.** The 25 month tokens found:
```text
Nov2023  Dec2023  Jan2024 Feb2024 Mar2024 Apr2024 May2024 Jun2024 July2024 Aug2024
Sept2024 Oct2024  Nov2024 Dec2024 Jan2025 June2025 Sept2025 Oct2025 Nov2025 dec2025
feb2026  May2026  June2026 July2026
```
Within one AMC: `Jun2024` **and** `June2025`; `July2024` (not `Jul`); `Sept2024` (not `Sep`);
lowercase `dec2025` and `feb2026`; plus both `360_ONE_MF_` and `360_ONE_` prefixes, both `.xls`
and `.xlsx`, and a Strapi hash on every file. No generator could guess these.

---

### 20. HDFC Mutual Fund

```text
AMFI URL:               https://www.hdfcfund.com/statutory-disclosure/portfolio/monthly-portfolio
Monthly selection:      NONE on this page — it shows the LATEST month only
Download mechanism:     direct <a href> to files.hdfcfund.com (Drupal s3fs)
API/endpoint:           none needed for the files
File formats:           .xlsx
One file or multiple:   MULTIPLE — 109 links, one per scheme
Historical months:      NOT_VERIFIED — no month/year selector present on this page
Recommended method:     PLAYWRIGHT (listing) -> DIRECT_FILE (download)
Difficulty:             Medium
Verification:           VERIFIED (current month)
```
**Split behaviour worth noting:** the HTML page returns **403** to plain `curl` (bot protection,
`Sec-Fetch`/TLS fingerprinting — browser-like headers alone did not defeat it), but the file host
`files.hdfcfund.com` is **completely open** to `curl`. So browser automation is needed only to
*list* the files; downloading is plain HTTP.
```text
https://files.hdfcfund.com/s3fs-public/2026-08/Monthly%20HDFC%20Small%20Cap%20Fund%20-%2031%20July%202026.xlsx
```
Verified: `200`, `PK`. Note the path segment is `2026-08` (the *upload* month) while the data is
**July 2026** — publication month ≠ data month. Any date logic must read the filename, not the path.

---

### 23. ICICI Prudential Mutual Fund

```text
AMFI URL:               https://www.icicipruamc.com/news-and-media/downloads?currentTabFilter=Disclosures&&subCatTabFilter=MonthlyPortfolioDisclosures
                        (301s to /media-center/downloads?... — same query string)
Actual portfolio page:  as above; React SPA

Monthly selection:      "Document Type" filter (= Monthly Portfolio Disclosures)
                        + "Financial Year" filter

Download mechanism:     DOWNLOAD button, not an <a href>. The real URL is carried in the
                        component's `knowledgeCenterData.url` field.

API/endpoint:           https://apimf.icicipruamc.com/nms/v1/downloads/files
                        (companion: /nms/v1/downloads/categories?userType=Investor)
HTTP method:            GET
Parameters:             NOT_VERIFIED — a bare GET returns {"statusCode":404,"message":"Resource not found"};
                        required query parameters not yet determined
Category constant:      MONTHLY_PORTFOLIO_DISCSLO_DWND

File formats:           .zip  (one ZIP containing all scheme files)
One file or multiple:   ONE ZIP per month
Historical months:      Yes via the Financial Year filter (enumeration not verified)
Recommended method:     API (once params are pinned down); PLAYWRIGHT as fallback
Difficulty:             Medium
Verification:           PARTIALLY_VERIFIED — file URL VERIFIED, listing params NOT_VERIFIED
```
Verified download (25 MB ZIP):
```text
https://www.icicipruamc.com/blob/downloads/Files/Monthly Portfolio Disclosures/2026/July/Monthly-Portfolio-Disclosure-July-2026.zip
```
**I explicitly tested whether this path is constructible — it is not:**
```text
2026/July/Monthly-Portfolio-Disclosure-July-2026.zip    -> 200, 25,265,087 bytes
2026/March/Monthly-Portfolio-Disclosure-March-2026.zip  -> 404
2024/July/Monthly-Portfolio-Disclosure-July-2024.zip    -> 404
```
So even where a URL *looks* like a clean template, only the listing tells the truth.

---

### 53. Union Mutual Fund — AMFI's URL is broken

```text
AMFI URL:               https://unionmf.com/about-us/downloads/monthly-portfolio     <-- DOES NOT RESOLVE
Actual portfolio page:  https://www.unionmf.com/about-us/downloads/monthly-portfolio  <-- works (note www.)

Monthly selection:      Year dropdown (2011..2026) + Month dropdown (1..12), AngularJS,
                        filtered PURELY CLIENT-SIDE (no request on change)

Download mechanism:     direct <a href>; every file is already in the page

API/endpoint:           none — the full dataset is inlined as a series of JS statements:
                        downloadMonthPortfolio.push({Title:"...", Url:"...", DocYear:"..."})

File formats:           .xlsx
One file or multiple:   MULTIPLE — ~20 files per month (one per scheme)
Historical months:      Yes — 1,519 entries embedded in one page
Recommended method:     HTML_SCRAPE (regex the push() calls) -> DIRECT_FILE
Difficulty:             Easy
Verification:           VERIFIED
```
The apex domain `unionmf.com` has **no DNS A record** (`Can't find unionmf.com: No answer`),
while `www.unionmf.com` resolves to `103.96.92.113`. **AMFI's published URL is dead as-is** —
the loader needs a host-normalisation step (try `www.`) or a per-AMC URL override.

```text
https://www.unionmf.com/docs/default-source/downloads/scheme-disclosures/portfolios-disclosure/
    month-portfolios/october-2022/monthly-portfolio-report-union-long-term-equity-fund-31.10.2022.xlsx?sfvrsn=143ef6c0_1
```
Verified: `200`, 232,466 bytes, `PK`.

Two data-quality traps:
1. Every record has `DocYear: "1900-01-01"` — the field the Angular year filter sorts on is
   **garbage**. Do not trust it; parse the month from the `Title` (`31.10.2022`) or the URL path
   segment (`month-portfolios/october-2022/`).
2. The dropdown advertises 2011-2026 but only ~16 distinct months appear in the
   `month-portfolios/<month>-<year>/` path form; older records use other path shapes.

---

## AMCs investigated but NOT fully resolved

These were each opened and inspected individually (page type, framework, bot protection,
where the monthly section lives). None are extrapolated from a neighbour. For each I record
what was actually established and what remains open, per Rule 10.

### 3. Aditya Birla Sun Life Mutual Fund
```text
AMFI URL:      https://mutualfund.adityabirlacapital.com/forms-and-downloads/portfolio
Established:   Page loads (200). Confirmed via <meta name="description"> that this IS the
               monthly portfolio page ("Download monthly portfolio of ... every month").
               Only 3 file links in the server HTML, none of them portfolios => the listing
               is rendered client-side.
Open:          Listing API not identified.
Method:        UNKNOWN (likely JAVASCRIPT_API)     Difficulty: Medium     Verification: NOT_VERIFIED
```

### 4. AlphaGrep Mutual Fund
```text
AMFI URL:      https://www.alphagrepmf.ai/disclosures
Established:   Angular SPA (`data-beasties-container`, `<base href="/">`). 58 KB shell,
               zero file links server-side. Very new/small AMC.
Open:          Backing API not identified.
Method:        UNKNOWN (likely JAVASCRIPT_API)     Difficulty: Medium     Verification: NOT_VERIFIED
```

### 7. Axis Mutual Fund
```text
AMFI URL:      https://www.axismf.com/statutory-disclosures
Established:   Next.js. RSC payload contains a "Portfolios" nested-list section with
               sdType:"nestedList" and a disclaimer stating fortnightly/monthly statements
               are hosted on the site. Only 5 unrelated PDFs in the payload => portfolio
               list is fetched on demand.
Open:          The nestedList data endpoint.
Method:        UNKNOWN (likely JAVASCRIPT_API)     Difficulty: Medium     Verification: NOT_VERIFIED
```

### 8. Bajaj Finserv Mutual Fund
```text
AMFI URL:      https://www.bajajamc.com/downloads?statutory-disclosures=
Established:   WordPress, 2.4 MB page, 763 file links. A "Monthly Portfolio" accordion
               section exists in the HTML but its panel is populated on expand — the only
               month-ish file in the static HTML is an ad-hoc mid-month small cap file.
Open:          The accordion's data source.
Method:        UNKNOWN (HTML_SCRAPE or JAVASCRIPT_API)   Difficulty: Medium   Verification: NOT_VERIFIED
```

### 10. Bank of India Mutual Fund
```text
AMFI URL:      https://www.boimf.in/investor-corner#t2   (a tab anchor, not a portfolio page)
Established:   jQuery site, 69 KB, only 1 unrelated PDF server-side. Meta keywords do include
               "monthly portfolio", so the section exists behind the #t2 tab.
Open:          Tab content loader.
Method:        UNKNOWN     Difficulty: Medium     Verification: NOT_VERIFIED
```

### 12. Canara Robeco Mutual Fund
```text
AMFI URL:      https://www.canararobeco.com/documents/statutory-disclosures/scheme-dashboard/scheme-monthly-portfolio/
Established:   WordPress. AMFI's URL is precise (monthly-only page). Confirmed a year
               query parameter is used: ?searchyear=2025-26 (financial-year format).
               Requesting that URL returned no file links to my scraper => list is JS-rendered.
Open:          Whether searchyear alone drives a server-rendered list.
Method:        HTTP_REQUEST (promising — explicit year param)  Difficulty: Medium  Verification: NOT_VERIFIED
```

### 15. Choice Mutual Fund
```text
AMFI URL:      https://choicemf.com/disclosures/monthly-portfolio     (monthly-specific — good)
Established:   Next.js App Router. RSC payload is tiny (4.9 KB) and contains no file URLs =>
               content fetched client-side after hydration.
Open:          Client API.
Method:        UNKNOWN (likely JAVASCRIPT_API)   Difficulty: Medium   Verification: NOT_VERIFIED
```

### 17. Edelweiss Mutual Fund
```text
AMFI URL:      https://www.edelweissmf.com/statutory#Portfolio-of-Schemes
Established:   Returns HTTP 403 to curl even with full browser-like headers
               (User-Agent, Accept, Accept-Language, Sec-Fetch-*, Upgrade-Insecure-Requests).
               Bot protection at the edge.
Open:          Everything past the block.
Method:        PLAYWRIGHT (forced by bot protection)   Difficulty: Hard   Verification: NOT_VERIFIED
```

### 18. Franklin Templeton Mutual Fund
```text
AMFI URL:      https://www.franklintempletonindia.com/investor/reports?firstFilter-12
               (redirects to /reports, dropping the filter — the AMFI deep-link is stale)
Established:   4.7 KB SPA shell with window.prerenderReady=false (prerender-dependent).
Open:          Reports API.
Method:        UNKNOWN (likely JAVASCRIPT_API)   Difficulty: Medium   Verification: NOT_VERIFIED
```

### 22. HSBC Mutual Fund
```text
AMFI URL:      https://www.assetmanagement.hsbc.co.in/en/mutual-funds/investor-resources/information-library#&accordion1446811090=2
Established:   Read timeout after 45 s on repeated attempts — the origin did not respond.
Open:          Everything.
Method:        UNKNOWN     Difficulty: Hard     Verification: NOT_VERIFIED
```

### 25. Invesco Mutual Fund
```text
AMFI URL:      https://invescomutualfund.com/literature-and-form?tab=Complete
               redirects to https://www.invescomutualfund.com/literature-forms/monthly-holdings
               (the redirect target is monthly-specific — useful)
Established:   Next.js, 1.8 MB page. RSC payload decoded (1.54 MB) but contains only 3
               unrelated PDFs => holdings table is client-fetched.
Open:          Holdings API.
Method:        UNKNOWN (likely JAVASCRIPT_API)   Difficulty: Medium   Verification: NOT_VERIFIED
```

### 26. ITI Mutual Fund
```text
AMFI URL:      https://www.itiamc.com/statuory-disclosure    (AMFI's typo "statuory" is the real path)
Established:   Angular app, 275 KB, zero file links server-side.
Open:          Backing API.
Method:        UNKNOWN (likely JAVASCRIPT_API)   Difficulty: Medium   Verification: NOT_VERIFIED
```

### 27. Jio BlackRock Mutual Fund
```text
AMFI URL:      https://www.jioblackrockamc.com/statutory-disclosure/disclosures/monthly-portfolio-disclosure
Established:   Next.js. RSC payload decoded (86 KB) — contains one unrelated PDF on an Azure
               Front Door CDN (cdnstorage-*.azurefd.net) => files live on Azure FD, list is
               client-fetched.
Open:          Listing API.
Method:        UNKNOWN (likely JAVASCRIPT_API)   Difficulty: Medium   Verification: NOT_VERIFIED
```

### 28. JM Financial Mutual Fund
```text
AMFI URL:      https://www.jmfinancialmf.com/downloads/Portfolio-Disclosure
Established:   3 KB React shell. Routes enumerated from main.db228524.js confirm
               /downloads/Portfolio-Disclosure is a real client route.
Open:          Data API (only routes, not endpoints, were recoverable from the bundle).
Method:        UNKNOWN (likely JAVASCRIPT_API)   Difficulty: Medium   Verification: NOT_VERIFIED
```

### 29. Kotak Mahindra Mutual Fund
```text
AMFI URL:      https://www.kotakmf.com/Information/forms-and-downloads
Established:   Redirected to validate.perfdrive.com — Radware Bot Manager challenge.
               Also note AMFI's URL is a generic downloads hub, not the monthly page.
Open:          Everything past the bot wall.
Method:        PLAYWRIGHT (forced)   Difficulty: Hard   Verification: NOT_VERIFIED
```

### 31. LIC Mutual Fund
```text
AMFI URL:      https://www.licmf.com/downloads/monthly-portfolio    (monthly-specific — good)
Established:   Page title confirms "Monthly Portfolio / Fortnightly Portfolio". Sibling routes
               /downloads/fortnightly-portfolio and /downloads/consolidated-portfolio exist,
               so the monthly/fortnightly split is by URL. 42 file links present but none are
               portfolios (they are policy PDFs + a dashboard xlsx) => list is JS-rendered.
Open:          Listing mechanism.
Method:        UNKNOWN     Difficulty: Medium     Verification: NOT_VERIFIED
```

### 32. Mahindra Manulife Mutual Fund
```text
AMFI URL:      https://www.mahindramanulife.com/downloads#mandatory-disclosures
Established:   9 KB React SPA shell (react-helmet-async), no server-side content.
Open:          Data API.
Method:        UNKNOWN (likely JAVASCRIPT_API)   Difficulty: Medium   Verification: NOT_VERIFIED
```

### 33. Mirae Asset Mutual Fund
```text
AMFI URL:      https://www.miraeassetmf.co.in/downloads/portfolio
Established:   Bootstrap tabs confirmed in HTML: "Monthly Portfolio" (#nav-portfolio-tab1),
               "Fortnightly Portfolio" (tab3), "Half Year Portfolio" (tab2) — so the monthly
               tab is identifiable. Its <ul id="nav-portfolio1"> is EMPTY server-side and there
               is a pagination widget => AJAX-filled, paginated.
Open:          The AJAX endpoint behind tab1.
Method:        UNKNOWN (likely JAVASCRIPT_API)   Difficulty: Medium   Verification: NOT_VERIFIED
```

### 35. Motilal Oswal Mutual Fund
```text
AMFI URL:      https://www.motilaloswalmf.com/downloads/scheme-portfolio-details
Established:   AEM (Adobe) site, 38 KB, 21 links but zero portfolio files server-side.
Open:          Listing API.
Method:        UNKNOWN (likely JAVASCRIPT_API)   Difficulty: Medium   Verification: NOT_VERIFIED
```

### 36. Navi Mutual Fund
```text
AMFI URL:      https://navi.com/mutual-fund/downloads/portfolio
Established:   WordPress-backed (wp-content present), 104 KB, zero file links server-side.
               A wp-json REST endpoint is plausible but was not confirmed.
Open:          Listing source.
Method:        UNKNOWN (WordPress REST is the first thing to try)  Difficulty: Medium  Verification: NOT_VERIFIED
```

### 41. PGIM India Mutual Fund
```text
AMFI URL:      https://www.pgimindia.com/mutual-funds/disclosures/Portfolios/Monthly-Portfolio
               (monthly-specific — good; sibling .../Portfolios/Fortnightly-portfolio confirms the split)
Established:   Angular. 184 KB page, only 4 file links, all unrelated (incl. an AMFI circular).
Open:          Listing API.
Method:        UNKNOWN (likely JAVASCRIPT_API)   Difficulty: Medium   Verification: NOT_VERIFIED
```

### 44. Quantum Mutual Fund — AMFI URL points at a single file
```text
AMFI URL:      https://www.quantumamc.com/FileCDN/FactSheet/5f6c4b6e-5264-472c-b378-d54727d45369.xlsx
Established:   This is NOT a page. It returns 200 with
               Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml...,
               952,771 bytes — i.e. AMFI has published a direct link to ONE .xlsx whose name is
               an opaque GUID. There is no month in the URL and no listing page to walk.
Implication:   The GUID presumably points at "the current month" and is silently replaced, or it
               is frozen at whatever month it was when AMFI recorded it. Either way there is no
               way to request a SPECIFIC month from this URL.
Open:          Quantum's real portfolio listing page (not published by AMFI for monthly).
Method:        DIRECT_FILE for "latest" only; needs a different source for history
Difficulty:    Medium     Verification: PARTIALLY_VERIFIED (file downloads; month unknown)
```

### 45/47. Shriram Mutual Fund
```text
AMFI URL:      https://www.shriramamc.in/investor-statutory-disclosures
Established:   Next.js; RSC payload decoded (881 KB) with 2,791 file links. The "Monthly"
               matches are all under .../Status-of-Redressal-of-Investor-Complaints/Monthly/...
               — i.e. investor-complaint reports, NOT portfolio disclosures. The portfolio
               section is elsewhere/lazy-loaded.
Open:          Portfolio listing.
Method:        UNKNOWN     Difficulty: Medium     Verification: NOT_VERIFIED
```
Flagged because a naive "filter links containing `monthly`" scraper **would ingest the wrong
documents here** and look successful.

### 49. Tata Mutual Fund
```text
AMFI URL:      https://www.tatamutualfund.com/schemes-related
               (redirects to /schemes-related/scheme-factsheet — factsheets, not portfolios;
                the AMFI link lands on the wrong sub-tab)
Established:   Next.js; RSC decoded (615 KB), 74 file links — all scheme FACTSHEET PDFs on
               betacms.tatamutualfund.com, plus one xlsx that is a Debt Index Replication Factor.
               No monthly portfolio files on the landing tab.
Open:          The portfolio tab and its data source.
Method:        UNKNOWN     Difficulty: Medium     Verification: NOT_VERIFIED
```

### 50. Taurus Mutual Fund
```text
AMFI URL:      https://taurusmutualfund.com/monthly-portfolio     (monthly-specific — good)
Established:   Drupal 8. The page is a Views listing with an EXPOSED FILTER — form controls
               edit-field-monthly-portfolio-target-id / field_monthly_portfolio_target_id and
               edit-submit-monthly-portfolio are present in the HTML. Exposed Drupal filters are
               normally drivable by GET query parameters, which would make this HTTP_REQUEST.
               No portfolio files appeared in the default (unfiltered) render.
Open:          Exact query-parameter names/values.
Method:        HTTP_REQUEST (promising)   Difficulty: Medium   Verification: NOT_VERIFIED
```

### 51. Trust Mutual Fund
```text
AMFI URL:      https://www.trustmf.com/disclosures?activeTab=portfolio-disclosures
Established:   947-byte Vite/React shell (<div id="root">). Bundle analysis found only client
               routes plus an unrelated host online.trustmf.com. Notably the bundle embeds
               OOXML schema strings => it builds/parses XLSX in the browser.
Open:          Data API.
Method:        UNKNOWN (likely JAVASCRIPT_API)   Difficulty: Medium   Verification: NOT_VERIFIED
```

### 54. UTI Mutual Fund
```text
AMFI URL:      https://www.utimf.com/downloads/consolidate-all-portfolio-disclosure
Established:   14 KB Angular shell (<base href="/">). Only third-party JS matched an API-ish
               pattern (/api2/event/ from Evergage/Salesforce personalisation) — no UTI endpoint
               recovered from the loaded bundles.
Open:          Data API.
Method:        UNKNOWN (likely JAVASCRIPT_API)   Difficulty: Medium   Verification: NOT_VERIFIED
```

### 56. WhiteOak Capital Mutual Fund
```text
AMFI URL:      https://mf.whiteoakamc.com/regulatory-disclosures/scheme-portfolios
Established:   Next.js. RSC payload decoded (52 KB) — only 3 unrelated PDFs, on
               content.whiteoakamc.com and a wocamc-prd-prelogin S3 bucket. Those hosts are
               where portfolio files will live.
Open:          Listing API.
Method:        UNKNOWN (likely JAVASCRIPT_API)   Difficulty: Medium   Verification: NOT_VERIFIED
```

### 57. Zerodha Mutual Fund
```text
AMFI URL:      https://www.zerodhafundhouse.com/resources/disclosures?source=footer
Established:   Next.js, 650 KB page, 392 file links. RSC decoded (268 KB) — the only xlsx files
               are NAV files (NAV_29.04.2026.xlsx, ZERODMFFUNOUT_AMFI_NAV.xlsx) on
               assets.zerodhafundhouse.com. No monthly portfolio files in the payload.
Open:          Portfolio section / API.
Method:        UNKNOWN     Difficulty: Medium     Verification: NOT_VERIFIED
```

### 6, 14, 30, 34, 39 — ASK / Carnelian / Lakshya / Monarch / Nuvama
```text
AMFI URL:      (none published)
Method:        UNKNOWN     Difficulty: n/a     Verification: NOT_VERIFIED
```
AMFI publishes no monthly portfolio URL for these five members. Out of scope until it does.
The loader should surface them as "no source" rather than silently skipping them.


---

## Step 10 — Grouping

The point of grouping is that **one code path should serve many AMCs**. What actually
differentiates these 52 sites is not the AMC — it is *where the list of files lives*.

```text
GROUP A — "Whole archive is already in the HTML"        (14 AMCs)  <-- the jackpot group
```
One GET of the AMFI-provided URL returns every month ever published. No month selection
exists at all; you filter client-side by parsing filenames/labels.
```text
  2  Abakkus            5  Angel One          11 Baroda BNP Paribas
 13  Capitalmind        16 DSP                19 Groww
 21  Helios             24 IL&FS (IDF)        37 Nippon India
 38  NJ                 40 Old Bridge         42 PPFAS
 45  Samco              52 Unifi              53 Union*
```
\* Union's data is in inline `push()` calls rather than `<a href>`, but it is the same idea:
one GET, whole archive. Sub-variant A2 (payload rather than DOM): **1 360 ONE**, **55 The Wealth
Company** — the archive is in the Next.js RSC payload and must be decoded first.

```text
GROUP B — "Reproducible HTTP/API call with month+year parameters"   (4 AMCs verified)
```
A real endpoint you can call from Python with `requests`. Best-quality group.
```text
 43  quant       POST /statutorydisclosures.aspx/displaydisclouser2  {id:MM, tab:YYYY, cat:...}
 46  SBI         POST /ajaxcall/CMS/GetSchemePortfolioSheets         {PSYear, PSMonth, PSFrequency}
 48  Sundaram    POST /ajax/...GetCategory                            Catid=Monthly (returns all years)
 23  ICICI Pru   GET  apimf.icicipruamc.com/nms/v1/downloads/files   (params NOT_VERIFIED)
```

```text
GROUP C — "Listing needs a browser, download does not"   (2 AMCs confirmed + most of Group E)
```
The HTML is bot-protected or client-rendered, but the resulting file URLs are plain, open HTTP.
Render once to harvest URLs, then download with `requests`.
```text
 20  HDFC        page 403s to curl; files.hdfcfund.com is wide open
 29  Kotak       Radware bot manager on the page
 17  Edelweiss   403 at the edge
```

```text
GROUP D — "Encrypted / opaque transport"    (1 AMC)
```
```text
  9  Bandhan     request AND response bodies are AES blobs (…/encdec/…);
                 file URL only exists after client-side decryption
```
Genuinely requires browser automation. This is the only AMC where I would not argue.

```text
GROUP E — "Client-rendered, endpoint not yet identified"   (24 AMCs)
```
```text
  3 ABSL        4 AlphaGrep    7 Axis        8 Bajaj Finserv   10 Bank of India
 12 Canara*    15 Choice      18 Franklin   22 HSBC**         25 Invesco
 26 ITI        27 Jio BlackRock  28 JM      31 LIC            32 Mahindra Manulife
 33 Mirae      35 Motilal     36 Navi       41 PGIM           47 Shriram
 49 Tata       50 Taurus*     51 Trust      54 UTI            56 WhiteOak   57 Zerodha
```
\* **12 Canara** (`?searchyear=2025-26`) and **50 Taurus** (Drupal exposed filter) both showed
concrete evidence of GET-parameter-driven filtering — they are the two most likely to drop
into Group B with a little more work. \*\* **22 HSBC** never responded.

```text
GROUP F — "No usable source"    (6 AMCs)
```
```text
  6 ASK    14 Carnelian    30 Lakshya    34 Monarch    39 Nuvama   (no AMFI URL at all)
 44 Quantum  (AMFI URL is a single GUID .xlsx; no month can be requested)
```

### The single most important cross-cutting finding

**Across all 52 AMCs I did not find one where monthly file URLs can be reliably constructed
from a template.** Every archive-style AMC appends an opaque token:

| AMC | Opaque component |
|---|---|
| Abakkus, Capitalmind, Old Bridge, 360 ONE, Wealth Company | Strapi 10-hex hash (`_a313e9e6dd`) |
| DSP | Kirby content hash + epoch (`06b9bd536e-1757771555/`) |
| Baroda BNP | document id (`_19490`) |
| Samco | upload id (`_1778319341`) |
| NJ | upload timestamp (`-20250908104712`) |
| Sundaram | upload timestamp *as the entire filename* (`monthlyportfolio_100826111829.xlsx`) |
| SBI, Union | Sitefinity `?sfvrsn=` version token |
| Bandhan | random GCS object prefix (`dca4dae3-`) |
| ICICI | verified 404s on constructed paths for other months |

Add to that the month-token chaos *within a single AMC* (360 ONE: `Jun2024` **and** `June2025`,
`July2024`, `Sept2024`, lowercase `dec2025`) and the outright typos (Samco ships
`IN_MF_MONTHLY_PORTFOLIO_Apirl_2026_...`), and the conclusion is forced:

> **Always discover URLs from a listing. Never generate them.**

Also cross-cutting: **publication month ≠ data month** (HDFC's July data sits under `2026-08/`;
Sundaram's filename timestamp is the upload moment). And **AMFI's URL is an entry point, not
the answer** — for Bandhan, Kotak, Tata, Franklin, Bank of India and Mahindra it lands on a
generic hub or the wrong tab, and for Union it does not resolve at all.

---

## Step 11 — Master Table

`Endpoint?` = a callable API endpoint was found. `Hist?` = historical months reachable.

| # | AMC | Method | Endpoint? | File Type | Hist? | Files/Month | Difficulty | Verified? |
|---|---|---|---|---|---|---|---|---|
| 1 | 360 ONE | HTML_SCRAPE (RSC) | – | xls/xlsx | Yes (25 mo) | 1 | Medium | VERIFIED |
| 2 | Abakkus | HTML_SCRAPE | – | xls/xlsx | Yes | 1 | Easy | VERIFIED |
| 3 | Aditya Birla SL | UNKNOWN | No | ? | ? | ? | Medium | NOT_VERIFIED |
| 4 | AlphaGrep | UNKNOWN | No | ? | ? | ? | Medium | NOT_VERIFIED |
| 5 | Angel One | HTML_SCRAPE | – | xlsx | Yes | Many (per scheme) | Easy | PARTIALLY_VERIFIED |
| 6 | ASK | UNKNOWN | No AMFI URL | – | – | – | – | NOT_VERIFIED |
| 7 | Axis | UNKNOWN | No | ? | ? | ? | Medium | NOT_VERIFIED |
| 8 | Bajaj Finserv | UNKNOWN | No | ? | ? | ? | Medium | NOT_VERIFIED |
| 9 | Bandhan | PLAYWRIGHT | Yes (encrypted) | xlsx | Yes | Several | Hard | VERIFIED |
| 10 | Bank of India | UNKNOWN | No | ? | ? | ? | Medium | NOT_VERIFIED |
| 11 | Baroda BNP Paribas | HTML_SCRAPE | – | xls | Yes | 1 | Easy | VERIFIED |
| 12 | Canara Robeco | HTTP_REQUEST? | `?searchyear=` seen | ? | Likely | ? | Medium | NOT_VERIFIED |
| 13 | Capitalmind | HTML_SCRAPE | – | xlsx | Yes | Many (per scheme) | Easy | VERIFIED |
| 14 | Carnelian | UNKNOWN | No AMFI URL | – | – | – | – | NOT_VERIFIED |
| 15 | Choice | UNKNOWN | No | ? | ? | ? | Medium | NOT_VERIFIED |
| 16 | DSP | HTML_SCRAPE | – | **zip** | Yes (to 2013) | 1 zip (many inside) | Easy | VERIFIED |
| 17 | Edelweiss | PLAYWRIGHT | No (403) | ? | ? | ? | Hard | NOT_VERIFIED |
| 18 | Franklin Templeton | UNKNOWN | No | ? | ? | ? | Medium | NOT_VERIFIED |
| 19 | Groww | HTML_SCRAPE | – | xls/xlsx | Yes (to 2023) | 1 | Easy | VERIFIED |
| 20 | HDFC | PLAYWRIGHT→DIRECT | No | xlsx | No (latest only) | 109 (per scheme) | Medium | VERIFIED |
| 21 | Helios | HTML_SCRAPE | – | xls | Yes (to 2023) | Many (per scheme) | Easy | PARTIALLY_VERIFIED |
| 22 | HSBC | UNKNOWN | No (timeout) | ? | ? | ? | Hard | NOT_VERIFIED |
| 23 | ICICI Prudential | API | **Yes** (params TBD) | **zip** | Yes | 1 zip | Medium | PARTIALLY_VERIFIED |
| 24 | IL&FS (IDF) | HTML_SCRAPE | – | xls/xlsx | Yes | 1 (combined report) | Easy | VERIFIED |
| 25 | Invesco | UNKNOWN | No | ? | ? | ? | Medium | NOT_VERIFIED |
| 26 | ITI | UNKNOWN | No | ? | ? | ? | Medium | NOT_VERIFIED |
| 27 | Jio BlackRock | UNKNOWN | No | ? | ? | ? | Medium | NOT_VERIFIED |
| 28 | JM Financial | UNKNOWN | No | ? | ? | ? | Medium | NOT_VERIFIED |
| 29 | Kotak Mahindra | PLAYWRIGHT | No (bot wall) | ? | ? | ? | Hard | NOT_VERIFIED |
| 30 | Lakshya | UNKNOWN | No AMFI URL | – | – | – | – | NOT_VERIFIED |
| 31 | LIC | UNKNOWN | No | ? | ? | ? | Medium | NOT_VERIFIED |
| 32 | Mahindra Manulife | UNKNOWN | No | ? | ? | ? | Medium | NOT_VERIFIED |
| 33 | Mirae Asset | UNKNOWN | No | ? | ? | ? | Medium | NOT_VERIFIED |
| 34 | Monarch | UNKNOWN | No AMFI URL | – | – | – | – | NOT_VERIFIED |
| 35 | Motilal Oswal | UNKNOWN | No | ? | ? | ? | Medium | NOT_VERIFIED |
| 36 | Navi | UNKNOWN | No | ? | ? | ? | Medium | NOT_VERIFIED |
| 37 | Nippon India | HTML_SCRAPE | – | xls | Yes (to 2013) | 1 | Easy | PARTIALLY_VERIFIED |
| 38 | NJ | HTML_SCRAPE→HTTP | – | xlsx (via PHP) | Yes (to 2022) | 1 | Easy/Med | PARTIALLY_VERIFIED |
| 39 | Nuvama | UNKNOWN | No AMFI URL | – | – | – | – | NOT_VERIFIED |
| 40 | Old Bridge | HTML_SCRAPE | – | xlsx | Yes | Many (per scheme) | Easy | PARTIALLY_VERIFIED |
| 41 | PGIM India | UNKNOWN | No | ? | ? | ? | Medium | NOT_VERIFIED |
| 42 | PPFAS | HTML_SCRAPE | – | xls | Yes (to 2013) | 1 | Easy | VERIFIED |
| 43 | quant | **API** | **Yes** | xlsx | Yes (2021+) | Many (per scheme) | Easy | VERIFIED |
| 44 | Quantum | DIRECT_FILE | AMFI gives 1 file | xlsx | **No** | 1 | Medium | PARTIALLY_VERIFIED |
| 45 | Samco | HTML_SCRAPE | – | xlsx | Yes | Many (per scheme) | Easy | PARTIALLY_VERIFIED |
| 46 | SBI | **API** | **Yes** | xlsx | Yes | 1 consolidated + per scheme | Easy | VERIFIED |
| 47 | Shriram | UNKNOWN | No | ? | ? | ? | Medium | NOT_VERIFIED |
| 48 | Sundaram | **API** | **Yes** | xlsx | Yes (to 2012) | **2** (Equity+FoF, Fixed Income) | Medium | VERIFIED |
| 49 | Tata | UNKNOWN | No | ? | ? | ? | Medium | NOT_VERIFIED |
| 50 | Taurus | HTTP_REQUEST? | Drupal filter seen | ? | Likely | ? | Medium | NOT_VERIFIED |
| 51 | Trust | UNKNOWN | No | ? | ? | ? | Medium | NOT_VERIFIED |
| 52 | Unifi | HTML_SCRAPE | – | xlsx | Yes | 1 | Easy | PARTIALLY_VERIFIED |
| 53 | Union | HTML_SCRAPE | – | xlsx | Yes (1,519 files) | ~20 (per scheme) | Easy | VERIFIED |
| 54 | UTI | UNKNOWN | No | ? | ? | ? | Medium | NOT_VERIFIED |
| 55 | The Wealth Company | HTML_SCRAPE (RSC) | – | xlsx | Current only | Many (per scheme) | Medium | VERIFIED |
| 56 | WhiteOak Capital | UNKNOWN | No | ? | ? | ? | Medium | NOT_VERIFIED |
| 57 | Zerodha | UNKNOWN | No | ? | ? | ? | Medium | NOT_VERIFIED |

### Scoreboard

| Verification | Count |
|---|---|
| VERIFIED (endpoint/URL exercised, real bytes returned) | **16** |
| PARTIALLY_VERIFIED | **9** |
| NOT_VERIFIED | **32** (incl. 5 with no AMFI URL) |
| **Total AMFI members** | **57** |

| Files per month | Count (of resolved AMCs) |
|---|---|
| Exactly 1 consolidated file | 9 |
| 1 ZIP containing many | 2 (DSP, ICICI) |
| Multiple files (one per scheme) | 10 |
| Both consolidated *and* per-scheme | 1 (SBI) |

**Step 7's warning is fully justified: one AMC/month ≠ one file.** SBI returned 242 file
references for a single month; Union carries ~20 per month; HDFC 109.

---

## Step 12 — Proposed Automation Plan

### The core insight that should shape the design

The obvious design is "one downloader per AMC, each knowing how to build its URLs." The
investigation says that design is wrong, for one reason: **no AMC's URLs are constructible.**

So the system should not be organised around *downloading*. It should be organised around
**discovery** — producing, for each AMC, a list of `(month, file_url, label)` records. Once you
have that list, downloading is the same trivial code for all 52.

That splits the problem into a hard part that varies per AMC (discovery) and an easy part that
does not (fetch, validate, store). It also means the per-AMC code you write is small: an
adapter's only job is to return records.

### Architecture

```text
        https://www.amfiindia.com/api/members        [VERIFIED, no auth]
                        │
                        ▼
        take mf_name + amc_monthly_portfolio_disclosure
        (57 members → 52 with a URL → 5 flagged "no source")
                        │
                        ▼
        ┌───────────────────────────────────┐
        │  AMC registry (checked-in config) │
        │  amc_id, adapter, url_override,   │
        │  month_regex, include/exclude     │
        └───────────────────────────────────┘
                        │
                        ▼
              DISCOVERY  (per AMC, pluggable)
        ┌──────────┬──────────┬──────────┬──────────┐
        │ ArchiveHtml│  ApiJson │ RscPayload│ Browser │
        │  (14 AMCs) │ (4 AMCs) │  (2 AMCs) │ (3+)    │
        └──────────┴──────────┴──────────┴──────────┘
                        │
                        ▼
        ┌───────────────────────────────────────────┐
        │  DiscoveredFile[]                          │
        │  amc, period(YYYY-MM), url, label,         │
        │  scheme?, kind=monthly, discovered_at      │
        └───────────────────────────────────────────┘
                        │
                        ▼
              file catalogue  (durable table)   ◄── this is the real product
                        │
                        ▼
        query: "give me AMC X for 2026-07"
                        │
                        ▼
              DOWNLOAD (identical for all AMCs)
                        │
                        ▼
              VALIDATE (magic bytes, size, openability)
                        │
                        ▼
              S3:  s3://bucket/amc={id}/period=YYYY-MM/{filename}
                   + manifest.json (source url, sha256, fetched_at)
```

### Why a persistent catalogue rather than fetch-on-demand

The brief's target behaviour is *"give me the portfolio disclosure for AMC X for YYYY-MM."*
Fetch-on-demand cannot serve that reliably, because for several AMCs the answer only exists
**today**:

- **HDFC** shows the latest month only — July 2026 is retrievable now, and likely not in six months.
- **The Wealth Company** exposes only the current month, with no month in the filename at all.
- **Quantum**'s AMFI URL is a single opaque GUID that presumably rotates.

So discovery should run **monthly regardless of demand**, and record what it saw. The catalogue
then answers historical queries from data you already captured, and the download step is just a
lookup. This also turns "the AMC redesigned their site" into a visible gap in the catalogue
rather than a silent failure at query time.

### The four adapters (in build order)

**1. `ArchiveHtml` — highest value per line of code.** One GET, regex/parse every file link,
derive the period from the filename or label. Serves 14 AMCs immediately. Per-AMC config is
just: URL, a link filter, and a month-parsing rule.

**2. `ApiJson` — highest quality.** Four AMCs with real parameterised endpoints (quant, SBI,
Sundaram, ICICI). These are the only ones that can answer "give me 2026-07" natively rather
than by filtering a big list. Worth doing early because they are also the easiest to test.

**3. `RscPayload`.** Same as ArchiveHtml but decodes `self.__next_f.push([1,"…"])` chunks first.
The decoder is ~15 lines and is already written and working (used it for AMFI, 360 ONE, Wealth
Company, and to rule out several others). Serves 2 AMCs, and is the natural first thing to try
on the Group E Next.js sites.

**4. `Browser` — last resort, and deliberately minimal.** Its job is *only* to render a page and
return harvested URLs. It must never download the file — because in every case I checked
(HDFC especially) the file host itself was open to plain `requests`. Needed today for Bandhan
(encrypted), HDFC, Kotak and Edelweiss (bot walls).

### Month matching

Do not normalise month strings ad hoc at each call site. Build one resolver that maps any of
these to `YYYY-MM`, because all of them appear in real filenames I collected:

```text
July 2026 · july-2026 · Jul-26 · July_31_2026 · 31-07-2026 · 31.10.2022
Jun2024 · June2025 · Sept2024 · dec2025 · Apirl_2026 (sic) · MONTHLY-PORTFOLIO-APR-23
```

Then match with a tolerance: a file is for `2026-07` if its parsed period is `2026-07`, and
prefer "as on 31 July 2026" style labels over path segments (HDFC's `2026-08/` path is the
publication month, not the data month).

### Guardrails the investigation says you will need

1. **Exclude fortnightly.** Helios lists fortnightly and monthly files side by side; Nippon has
   joint `FORTNIGHTLY-MONTHLY-*` files; IL&FS ships one combined workbook. Filter on `monthly`
   and explicitly reject `fortnightly` — and treat combined files as a distinct kind.
2. **Exclude look-alikes.** Shriram's only `Monthly` files are investor-complaint reports;
   Unifi's `Notice_Monthly-portfolio_*.pdf` are hosting notices. A `monthly` substring match
   is not sufficient evidence that a file is a portfolio.
3. **Expect N files per month.** Model the unit of work as *(AMC, month) → set of files*, never
   a single file. Record an expected-count per AMC so a drop from 242 to 3 files is an alert.
4. **Normalise hosts.** Union's AMFI URL does not resolve without `www.`; support a per-AMC
   `url_override` and keep the AMFI value as provenance.
5. **Validate by magic bytes, not extension.** Many `.xls` files are actually XLSX (`PK`)
   containers — Abakkus, Baroda BNP, PPFAS, DSP all do this. A parser picked by extension will
   fail on them.
6. **Re-read the AMFI list every run.** It is a live API and the membership changes — five
   members currently have no monthly URL, and those will presumably appear later.

### Suggested sequencing

| Phase | Work | AMCs covered |
|---|---|---|
| 1 | AMFI loader + catalogue schema + download/validate/S3 (shared) | infrastructure |
| 2 | `ArchiveHtml` adapter | 14 |
| 3 | `ApiJson` adapter (quant, SBI, Sundaram) | +3 |
| 4 | `RscPayload` adapter (360 ONE, Wealth Co) | +2 |
| 5 | Pin down ICICI params; try `searchyear`/Drupal filters for Canara & Taurus | +3 |
| 6 | Finish Group E discovery (24 AMCs) — one browser session each to capture the XHR | +24 |
| 7 | `Browser` adapter for Bandhan, HDFC, Kotak, Edelweiss | +4 |

Phases 1-4 get ~19 AMCs on pure HTTP with roughly four adapters — which is the "don't build 52
systems" outcome you were aiming for. Phase 6 is the bulk of the remaining discovery effort and
is mechanical: open the page, capture the XHR, write down the endpoint.

### Honest status of this investigation

16 AMCs verified end-to-end, 9 partially, and **24 of the 52 still need their endpoint
identified** (plus 5 with no AMFI URL, and Quantum which has no month-addressable source).
I did open and inspect each of those 24 individually — the notes above record page type,
framework, bot protection and where the monthly section lives — but I did not capture their
XHR, so I have not claimed endpoints for them. Per Rule 8, nothing above is asserted as working
unless I ran it.

---

# ROUND 2 — Group E endpoint capture

**Correction to the earlier count:** Group E was listed as "24 AMCs" but the list under it
actually contained **26**. The correct figure is 26; the numbers below supersede the Round 1
scoreboard.

Method: loaded each site in a real browser across 4 parallel tabs, hooked `window.fetch` and
`XMLHttpRequest.prototype.open/send` to capture URL + body + headers, drove the month/year
controls, then re-tested every captured endpoint with plain `curl` to see whether it works
outside the browser.

---

## Newly VERIFIED (endpoint or file exercised, real bytes returned)

### 3. Aditya Birla Sun Life — resolved, and simpler than expected

```text
Actual portfolio page:  https://mutualfund.adityabirlacapital.com/forms-and-downloads/portfolio
Monthly selection:      none needed — 210 file links render in the DOM
Download mechanism:     direct <a href> (Sitecore media library)
Sitecore API (optional): GET /postlogin/CustomApi/Resources/FactsheetAccordionById
                         ?id=<guid>&ctype=<sitecore-path>&month=&year=
File formats:           .zip (one per month, scheme files inside)
One file or multiple:   ONE ZIP per month
Historical months:      Yes — /monthly-portfolio/<year>/ folders
Recommended method:     HTML_SCRAPE -> DIRECT_FILE   (browser render to get the DOM)
Difficulty:             Easy/Medium
Verification:           VERIFIED
```
```text
/-/media/bsl/files/resources/monthly-portfolio/2026/absl_monthly_portfolio_report_july-2026.zip
/-/media/bsl/files/resources/monthly-portfolio/2026/monthly-portfolio-30-june-2026.zip
/-/media/bsl/files/resources/monthly-portfolio/2026/31052026_abslmf_monthly-portfolio.zip
```
Verified: `200`, 1,776,762 bytes, `PK`. Three consecutive months, three **different** naming
conventions (`july-2026`, `30-june-2026`, `31052026_` prefix) — same lesson again.

---

### 25. Invesco — the cleanest JSON API found in the whole exercise

```text
Actual portfolio page:  https://www.invescomutualfund.com/literature-forms/monthly-holdings
                        (AMFI's /literature-and-form?tab=Complete redirects here)
Monthly selection:      Year dropdown + classification tab; months come back all at once

API/endpoint:           https://www.invescomutualfund.com/api/CompleteMonthlyHoldings
HTTP method:            GET
Parameters:             ?year=<YYYY>&classification=<equity|debt|...>
Required headers:       NONE — plain curl works, no auth, no referer, no cookie
Month parameter:        none — the response carries all 12 months as fields
Year parameter:         year=YYYY
Response type:          application/json (array)
Where file URL appears: JanUrl / FebUrl / MarUrl / … / DecUrl, per scheme object
```
Example:
```bash
curl -s "https://www.invescomutualfund.com/api/CompleteMonthlyHoldings?year=2026&classification=equity"
```
```json
[{"Name":"Invesco India ELSS Tax Saver Fund",
  "JanUrl":"https://www.invescomutualfund.com/docs/default-source/completes-monthly-holding/elss-tax-saver735afb07eee8616aaa28ff00007d74af.xlsx?sfvrsn=7cb29ac2_0",
  "MayUrl":"https://www.invescomutualfund.com/docs/default-source/completes-monthly-holding/invesco-india-elss-tax-saver-fund_may_2026_equity.xlsx?sfvrsn=fdf89cc2_0", …}]
```
```text
File formats:           .xlsx
One file or multiple:   MULTIPLE — one per scheme per month
Historical months:      Yes — year=2024 returns populated JanUrl..DecUrl
Recommended method:     API
Difficulty:             Easy
Verification:           VERIFIED (2026 and 2024 both returned populated URLs)
```
Note the **month→URL mapping is explicit in the response**, which removes all filename-parsing
risk. Also note within one scheme: `JanUrl` uses an opaque Sitefinity hash while `MayUrl` uses a
readable `..._may_2026_equity.xlsx` — inconsistent even inside a single JSON object.

---

### 35. Motilal Oswal — AEM search API with year+month+category

```text
Actual portfolio page:  https://www.motilaloswalmf.com/downloads/scheme-portfolio-details

API/endpoint:           https://www.motilaloswalmf.com/content/aem-cloud-dept-backend-motilal-oswal/api/search-documents.json
HTTP method:            GET
Parameters:             ?year=<YYYY>&category=month%20end%20portfolio&month=<mmm>&type=mf
Required headers:       NONE — plain curl works
Month parameter:        month = lowercase 3-letter (jan..dec)
Year parameter:         year = YYYY
Category parameter:     category = "month end portfolio"  (URL-encoded, WITH SPACES)
Response type:          application/json
Where file URL appears: results[].path  (prefix with https://www.motilaloswalmf.com)

Companion endpoint:     /api/downloads-dropdown-options.json?type=mf
                        -> {"month-end-portfolio":{"2026":["apr","jul","aug",...],"2025":[...]}}
                        This ENUMERATES exactly which year/month combinations exist.
```
Example:
```bash
curl -s "https://www.motilaloswalmf.com/content/aem-cloud-dept-backend-motilal-oswal/api/search-documents.json?year=2026&category=month%20end%20portfolio&month=aug&type=mf"
```
```json
{"totalMatches":2,"count":2,"results":[
  {"path":"/content/dam/motilal-mf/downloads/mf/month-end-portfolio/2026/aug/Monthly Portfolio 31-07-2026-Final.xlsx",
   "title":"scheme portfolio details july 2026","year":"2026","month":"aug",
   "category":"month end portfolio","publishDate":"10-08-2026"}]}
```
```text
File formats:           .xlsx (and .xlsb for some fortnightly files)
One file or multiple:   ONE monthly file per month
Historical months:      Yes — year=2025&month=mar returned 3 results
Recommended method:     API
Difficulty:             Easy
Verification:           VERIFIED — file downloaded: 200, 2,169,613 bytes, PK
```

**Two traps, both important:**
1. **The `category` value must keep its literal spaces** (`month%20end%20portfolio`).
   The kebab-case form `month-end-portfolio` — which is what the *dropdown-options* endpoint
   uses as its key — returns `totalMatches: 0`. Two different spellings of the same category in
   two sibling endpoints of the same API.
2. **`month` is the PUBLICATION month, not the data month.** `month=aug` returns the file whose
   title is *"scheme portfolio details july 2026"*. And the `month end portfolio` category
   **also contains fortnightly files** (`fortnightly portfolio report - 15th mar 2025`,
   `copy of fortnightly portfolio 31-07-2026.xlsb`). Filtering on category alone would silently
   ingest fortnightly data — exactly the failure mode Rule 1 warns about.

---

## Endpoint captured, but NOT callable from plain HTTP yet

### 7. Axis — perfect API shape, blocked by a bearer token

```text
Actual portfolio page:  https://www.axismf.com/statutory-disclosures -> section 9 "Portfolios"

API/endpoint:           POST https://www.axismf.com/cms/get-scheme-documents
Parameters:             {"sdType":"yearMonthSchemeDocs",
                         "sdID":"sdMonthSchemePortfolio",     <-- MONTHLY (vs fortnightly sibling)
                         "year":"2026","month":"August","schemeCode":"Consolidated"}
Companion:              POST /cms/get-nested-list  {"sdParentID":"sdPortfolios"}
Required headers:       Authorization: Bearer <128-byte hex>
                        browser-id: <uuid>
                        Content-Type: application/json
Month parameter:        month = full English month name
Year parameter:         year = "YYYY"
```
This is the ideal parameter shape — explicit year, explicit month, and an `sdID` that separates
monthly from fortnightly. But plain `curl` returns:
```text
{"data":null,"error":{"status":403,"name":"ForbiddenError"}}
```
and `GET /cms/token` returns `404`. The bearer token is minted somewhere in the app bootstrap
(`/cms/token`, `/api/auth/session` are both called on load) — the mint call was not isolated.
```text
Recommended method:     HTTP_REQUEST once the token bootstrap is found; PLAYWRIGHT until then
Difficulty:             Medium     Verification: PARTIALLY_VERIFIED (params captured, call 403s)
```

### 31. LIC — cascading form API, monthly explicitly typed

```text
Actual portfolio page:  https://www.licmf.com/downloads/monthly-portfolio

API/endpoint:           POST https://www.licmf.com/downloads/portfolio-filter-options
                        POST https://www.licmf.com/downloads/portfolio-funds
Content-Type:           application/x-www-form-urlencoded
Observed bodies:        fund_category=Equity&filter=category
                        scheme_code=&filter=fund_name&type=monthly_portfolio
                        year=&filter=year&type=monthly_portfolio&scheme_code=
                        scheme_code=LEULIS                      (portfolio-funds)
Type parameter:         type=monthly_portfolio    <-- separates monthly from fortnightly
Year values:            2021, 2022, 2023, 2024, 2025, 2026
Month values:           1..12 (numeric)
Scheme codes:           LEULIS, LEEQTF, LEMIDF, LEBFSF, …
```
The endpoint is live (`200`) from `curl`, but my parameter combination returned only an empty
`<option value=''>Month</option>` — the cascade order (category → scheme → year → month) has to
be replayed, and possibly a session cookie carried.
```text
Recommended method:     HTTP_REQUEST (cascade replay)   Difficulty: Medium
Verification:           PARTIALLY_VERIFIED (endpoints + param names captured; 200 but empty)
```

### 33. Mirae Asset

```text
Actual portfolio page:  https://www.miraeassetmf.co.in/downloads/portfolio  ("Monthly Portfolio" tab)
API/endpoint:           POST https://www.miraeassetmf.co.in/AjaxService/GetDownloadsData
                        (GET returns 302 -> /error, so it is POST-only)
Download mechanism:     direct <a href> once rendered
File formats:           .xlsx
One file or multiple:   MULTIPLE — one per scheme
Historical months:      Likely (tab + pagination) — NOT_VERIFIED
Verification:           PARTIALLY_VERIFIED (10 current-month links harvested; POST body not captured)
```
```text
https://www.miraeassetmf.co.in/docs/default-source/portfolios/mascf-july2026.xlsx
https://www.miraeassetmf.co.in/docs/default-source/portfolios/evetf-july2026.xlsx
```
Encouragingly, Mirae's filenames are `<schemecode><month><year>.xlsx` with **no hash** — the
closest thing to a constructible pattern seen, though the scheme-code list still has to be
discovered.

### 50. Taurus — Drupal Views AJAX, term IDs recovered

```text
Actual portfolio page:  https://taurusmutualfund.com/monthly-portfolio
API/endpoint:           POST https://taurusmutualfund.com/views/ajax?_wrapper_format=drupal_ajax
Content-Type:           application/x-www-form-urlencoded
Parameters:             field_monthly_portfolio_target_id=<year term id>
                        field_month_target_id=<month term id>
                        view_name=monthly_portfolio&view_display_id=page_1
                        view_path=%2Fmonthly-portfolio&view_base_path=monthly-portfolio
                        pager_element=0&_drupal_ajax=1
Year term IDs:          567=2026, 558=2025, 514=2024, 473=2023
Month term IDs:         281=January, 282=February, 283=March, 284=April, …
```
The endpoint answers `200` with a valid `drupal_ajax` envelope from plain `curl` — but the
`insert` command carried **no portfolio rows**, and driving the real form in the browser
(selecting 2026 + Apply) also produced no portfolio files. Either the view is currently empty
or both filters must be set to non-`All` values.
```text
Recommended method:     HTTP_REQUEST (very close)   Difficulty: Medium
Verification:           PARTIALLY_VERIFIED (endpoint + all term IDs captured; zero rows returned)
```

---

## Endpoint identified, not yet exercised

| # | AMC | Endpoint captured | Note |
|---|---|---|---|
| 8 | Bajaj Finserv | `POST /wp-admin/admin-ajax.php` (FormData) | WordPress AJAX; `bd-filter-year` / `bd-filter-month` selects populate only after the Monthly Portfolio accordion loads. The `action` param was not captured. |
| 12 | Canara Robeco | page has `year` (2026/2025/2024) + `month` (July/June/May) selects | **Submitting fires Google reCAPTCHA** (`recaptcha/api2/clr`). `?searchyear=` GET returns no links server-side. reCAPTCHA makes this browser-only. |
| 27 | Jio BlackRock | `service.jioblackrockamc.com/v1/jiobr/api/auth/generateInvestorSession` | Page shows "View by fiscal year **2026-2027**" + "View by month **August**" → *"No documents found."* Fiscal-year addressing, and August is empty. |
| 41 | PGIM India | `POST /api/v1/brochure/published/disclosure` (GET → `405 Method Not Allowed`) | Also `/api/v1/brochure/disclosure/section`, `/api/v1/brochure/get/file`. Clean REST surface; body shape not captured. |
| 49 | Tata | AMFI URL redirects to `/schemes-related/scheme-factsheet` | A "Portfolios" tab exists on the page; AMFI links to the **wrong sub-tab**. |
| 51 | Trust | `POST /api/api/Trust/GetData` (GET → `405`) | Note the doubled `/api/api/`. Page tabs confirm: Fortnightly / **Monthly** / Quarterly / Half-yearly Disclosure. |
| 54 | UTI | `www.utimf.com/api/*` (e.g. `/api/page/forms-and-downloads-downloads`) | UI has "Select Year", "Select Month", "Get Portfolio Disclosure"; the custom dropdowns are not `<select>` elements so scripted selection did not fire the query. |

## Still NOT_VERIFIED after Round 2 (not reached in this pass)

```text
  4 AlphaGrep    10 Bank of India   15 Choice      18 Franklin Templeton
 22 HSBC         26 ITI             28 JM Financial 32 Mahindra Manulife
 36 Navi         47 Shriram         56 WhiteOak    57 Zerodha
```
Plus the two hard blocks from Round 1 — **17 Edelweiss** (403) and **29 Kotak** (Radware) — and
the six with no usable source (**6, 14, 30, 34, 39**, and **44 Quantum**).

---

## Revised scoreboard (supersedes Round 1)

| Verification | Round 1 | **Round 2** |
|---|---|---|
| VERIFIED | 16 | **18** |
| PARTIALLY_VERIFIED | 9 | **13** |
| NOT_VERIFIED | 32 | **26** |
| Total | 57 | 57 |

**VERIFIED (18):** 360 ONE, Abakkus, **ABSL**, Bandhan, Baroda BNP, Capitalmind, DSP, Groww,
HDFC, IL&FS, **Invesco**, **Motilal Oswal**, PPFAS, quant, SBI, Sundaram, Union, Wealth Company.

**PARTIALLY_VERIFIED (13):** Angel One, **Axis**, Helios, ICICI, **LIC**, **Mirae**, Nippon, NJ,
Old Bridge, Quantum, Samco, **Taurus**, Unifi.

### Callable-from-Python APIs, final list

| AMC | Endpoint | Auth | Verified |
|---|---|---|---|
| **Invesco** | `GET /api/CompleteMonthlyHoldings?year=&classification=` | none | ✅ |
| **Motilal** | `GET …/api/search-documents.json?year=&category=&month=&type=mf` | none | ✅ |
| **SBI** | `POST /ajaxcall/CMS/GetSchemePortfolioSheets` | none | ✅ |
| **quant** | `POST /statutorydisclosures.aspx/displaydisclouser2` | none | ✅ |
| **Sundaram** | `POST /ajax/…GetCategory` | none | ✅ |
| Axis | `POST /cms/get-scheme-documents` | **Bearer** | ⚠️ 403 |
| LIC | `POST /downloads/portfolio-filter-options` | none | ⚠️ empty |
| Taurus | `POST /views/ajax` | none | ⚠️ empty |
| ICICI | `GET /nms/v1/downloads/files` | none | ⚠️ params |

**Five AMCs are now fully callable from plain `requests` with no auth at all.**

### What Round 2 changes about the plan

Nothing structural — it **reinforces** the discovery-first design:

- **Invesco is the model response shape.** Returning `JanUrl…DecUrl` per scheme removes filename
  parsing entirely. Where an API gives an explicit month→URL mapping, use it and never parse.
- **Motilal proves month parameters lie.** `month=aug` returns July data, and the same category
  mixes fortnightly in. The `kind` (monthly vs fortnightly) and the `period` must both be
  validated from the document **title**, not from the query you sent.
- **A new adapter is justified: `FormApi`** — for cascading form endpoints (LIC, Taurus, Bajaj,
  PGIM, Trust, UTI). They share a shape: POST form-encoded, replay a dropdown cascade, parse an
  HTML fragment. That is one adapter for ~6 AMCs, not six.
- **reCAPTCHA is a hard boundary.** Canara Robeco fires reCAPTCHA on filter submit, so it joins
  Bandhan/Edelweiss/Kotak in the browser-only group regardless of how clean its params look.
- Revised adapter coverage: `ArchiveHtml` 14 · `ApiJson` 5 · `FormApi` ~6 · `RscPayload` 2 ·
  `Browser` ~5 — **five adapters for ~32 AMCs.**

---

# ROUND 3 — the final 12 (Group E closed)

All 26 Group E AMCs have now been individually opened, hooked and tested.

---

## Newly VERIFIED

### 56. WhiteOak Capital — a public Strapi REST API, and the best filtering of all 52

```text
Actual portfolio page:  https://mf.whiteoakamc.com/regulatory-disclosures/scheme-portfolios

API/endpoint:           https://cms.whiteoakamc.com/api/scheme-portfolios
HTTP method:            GET
Required headers:       NONE — no auth, no API key
Parameters (Strapi v4 query syntax):
    filters[period][$eq]=Monthly                <-- separates monthly from fortnightly
    filters[published_date][$gte]=YYYY-MM-DD
    filters[published_date][$lte]=YYYY-MM-DD
    populate=*
    pagination[page]=1&pagination[pageSize]=100
Response type:          application/json
Where file URL appears: data[].attributes.doc_file.data.attributes.url  (direct S3 URL)
Useful fields:          period, published_date, scheme_name, doc_name
Total corpus:           1,059 records
```
Example:
```bash
curl -s "https://cms.whiteoakamc.com/api/scheme-portfolios?filters%5Bperiod%5D%5B%24eq%5D=Monthly&filters%5Bpublished_date%5D%5B%24gte%5D=2026-07-01&filters%5Bpublished_date%5D%5B%24lte%5D=2026-07-31&populate=*&pagination%5BpageSize%5D=100"
```
Returned `total: 21`, e.g.
```text
Monthly  2026-07-09  WhiteOak Capital Arbitrage Fund
   -> https://content.whiteoakamc.com/WOC_Arbitrage_Fund_June_2026_db690a16c1.xlsx
```
```text
File formats:           .xlsx
One file or multiple:   MULTIPLE — one per scheme
Historical months:      Yes — corpus goes back to 2021 (records inherited from YES AMC)
Recommended method:     API
Difficulty:             Easy
Verification:           VERIFIED — file downloaded: 200, 108,661 bytes, PK
```
**Caveat worth writing into the config:** `published_date` is the *publication* date. The July
publication window returns **June 2026** data. Filter on `published_date`, but derive the actual
period from `doc_name` / the filename. A `period` field this explicit is rare and valuable — it
is the cleanest monthly-vs-fortnightly separator found anywhere in this exercise.

---

### 10. Bank of India — ASP.NET web service, fully callable

```text
AMFI URL:               https://www.boimf.in/investor-corner#t2   (tab anchor)
Actual portfolio page:  same page, "MONTHLY PORTFOLIO" tab

API/endpoint:           POST https://www.boimf.in/AjaxService.asmx/GetDocuments
Content-Type:           application/json
Required headers:       none beyond Content-Type
Parameters:             {"pagno":0,"category":null,"fromDate":null,"toDate":null,
                         "LibraryName":"InvestorCorner",
                         "folderName":"MONTHLY PORTFOLIO",     <-- the monthly selector
                         "CategoryValue":"no"}
Month/Year parameter:   none — one call returns the whole archive
                        (fromDate/toDate exist and are probably usable as a range filter)
Response type:          JSON (142 KB) containing the document list
```
Discovery note: a bare `GET .../CategoryList` returns `Missing parameter: LibraryName.`, and
supplying that returns `Missing parameter: Tabname.` — the service tells you what it wants, but
the working combination came from hooking the real page.

```text
File formats:           .xlsx
One file or multiple:   ONE consolidated file per month
Historical months:      Yes — 178 links, back through prior years
Recommended method:     API
Difficulty:             Easy
Verification:           VERIFIED — 200, 529,483 bytes, PK
```
```text
https://www.boimf.in/docs/default-source/investorcorner/monthly-portfolio/monthly-portfolio---31-july-2026.xlsx?sfvrsn=a305d2cb_3
https://www.boimf.in/docs/default-source/investorcorner/monthly-portfolio/monthly-portfolio---30-june-2026.xlsx?sfvrsn=ea175794_3
```
Sitefinity `?sfvrsn=` token required. Naming here is unusually consistent
(`monthly-portfolio---DD-month-YYYY.xlsx`) but the version token still blocks construction.

---

### 47. Shriram — resolved; my Round 1 read was wrong

```text
Actual portfolio page:  https://www.shriramamc.in/investor-statutory-disclosures
                        (section: "Monthly / Fortnightly / Weekly Portfolio of Scheme(s)")
Monthly selection:      accordion section, then FY sub-folders
Download mechanism:     direct <a href> on cdn.shriramamc.in
File formats:           .xls
One file or multiple:   ONE consolidated file per month
Historical months:      Yes — organised by financial year folder
Recommended method:     HTML_SCRAPE -> DIRECT_FILE (browser render for the accordion)
Difficulty:             Easy/Medium
Verification:           VERIFIED — 200, 539,136 bytes, OLE .xls
```
```text
https://cdn.shriramamc.in/uploads/Statutory-disclosure/Monthly--Fortnightly--Weekly-Portfolio-of-Scheme(s)/
    Monthly-Portfolio-for-the-Financial-Year/2026-2027/Monthly-Portfolio-Shriram-Mutual-Fund-July-2026.xls
```
**Correcting Round 1:** I reported that Shriram's only `Monthly` matches were investor-complaint
reports. That was true of the *initial* RSC payload, but wrong as a conclusion — the real
portfolio files sit in a lazily-rendered accordion and are cleanly named. Shriram moves from
NOT_VERIFIED to VERIFIED. The Round 1 warning still stands as a warning: a substring match on
`monthly` on that site does hit complaint reports first.

---

### 28. JM Financial

```text
Actual portfolio page:  https://www.jmfinancialmf.com/downloads/Portfolio-Disclosure
API/endpoint:           POST https://jmmfapi.jmfinancialmf.com/api/GetDownloadNew
                        POST https://jmmfapi.jmfinancialmf.com/api/GetDownloadDrop
                        (both return 405 on GET — POST-only; body not captured)
Download mechanism:     direct <a href> once rendered
File formats:           .xlsx
One file or multiple:   MULTIPLE — one per scheme
Historical months:      Likely — NOT_VERIFIED
Recommended method:     HTML_SCRAPE -> DIRECT_FILE (API preferable once body is known)
Difficulty:             Easy/Medium
Verification:           VERIFIED (file) — 200, 95,030 bytes, PK
```
```text
https://www.jmfinancialmf.com/CMS/downloads/Portfolio%20Disclosure/Monthly%20Portfolio%20of%20Schemes/
    Monthly%20Portfolio-%20JM%20Overnight%20Fund%20-%20%20%20July%2031,%202026.xlsx
```
Note `%20%20%20` — **three consecutive spaces** inside the filename, and inconsistently so
(the Medium-to-Long Duration file has one). Any URL builder would fail; the encoder must
preserve the exact string as scraped.

---

## Endpoint captured, not callable from plain HTTP

### 36. Navi — WordPress REST with an explicit `type=Monthly`

```text
Actual portfolio page:  https://navi.com/mutual-fund/downloads/portfolio
Monthly selection:      Financial Year dropdown (2009-2010 … 2026-2027) + Month dropdown
API/endpoint:           POST https://navi.com/wp-json/nv/v1/documents
Content-Type:           application/x-www-form-urlencoded
Parameters:             financial_year=2026-2027&value=July&category=884&type=Monthly&order=DESC
Month parameter:        value = full English month name
Year parameter:         financial_year = "YYYY-YYYY"
Type parameter:         type=Monthly       <-- clean monthly/fortnightly separator
Category:               884  (portfolio category id)
```
Plain `curl` returns **403** — a nonce or Referer check. Endpoint and full parameter set are
confirmed from the live page.
```text
File formats:           .xlsx
One file or multiple:   MULTIPLE — 17 links for July 2026, one per scheme
Historical months:      Yes — dropdown offers 2009-2010 onward
Recommended method:     HTTP_REQUEST once the nonce is sourced; PLAYWRIGHT until then
Difficulty:             Medium     Verification: PARTIALLY_VERIFIED
```
```text
https://public-assets.prod.navi-tech.in/navi-website-assests/documents/Navi_Large_Midcap_Fund_1st_31st_July_2026_20260810120900.xlsx
```
(Note the typo `assests` in Navi's own asset path, and a trailing upload timestamp.)

### 32. Mahindra Manulife — second encrypted API found

```text
API/endpoint:           https://investorapi.mahindramanulife.com/api/v1/web/fetch-all-funds
Response:               {"payload":"pqmD2cnJTvr5zJ7a6ivQQGRzkULRxaSsob1BVdN8p6y55vuz…"}
```
Responses are an opaque encrypted blob, exactly like Bandhan. Mahindra Manulife therefore joins
the **browser-only** group regardless of how clean the REST paths look.
```text
Recommended method:     PLAYWRIGHT     Difficulty: Hard     Verification: PARTIALLY_VERIFIED
```

### 4. AlphaGrep — a static JSON manifest, cleanly typed

```text
API/endpoint:           GET https://www.alphagrepmf.ai/assets/documents/files.json
Required headers:       none — plain static file, 200
Response:               {"monthly":[…], "halfyearly":[…], "fortnightly":[…]}
```
```json
{"monthly":[{"schemeName":"AlphaGrep Multi Asset Allocation Fund",
             "folderName":"AlphaGrep_Multi_Asset_Allocation_Fund",
             "financialYears":[{"year":"FY 2026-27","yearFolder":"FY-2026-27",
                                "documents":[{"title":"July Report","fileName":"July_2026"}]}]}]}
```
The manifest is **top-level split by disclosure type** — `monthly` is its own key, which is the
cleanest separation in the whole exercise. It carries every part needed to build a file URL
(`folderName` + `yearFolder` + `fileName`) but **not the base path or extension**; the Angular
app assembles those, and I could not get the Portfolio Disclosure panel to render a concrete
`<a href>` to confirm the pattern.
```text
One file or multiple:   ONE per scheme per month (currently a single-scheme AMC)
Historical months:      FY 2026-27 only at present (new AMC)
Recommended method:     API (manifest) once the base path is confirmed
Difficulty:             Easy     Verification: PARTIALLY_VERIFIED (manifest verified; file URL not)
```

### 15. Choice — monthly-specific API, module error

```text
API/endpoint:           https://choicemf.com/api/monthly-portfolio-report/portfolio-website-list
                        (no trailing slash -> 301; with slash -> {"message":"invalid module","Status_code":400})
UI:                     "Select Year 2026-27" + "Select Month July 2026" + per-scheme rows
```
The route name could not be more explicit (`monthly-portfolio-report`), but the endpoint rejects
the request without the module/params the SPA supplies.
```text
Recommended method:     API once params are known     Difficulty: Medium
Verification:           PARTIALLY_VERIFIED (endpoint identified, call rejected)
```

### 18. Franklin Templeton — BloomReach delivery API responds

```text
API/endpoint:           GET https://www.franklintempletonindia.com/resourceapi/reports?first-load=true&segment=investor
                        (also /api/literature/v1/responseLitJson?type=report)
Status:                 200 from plain curl — BloomReach (brxm) page-model JSON
```
The AMFI deep link `?firstFilter-12` is dropped on redirect to `/reports`. The API answers
without auth; the report-type filter for monthly portfolio was not isolated.
```text
Recommended method:     API     Difficulty: Medium     Verification: PARTIALLY_VERIFIED
```

### 26. ITI — API found, access denied

```text
API/endpoint:           https://itiamc.com/jeeth/api/v1/catalog/getPartnerDocumentByType
                        (also /getFundDetails, /getLatestNav)
Plain curl:             {"status":-100,"message":"Access denied."}  HTTP 403
```
```text
Recommended method:     HTTP_REQUEST once auth headers are known; PLAYWRIGHT otherwise
Difficulty:             Medium     Verification: PARTIALLY_VERIFIED
```

### 57. Zerodha — appears NOT to publish a monthly portfolio here

```text
AMFI URL:               https://www.zerodhafundhouse.com/resources/disclosures?source=footer
Findings:               268 file links. The "Portfolio Disclosures" section contains
                        QUARTERLY portfolios only, e.g.
                          ZN250 - Quarterly Portfolio March 2025.xlsx
                          ZELSS - Quarterly Portfolio March 2025.xlsx
                        The 33 links matching "monthly" are all
                          "Monthly AAUM Disclosure Report - July 2026.xlsx"  (AUM, not portfolio)
                        A full-text scan of the rendered page for "monthly portfolio" returns FALSE.
```
So on the page AMFI points to, Zerodha publishes **quarterly** portfolio disclosures and
**monthly AAUM** — but no monthly portfolio. Either it lives elsewhere or it is not published in
this form. Flagged rather than assumed.
```text
Recommended method:     UNKNOWN — needs a different source
Difficulty:             n/a     Verification: PARTIALLY_VERIFIED (page fully inspected; no monthly portfolio present)
```
This is the second AMC (with Quantum) where the AMFI-provided URL does not lead to a
month-addressable monthly portfolio at all.

### 22. HSBC — still unreachable

Third attempt, 40 s timeout, plain `curl` with browser headers: `000` (no response). Two earlier
attempts also timed out. The origin does not respond from this network.
```text
Recommended method:     UNKNOWN     Difficulty: Hard     Verification: NOT_VERIFIED
```

---

## Final scoreboard

| Verification | Round 1 | Round 2 | **Round 3 (final)** |
|---|---|---|---|
| VERIFIED | 16 | 18 | **22** |
| PARTIALLY_VERIFIED | 9 | 13 | **20** |
| NOT_VERIFIED | 32 | 26 | **15** |
| Total | 57 | 57 | 57 |

**VERIFIED (22):** 360 ONE · Abakkus · ABSL · **Bank of India** · Bandhan · Baroda BNP ·
Capitalmind · DSP · Groww · HDFC · IL&FS · Invesco · **JM Financial** · Motilal Oswal · PPFAS ·
quant · SBI · **Shriram** · Sundaram · Union · Wealth Company · **WhiteOak**

**PARTIALLY_VERIFIED (20):** AlphaGrep · Angel One · Axis · Choice · Franklin · Helios · ICICI ·
ITI · LIC · Mahindra Manulife · Mirae · Navi · Nippon · NJ · Old Bridge · Quantum · Samco ·
Taurus · Unifi · Zerodha

**NOT_VERIFIED (15):** ASK · Bajaj Finserv · Canara Robeco · Carnelian · Edelweiss · HSBC ·
Jio BlackRock · Kotak · Lakshya · Monarch · Nuvama · PGIM · Tata · Trust · UTI

### Callable from plain `requests`, no auth — final list (7)

| AMC | Endpoint | Monthly selector |
|---|---|---|
| **Invesco** | `GET /api/CompleteMonthlyHoldings?year=&classification=` | response has `JanUrl…DecUrl` |
| **Motilal** | `GET …/api/search-documents.json?year=&month=&category=month%20end%20portfolio` | `category` (leaky — see trap) |
| **SBI** | `POST /ajaxcall/CMS/GetSchemePortfolioSheets` | `PSFrequency=Monthly` |
| **quant** | `POST /statutorydisclosures.aspx/displaydisclouser2` | `cat=MONTHLY PORTFOLIO - FUND - WISE` |
| **Sundaram** | `POST /ajax/…GetCategory` | `Catid=Monthly` |
| **WhiteOak** | `GET cms.whiteoakamc.com/api/scheme-portfolios` | `filters[period][$eq]=Monthly` |
| **Bank of India** | `POST /AjaxService.asmx/GetDocuments` | `folderName=MONTHLY PORTFOLIO` |

---

## What Round 3 changes about the plan

**1. A `StrapiApi` adapter is now worth its own slot.** WhiteOak's `cms.whiteoakamc.com/api/…`
is a stock Strapi v4 endpoint, and Abakkus, Capitalmind, Old Bridge, 360 ONE and The Wealth
Company are all Strapi-backed sites currently handled by scraping `/uploads/…` out of HTML.
It is worth probing each of those for an exposed `/api/` collection — if even two more answer,
they graduate from HTML_SCRAPE to a filterable API with a real `period` field.

**2. Encrypted-payload APIs are a recognised category, not a one-off.** Bandhan and now
Mahindra Manulife both return opaque blobs. Detect this at discovery time (response is a single
JSON string field, no readable URLs) and route to the browser adapter automatically rather than
failing.

**3. Explicit type fields are the reliable monthly filter — use them wherever they exist.**
The strongest separators found: AlphaGrep's top-level `"monthly"` manifest key, WhiteOak's
`period`, Navi's `type=Monthly`, LIC's `type=monthly_portfolio`, SBI's `PSFrequency`, Axis's
`sdID=sdMonthSchemePortfolio`, BoI's `folderName`. Where a source offers one, never fall back to
filename matching. Motilal is the counter-example that proves the rule — its `category` is *not*
a reliable separator and leaks fortnightly files.

**4. Two AMCs need a source that AMFI does not provide.** Quantum (single opaque GUID) and
Zerodha (publishes quarterly portfolios + monthly AAUM, no monthly portfolio on that page).
These should be raised with the business rather than engineered around.

**5. Revised adapter coverage — five adapters now reach ~40 of 52:**

```text
ArchiveHtml   16   Abakkus, Angel One, Baroda BNP, Capitalmind, DSP, Groww, Helios,
                   IL&FS, JM, Nippon, NJ, Old Bridge, PPFAS, Samco, Shriram, Unifi, Union, ABSL
ApiJson        7   Invesco, Motilal, SBI, quant, Sundaram, WhiteOak, Bank of India
FormApi       ~7   LIC, Taurus, Navi, Bajaj, PGIM, Trust, UTI
RscPayload     2   360 ONE, The Wealth Company
Browser       ~6   Bandhan, Mahindra Manulife, HDFC, Kotak, Edelweiss, Canara Robeco
```

Everything else in the earlier plan stands unchanged — discovery-first, persistent catalogue,
never construct URLs, validate by magic bytes, and treat publication month as distinct from data
month (now confirmed on HDFC, Motilal, Sundaram, WhiteOak and ABSL).

---

# CORRECTION — Zerodha (supersedes the Round 3 entry)

**My Round 3 conclusion that Zerodha does not publish a monthly portfolio was WRONG.**
It does. The Round 3 entry for AMC #57 should be disregarded entirely.

```text
AMC:                    Zerodha Mutual Fund
AMFI URL:               https://www.zerodhafundhouse.com/resources/disclosures?source=footer
Actual portfolio page:  same page

Monthly selection:      accordion — "Portfolio Disclosures" > "Monthly Portfolio"
Download mechanism:     direct file URLs, embedded in the Next.js __NEXT_DATA__ payload
                        (Pages Router — __NEXT_DATA__ present, self.__next_f absent)

API/endpoint:           none needed
Where file URL appears: __NEXT_DATA__ -> node {"id":"portfolio-disclosures"}
                          -> data[] -> {"id":"monthly-portfolio-disclosures","title":"Monthly Portfolio"}
                          -> files[] -> {id, name, url, modTs}

File formats:           .xlsx
One file or multiple:   MULTIPLE — 21 files for July 2026, one per scheme
Historical months:      Yes — 338 files across 39 period tokens, Nov 2023 .. Jul 2026
Recommended method:     HTML_SCRAPE (__NEXT_DATA__) -> DIRECT_FILE
Difficulty:             Easy
Verification:           VERIFIED — 200, 181,708 bytes, PK
```
```json
{"id":"portfolio-disclosures","title":"Portfolio Disclosures","data":[
  {"id":"monthly-portfolio-disclosures","title":"Monthly Portfolio","files":[
    {"id":"5f293534-bf64-4e50-a4dd-b72dbb1c80ff",
     "name":"ZMIDS - Monthly Portfolio July 2026",
     "url":"https://assets.zerodhafundhouse.com/statutory-reports/portfolio-disclosures/ZMIDS - Monthly Portfolio July 2026.xlsx",
     "modTs":"2026-08-10T07:48:08Z"}]}]}
```

## Why I got it wrong — two method failures, both mine

1. **Round 1 (HTTP):** my file-URL regex was
   `["']([^"'\\\s]{4,200}?\.(?:xlsx|xls|csv|zip|pdf))["']`. The `\s` exclusion makes it
   **impossible to match a URL containing a space** — and every Zerodha portfolio URL contains
   literal spaces (`.../ZMIDS - Monthly Portfolio July 2026.xlsx`). It found only the 3
   space-free NAV files and I read that as "no monthly portfolios".
2. **Round 3 (browser):** I checked `document.querySelectorAll('a')` and `document.body.innerText`.
   The "Monthly Portfolio" accordion was **collapsed**, so only the expanded Quarterly rows were
   in the DOM. My `textHasMonthlyPortfolio` scan returned `false` for the same reason — it read
   rendered text, not the payload.

Both failures point the same way: **I inspected the rendering instead of the data.** Galling
because this audit already warns about space-bearing filenames (Groww, IL&FS, JM Financial) —
I applied that lesson to those AMCs and missed it here.

## Consequences for the rest of the audit

- The claim **"two AMCs need a source AMFI does not provide"** is now **one** (Quantum only),
  and even that should be narrowed: what was verified is that *AMFI's URL* is a single opaque
  `.xlsx` with no month addressability. I never located Quantum's own portfolio listing page,
  so "no month-addressable source exists" overstated the evidence.
- Any conclusion of the form *"no monthly files here"* that rested on rendered DOM or on the
  whitespace-excluding regex is suspect and should be re-tested against `__NEXT_DATA__` /
  RSC payloads before being trusted.
- **Rule for the discovery layer:** absence of matches is never proof of absence. A discovery
  run that returns zero documents must be an explicit error state (as this adapter correctly
  does with its `RuntimeError`), never silently "this AMC has nothing".

## Data-quality landmines in Zerodha's period tokens (39 distinct)

```text
'- December 2023'  '- November 2023'          <- leading dash
'December  2024'   'January  2025'  'November  2024'   <- DOUBLE space
'Aug 2025'  'Oct 2025'  'Sep 2025'  'Sept 2025'        <- abbreviated
```
Worst case — **September 2025 is split across two spellings**:
```text
'Sep 2025'  -> 13 files
'Sept 2025' -> 13 files
regex \bSeptember\s+2025\b  -> 0 matches
regex \bSep(t|tember)?\s+2025\b -> 26 matches
```
A full-month-name matcher returns **zero** files for 2025-09 while 26 exist. December 2024 is
likewise split `'December 2024'` (6) + `'December  2024'` (1) by a double space.

Also note the URLs contain literal spaces; `requests` encodes them automatically, so both the
raw and percent-encoded forms return `200` with identical bytes.

---

# RE-TEST OF ALL "ABSENCE" CONCLUSIONS

After the Zerodha error I re-ran every AMC where I had concluded *"no monthly portfolio files
here"*, using a scanner with both original bugs fixed:

```python
# was: ["']([^"'\\\s]{4,200}?\.(?:xlsx|xls|csv|zip|pdf))["']   <- \s made spaces unmatchable
FILE_RE = re.compile(r'''["']([^"'<>]{4,300}?\.(?:xlsx|xls|csv|zip))(?=["'?#])''', re.I)
# and: decode BOTH __NEXT_DATA__ (Pages Router) and self.__next_f (App Router), plus raw HTML
```

### Positive controls first — does the scanner actually catch what I missed?

| AMC | Expected | Scanner result |
|---|---|---|
| Zerodha | should find files (the case I got wrong) | **676 hits** ✅ |
| Groww | known-good archive | **80 hits** ✅ |
| JM Financial | files exist but are client-rendered | 0 hits (correct — not in server payload) |

The scanner reproduces the miss, so a zero from it is meaningful. **Important calibration:**
`0 hits` means *"not present in the server response"*, **not** *"does not exist"* — JM proves
that, since its files are real but arrive via client-side render.

### Results — 16 AMCs re-tested

| # | AMC | HTTP | Router payload | "monthly portfolio" mentions | Monthly FILE hits |
|---|---|---|---|---|---|
| 4 | AlphaGrep | 200 | – | 0 | 0 |
| 7 | Axis | 200 | `__next_f` | 1 | 0 |
| 8 | Bajaj Finserv | 200 | – | 1 | 0 |
| 12 | Canara Robeco | 200 | – | 31 | 0 |
| 15 | Choice | 200 | `__next_f` | 5 | 0 |
| 18 | Franklin | 200 | – | 0 | 0 |
| 26 | ITI | 200 | – | 0 | 0 |
| 27 | Jio BlackRock | 200 | `__next_f` | 49 | 0 |
| 31 | LIC | 200 | – | 18 | 0 |
| 32 | Mahindra Manulife | 200 | – | 0 | 0 |
| 41 | PGIM India | 200 | – | 4 | 0 |
| 49 | Tata | 200 | `__next_f` | 0 | 0 |
| 50 | Taurus | 200 | – | 36 | 0 |
| 51 | Trust | 200 | – | 0 | 0 |
| 54 | UTI | 200 | – | 0 | 0 |

**No further false negatives.** All 16 genuinely serve zero monthly-portfolio file URLs in the
HTTP response; their data really is client-fetched, which matches how they were already
classified. The high mention counts (Jio BlackRock 49, Taurus 36, Canara 31) are UI labels and
section headings, not files.

**Zerodha was the only site of the whole 52 using Pages Router `__NEXT_DATA__`** — which is
precisely why it slipped through a scan built around `self.__next_f`.

---

# CORRECTION 2 — Quantum (supersedes the Round 1/3 entries)

**My claim that Quantum has "no month-addressable source" was also WRONG.** I had only ever
tested the single `.xlsx` URL AMFI publishes and never located Quantum's own listing page.
It exists, it is server-rendered, and it is month-addressable by URL.

```text
AMC:                    Quantum Mutual Fund
AMFI URL:               https://www.quantumamc.com/FileCDN/FactSheet/5f6c4b6e-…-d54727d45369.xlsx
                        (a single frozen file — NOT the current month; see below)
Actual portfolio page:  https://www.quantumamc.com/portfolio/combined/{scheme}/{page}/{year}/{month}
                        reachable from the homepage as Portfolio -> "Fund Portfolio"

Monthly selection:      three dropdowns, and they are encoded directly in the URL path
                          ddlProductScheme  -1 = All Funds (or 3,4,10,11,15,16,… per scheme)
                          ddlYear           2010 … 2026
                          ddlMonth          1 … 12
                          ddlPageList       pagination (1-7 when unfiltered)
                        0/0 = unfiltered (returns every month, newest first)

Download mechanism:     direct <a href> to /FileCDN/FactSheet/<GUID>.xlsx
API/endpoint:           none needed — plain server-rendered GET
HTTP method:            GET
Required headers:       none — plain curl works
```

Verified by driving the dropdowns and then reproducing over `curl`:
```bash
curl -s "https://www.quantumamc.com/portfolio/combined/-1/1/2025/3"   # -> 1 GUID, "March 2025 - All Funds"
curl -s "https://www.quantumamc.com/portfolio/combined/-1/1/2026/7"   # -> 1 GUID, July 2026
curl -s "https://www.quantumamc.com/portfolio/combined/-1/1/0/0"      # -> 20 GUIDs, all months
```
```text
File formats:           .xlsx
One file or multiple:   ONE consolidated "All Funds" file per month
                        (per-scheme files also available via ddlProductScheme)
Historical months:      Yes — year dropdown goes back to 2010
Recommended method:     HTTP_REQUEST  (year+month in the path) -> DIRECT_FILE
Difficulty:             Easy
Verification:           VERIFIED — July 2026 file: 200, 979,848 bytes, PK
```

**The filename is a bare GUID with no month in it**, so the listing page is mandatory — but the
*listing* is addressable by year and month, which is all the downloader needs.

**AMFI's link is stale, not merely opaque.** AMFI's GUID `5f6c4b6e…` returns 952,771 bytes,
while the current July 2026 file `d701fe6a…` returns 979,848 bytes — different files. So AMFI is
pointing at a frozen older month, confirming that AMFI's URL must be treated as an *entry point
hint* only, never as the document itself.

---

## Revised scoreboard (final)

| Verification | Round 3 | **After re-test** |
|---|---|---|
| VERIFIED | 22 | **24** |
| PARTIALLY_VERIFIED | 20 | **18** |
| NOT_VERIFIED | 15 | **15** |

Zerodha and Quantum both move to **VERIFIED**. Quantum additionally joins the
**callable-from-plain-`requests`** group (now 8), as a path-parameterised GET.

### "No usable source" — now ZERO AMCs

Both claims in that category were mine and both were wrong. The only AMCs genuinely without a
monthly portfolio source are the **5 with no AMFI URL at all** (ASK, Carnelian, Lakshya,
Monarch, Nuvama) — and that is AMFI's gap, not a broken site.

### Method rules added as a result

1. **Never conclude absence from rendered DOM.** Accordions and tabs are collapsed on load;
   `querySelectorAll('a')` and `innerText` see only what is expanded. Zerodha's entire archive
   was one collapsed accordion away.
2. **Never write a file-URL regex that excludes whitespace.** Real AMC URLs contain literal
   spaces (Zerodha, Groww, IL&FS, JM Financial — JM has *three consecutive* spaces).
3. **Decode both Next.js routers.** `__NEXT_DATA__` and `self.__next_f` are mutually exclusive
   per site; checking only one silently blanks the other.
4. **Follow the AMC's own navigation before declaring a page missing.** Quantum's listing page
   was two clicks from its homepage; I had guessed at URLs instead and taken the 404s as
   evidence of absence.
5. **Zero results must be a hard error**, never a silent "this AMC has nothing" — the guard that
   would have caught both of these errors.
