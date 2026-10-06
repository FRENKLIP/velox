from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app import booking
from app.config import load_restaurant
from app.db import Database

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def restaurant():
    return load_restaurant(str(ROOT / "restaurant.yaml"))


@pytest.fixture
def db(tmp_path):
    return Database(str(tmp_path / "test.db"))


@pytest.fixture
def now(restaurant):
    # A Tuesday afternoon.
    return datetime(2026, 10, 6, 15, 0, tzinfo=restaurant["tz"])


def slot(restaurant, day, at):
    return booking.parse_slot(restaurant, day, at)


def test_free_slot_is_available(restaurant, db, now):
    assert booking.check_slot(restaurant, db, slot(restaurant, "2026-10-06", "19:00"), 4, now).ok


def test_past_time_is_rejected(restaurant, db, now):
    check = booking.check_slot(restaurant, db, slot(restaurant, "2026-10-06", "13:00"), 2, now)
    assert not check.ok and "past" in check.reason


def test_off_grid_time_is_rejected(restaurant, db, now):
    assert not booking.check_slot(restaurant, db, slot(restaurant, "2026-10-06", "19:15"), 2, now).ok


def test_before_opening_and_too_close_to_closing(restaurant, db, now):
    assert not booking.check_slot(restaurant, db, slot(restaurant, "2026-10-07", "11:30"), 2, now).ok
    # Tuesday closes 23:00, last booking 21:30.
    assert booking.check_slot(restaurant, db, slot(restaurant, "2026-10-06", "21:30"), 2, now).ok
    assert not booking.check_slot(restaurant, db, slot(restaurant, "2026-10-06", "22:00"), 2, now).ok


def test_midnight_closing_allows_late_bookings(restaurant, db, now):
    # Friday closes at 00:00, so 22:30 is the last start.
    assert booking.check_slot(restaurant, db, slot(restaurant, "2026-10-09", "22:30"), 2, now).ok
    assert not booking.check_slot(restaurant, db, slot(restaurant, "2026-10-09", "23:00"), 2, now).ok


def test_large_group_goes_to_staff(restaurant, db, now):
    check = booking.check_slot(restaurant, db, slot(restaurant, "2026-10-06", "19:00"), 9, now)
    assert not check.ok and "staff" in check.reason


def test_full_slot_and_overlap(restaurant, db, now):
    at = slot(restaurant, "2026-10-06", "19:00")
    for i in range(restaurant["booking"]["max_tables"]):
        db.add_reservation(f"+3556900000{i}", f"Guest {i}", 2, at, "")
    assert not booking.check_slot(restaurant, db, at, 2, now).ok
    # Tables are held for 2 hours, so 20:30 overlaps and 21:00 is free again.
    assert not booking.check_slot(restaurant, db, at + timedelta(minutes=90), 2, now).ok
    assert booking.check_slot(restaurant, db, at + timedelta(minutes=120), 2, now).ok
    assert booking.suggest_alternatives(restaurant, db, at, 2, now)[0] == "17:00"


def test_cancelled_bookings_free_the_table(restaurant, db, now):
    at = slot(restaurant, "2026-10-06", "19:00")
    ids = [db.add_reservation(f"+3556900000{i}", "G", 2, at, "") for i in range(10)]
    db.set_status(ids[0], "cancelled")
    assert booking.check_slot(restaurant, db, at, 2, now).ok


def test_reminders_are_due_once(restaurant, db, now):
    rid = db.add_reservation("+355690000001", "Ana", 2, slot(restaurant, "2026-10-06", "17:30"), "")
    db.add_reservation("+355690000002", "Ben", 2, slot(restaurant, "2026-10-06", "20:00"), "")
    due = db.due_reminders(now, 3)
    assert [r["id"] for r in due] == [rid]
    db.mark_reminder_sent(rid)
    assert db.due_reminders(now, 3) == []
    assert db.latest_reminded_reservation("+355690000001", now)["id"] == rid
