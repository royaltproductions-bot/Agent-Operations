"""Compare daily sales across matching calendar windows using standard-library Python.

Input CSV columns: date,net_sales (ISO dates, decimal USD).
Usage: python sales_period_compare.py daily.csv 2026-10-01 2026-10-07
"""
import argparse
import csv
import json
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation


def load_daily(path):
    totals = defaultdict(lambda: Decimal("0"))
    with open(path, newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or not {"date", "net_sales"} <= set(reader.fieldnames):
            raise ValueError("CSV must contain date and net_sales columns")
        for line, row in enumerate(reader, start=2):
            try:
                day = date.fromisoformat(row["date"].strip())
                amount = Decimal(row["net_sales"].strip())
                if not amount.is_finite() or amount.as_tuple().exponent < -2:
                    raise ValueError("amount must have at most two decimal places")
            except (ValueError, InvalidOperation, AttributeError) as exc:
                raise ValueError(f"Invalid record at CSV line {line}: {exc}") from exc
            totals[day] += amount
    return totals


def compare(daily, start, end):
    if end < start or (end - start).days > 365:
        raise ValueError("Choose a valid window of 366 days or fewer")
    try:
        baseline_start = start.replace(year=start.year - 1)
        baseline_end = end.replace(year=end.year - 1)
    except ValueError as exc:
        raise ValueError("Previous-year window cannot represent leap-day endpoint") from exc

    def total_and_coverage(a, b):
        total = Decimal("0")
        covered = 0
        day = a
        while day <= b:
            if day in daily:
                total += daily[day]
                covered += 1
            day += timedelta(days=1)
        return total, covered, (b - a).days + 1

    baseline, b_covered, b_days = total_and_coverage(baseline_start, baseline_end)
    impact, i_covered, i_days = total_and_coverage(start, end)
    difference = impact - baseline
    percentage = (difference / baseline * 100) if baseline > 0 else None
    return {
        "baseline_start": baseline_start.isoformat(),
        "baseline_end": baseline_end.isoformat(),
        "impact_start": start.isoformat(),
        "impact_end": end.isoformat(),
        "baseline_sales_usd": str(baseline),
        "impact_sales_usd": str(impact),
        "difference_usd": str(difference),
        "percentage_change": str(percentage.quantize(Decimal("0.01"))) if percentage is not None else None,
        "baseline_recorded_days": b_covered,
        "baseline_calendar_days": b_days,
        "impact_recorded_days": i_covered,
        "impact_calendar_days": i_days,
        "warning": "Missing dates are unknown, not verified zero sales. Descriptive only; not evidence of causation.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_file")
    parser.add_argument("start", type=date.fromisoformat)
    parser.add_argument("end", type=date.fromisoformat)
    args = parser.parse_args()
    print(json.dumps(compare(load_daily(args.csv_file), args.start, args.end), indent=2))


if __name__ == "__main__":
    main()
