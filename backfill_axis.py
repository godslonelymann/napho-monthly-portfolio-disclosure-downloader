"""Walk the full Axis monthly-portfolio archive, one month at a time.

The per-AMC scripts in ``verified/`` deliberately handle a single period so
they all behave the same way.  Axis is the one AMC whose catalogue exposes a
usable history (2012-10 onward), so this runner replays ``discover`` across
every month and reuses the shared downloader for each one.

Two months are known to be genuinely empty upstream rather than broken:
2023-04, where Axis only published weekly files, and the current month, which
has no monthly portfolio until the month closes.  Both are reported as
"absent" and do not count as failures.

Configuration comes from ``.env`` like everything else.  Override the range
with ``AXIS_BACKFILL_START`` / ``AXIS_BACKFILL_END`` (both ``YYYY-MM``).
"""

from __future__ import annotations

import importlib.util
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from core.cli import download_documents
from core.config import previous_month, settings, valid_period
from core.http import create_session


def _load_axis():
    """Import ``verified/Axis_Mutual_Fund.py``."""
    path = ROOT / "verified" / "Axis_Mutual_Fund.py"
    spec = importlib.util.spec_from_file_location("axis_downloader", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def month_range(start: str, end: str) -> list[str]:
    periods = []
    year, month = int(start[:4]), int(start[5:7])
    while f"{year:04d}-{month:02d}" <= end:
        periods.append(f"{year:04d}-{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return periods


def main() -> int:
    axis = _load_axis()
    config = settings()
    session = create_session()

    start = valid_period(os.getenv("AXIS_BACKFILL_START", "2012-10"))
    end = valid_period(os.getenv("AXIS_BACKFILL_END", previous_month()))
    periods = month_range(start, end)
    root = config.output_dir / re.sub(r"[^a-z0-9._-]+", "_", axis.AMC.lower()).strip("_")

    print(f"axis backfill {start} -> {end} ({len(periods)} months)")
    print(f"output={root} download={config.download}")

    absent, failed, saved = [], [], 0
    for period in periods:
        try:
            documents = axis.discover(period, session=session)
        except axis.NoMonthlyPortfolio:
            absent.append(period)
            print(f"absent     {period}")
            continue
        except Exception as exc:
            failed.append(period)
            print(f"failed     {period}: {exc}")
            continue

        if not config.download:
            for document in documents:
                print(f"found      {period} {document.url}")
            saved += len(documents)
            continue

        try:
            download_documents(session, documents, root / period, delay_seconds=config.delay_seconds)
            saved += len(documents)
        except Exception as exc:
            failed.append(period)
            print(f"failed     {period}: {exc}")

    print(f"\ndone: {saved} file(s), {len(absent)} absent, {len(failed)} failed")
    if absent:
        print(f"absent months: {', '.join(absent)}")
    if failed:
        print(f"failed months: {', '.join(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
