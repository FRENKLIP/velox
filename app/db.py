"""SQLite storage for conversations and reservations."""

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    phone TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    text TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_phone ON messages (phone, created_at);

CREATE TABLE IF NOT EXISTS reservations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    phone TEXT NOT NULL,
    name TEXT NOT NULL,
    party_size INTEGER NOT NULL,
    starts_at TEXT NOT NULL,          -- ISO 8601 with UTC offset, restaurant local time
    notes TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'booked' CHECK (status IN ('booked', 'confirmed', 'cancelled')),
    reminder_sent INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reservations_start ON reservations (starts_at);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path: str):
        self.path = path
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # Conversations

    def add_message(self, phone: str, role: str, text: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO messages (phone, role, text, created_at) VALUES (?, ?, ?, ?)",
                (phone, role, text, _now()),
            )

    def recent_messages(self, phone: str, hours: int = 24, limit: int = 20) -> list[dict]:
        since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT role, text FROM messages WHERE phone = ? AND created_at >= ? "
                "ORDER BY id DESC LIMIT ?",
                (phone, since, limit),
            ).fetchall()
        return [dict(r) for r in reversed(rows)]

    # Reservations

    def add_reservation(self, phone: str, name: str, party_size: int, starts_at: datetime, notes: str) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                "INSERT INTO reservations (phone, name, party_size, starts_at, notes, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (phone, name, party_size, starts_at.isoformat(), notes, _now()),
            )
            return cur.lastrowid

    def active_reservations_between(self, start: datetime, end: datetime) -> list[dict]:
        """Booked or confirmed reservations starting in [start, end)."""
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM reservations WHERE status != 'cancelled'",
            ).fetchall()
        result = []
        for r in rows:
            starts_at = datetime.fromisoformat(r["starts_at"])
            if start <= starts_at < end:
                result.append({**dict(r), "starts_at": starts_at})
        return result

    def due_reminders(self, now: datetime, hours_before: int) -> list[dict]:
        upcoming = self.active_reservations_between(now, now + timedelta(hours=hours_before))
        return [r for r in upcoming if r["status"] == "booked" and not r["reminder_sent"]]

    def mark_reminder_sent(self, reservation_id: int) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE reservations SET reminder_sent = 1 WHERE id = ?", (reservation_id,))

    def latest_reminded_reservation(self, phone: str, now: datetime) -> dict | None:
        """The next upcoming reservation for this phone that got a reminder and is still open."""
        upcoming = self.active_reservations_between(now, now + timedelta(days=2))
        mine = [r for r in upcoming if r["phone"] == phone and r["reminder_sent"] and r["status"] == "booked"]
        return min(mine, key=lambda r: r["starts_at"]) if mine else None

    def set_status(self, reservation_id: int, status: str) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE reservations SET status = ? WHERE id = ?", (status, reservation_id))
