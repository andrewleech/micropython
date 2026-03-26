"""Tests for the database layer."""

import sqlite3
from pathlib import Path

import pytest

from mpy_triage.db import get_connection, get_sync_state, init_db, set_sync_state

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "data" / "schema.sql"


@pytest.fixture()
def tmp_db(tmp_path: Path) -> sqlite3.Connection:
    """Create a temporary database with schema applied."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_db(conn, SCHEMA_PATH)
    return conn


def test_schema_creates_tables(tmp_db: sqlite3.Connection) -> None:
    """init_db should create the expected tables."""
    cursor = tmp_db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = {row[0] for row in cursor.fetchall()}
    assert "items" in tables
    assert "sync_state" in tables


def test_wal_mode(tmp_path: Path) -> None:
    """get_connection should enable WAL journal mode."""
    conn = get_connection(tmp_path / "wal.db")
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode == "wal"


def test_row_factory(tmp_path: Path) -> None:
    """get_connection should set row_factory to sqlite3.Row."""
    conn = get_connection(tmp_path / "row.db")
    assert conn.row_factory is sqlite3.Row


def test_sync_state_roundtrip(tmp_db: sqlite3.Connection) -> None:
    """set_sync_state then get_sync_state should return the stored value."""
    set_sync_state(tmp_db, "last_updated", "2024-01-15")
    assert get_sync_state(tmp_db, "last_updated") == "2024-01-15"


def test_sync_state_overwrite(tmp_db: sqlite3.Connection) -> None:
    """set_sync_state should overwrite an existing key."""
    set_sync_state(tmp_db, "cursor", "abc")
    set_sync_state(tmp_db, "cursor", "def")
    assert get_sync_state(tmp_db, "cursor") == "def"


def test_get_sync_state_missing(tmp_db: sqlite3.Connection) -> None:
    """get_sync_state should return None for a key that does not exist."""
    assert get_sync_state(tmp_db, "nonexistent") is None


def test_idempotent_init_db(tmp_path: Path) -> None:
    """Calling init_db twice should not raise an error."""
    db_path = tmp_path / "idem.db"
    conn = get_connection(db_path)
    init_db(conn, SCHEMA_PATH)
    init_db(conn, SCHEMA_PATH)  # second call should be fine
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = {row[0] for row in cursor.fetchall()}
    assert "sync_state" in tables
