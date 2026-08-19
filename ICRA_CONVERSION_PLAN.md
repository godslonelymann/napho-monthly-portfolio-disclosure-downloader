# AMC Monthly Portfolio → ICRA Rows

**Implementation plan — 2026-08-18**

Give it one AMC's monthly portfolio file. Get back rows shaped like
`ICRA_Sample.xlsx` → `Portfolio Data_May2026`, plus the security name.

Every number here was measured against the May-2026 sample (127,912 rows,
2,089 schemes, 7,302 distinct ISINs).

---

## Output — 14 columns

ICRA's 13, plus `Security_Name`.

| # | Column | Source |
|---|---|---|
| 1 | `AMFI Code` | scheme name lookup |
| 2 | `Port_Date` | month-end **date** — `2026-05-31`, not `2026-05` |
| 3 | `ISIN` | file, or name lookup, or blank |
| 4 | `Instrument_Name` | ISIN type code |
| 5 | `Nature_Name` | ISIN type code |
| 6 | `Basic_Industry` | blank |
| 7 | `Industry` | blank |
| 8 | `Sector_Name` | blank |
| 9 | `Macro_Economic_Sector` | blank |
| 10 | `Corpus_Per` | file |
| 11 | `Mkt_Value` | file |
| 12 | `No_Of_Shares` | file |
| 13 | `Fund_Name` | scheme name lookup |
| 14 | `Security_Name` | external masters |

Optional 15th, audit only: `Name_Source` — which source supplied column 14
(`nse` / `bse` / `amfi` / `figi` / `prefix`). Needed because prefix-derived
names are the *issuer*, not the full security description.

---

## Step 0 — Build the masters

One-off, then refresh monthly. Nothing here touches an AMC file.

### 0a. Download

| Source | URL | ISINs |
|---|---|---|
| NSE equity | `nsearchives.nseindia.com/content/equities/EQUITY_L.csv` | 2,553 |
| NSE SME | `nsearchives.nseindia.com/emerge/corporates/content/SME_EQUITY_L.csv` | 561 |
| NSE ETF | `nsearchives.nseindia.com/content/equities/eq_etfseclist.csv` | 342 |
| BSE | `api.bseindia.com/BseIndiaAPI/api/ListofScripData/w?segment=Debt&status=Active` | 8,853 |
| AMFI | `portal.amfiindia.com/spages/NAVAll.txt` | 17,441 |

NSE needs `Referer: https://www.nseindia.com/`. BSE needs `Referer` **and**
`Origin: https://www.bseindia.com`. AMFI 302-redirects `www` → `portal`, so
follow redirects.

### 0b. Merge → `data/external/security_master.csv`

Priority NSE → BSE → AMFI, first hit wins. **29,750 ISINs.**

Name column per source: NSE `NAME OF COMPANY`; BSE `Issuer_Name` (falling back
to `Scrip_Name`); AMFI field 3, taking ISINs from fields 1 and 2.

### 0c. Derive → `data/external/issuer_prefixes.csv`

An Indian ISIN's **first 7 characters are the issuer**:

```
INE002A 01 018   Reliance equity   ← on the NSE list
INE002A 14 LP6   Reliance CP       ← on no list anywhere
        └─ same issuer code
```

Group the master by `ISIN[:7]` for `INE…`/`INF…`, take the most common name.
**5,967 issuer codes, for free.** This is what makes CD, CP and unlisted bonds
resolvable at all.

### 0d. OpenFIGI cache → `data/external/figi_cache.csv`

`POST https://api.openfigi.com/v3/mapping`, body
`[{"idType":"ID_ISIN","idValue":"..."}]`, 25 per request. No key needed at low
rate. Only ever called for ISINs missing from 0b **and** 0c, so it shrinks to
near zero after the first build. Cache every result, including misses.

Covers foreign equity, SDL, Gilt and T-Bill at ~100%. Returns nothing for
CP or CD — do not waste calls there.

---

## Step 1 — Parse the sheets

Read each AMC file, emit one row per holding.

Read from the row: **ISIN, name, quantity, market value, %, section header**.
The scheme name comes from the sheet name, the title rows above the table, or
the filename.

Fills `ISIN`, `Corpus_Per`, `Mkt_Value`, `No_Of_Shares`. Leaves 6–9 blank.

Three things that are not optional:

**Open by bytes, not extension.** Six AMCs ship `.xlsx` files named `.xls`
(Nippon India, Baroda BNP, PPFAS, Abakkus, Shriram, UTI). `PK\x03\x04` → xlsx,
`\xd0\xcf\x11\xe0` → real xls. Trusting the suffix loses all six.

**Find columns by content, not by heading.** Headings point at the wrong column
— Kotak's "Name of Instrument" heading sits in col 0 while every name is in
col 2. Find the ISIN column by check-digit validity; find the name column among
the rest by highest distinct-value ratio (names are near-unique; the industry
column beside them repeats "Banks" hundreds of times).

**Validate the ISIN check digit.** `AUBANK300626` and `BHEL29052025` are futures
contracts that match the 12-character shape exactly. ISO 6166: letters → two
digits (A=10), then Luhn. Failures go to a contracts file, not to the ISIN column.

Also: don't assume one table per sheet (Tata has 123 tables in 66 sheets), and
don't assume a fixed header row (real files start anywhere from row 2 to 13).

**Units.** Some AMCs send `5.98`, some send `0.0598`; some lakhs, some crores.
Detect per scheme: `Corpus_Per ≈ Mkt_Value / Σ Mkt_Value × 100` holds whatever
the scale is. Cross-check by summing the percent column — ≈100 → percent,
≈1 → fraction, **anything else → abstain and flag the scheme** rather than guess.

`pipeline/convert.py` currently hardcodes `pct × 100` and `mkt ÷ 100`; that is
what this replaces.

---

## Step 2 — AMFI code

Scheme name → `AMFI Code` + `Fund_Name`.

`navall.txt` groups schemes under the same 52 AMC names as your folders, which
narrows the search from 14,273 schemes to ~300. Then `Scheme Master` (columns
`Sub Plan` and `Option Type`) picks Regular + Growth, which is what ICRA uses.

No match → review file. **Report the unmatched count on every run** — a scheme
with no AMFI code cannot be written out, so this number is the real measure of
completeness.

---

## Step 3 — ISIN missing → map it

Row is a real security but the AMC printed no ISIN. Match the **name** against
`security_master.csv`.

External masters carry one official spelling and AMCs type many, so normalise
both sides before comparing: uppercase, strip punctuation, fold `Ltd` / `Ltd.` /
`Limited`, strip footnote marks (`*^£$#~`) and `EQ-` / `FUT-` prefixes.

Found → fill it. Not found → leave blank and log.

---

## Step 4 — Instruments that have no ISIN

10.4% of ICRA rows (13,309) carry no ISIN and are **supposed to be blank**.
Closed set of 18 — do not send these to the review file.

| rows | Instrument_Name | Nature_Name | recognise by |
|---:|---|---|---|
| 7,737 | Futures | EQ | derivatives sheet / section |
| 1,818 | Money Market | Others | section header |
| 1,219 | Net Receivables/(Payables) | Others | name |
| 723 | Current Assets | Others | name |
| 416 | IRS | Debt | section header |
| 344 | Margin Deposit | Debt | name |
| 243 | Undisclosed - Others | Others | fallback |
| 234 | Cash | Others | name |
| 225 | Equity | EQ | unlisted / awaiting listing |
| 109 | Reverse repo | Others | name |
| 91 | Options | EQ | derivatives section |
| 61 | Repo | Others | name |
| 39 | Gold | Others | name |
| 20 | Silver | Others | name |
| 18 | Commodity Futures | Others | derivatives section |
| 5 | MFEquity | EQ | name |
| 4 | FD | Debt | name |
| 3 | Preference Shares | EQ | section header |

Futures and Money Market are 70% of it and both come off the section header
alone. Build it as one central list with a per-AMC override file for stragglers,
not 52 separate configs.

For these rows `Security_Name` is the name printed on the row itself
(`Net Receivables`, `TREPS`, `Gold`) — already read in step 1, nothing to look up.

---

## Step 5 — ISIN → Instrument_Name + Nature_Name

Key on **`ISIN[:3] + ISIN[7:9]`**. The 3-character prefix matters: keying on the
two-digit code alone scores 90.6%, prefix-aware scores 96.7%, because `INF…`
fund units collide with `INE…` equities on the same code.

| Key | Type | Measured |
|---|---|---|
| `INE…01` | Equity | 1,261 / 1,261 |
| `INE…14` | CP | 991 / 991 |
| `INE…16` | CD | 387 / 387 |
| `IN1` `IN2` `IN3` | SDL | 1,263 / 1,263 |
| `IN0` | Gilt / T Bill | mixed — needs maturity |
| `INF` | MF units | mixed — use the ICRA seed |

Accuracy: **99.2%** of rows that have an ISIN; **~96%** on an ISIN never seen
before. Foreign ISINs (866 in the sample) score 99.5% — allow all countries,
not just `IN`.

Unmatched → ICRA seed table → review file.

For step-4 rows the type comes from the section header instead.

---

## Step 6 — ISIN → Security_Name

External sources only, in this order:

```
1. security_master.csv     exact match         NSE / BSE / AMFI
2. figi_cache.csv          exact match         OpenFIGI
3. issuer_prefixes.csv     ISIN[:7]            issuer name, not full description
4. blank + log
```

Measured fill rate, row-weighted, on the 114,603 rows that carry an ISIN:

| Stage | Fill |
|---|---|
| NSE + BSE + AMFI | 82.3% |
| + OpenFIGI | 93.2% |
| + issuer prefix | **97.5%** |

| Instrument | rows | exch | +figi | +prefix |
|---|---:|---:|---:|---:|
| Equity | 89,080 | 97% | 100% | 100% |
| Bond | 9,796 | 58% | 83% | 83% |
| CD | 3,972 | 0% | 0% | **89%** |
| SDL | 3,645 | 0% | 100% | 100% |
| Gilt | 2,445 | 0% | 100% | 100% |
| CP | 2,354 | 16% | 16% | **74%** |
| T Bill | 1,362 | 0% | 100% | 100% |

**2,914 rows stay blank — 2.3% of the sheet.** Bonds 1,659, CP 618, CD 450,
AIF 142. Unlisted issuers present in no external source; nothing reaches them.

Set `Name_Source` on every filled row so prefix-derived names stay
distinguishable from exact ones.

---

## Step 7 — Check it

Three checks, run per scheme. Keep it to these.

1. `Corpus_Per` sums to ~100
2. Totals match the file's own Grand Total row
3. `Corpus_Per ≈ Mkt_Value / Σ Mkt_Value × 100` holds per row

Comparing against ICRA is also worth doing, but note the AMFI codes and the
instrument seed are extracted *from* ICRA May-2026 — so a row-count or ISIN-set
match against it confirms parsing, not the seed. Checks 1–3 are the ones that
test the pipeline against the source files themselves.

Every run prints three numbers:

- schemes written
- schemes in the review file *(no AMFI code — these were dropped)*
- rows with a blank `Security_Name`

---

## Modules

| File | Step | Status |
|---|---|---|
| `pipeline/masters.py` | 0 | build — download, merge, prefixes, FIGI cache |
| `pipeline/extract.py` | 1 | build — the real work |
| `pipeline/schemes.py` | 2 | build — name → AMFI code |
| `pipeline/non_isin.py` | 4 | build — the 18-row list |
| `pipeline/isin_type.py` | 5 | build — ~30 lines + ICRA seed |
| `pipeline/names.py` | 3, 6 | build — both directions over the masters |
| `pipeline/convert.py` | join | exists — needs the 4 blanks, `Port_Date`, `Security_Name`, detected units |
| `pipeline/schema.py` | — | exists — needs `Port_Date` and `Security_Name` |
| `pipeline/validate.py` | 7 | exists — needs checks 2 and 3 |
| `pipeline/lookups.py` | seed | done |

`pipeline/isin_names.py` harvests names from the AMC files. **Not used** — its
canonical name is decided by a vote across all months with no month dimension,
which returns `Zomato Limited` for a company renamed in April 2025. Keep the
file; don't wire it in.

---

## Order of work

| # | Do | Why here |
|---|---|---|
| 1 | Step 0 — masters | pure download + merge, no AMC files, unblocks 3, 5, 6 |
| 2 | Step 1 — parser | the only real work; everything downstream is joins |
| 3 | Survey: run step 1 over all 52 AMCs, report only | tells you which AMCs actually break, before writing any per-AMC code |
| 4 | Steps 5, 6 — ISIN → type, ISIN → name | pure lookups against work already done |
| 5 | Step 2 — AMFI resolver | biggest cause of dropped rows |
| 6 | Steps 3, 4 — the no-ISIN rows | Futures + Money Market first, 70% of it |
| 7 | Step 7 — checks | turns "it ran" into "it's right" |
| 8 | Per-AMC fixes, driven by the survey | expect 8–12 of 52, not all |

Run the survey before writing any AMC-specific code. It is the only step that
tells you how big steps 6 and 8 really are.

---

## Reference — layouts

| Layout | AMCs | Scheme name lives in |
|---|---|---|
| One workbook, sheet per scheme, has an index | Axis (89), SBI (126), Tata (70), Motilal (86), Edelweiss (72) | the index sheet |
| One workbook, sheet per scheme, no index | Kotak (117), Quant, DSP, Groww (59), Franklin (40) | title rows above the table |
| One file per scheme | HDFC (109), ICICI (143), Mirae (93), HSBC (45), Bandhan (77) | title rows, then the filename |
| Several schemes stacked in one sheet | PPFAS, IL&FS | the title row above each table |

## Reference — sources that don't work

Probed and rejected, so they don't get retried:

- **NSDL**, **CDSL** — JS applications, no reachable data endpoint
- **CCIL**, **FBIL** — 404 or empty
- **BSE** `DebtSecurities`, `ddlDebtIssuer`, `DebtCorpBond` — return HTML shells
- **NSE** `liveBonds-traded-on-cm` — has 607 ISINs but no issuer name, only a
  ticker like `852MFL31`
