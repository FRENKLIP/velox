"""Sending WhatsApp messages through Twilio."""

import logging

from twilio.rest import Client

from .config import Settings

log = logging.getLogger(__name__)

WHATSAPP_MAX_CHARS = 1600  # Twilio's limit for one WhatsApp message body


def _whatsapp(number: str) -> str:
    return number if number.startswith("whatsapp:") else f"whatsapp:{number}"


class Messenger:
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
        for start in range(0, len(body), WHATSAPP_MAX_CHARS):
            self.client.messages.create(
                from_=_whatsapp(self.settings.twilio_whatsapp_from),
                to=_whatsapp(to),
                body=body[start : start + WHATSAPP_MAX_CHARS],
            )

    def notify_owner(self, body: str) -> None:
        if self.settings.owner_whatsapp:
            self.send(self.settings.owner_whatsapp, body)
        else:
            log.info("OWNER_WHATSAPP not set; owner message: %s", body)
