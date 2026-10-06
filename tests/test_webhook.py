"""The Twilio webhook, with the assistant stubbed out."""

import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def main(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setenv("VALIDATE_TWILIO_SIGNATURE", "false")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "w.db"))
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "")
    import app.main

    return importlib.reload(app.main)


def test_replies_inside_the_webhook(main, monkeypatch):
    monkeypatch.setattr(main.assistant, "reply", lambda phone, text: "Po, kemi vend & ju presim!")
    with TestClient(main.app) as client:
        r = client.post("/whatsapp", data={"From": "whatsapp:+355685397289", "Body": "A keni vend?"})
    assert r.status_code == 200
    assert "<Message>Po, kemi vend &amp; ju presim!</Message>" in r.text


def test_media_only_message_gets_text_only_notice(main):
    with TestClient(main.app) as client:
        r = client.post("/whatsapp", data={"From": "whatsapp:+355685397289", "Body": ""})
    assert "mesazhe me tekst" in r.text


def test_errors_fall_back_to_staff_handoff(main, monkeypatch):
    def boom(phone, text):
        raise RuntimeError("down")

    monkeypatch.setattr(main.assistant, "reply", boom)
    with TestClient(main.app) as client:
        r = client.post("/whatsapp", data={"From": "whatsapp:+355685397289", "Body": "hej"})
    assert "stafi" in r.text
