-- MicroPython Issue Triage Database Schema

-- Issues and PRs
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY,
    number INTEGER NOT NULL,
    repo TEXT NOT NULL DEFAULT 'micropython/micropython',
    type TEXT NOT NULL,      -- 'issue' or 'pr'
    title TEXT,
    body TEXT,
    author TEXT,
    state TEXT,              -- open, closed
    labels TEXT,             -- JSON array of label names
    created_at TEXT,
    closed_at TEXT,
    updated_at TEXT,
    UNIQUE(repo, number)
);

-- Sync state for incremental updates
CREATE TABLE IF NOT EXISTS sync_state (
    key TEXT PRIMARY KEY,
    value TEXT
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_items_repo ON items(repo);
CREATE INDEX IF NOT EXISTS idx_items_type ON items(type);
CREATE INDEX IF NOT EXISTS idx_items_state ON items(state);
CREATE INDEX IF NOT EXISTS idx_items_created ON items(created_at);
CREATE INDEX IF NOT EXISTS idx_items_repo_number ON items(repo, number);
