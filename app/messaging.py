"""Sending WhatsApp messages through Twilio, Meta's WhatsApp Cloud API, or the WhatsApp Web bridge."""

import logging
from typing import Protocol

import httpx

from twilio.base.exceptions import TwilioRestException
from twilio.rest import Client

from .config import Settings

log = logging.getLogger(__name__)

WHATSAPP_MAX_CHARS = 1600  # Twilio's limit for one WhatsApp message body
META_MAX_CHARS = 4096  # Cloud API limit for one text message


class Messenger(Protocol):
    def send(self, to: str, body: str) -> None: ...
    def notify_owner(self, body: str) -> None: ...


def _whatsapp(number: str) -> str:
    return number if number.startswith("whatsapp:") else f"whatsapp:{number}"


class TwilioMessenger:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = (
            Client(settings.twilio_account_sid, settings.twilio_auth_token)
            if settings.twilio_account_sid and settings.twilio_auth_token
            else None
        )

    def send(self, to: str, body: str) -> None:
        if not body.strip():
            return
        if self.client is None:
            log.warning("Twilio not configured; would send to %s: %s", to, body)
            return
        try:
            for start in range(0, len(body), WHATSAPP_MAX_CHARS):
                self.client.messages.create(
                    from_=_whatsapp(self.settings.twilio_whatsapp_from),
                    to=_whatsapp(to),
                    body=body[start : start + WHATSAPP_MAX_CHARS],
                )
        except TwilioRestException as e:
            # A failed alert must not break the conversation or lose a booking.
            log.error("Could not send WhatsApp message to %s (Twilio error %s): %s", to, e.code, e.msg)

    def notify_owner(self, body: str) -> None:
        if self.settings.owner_whatsapp:
            self.send(self.settings.owner_whatsapp, body)
        else:
            log.info("OWNER_WHATSAPP not set; owner message: %s", body)


class MetaMessenger:
    """Meta's WhatsApp Cloud API, including its free test number."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.url = (
            f"https://graph.facebook.com/{settings.meta_graph_version}/{settings.meta_phone_number_id}/messages"
        )

    def send(self, to: str, body: str) -> None:
        if not body.strip():
            return
        if not (self.settings.meta_access_token and self.settings.meta_phone_number_id):
            log.warning("Meta WhatsApp not configured; would send to %s: %s", to, body)
            return
        for start in range(0, len(body), META_MAX_CHARS):
            payload = {
                "messaging_product": "whatsapp",
                "to": to.lstrip("+"),
                "type": "text",
                "text": {"body": body[start : start + META_MAX_CHARS]},
            }
            try:
                r = httpx.post(
                    self.url,
                    json=payload,
                    headers={"Authorization": f"Bearer {self.settings.meta_access_token}"},
                    timeout=15,
                )
            except httpx.HTTPError as e:
                log.error("Could not reach Meta to message %s: %s", to, e)
                return
            if r.status_code >= 400:
                # A failed alert must not break the conversation or lose a booking.
                log.error("Meta refused message to %s (HTTP %s): %s", to, r.status_code, r.text)
                return

    def notify_owner(self, body: str) -> None:
        if self.settings.owner_whatsapp:
            self.send(self.settings.owner_whatsapp, body)
        else:
            log.info("OWNER_WHATSAPP not set; owner message: %s", body)


class BridgeMessenger:
    """The WhatsApp Web bridge in bridge/, which runs a normal WhatsApp account (demo only)."""

    def __init__(self, settings: Settings):
        self.settings = settings

    def send(self, to: str, body: str) -> None:
        if not body.strip():
            return
        try:
            r = httpx.post(
                f"{self.settings.bridge_url}/send",
                json={"to": to, "body": body},
                headers={"X-Bridge-Token": self.settings.bridge_token},
                timeout=30,
            )
        except httpx.HTTPError as e:
            log.error("Could not reach the WhatsApp bridge to message %s: %s", to, e)
            return
        if r.status_code >= 400:
            # A failed alert must not break the conversation or lose a booking.
            log.error("Bridge refused message to %s (HTTP %s): %s", to, r.status_code, r.text)

    def notify_owner(self, body: str) -> None:
        if self.settings.owner_whatsapp:
            self.send(self.settings.owner_whatsapp, body)
        else:
            log.info("OWNER_WHATSAPP not set; owner message: %s", body)


def make_messenger(settings: Settings) -> Messenger:
    if settings.whatsapp_provider == "meta":
        return MetaMessenger(settings)
    if settings.whatsapp_provider == "bridge":
        return BridgeMessenger(settings)
    return TwilioMessenger(settings)
