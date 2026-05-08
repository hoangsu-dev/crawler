"""One-time / idempotent seeder: reads universities.csv into snapshots.db.

Run this whenever you edit universities.csv:
    python init_db.py

It is safe to re-run; rows are upserted by name.
"""
import csv
import sys

import db

CSV_FILE = "universities.csv"


def parse_bool(val):
    return str(val).strip().lower() in ("true", "1", "yes", "y", "x")


def main():
    db.init_schema()

    try:
        with open(CSV_FILE, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
    except FileNotFoundError:
        print(f"❌ {CSV_FILE} not found. Create it first.")
        sys.exit(1)

    count = 0
    for row in rows:
        name = (row.get("name") or "").strip()
        url = (row.get("url") or "").strip()
        if not name or not url:
            continue
        db.upsert_university(
            name=name,
            url=url,
            region=(row.get("region") or "").strip() or None,
            is_competitor=parse_bool(row.get("is_competitor", "")),
            notes=(row.get("notes") or "").strip() or None,
        )
        count += 1

    print(f"✅ Seeded {count} universities into {db.DB_FILE}")
    competitors = sum(1 for u in db.list_universities() if u["is_competitor"])
    print(f"   ({competitors} marked as competitor)")


if __name__ == "__main__":
    main()
