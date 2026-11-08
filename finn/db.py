import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime, timezone


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


@contextmanager
def connect():
    path = Path(os.getenv('FINN_DB', 'data/finn.db'))
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL')
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init():
    with connect() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS events(
          id TEXT PRIMARY KEY, ticker TEXT, published TEXT, category TEXT,
          priority INTEGER, title TEXT, reason TEXT, payload TEXT, demo INTEGER DEFAULT 0);
        CREATE INDEX IF NOT EXISTS event_window ON events(published);
        CREATE TABLE IF NOT EXISTS alerts(
          seq INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT, rule TEXT,
          created TEXT, payload TEXT, UNIQUE(event_id, rule));
        CREATE TABLE IF NOT EXISTS documents(
          id TEXT PRIMARY KEY, ticker TEXT, kind TEXT, title TEXT, url TEXT,
          sha TEXT, fetched TEXT, published TEXT, discovery TEXT,
          UNIQUE(ticker, kind, sha));
        CREATE TABLE IF NOT EXISTS chunks(
          id TEXT PRIMARY KEY, document_id TEXT, page INTEGER, text TEXT);
        CREATE INDEX IF NOT EXISTS chunks_doc ON chunks(document_id);
        CREATE TABLE IF NOT EXISTS llm_cache(key TEXT PRIMARY KEY, payload TEXT);
        CREATE TABLE IF NOT EXISTS usage(
          id INTEGER PRIMARY KEY, created TEXT, lane TEXT, model TEXT,
          input_tokens INTEGER DEFAULT 0, output_tokens INTEGER DEFAULT 0, status TEXT);
        ''')


def get_state(key, default=None):
    with connect() as db:
        row = db.execute('SELECT value FROM state WHERE key=?', (key,)).fetchone()
    return json.loads(row[0]) if row else default


def set_state(key, value):
    with connect() as db:
        db.execute('INSERT OR REPLACE INTO state VALUES (?,?)', (key, json.dumps(value)))
