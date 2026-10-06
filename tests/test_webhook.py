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


@pytest.fixture
def meta_main(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setenv("WHATSAPP_PROVIDER", "meta")
    monkeypatch.setenv("META_VERIFY_TOKEN", "velox-verify")
    monkeypatch.setenv("META_APP_SECRET", "s3cret")
    monkeypatch.setenv("META_ACCESS_TOKEN", "")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "m.db"))
    import app.main

    return importlib.reload(app.main)


def test_meta_verification(meta_main):
    with TestClient(meta_main.app) as client:
        ok = client.get("/meta/webhook", params={
            "hub.mode": "subscribe", "hub.verify_token": "velox-verify", "hub.challenge": "12345"})
        bad = client.get("/meta/webhook", params={
            "hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "12345"})
    assert ok.status_code == 200 and ok.text == "12345"
    assert bad.status_code == 403


def test_meta_message_is_answered(meta_main, monkeypatch):
    import hashlib
    import hmac
    import json

    sent = []
    monkeypatch.setattr(meta_main.assistant, "reply", lambda phone, text: f"echo {text}")
    monkeypatch.setattr(meta_main.messenger, "send", lambda to, body: sent.append((to, body)))
    body = json.dumps({"entry": [{"changes": [{"value": {"messages": [
        {"from": "355685397289", "type": "text", "text": {"body": "A keni vend?"}}]}}]}]}).encode()
    signature = "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()

    with TestClient(meta_main.app) as client:
        unsigned = client.post("/meta/webhook", content=body, headers={"Content-Type": "application/json"})
        r = client.post("/meta/webhook", content=body,
                        headers={"Content-Type": "application/json", "X-Hub-Signature-256": signature})
    assert unsigned.status_code == 403
    assert r.status_code == 200
    assert sent == [("+355685397289", "echo A keni vend?")]


@pytest.fixture
def bridge_main(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setenv("WHATSAPP_PROVIDER", "bridge")
    monkeypatch.setenv("BRIDGE_TOKEN", "bridge-secret")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "b.db"))
    import app.main

    return importlib.reload(app.main)


def test_bridge_message_is_answered(bridge_main, monkeypatch):
    monkeypatch.setattr(bridge_main.assistant, "reply", lambda phone, text: f"echo {phone} {text}")
    payload = {"from": "+355685397289", "text": "A keni vend?"}
    with TestClient(bridge_main.app) as client:
        no_token = client.post("/bridge/message", json=payload)
        r = client.post("/bridge/message", json=payload, headers={"X-Bridge-Token": "bridge-secret"})
        empty = client.post("/bridge/message", json={"from": "+355685397289", "text": ""},
                            headers={"X-Bridge-Token": "bridge-secret"})
    assert no_token.status_code == 403
    assert r.json() == {"reply": "echo +355685397289 A keni vend?"}
    assert "mesazhe me tekst" in empty.json()["reply"]
