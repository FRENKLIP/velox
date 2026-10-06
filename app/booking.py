"""Reservation rules: opening hours, slots and table availability."""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from .db import Database

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


@dataclass
class Check:
    ok: bool
    reason: str  # short English reason, shown to the model as a tool result


def parse_slot(restaurant: dict, day: str, at: str) -> datetime:
    """Turn 'YYYY-MM-DD' and 'HH:MM' into an aware datetime in the restaurant's time zone."""
    d = date.fromisoformat(day)
    t = time.fromisoformat(at)
    return datetime.combine(d, t, tzinfo=restaurant["tz"])


def opening_window(restaurant: dict, day: date) -> tuple[datetime, datetime] | None:
    hours = restaurant.get("opening_hours", {}).get(DAYS[day.weekday()])
    if not hours:
        return None
    tz = restaurant["tz"]
    opens = datetime.combine(day, time.fromisoformat(hours[0]), tzinfo=tz)
    closes = datetime.combine(day, time.fromisoformat(hours[1]), tzinfo=tz)
    if closes <= opens:  # closes at or after midnight
        closes += timedelta(days=1)
    return opens, closes


def check_slot(restaurant: dict, db: Database, starts_at: datetime, party_size: int, now: datetime) -> Check:
    rules = restaurant["booking"]

    if party_size < 1:
        return Check(False, "Party size must be at least 1.")
    if party_size > rules["max_party_size"]:
        return Check(False, f"Groups larger than {rules['max_party_size']} must be arranged with the staff.")
    if starts_at <= now:
        return Check(False, "That time is in the past.")
    if starts_at.minute % rules["slot_minutes"] != 0 or starts_at.second != 0:
        return Check(False, f"Bookings start every {rules['slot_minutes']} minutes, for example 19:00 or 19:30.")

    window = opening_window(restaurant, starts_at.date())
    if window is None:
        return Check(False, "The restaurant is closed that day.")
    opens, closes = window
    last_start = closes - timedelta(minutes=rules["last_booking_before_close_minutes"])
    if not opens <= starts_at <= last_start:
        return Check(
            False,
            f"Outside booking hours. That day we take bookings from {opens:%H:%M} to {last_start:%H:%M}.",
        )

    hold = timedelta(minutes=rules["table_minutes"])
    overlapping = db.active_reservations_between(starts_at - hold + timedelta(seconds=1), starts_at + hold)
    if len(overlapping) >= rules["max_tables"]:
        return Check(False, "Fully booked at that time.")

    return Check(True, "Available.")


def suggest_alternatives(restaurant: dict, db: Database, starts_at: datetime, party_size: int, now: datetime, count: int = 3) -> list[str]:
    """Nearest free slots on the same day, closest first."""
    step = timedelta(minutes=restaurant["booking"]["slot_minutes"])
    found: list[datetime] = []
    for i in range(1, 13):
        for candidate in (starts_at - i * step, starts_at + i * step):
            if candidate.date() == starts_at.date() and check_slot(restaurant, db, candidate, party_size, now).ok:
                found.append(candidate)
        if len(found) >= count:
            break
    return [f"{c:%H:%M}" for c in found[:count]]
