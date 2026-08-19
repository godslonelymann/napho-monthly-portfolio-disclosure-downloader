#!/usr/bin/env python3
"""Verify a Bandhan monthly portfolio download is complete and correct.

Bandhan is the one AMC where a naive per-scheme scrape can silently produce
a wrong-looking-but-plausible dataset: the disclosure page leaves a stale
row on screen for several seconds after every dropdown change, so a slow
verify step can save the *previous* scheme's file under the *new* scheme's
name without ever raising an error. Bandhan_Mutual_Fund.py guards against
that live (network-response wait + row-label re-check), but this script is
the independent, after-the-fact check that nothing slipped through -- run
it any time you want to be sure a month is actually complete.

Five checks, cheapest first:

1.  discovery   -- every scheme the site's own dropdown offered got a final
                   answer (downloaded, or confirmed absent on the site),
                   none left unresolved.
2.  files       -- every "found" file on disk is non-empty, has the right
                   magic bytes, and openpyxl can open it.
3.  period      -- every "found" file's own on-page label names the month
                   we asked for (catches the stale-row bug after the fact,
                   independent of the live guard).
4.  icra        -- every Bandhan scheme ICRA's universe expects for the
                   period matched to a downloaded file (via
                   scripts/audit_icra_coverage.py).
5.  cross_check -- optional: if the consolidated portfolio-summary
                   workbook(s) (portfolio-summary/monthly -- debt, and
                   equity/hybrid in months that publish one) were also
                   saved, every scheme they list should also have its own
                   per-scheme file.

Usage::

    python scripts/verify_bandhan.py --period 2026-05
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.periods import extract_periods  # noqa: E402

AMC_SLUG = "bandhan"
# Named after each source page's own URL path segment (see
# verified/Bandhan_Mutual_Fund.py): the per-scheme disclosure page is
# ".../scheme-portfolios/monthly-half-yearly", the consolidated summary page
# is ".../portfolio-summary/monthly".
MONTHLY_SUBDIR = "monthly-half-yearly"
SUMMARY_SUBDIR = "monthly"
ICRA_AMC_NAME = "Bandhan Mutual Fund"
MIN_FILE_BYTES = 1024


def _month_slug(period: str) -> str:
    year, month = period.split("-")
    months = [
        "january", "february", "march", "april", "may", "june",
        "july", "august", "september", "october", "november", "december",
    ]
    return f"{months[int(month) - 1]}_{year}"


@dataclass
class Layer:
    name: str
    passed: bool
    summary: str
    details: dict = field(default_factory=dict)


def check_discovery(period_dir: Path) -> Layer:
    report_path = period_dir / ".bandhan_discovery_report.json"
    if not report_path.exists():
        return Layer(
            "discovery", False,
            f"no discovery report at {report_path} -- run the downloader first",
        )
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return Layer("discovery", False, f"discovery report is not valid JSON: {exc}")

    schemes = report.get("schemes", {})
    total_expected = report.get("total_schemes", len(schemes))
    found = [name for name, e in schemes.items() if e.get("status") == "found"]
    not_published = [name for name, e in schemes.items() if e.get("status") == "not_published"]
    # A dead dropdown entry: the site offers the scheme but serves nothing for
    # it and fires no listing request at all. Confirmed absent, not our gap.
    unavailable = [name for name, e in schemes.items() if e.get("status") == "unavailable_on_site"]
    errored = {name: e.get("reason", "") for name, e in schemes.items() if e.get("status") == "error"}
    unattempted = max(0, total_expected - len(schemes))

    passed = not errored and unattempted == 0
    summary = (
        f"{len(found)} downloaded, {len(not_published) + len(unavailable)} confirmed absent on site, "
        f"{len(errored)} unresolved, {unattempted} never attempted (of {total_expected} schemes)"
    )
    return Layer(
        "discovery", passed, summary,
        {
            "found": found,
            "not_published": not_published,
            "unavailable_on_site": unavailable,
            "errored": errored,
            "unattempted": unattempted,
        },
    )


def _xlsx_sheet_count(path: Path) -> int | None:
    try:
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
        return sum(1 for n in names if re.match(r"xl/worksheets/sheet\d+\.xml$", n))
    except (zipfile.BadZipFile, OSError):
        return None


def check_files(period_dir: Path, discovery: Layer) -> Layer:
    bad: dict[str, str] = {}
    checked = 0
    for name in discovery.details.get("found", []):
        docs = _load_docs_for(period_dir, name)
        for doc in docs:
            checked += 1
            path = period_dir / doc["filename"]
            if not path.exists():
                bad[doc["filename"]] = "missing on disk"
                continue
            size = path.stat().st_size
            if size < MIN_FILE_BYTES:
                bad[doc["filename"]] = f"only {size} bytes"
                continue
            head = path.read_bytes()[:4]
            if not head.startswith(b"PK"):
                bad[doc["filename"]] = "not a ZIP/XLSX payload"
                continue
            sheets = _xlsx_sheet_count(path)
            if not sheets:
                bad[doc["filename"]] = "no worksheets found inside the archive"
    passed = not bad
    summary = f"{checked - len(bad)}/{checked} files on disk are valid, non-empty workbooks"
    return Layer("files", passed, summary, {"bad": bad, "checked": checked})


def _load_docs_for(period_dir: Path, scheme_name: str) -> list[dict]:
    report_path = period_dir / ".bandhan_discovery_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    return report.get("schemes", {}).get(scheme_name, {}).get("documents", [])


def check_period(period_dir: Path, period: str, discovery: Layer) -> Layer:
    wrong: dict[str, str] = {}
    checked = 0
    for name in discovery.details.get("found", []):
        for doc in _load_docs_for(period_dir, name):
            checked += 1
            label = doc.get("label", "")
            periods_in_label = extract_periods(label)
            if period not in periods_in_label:
                wrong[doc["filename"]] = f"label {label!r} does not resolve to {period}"
    passed = not wrong
    summary = f"{checked - len(wrong)}/{checked} files carry a label confirming they belong to {period}"
    return Layer("period", passed, summary, {"wrong": wrong, "checked": checked})


def check_icra(period: str, raw_dir: Path, out_dir: Path) -> Layer:
    cmd = [
        sys.executable, str(ROOT / "scripts" / "audit_icra_coverage.py"),
        "--period", period, "--amc", f"{AMC_SLUG}/{MONTHLY_SUBDIR}",
        "--raw-dir", str(raw_dir), "--out-dir", str(out_dir),
    ]
    result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    if result.returncode != 0:
        return Layer(
            "icra", False,
            f"audit_icra_coverage.py exited {result.returncode}",
            {"stderr": result.stderr[-2000:]},
        )

    slug = _month_slug(period)
    coverage_path = out_dir / f"icra_coverage_{slug}.csv"
    missing_path = out_dir / f"icra_missing_{slug}.csv"

    matched = []
    if coverage_path.exists():
        with coverage_path.open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                if row.get("amc_icra_name") == ICRA_AMC_NAME:
                    matched.append(row.get("matched_icra_fund") or row.get("detected_scheme"))

    missing = []
    if missing_path.exists():
        with missing_path.open(newline="", encoding="utf-8") as fh:
            for row in csv.reader(fh):
                if len(row) > 1 and row[1] == ICRA_AMC_NAME:
                    missing.append(row[2])

    passed = not missing
    total = len(matched) + len(missing)
    summary = f"{len(matched)}/{total} ICRA-required Bandhan schemes matched to a downloaded file"
    return Layer("icra", passed, summary, {"matched": matched, "missing": missing})


def _icra_resolved_names(period: str, raw_dir: Path, amc_dir_name: str, out_dir: Path) -> set[str]:
    """Run the same matching engine audit_icra_coverage.py uses on a given
    raw-data directory and return the ICRA fund names it resolved.

    Used to compare the portfolio-summary workbook(s) against the per-scheme
    downloads by *meaning* rather than by raw sheet-title text: the debt
    workbook's sheet names are abbreviated ("Bandhan ON", "Bandhan CBF") in
    a way no simple string match survives, but audit_icra_coverage.py
    already knows how to resolve those abbreviations to full ICRA fund
    names, so reusing it here is both correct and avoids re-implementing
    that resolution logic a second time.
    """
    cmd = [
        sys.executable, str(ROOT / "scripts" / "audit_icra_coverage.py"),
        "--period", period, "--amc", amc_dir_name,
        "--raw-dir", str(raw_dir), "--out-dir", str(out_dir),
    ]
    result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"audit_icra_coverage.py exited {result.returncode}: {result.stderr[-500:]}")
    slug = _month_slug(period)
    coverage_path = out_dir / f"icra_coverage_{slug}.csv"
    names: set[str] = set()
    if coverage_path.exists():
        with coverage_path.open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                if row.get("amc_icra_name") == ICRA_AMC_NAME:
                    name = row.get("matched_icra_fund") or row.get("detected_scheme")
                    if name:
                        names.add(name)
    return names


def check_cross_reference(period: str, raw_dir: Path, icra_matched: set[str]) -> Layer:
    summary_dir = raw_dir / AMC_SLUG / SUMMARY_SUBDIR / period
    candidates = list(summary_dir.glob("*.xlsx")) if summary_dir.exists() else []
    if not candidates:
        return Layer(
            "cross_check", True,
            "skipped -- no consolidated portfolio-summary workbook saved for this period "
            "(optional cross-check against https://bandhanmutual.com/downloads/portfolio-summary/monthly)",
        )
    try:
        summary_names = _icra_resolved_names(
            period, raw_dir, f"{AMC_SLUG}/{SUMMARY_SUBDIR}", Path(tempfile.mkdtemp(prefix="bandhan_crosscheck_"))
        )
    except RuntimeError as exc:
        return Layer("cross_check", False, f"could not audit the portfolio-summary workbook(s): {exc}")

    if not summary_names:
        return Layer("cross_check", True, "skipped -- portfolio-summary workbook(s) resolved no ICRA schemes to compare")

    missing = sorted(summary_names - icra_matched)
    passed = not missing
    summary = f"{len(summary_names) - len(missing)}/{len(summary_names)} portfolio-summary schemes also matched among the per-scheme downloads"
    return Layer("cross_check", passed, summary, {"missing": missing})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--period", required=True, help="YYYY-MM")
    parser.add_argument("--raw-dir", default=str(ROOT / "data" / "raw"))
    parser.add_argument("--out-dir", default=str(ROOT / "outputs"), help="where audit_icra_coverage.py's CSVs are written/read")
    parser.add_argument("--out", default=None, help="path for the JSON report (default <out-dir>/bandhan_<period>_verification.json)")
    args = parser.parse_args(argv)

    raw_dir = Path(args.raw_dir)
    out_dir = Path(args.out_dir)
    period_dir = raw_dir / AMC_SLUG / MONTHLY_SUBDIR / args.period
    out_path = Path(args.out) if args.out else out_dir / f"bandhan_{args.period}_verification.json"

    discovery = check_discovery(period_dir)
    layers = [discovery]
    icra_layer = check_icra(args.period, raw_dir, out_dir)
    if discovery.passed or discovery.details.get("found"):
        layers.append(check_files(period_dir, discovery))
        layers.append(check_period(period_dir, args.period, discovery))
        layers.append(check_cross_reference(args.period, raw_dir, set(icra_layer.details.get("matched", []))))
    layers.append(icra_layer)

    overall_pass = all(layer.passed for layer in layers)

    print(f"Bandhan verification for {args.period}")
    print("-" * 60)
    for layer in layers:
        status = "PASS" if layer.passed else "FAIL"
        print(f"[{status}] {layer.name:<12} {layer.summary}")
    print("-" * 60)
    print(f"OVERALL: {'PASS' if overall_pass else 'FAIL'}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(
            {
                "period": args.period,
                "overall_pass": overall_pass,
                "layers": {
                    layer.name: {"passed": layer.passed, "summary": layer.summary, "details": layer.details}
                    for layer in layers
                },
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    try:
        shown_path = out_path.relative_to(ROOT)
    except ValueError:
        shown_path = out_path
    print(f"\nFull report: {shown_path}")
    return 0 if overall_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
