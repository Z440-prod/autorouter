"""Transactional task snapshots and their event journal."""
import json
import sqlite3
import time


class TaskStore:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript(
            """CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY,state TEXT NOT NULL); CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY,task TEXT,time REAL,kind TEXT,data TEXT);"""
        )

    def save(self, s, kind, data):
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO tasks VALUES(?,?)", (s["id"], json.dumps(s))
            )
            self.db.execute(
                "INSERT INTO events(task,time,kind,data) VALUES(?,?,?,?)",
                (s["id"], time.time(), kind, json.dumps(data)),
            )

    def get(self, id):
        r = self.db.execute("SELECT state FROM tasks WHERE id=?", (id,)).fetchone()
        if not r:
            raise ValueError("Unknown task")
        return json.loads(r[0])
