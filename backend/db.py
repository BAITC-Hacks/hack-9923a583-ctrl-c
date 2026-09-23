"""SQLite connection and schema for the contractor catalog."""

from contextlib import closing, contextmanager
from pathlib import Path
import sqlite3


ROOT_DIR = Path(__file__).resolve().parent
DEFAULT_DB_PATH = ROOT_DIR / "data" / "vendors.sqlite3"
CATALOG_NOT_READY_MESSAGE = (
    "Каталог подрядчиков не готов. Выполните импорт: python backend/import_dataset.py"
)
CATALOG_COLUMNS = {
    "id", "anon_name", "categories", "city", "city_imputed", "synthetic",
    "price_from_kzt", "price_imputed", "event_formats", "languages",
    "max_hours", "busy_dates", "description",
}


class CatalogNotReadyError(RuntimeError):
    """The catalog is missing, empty, or has an incompatible schema."""


def _catalog_size(connection: sqlite3.Connection) -> int:
    columns = {row["name"] for row in connection.execute("PRAGMA table_info(vendors)")}
    if not CATALOG_COLUMNS <= columns:
        raise CatalogNotReadyError(CATALOG_NOT_READY_MESSAGE)
    count = connection.execute("SELECT COUNT(*) FROM vendors").fetchone()[0]
    if count == 0:
        raise CatalogNotReadyError(CATALOG_NOT_READY_MESSAGE)
    return count


@contextmanager
def catalog_connection(db_path: Path | str = DEFAULT_DB_PATH):
    """Open a populated catalog read-only, without creating files on reads."""
    path = Path(db_path).resolve()
    if not path.is_file():
        raise CatalogNotReadyError(CATALOG_NOT_READY_MESSAGE)
    try:
        with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)) as connection:
            connection.row_factory = sqlite3.Row
            _catalog_size(connection)
            yield connection
    except sqlite3.DatabaseError as exc:
        raise CatalogNotReadyError(CATALOG_NOT_READY_MESSAGE) from exc


def catalog_size(db_path: Path | str = DEFAULT_DB_PATH) -> int:
    """Check readiness and return the number of imported profiles."""
    with catalog_connection(db_path) as connection:
        return connection.execute("SELECT COUNT(*) FROM vendors").fetchone()[0]


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
    with closing(connect(db_path)) as connection, connection:
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

