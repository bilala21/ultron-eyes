"""Event storage: SQLite for metadata, JPEG files for snapshots, automatic retention."""

from __future__ import annotations

import json
import shutil
import sqlite3
import threading
import time
from functools import wraps
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from .vision import Analysis

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    camera INTEGER NOT NULL,
    camera_name TEXT NOT NULL,
    labels TEXT NOT NULL,          -- ",car,person," (wrapped in commas for LIKE matching)
    objects TEXT NOT NULL,         -- JSON list of {label, confidence, box}
    plates TEXT NOT NULL,          -- JSON list of {text, confidence, box, known_name}
    snapshot TEXT NOT NULL,
    thumb TEXT NOT NULL,
    latency_ms INTEGER,            -- motion trigger to event saved
    review INTEGER,                -- NULL = not reviewed, 1 = correct, 0 = wrong
    review_note TEXT
);
CREATE INDEX IF NOT EXISTS events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS events_camera ON events(camera);
"""


@dataclass
class Filter:
    start: float | None = None
    end: float | None = None
    cameras: list[int] | None = None
    labels: list[str] | None = None          # any of these labels
    known_names: list[str] | None = None     # plates matched to these owners
    unknown_plates: bool = False             # plates that matched nobody
    plate_text: str | None = None
    limit: int = 200


def locked(fn):
    """One SQLite connection is shared by the camera worker and the web server's
    thread pool; serialize access so concurrent requests can't corrupt its state."""
    @wraps(fn)
    def wrapper(self, *a, **kw):
        with self.lock:
            return fn(self, *a, **kw)
    return wrapper


class Store:
    def __init__(self, data_dir: Path):
        self.lock = threading.RLock()
        self.data_dir = Path(data_dir)
        (self.data_dir / "snapshots").mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.data_dir / "events.db", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)

    @locked
    def add(self, ts: float, camera: int, camera_name: str, jpeg: bytes, img: np.ndarray,
            analysis: Analysis, latency_ms: int | None = None) -> int:
        day = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
        folder = self.data_dir / "snapshots" / day
        folder.mkdir(parents=True, exist_ok=True)
        stem = f"{datetime.fromtimestamp(ts).strftime('%H%M%S')}-cam{camera}-{int(ts * 1000) % 1000:03d}"
        snap, thumb = folder / f"{stem}.jpg", folder / f"{stem}.thumb.jpg"
        snap.write_bytes(jpeg)
        h, w = img.shape[:2]
        cv2.imwrite(str(thumb), cv2.resize(img, (480, int(480 * h / w))), [cv2.IMWRITE_JPEG_QUALITY, 80])
        cur = self.db.execute(
            "INSERT INTO events (ts, camera, camera_name, labels, objects, plates, snapshot, thumb, latency_ms) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (ts, camera, camera_name, "," + ",".join(analysis.labels) + ",",
             json.dumps([asdict(o) for o in analysis.objects]), json.dumps([asdict(p) for p in analysis.plates]),
             str(snap.relative_to(self.data_dir)), str(thumb.relative_to(self.data_dir)), latency_ms),
        )
        self.db.commit()
        return cur.lastrowid

    @locked
    def search(self, f: Filter) -> list[dict]:
        where, args = [], []
        if f.start is not None:
            where.append("ts >= ?"); args.append(f.start)
        if f.end is not None:
            where.append("ts < ?"); args.append(f.end)
        if f.cameras:
            where.append(f"camera IN ({','.join('?' * len(f.cameras))})"); args += f.cameras
        if f.labels:
            where.append("(" + " OR ".join("labels LIKE ?" for _ in f.labels) + ")")
            args += [f"%,{label},%" for label in f.labels]
        rows = [self._row(r) for r in self.db.execute(
            f"SELECT * FROM events {'WHERE ' + ' AND '.join(where) if where else ''} ORDER BY ts DESC LIMIT ?",
            args + [f.limit * 5 if (f.known_names or f.unknown_plates or f.plate_text) else f.limit])]
        if f.known_names:
            names = {n.lower() for n in f.known_names}
            rows = [r for r in rows if any((p["known_name"] or "").lower() in names for p in r["plates"])]
        if f.unknown_plates:
            rows = [r for r in rows if any(not p["known_name"] for p in r["plates"])]
        if f.plate_text:
            rows = [r for r in rows if any(f.plate_text in p["text"] for p in r["plates"])]
        return rows[: f.limit]

    @locked
    def get(self, event_id: int) -> dict | None:
        r = self.db.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
        return self._row(r) if r else None

    @locked
    def review(self, event_id: int, correct: bool, note: str | None = None) -> None:
        self.db.execute("UPDATE events SET review = ?, review_note = ? WHERE id = ?", (int(correct), note, event_id))
        self.db.commit()

    @locked
    def stats(self) -> dict:
        q = self.db.execute
        reviewed = q("SELECT COUNT(*), SUM(review) FROM events WHERE review IS NOT NULL").fetchone()
        lat = [r[0] for r in q("SELECT latency_ms FROM events WHERE latency_ms IS NOT NULL ORDER BY latency_ms")]
        return {
            "events": q("SELECT COUNT(*) FROM events").fetchone()[0],
            "reviewed": reviewed[0],
            "correct": reviewed[1] or 0,
            "precision": round((reviewed[1] or 0) / reviewed[0], 3) if reviewed[0] else None,
            "latency_ms_median": lat[len(lat) // 2] if lat else None,
            "latency_ms_p95": lat[int(len(lat) * 0.95)] if lat else None,
        }

    @locked
    def purge(self, retention_days: int) -> int:
        cutoff = time.time() - retention_days * 86400
        old = self.db.execute("SELECT id, snapshot, thumb FROM events WHERE ts < ?", (cutoff,)).fetchall()
        for r in old:
            for p in (r["snapshot"], r["thumb"]):
                (self.data_dir / p).unlink(missing_ok=True)
        self.db.execute("DELETE FROM events WHERE ts < ?", (cutoff,))
        self.db.commit()
        for d in (self.data_dir / "snapshots").iterdir():
            if d.is_dir() and not any(d.iterdir()):
                shutil.rmtree(d)
        return len(old)

    @staticmethod
    def _row(r: sqlite3.Row) -> dict:
        d = dict(r)
        d["labels"] = [x for x in d["labels"].split(",") if x]
        d["objects"] = json.loads(d["objects"])
        d["plates"] = json.loads(d["plates"])
        return d
