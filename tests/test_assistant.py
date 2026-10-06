"""Conversation flow with a fake Claude client, so no API key or network is needed."""

import copy
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.assistant import Assistant
from app.config import load_restaurant
from app.db import Database

ROOT = Path(__file__).resolve().parent.parent


class FakeMessenger:
    def __init__(self):
        self.owner = []
        self.sent = []

    def send(self, to, body):
        self.sent.append((to, body))

    def notify_owner(self, body):
        self.owner.append(body)


def tool_use(id, name, input):
    return SimpleNamespace(type="tool_use", id=id, name=name, input=input)


def text(t):
    return SimpleNamespace(type="text", text=t)


class FakeMessages:
    """Returns scripted responses and records each request."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def create(self, **params):
        # Copy the messages: the assistant keeps appending to the same list.
        self.requests.append({**params, "messages": copy.deepcopy(params["messages"])})
        stop_reason, content = self.responses.pop(0)
        return SimpleNamespace(stop_reason=stop_reason, content=content, stop_details=None)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    restaurant = load_restaurant(str(ROOT / "restaurant.yaml"))
    db = Database(str(tmp_path / "t.db"))
    messenger = FakeMessenger()
    assistant = Assistant(restaurant, db, messenger, "claude-opus-5-5")
    return assistant, db, messenger


def install(assistant, responses):
    fake = FakeMessages(responses)
    assistant.client = SimpleNamespace(beta=SimpleNamespace(messages=fake), messages=fake)
    return fake


def test_books_a_table_and_tells_the_owner(setup):
    assistant, db, messenger = setup
    day = (assistant.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    args = {"date": day, "time": "19:00", "party_size": 4}
    fake = install(
        assistant,
        [
            ("tool_use", [tool_use("t1", "check_availability", args)]),
            ("tool_use", [tool_use("t2", "create_reservation", {**args, "name": "Arben", "notes": ""})]),
            ("end_turn", [text("U rezervua! Ju presim nesër në 19:00.")]),
        ],
    )

    answer = assistant.reply("+355691111111", "Dua nje tavoline per 4 neser ne 7 te mbremjes, Arben")

    assert answer == "U rezervua! Ju presim nesër në 19:00."
    first = fake.requests[0]
    assert first["model"] == "claude-opus-5-5"
    assert first["fallbacks"] == "default" and first["output_config"] == {"effort": "low"}
    # The tool results went back in one user turn each round.
    assert fake.requests[1]["messages"][-1]["content"][0]["content"] == "available"
    assert len(messenger.owner) == 1 and "Arben" in messenger.owner[0]
    start = datetime.fromisoformat(f"{day}T19:00").replace(tzinfo=assistant.restaurant["tz"])
    assert len(db.active_reservations_between(start, start + timedelta(minutes=1))) == 1
    assert [m["role"] for m in db.recent_messages("+355691111111")] == ["user", "assistant"]


def test_failed_booking_is_reported_as_error(setup):
    assistant, db, messenger = setup
    fake = install(
        assistant,
        [
            ("tool_use", [tool_use("t1", "create_reservation", {
                "date": "2020-01-01", "time": "19:00", "party_size": 2, "name": "X", "notes": ""})]),
            ("end_turn", [text("Na vjen keq, ajo orë ka kaluar.")]),
        ],
    )
    assistant.reply("+355692222222", "rezervo")
    result = fake.requests[1]["messages"][-1]["content"][0]
    assert result["is_error"] is True and "past" in result["content"]
    assert messenger.owner == []


def test_refusal_hands_over_to_staff(setup):
    assistant, db, messenger = setup
    install(assistant, [("refusal", [])])
    answer = assistant.reply("+355693333333", "...")
    assert "stafi" in answer
    assert len(messenger.owner) == 1


def test_history_is_merged_and_starts_with_user(setup):
    assistant, db, messenger = setup
    db.add_message("+355694444444", "assistant", "stale")
    db.add_message("+355694444444", "user", "Pershendetje")
    fake = install(assistant, [("end_turn", [text("Përshëndetje!")])])
    assistant.reply("+355694444444", "a jeni hapur sot?")
    messages = fake.requests[0]["messages"]
    assert messages == [{"role": "user", "content": "Pershendetje\na jeni hapur sot?"}]
