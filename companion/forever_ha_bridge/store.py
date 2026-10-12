"""Transactional checkpoint and bounded outbox; acknowledgement deletes rows."""
import json
import sqlite3
import time
import uuid
from pathlib import Path


class Store:
    def __init__(self, path, max_rows=20000, max_bytes=100 * 1024 * 1024):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS outbox (seq INTEGER PRIMARY KEY AUTOINCREMENT, body TEXT NOT NULL);
        """)
        self.max_rows, self.max_bytes = max_rows, max_bytes
        self.session = self.get("bridge_session") or str(uuid.uuid4())
        with self.db:
            self.set("bridge_session", self.session)

    def get(self, key, default=None):
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key, value):
        self.db.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, json.dumps(value)))

    def ingest(self, key, checkpoint, envelopes):
        with self.db:
            for envelope in envelopes:
                envelope = dict(envelope, version=1, session=self.session, received_at=time.time())
                cursor = self.db.execute("INSERT INTO outbox(body) VALUES ('')")
                envelope["sequence"] = cursor.lastrowid
                body = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
                if len(body.encode()) > 4 * 1024 * 1024:
                    self.db.execute("DELETE FROM outbox WHERE seq=?", (cursor.lastrowid,))
                    self.set("dropped_records", self.get("dropped_records", 0) + 1)
                    continue
                self.db.execute("UPDATE outbox SET body=? WHERE seq=?", (body, cursor.lastrowid))
            self.set(key, checkpoint)
            rows, size = self.db.execute("SELECT COUNT(*),COALESCE(SUM(length(CAST(body AS BLOB))),0) FROM outbox").fetchone()
            dropped = 0
            while rows > self.max_rows or size > self.max_bytes:
                seq, length = self.db.execute("SELECT seq,length(CAST(body AS BLOB)) FROM outbox ORDER BY seq LIMIT 1").fetchone()
                self.db.execute("DELETE FROM outbox WHERE seq=?", (seq,))
                rows, size, dropped = rows - 1, size - length, dropped + 1
            if dropped:
                self.set("dropped_records", self.get("dropped_records", 0) + dropped)

    def batch(self, limit=50, max_bytes=3 * 1024 * 1024):
        out, size = [], 0
        for _, body in self.db.execute("SELECT seq,body FROM outbox ORDER BY seq LIMIT ?", (limit,)):
            length = len(body.encode())
            if out and size + length > max_bytes:
                break
            out.append(json.loads(body))
            size += length
        return out

    def ack(self, batch):
        with self.db:
            self.db.executemany("DELETE FROM outbox WHERE seq=?", [(e["sequence"],) for e in batch])

    def diagnostics(self):
        rows, size = self.db.execute("SELECT COUNT(*),COALESCE(SUM(length(CAST(body AS BLOB))),0) FROM outbox").fetchone()
        return {"queued_records": rows, "queued_bytes": size, "dropped_records": self.get("dropped_records", 0)}

    def close(self):
        self.db.close()
