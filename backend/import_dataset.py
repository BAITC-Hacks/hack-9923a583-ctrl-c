"""Import the provided CSV dataset into the local SQLite catalog."""

import argparse
import csv
import json
from pathlib import Path

try:  # Supports both `python backend/import_dataset.py` and module execution.
    from .db import DEFAULT_DB_PATH, initialize_database, connect
except ImportError:  # pragma: no cover - script mode
    from db import DEFAULT_DB_PATH, initialize_database, connect


PROJECT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CSV_PATH = PROJECT_DIR / "hackathon dataset anonymized .csv"
LIST_FIELDS = ("categories", "event_formats", "languages", "busy_dates")
BOOL_FIELDS = ("city_imputed", "synthetic", "price_imputed")


def import_csv(csv_path: Path, db_path: Path = DEFAULT_DB_PATH) -> int:
    initialize_database(db_path)
    with csv_path.open("r", encoding="utf-8-sig", newline="") as source:
        rows = list(csv.DictReader(source))

    values = []
    for row in rows:
        item = dict(row)
        for field in LIST_FIELDS:
            item[field] = json.dumps(
                [value for value in (item[field] or "").split("|") if value],
                ensure_ascii=False,
            )
        for field in BOOL_FIELDS:
            item[field] = int((item[field] or "").strip().lower() == "true")
        for field in ("price_from_kzt", "max_hours"):
            item[field] = item[field] or None
        values.append(tuple(item[field] for field in (
            "id", "anon_name", "categories", "city", "city_imputed", "synthetic",
            "price_from_kzt", "price_imputed", "event_formats", "languages",
            "max_hours", "busy_dates", "description",
        )))

    with connect(db_path) as connection:
        connection.executemany(
            """INSERT OR REPLACE INTO vendors (
                id, anon_name, categories, city, city_imputed, synthetic,
                price_from_kzt, price_imputed, event_formats, languages,
                max_hours, busy_dates, description
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            values,
        )
    return len(values)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV_PATH)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    args = parser.parse_args()
    count = import_csv(args.csv, args.db)
    print(f"Импортировано профилей: {count}. База: {args.db}")


if __name__ == "__main__":
    main()
