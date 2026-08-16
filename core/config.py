"""Environment-backed configuration for all downloaders.

The project intentionally has no required command-line arguments.  Values are
loaded from the repository's ``.env`` file, with sensible defaults for local
use and a previous-month default that avoids downloading an incomplete current
month.
"""

from __future__ import annotations

import calendar
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_dotenv(path: Path = ROOT / ".env") -> None:
    """Load simple KEY=VALUE pairs without overwriting the process environment."""

    if not path.exists():
        return
    try:
        from dotenv import load_dotenv

        load_dotenv(path, override=False)
        return
    except ImportError:
        pass

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv()


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def previous_month(today: date | None = None) -> str:
    current = today or date.today()
    year, month = current.year, current.month - 1
    if month == 0:
        year, month = year - 1, 12
    return f"{year:04d}-{month:02d}"


def valid_period(value: str) -> str:
    value = value.strip()
    if len(value) != 7 or value[4] != "-":
        raise ValueError(f"Period must be YYYY-MM, got {value!r}")
    year, month = value[:4], value[5:]
    if not (year.isdigit() and month.isdigit() and 1 <= int(month) <= 12):
        raise ValueError(f"Period must be YYYY-MM, got {value!r}")
    return value


@dataclass(frozen=True)
class Settings:
    period: str
    output_dir: Path
    download: bool
    max_files: int
    delay_seconds: float
    connect_timeout: int
    read_timeout: int
    discovery_timeout: int
    retry_total: int
    retry_backoff: float
    process_timeout: int
    headless: bool
    user_agent: str
    extract_archives: bool
    keep_archives: bool
    validate: bool
    validate_only: bool


def settings() -> Settings:
    period = valid_period(os.getenv("AMC_PERIOD", previous_month()))
    output = Path(os.getenv("AMC_OUTPUT_DIR", "data/raw"))
    if not output.is_absolute():
        output = ROOT / output
    return Settings(
        period=period,
        output_dir=output.resolve(),
        download=_bool("AMC_DOWNLOAD", True),
        max_files=max(0, _int("AMC_MAX_FILES", 0)),
        delay_seconds=max(0.0, _float("AMC_DELAY_SECONDS", 0.0)),
        connect_timeout=max(1, _int("AMC_CONNECT_TIMEOUT", 30)),
        read_timeout=max(1, _int("AMC_READ_TIMEOUT", 120)),
        discovery_timeout=max(1, _int("AMC_DISCOVERY_TIMEOUT", 30)),
        retry_total=max(0, _int("AMC_RETRY_TOTAL", 2)),
        retry_backoff=max(0.0, _float("AMC_RETRY_BACKOFF", 0.5)),
        process_timeout=max(1, _int("AMC_PROCESS_TIMEOUT", 600)),
        headless=_bool("AMC_BROWSER_HEADLESS", True),
        extract_archives=_bool("AMC_EXTRACT_ARCHIVES", True),
        keep_archives=_bool("AMC_KEEP_ARCHIVES", False),
        # Escape hatch for the expected-vs-downloaded validation pipeline
        # (core.expectations / core.validation): default on, but a single
        # env var can fall back to the old discover-then-download-and-trust
        # behavior for every script at once if validation itself turns out
        # to be the thing that's broken.
        validate=_bool("AMC_VALIDATE", True),
        # Audit mode: discover live, then check what's already on disk
        # against that expected set -- without downloading anything. Lets
        # an already-downloaded month be checked for silent gaps after the
        # fact, at the cost of one live discovery pass instead of a full
        # re-download of every file.
        validate_only=_bool("AMC_VALIDATE_ONLY", False),
        user_agent=os.getenv(
            "AMC_USER_AGENT",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0 Safari/537.36 portfolio-downloader/1.0",
        ),
    )


def latest_day(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]
