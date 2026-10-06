"""Web server: receives WhatsApp messages from Twilio and sends reminders."""

import asyncio
import logging
from contextlib import asynccontextmanager

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from twilio.request_validator import RequestValidator
from twilio.twiml.messaging_response import MessagingResponse

from .assistant import HANDOFF_SQ, Assistant
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

TEXT_ONLY = "Për momentin mund të lexoj vetëm mesazhe me tekst."
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


def answer_message(phone: str, text: str) -> str:
    quick = handle_reminder_answer(phone, text)
    if quick is not None:
        db.add_message(phone, "user", text)
        db.add_message(phone, "assistant", quick)
        return quick
    return assistant.reply(phone, text)


def process_message(phone: str, text: str) -> None:
    try:
        messenger.send(phone, answer_message(phone, text))
    except Exception:
        log.exception("Failed to handle message from %s", phone)


def twiml_reply(body: str) -> Response:
    twiml = MessagingResponse()
    twiml.message(body)
    return Response(content=str(twiml), media_type="application/xml")


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
    if not phone:
        return Response(content=EMPTY_TWIML, media_type="application/xml")
    if not text:
        return twiml_reply(TEXT_ONLY)

    if settings.reply_in_webhook:
        # Answer inside Twilio's request. Needed by senders that refuse free-form API messages,
        # such as Twilio's WhatsApp trial number; Twilio waits 15 seconds at most.
        try:
            body = await asyncio.wait_for(run_in_threadpool(answer_message, phone, text), timeout=13)
        except asyncio.TimeoutError:
            log.warning("Reply to %s took too long for the webhook", phone)
            return Response(content=EMPTY_TWIML, media_type="application/xml")
        except Exception:
            log.exception("Failed to handle message from %s", phone)
            body = HANDOFF_SQ
        return twiml_reply(body)

    # Reply in the background through the API: no time limit.
    background.add_task(process_message, phone, text)

    return Response(content=EMPTY_TWIML, media_type="application/xml")
