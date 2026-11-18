"""Durable job metadata for the single local web process."""

from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from uuid import uuid4

from .planning import Request


class QueueFull(RuntimeError):
    pass


class Jobs:
    def __init__(self, directory: Path):
        self.path = directory / "jobs.sqlite3"

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        return db

    def initialise(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as db, db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, client_id TEXT NOT NULL, trigger TEXT NOT NULL, created TEXT NOT NULL,
                    status TEXT NOT NULL, stage TEXT NOT NULL, outcome TEXT, run_directory TEXT, error TEXT
                )
            """)
            db.execute("""
                UPDATE runs SET status='interrupted', stage='finished', error='The server stopped before this run completed.'
                WHERE status IN ('queued', 'running')
            """)

    def enqueue(self, request: Request) -> str:
        run_id = uuid4().hex
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            pending = db.execute("SELECT COUNT(*) FROM runs WHERE status IN ('queued', 'running')").fetchone()[0]
            if pending >= 8:
                raise QueueFull("The briefing queue is full. Try again after a run finishes.")
            db.execute("""
                INSERT INTO runs (id, client_id, trigger, created, status, stage) VALUES (?, ?, ?, ?, 'queued', 'queued')
            """, (run_id, request.client, request.trigger, datetime.now(timezone.utc).isoformat()))
        return run_id

    def get(self, run_id: str) -> dict | None:
        with closing(self.connect()) as db:
            row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        return dict(row) if row else None

    def start(self, run_id: str) -> bool:
        with closing(self.connect()) as db, db:
            result = db.execute("UPDATE runs SET status='running', stage='resolving' WHERE id=? AND status='queued'",
                                (run_id,))
            return result.rowcount == 1

    def progress(self, run_id: str, stage: str) -> None:
        with closing(self.connect()) as db, db:
            db.execute("UPDATE runs SET stage=? WHERE id=? AND status='running'", (stage, run_id))

    def finish(self, run_id: str, status: str, *, outcome=None, run_directory=None, error=None) -> None:
        with closing(self.connect()) as db, db:
            db.execute("""
                UPDATE runs SET status=?, stage='finished', outcome=?, run_directory=?, error=?
                WHERE id=? AND status IN ('queued', 'running')
            """, (status, outcome, run_directory, error, run_id))
