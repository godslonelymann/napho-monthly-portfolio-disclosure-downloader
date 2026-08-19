#!/bin/bash
# Run the ICRA conversion pipeline for one AMC and one month.
#
# Usage:
#   ./run.sh <amc> <YYYY-MM>
#
# Examples:
#   ./run.sh 360_one 2026-05
#   ./run.sh sbi 2024-11
#
# Output CSV lands at data/parsed/<amc>/<YYYY-MM>.csv

set -e

AMC="$1"
PERIOD="$2"

if [ -z "$AMC" ] || [ -z "$PERIOD" ]; then
    echo "Usage: ./run.sh <amc> <YYYY-MM>"
    echo "Example: ./run.sh 360_one 2026-05"
    exit 1
fi

.venv/bin/python3 -m pipeline.run_all --amc "$AMC" --period "$PERIOD"
