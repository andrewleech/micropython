"""Database layer for mpy-triage.

Manages SQLite connections, schema initialization, sync state, and
sqlite-vec extension loading.
"""

import logging
import sqlite3
from pathlib import Path

log = logging.getLogger(__name__)


def get_connection(db_path: Path) -> sqlite3.Connection:
    """Open an SQLite connection with Row factory and WAL mode.

    Args:
        db_path: Path to the SQLite database file.

    Returns:
        An open sqlite3.Connection.
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db(conn: sqlite3.Connection, schema_path: Path) -> None:
    """Read and execute a schema SQL file against the connection.

    Safe to call multiple times (uses IF NOT EXISTS in schema).

    Args:
        conn: An open sqlite3 connection.
        schema_path: Path to the .sql schema file.
    """
    schema_path = Path(schema_path)
    schema_sql = schema_path.read_text()
    conn.executescript(schema_sql)
    conn.commit()


def get_sync_state(conn: sqlite3.Connection, key: str) -> str | None:
    """Retrieve a value from the sync_state table.

    Args:
        conn: An open sqlite3 connection.
        key: The sync state key to look up.

    Returns:
        The stored value string, or None if the key is not found.
    """
    cursor = conn.execute("SELECT value FROM sync_state WHERE key = ?", (key,))
    row = cursor.fetchone()
    return row[0] if row else None


def set_sync_state(conn: sqlite3.Connection, key: str, value: str) -> None:
    """Insert or update a value in the sync_state table.

    Args:
        conn: An open sqlite3 connection.
        key: The sync state key.
        value: The value to store.
    """
    conn.execute(
        "INSERT OR REPLACE INTO sync_state (key, value) VALUES (?, ?)",
        (key, value),
    )
    conn.commit()


def load_vec_extension(conn: sqlite3.Connection) -> None:
    """Load the sqlite-vec extension into the connection.

    Args:
        conn: An open sqlite3 connection.
    """
    import sqlite_vec

    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    log.info("sqlite-vec extension loaded")
