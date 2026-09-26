"""Small local SQLite journal. Requests are never deleted or silently rescored."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def encode(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS requests (seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS runs (seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT, data TEXT NOT NULL);
        """)
        self.db.commit()

    def create_request(self, row):
        cursor = self.db.execute("INSERT INTO requests (id,data) VALUES (?,?)", (row["id"], encode(row)))
        row = {**row, "seq": cursor.lastrowid}
        self.save_request(row)
        return row

    def save_request(self, row):
        self.db.execute("UPDATE requests SET data=? WHERE id=?", (encode(row), row["id"]))
        self.db.commit()

    def request(self, request_id):
        value = self.db.execute("SELECT data FROM requests WHERE id=?", (request_id,)).fetchone()
        return json.loads(value[0]) if value else None

    def requests(self):
        return [json.loads(r[0]) for r in self.db.execute("SELECT data FROM requests ORDER BY seq")]

    def save_run(self, run):
        self.db.execute("INSERT INTO runs (id,data) VALUES (?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data", (run["id"], encode(run)))
        self.db.commit()

    def runs(self):
        return [json.loads(r[0]) for r in self.db.execute("SELECT data FROM runs ORDER BY seq")]

    def get(self, key, default=None):
        value = self.db.execute("SELECT data FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(value[0]) if value else default

    def set(self, key, value):
        self.db.execute("INSERT INTO settings (key,data) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET data=excluded.data", (key, encode(value)))
        self.db.commit()

    def event(self, kind, **data):
        entry = {"at": utcnow(), "kind": kind, **data}
        cursor = self.db.execute("INSERT INTO events (data) VALUES (?)", (encode(entry),))
        self.db.commit()
        return {"seq": cursor.lastrowid, **entry}

    def events(self, limit=40):
        return [{"seq": seq, **json.loads(data)} for seq, data in self.db.execute("SELECT seq,data FROM events ORDER BY seq DESC LIMIT ?", (limit,))][::-1]

    def recover(self):
        for row in self.requests():
            if row["status"] in ("queued", "generating", "evaluating"):
                row.update(status="interrupted", error="The local server stopped before this request completed.", finished_at=utcnow())
                self.save_request(row)
        for run in self.runs():
            if run["status"] in ("running", "stopping"):
                run.update(status="interrupted", ended_at=utcnow())
                self.save_run(run)

    def close(self):
        self.db.close()
