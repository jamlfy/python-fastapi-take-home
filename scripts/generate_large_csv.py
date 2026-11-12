"""Generate a reproducible, large coffee CSV with occasional dirty records.

Usage: uv run python scripts/generate_large_csv.py --output data/coffee_health_15m.csv
"""

from __future__ import annotations

import argparse
import csv
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/coffee_health_raw.csv"
DEFAULT_OUTPUT = ROOT / "data/coffee_health_15m.csv"
HEADER = (
    "ID", "Age", "Gender", "Country", "Coffee_Intake", "Caffeine_mg",
    "Sleep_Hours", "Sleep_Quality", "BMI", "Heart_Rate", "Stress_Level",
    "Physical_Activity_Hours", "Health_Issues", "Occupation", "Smoking",
    "Alcohol_Consumption",
)


def generate(output: Path, count: int, seed: int, source: Path = SOURCE) -> None:
    if count < 0:
        raise ValueError("count must be non-negative")
    if output.resolve() == source.resolve():
        raise ValueError("output must differ from source")

    with source.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        if tuple(next(reader)) != HEADER:
            raise ValueError("unexpected source CSV header")
        samples = [row for row in reader if len(row) == len(HEADER) and row[0].isdigit()]
    if not samples:
        raise ValueError("source CSV has no usable rows")

    rng = random.Random(seed)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(HEADER)
        previous: list[str] | None = None
        for index in range(count):
            row = rng.choice(samples).copy()
            row[0] = str(1_000_000 + index)

            # One dirty record per 100 rows; cycle through representative cases.
            if index % 100 == 99:
                case = (index // 100) % 11
                if case == 0:
                    row[2], row[3], row[7] = " male ", " south korea ", " good "
                elif case == 1:
                    row[4], row[5] = "7,5", "712,5 mg"
                elif case == 2:
                    row[14], row[15] = "YES", "no"
                elif case == 3:
                    row[6] = "-1"
                elif case == 4:
                    row[1] = "thirty"
                elif case == 5:
                    row[14] = "-1"
                elif case == 6:
                    row[4], row[5] = "0", "400.0"
                elif case == 7:
                    row[2] = "?"
                elif case == 8:
                    row.append("extra")
                elif case == 9 and previous is not None:
                    row = previous.copy()  # Exact duplicate of the preceding record.
                elif previous is not None:
                    row = previous.copy()
                    row[1] = "18" if row[1] != "18" else "19"  # Conflicting duplicate.

            writer.writerow(row)
            if len(row) == len(HEADER):
                previous = row


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--count", type=int, default=15_000_000)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    generate(args.output, args.count, args.seed)
    print(f"Wrote {args.count} data records to {args.output}")


if __name__ == "__main__":
    main()
