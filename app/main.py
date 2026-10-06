"""Web server: receives WhatsApp messages from Twilio and sends reminders."""

import logging
from contextlib import asynccontextmanager

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, Response
from twilio.request_validator import RequestValidator

from .assistant import Assistant
from .config import load_restaurant, load_settings
from .db import Database
from .messaging import Messenger

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("velox")

settings = load_settings()
restaurant = load_restaurant(settings.restaurant_file)
db = Database(settings.database_path)
messenger = Messenger(settings)
assistant = Assistant(restaurant, db, messenger, settings.anthropic_model)

EMPTY_TWIML = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'

# Only the exact digits from the reminder; a "po" could be an answer to the assistant instead.
CONFIRM_WORDS = {"1"}
CANCEL_WORDS = {"2"}


def send_reminders() -> None:
    now = assistant.now()
    for r in db.due_reminders(now, restaurant["booking"]["reminder_hours_before"]):
        messenger.send(
            r["phone"],
            f"Përshëndetje {r['name']}! Ju kujtojmë rezervimin tuaj sot në {restaurant['name']} "
            f"në orën {r['starts_at']:%H:%M} për {r['party_size']} persona.\n"
            "Shkruani 1 për ta konfirmuar ose 2 për ta anuluar.",
        )
        db.mark_reminder_sent(r["id"])
        log.info("Reminder sent for reservation %s", r["id"])


def handle_reminder_answer(phone: str, text: str) -> str | None:
    """Confirm or cancel the booking a reminder asked about. Returns a reply, or None if not a reminder answer."""
    word = text.strip().lower().rstrip(".!")
    if word not in CONFIRM_WORDS | CANCEL_WORDS:
        return None
    reservation = db.latest_reminded_reservation(phone, assistant.now())
    if reservation is None:
        return None
    when = f"{reservation['starts_at']:%H:%M}"
    if word in CONFIRM_WORDS:
        db.set_status(reservation["id"], "confirmed")
        messenger.notify_owner(f"Rezervimi #{reservation['id']} ({reservation['name']}, {when}) u konfirmua.")
        return f"Faleminderit! Rezervimi juaj në orën {when} u konfirmua. Ju presim!"
    db.set_status(reservation["id"], "cancelled")
    messenger.notify_owner(f"Rezervimi #{reservation['id']} ({reservation['name']}, {when}) u anulua nga klienti.")
    return "U anulua. Faleminderit që na njoftuat, ju presim herë tjetër!"


def process_message(phone: str, text: str) -> None:
    try:
        quick = handle_reminder_answer(phone, text)
        if quick is not None:
            db.add_message(phone, "user", text)
            db.add_message(phone, "assistant", quick)
            messenger.send(phone, quick)
            return
        messenger.send(phone, assistant.reply(phone, text))
    except Exception:
        log.exception("Failed to handle message from %s", phone)


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler = BackgroundScheduler(timezone=restaurant["tz"])
    scheduler.add_job(send_reminders, "interval", minutes=5, id="reminders", max_instances=1)
    scheduler.start()
    yield
    scheduler.shutdown()


app = FastAPI(title="Velox WhatsApp demo", lifespan=lifespan)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "restaurant": restaurant["name"]}


@app.post("/whatsapp")
async def whatsapp_webhook(request: Request, background: BackgroundTasks) -> Response:
    form = dict(await request.form())

    if settings.validate_twilio_signature:
        url = f"{settings.public_base_url}/whatsapp" if settings.public_base_url else str(request.url)
        signature = request.headers.get("X-Twilio-Signature", "")
        if not RequestValidator(settings.twilio_auth_token).validate(url, form, signature):
            raise HTTPException(status_code=403, detail="Invalid Twilio signature")

    phone = str(form.get("From", "")).removeprefix("whatsapp:")
    text = str(form.get("Body", "")).strip()
    if phone and text:
        # Reply in the background: Twilio stops waiting after 15 seconds, and Claude can take longer.
        background.add_task(process_message, phone, text)
    elif phone:
        background.add_task(messenger.send, phone, "Për momentin mund të lexoj vetëm mesazhe me tekst.")

    return Response(content=EMPTY_TWIML, media_type="application/xml")
