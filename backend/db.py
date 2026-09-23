"""SQLite connection and schema for the contractor catalog."""

from pathlib import Path
import sqlite3


ROOT_DIR = Path(__file__).resolve().parent
DEFAULT_DB_PATH = ROOT_DIR / "data" / "vendors.sqlite3"


def connect(db_path: Path | str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Open the catalog database and return rows as dictionaries."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_database(db_path: Path | str = DEFAULT_DB_PATH) -> None:
    """Create the initial catalog schema if it does not exist."""
    with connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS vendors (
                id TEXT PRIMARY KEY,
                anon_name TEXT NOT NULL,
                categories TEXT NOT NULL,
                city TEXT NOT NULL,
                city_imputed INTEGER NOT NULL DEFAULT 0,
                synthetic INTEGER NOT NULL DEFAULT 0,
                price_from_kzt INTEGER,
                price_imputed INTEGER NOT NULL DEFAULT 0,
                event_formats TEXT NOT NULL,
                languages TEXT NOT NULL,
                max_hours REAL,
                busy_dates TEXT NOT NULL,
                description TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_vendors_city ON vendors(city);
            """
        )

