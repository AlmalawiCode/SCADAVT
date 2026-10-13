"""Download Melbourne Water's weekly water use per person (litres/person/day).

The series is published by Melbourne Water Corporation in its "Water storage
levels" web application. Melbourne Water reserves all rights in the data, so
this repository does not redistribute it; run this script to obtain your own
copy for research use.

    python tools/fetch_consumption.py --from 2022-01-06 --to 2026-10-01

Each request returns the week ending on the given Thursday. The values are
written unchanged to data/consumption_per_person.csv (date,litres_per_person_per_day).
"""

import argparse
import csv
import datetime as dt
import io
import time
import urllib.request
from pathlib import Path

URL = "https://api.melbournewater.com.au/water-storage/levels/usage/week/file?searchDate={}"


def fetch_week(day: dt.date) -> list[list[str]]:
    with urllib.request.urlopen(URL.format(day.isoformat()), timeout=30) as r:
        return list(csv.reader(io.StringIO(r.read().decode("utf-8-sig"))))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--from", dest="start", type=dt.date.fromisoformat, default=dt.date(2022, 1, 6))
    ap.add_argument("--to", dest="end", type=dt.date.fromisoformat, default=dt.date(2026, 10, 1))
    ap.add_argument("--out", type=Path, default=Path(__file__).resolve().parents[1] / "data" / "consumption_per_person.csv")
    ap.add_argument("--raw", type=Path, help="optional folder to keep each weekly CSV as downloaded")
    a = ap.parse_args()
    day, rows = a.start, []
    while day <= a.end:
        try:
            table = fetch_week(day)
        except Exception as e:  # network error or week not published
            print(f"{day}: {e}")
            table = []
        if a.raw and table:
            a.raw.mkdir(parents=True, exist_ok=True)
            with open(a.raw / f"{day}.csv", "w", newline="") as fh:
                csv.writer(fh).writerows(table)
        value = None
        for r in table[1:]:
            # the weekly file lists the week-ending date and the per-person use
            nums = [c for c in r if c.strip().replace(".", "", 1).isdigit()]
            if nums:
                value = nums[-1]
        if value:
            rows.append((day.isoformat(), value))
            print(day, value)
        day += dt.timedelta(days=7)
        time.sleep(0.5)
    with open(a.out, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["date", "litres_per_person_per_day"])
        w.writerows(rows)
    print(f"{len(rows)} weeks -> {a.out}")


if __name__ == "__main__":
    main()
