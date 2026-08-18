"""Filter CSV rows by exact status values."""

from pathlib import Path

import pandas as pd


INPUT_FILE = Path("portfolio_manifest-remote-server.csv")
OUTPUT_FILE = Path("filtered_data.csv")
EXCLUDED_STATUSES = {
    "NOT_YET_PUBLISHED",
    "YEAR_NOT_AVAILABLE",
    "MONTH_NOT_AVAILABLE",
    "ALREADY_EXISTS",
}


def main() -> None:
    """Load, filter, print, and export the CSV data."""
    data = pd.read_csv(INPUT_FILE)

    filtered_data = data[~data["status"].isin(EXCLUDED_STATUSES)]

    print(f"Total remaining entries: {len(filtered_data)}")
    print(filtered_data)

    filtered_data.to_csv(OUTPUT_FILE, index=False)


if __name__ == "__main__":
    main()
