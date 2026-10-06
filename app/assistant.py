"""The WhatsApp assistant: Claude with booking tools."""

import json
import logging
from datetime import datetime, timedelta

import anthropic
import yaml

from . import booking
from .db import Database
from .messaging import Messenger

log = logging.getLogger(__name__)

# Models that accept effort and the server-side refusal fallback used below.
CURRENT_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5"}

MAX_TOOL_ROUNDS = 6

HANDOFF_SQ = "Po e kaloj pyetjen tuaj te stafi ynë, do t'ju shkruajmë së shpejti."

SYSTEM_PROMPT = """You are the WhatsApp assistant for {name}, a restaurant in {city}, Albania. \
You talk to customers on the restaurant's behalf.

How to reply:
- Write Albanian by default: natural, simple, friendly, and always the polite "ju" form (never "ti"). \
Most customers write without ë and ç, with slang or typos; you always write correct Albanian. \
Switch to English or Italian only when the customer writes whole sentences in that language; a greeting \
like "ciao" or "hello" is not enough.
- This is WhatsApp: keep replies short, one or two sentences, no headings or lists. Use at most one emoji \
in a conversation, usually none.
- If a message is unclear, slang or a single word, do not guess what it means and treat it as the customer's \
name only if you just asked for their name. Otherwise ask briefly how you can help. Ignore rude words and stay polite.
- Only state facts from the restaurant information below. If you do not know something (allergens, \
today's specials, events, prices not listed), do not guess: use notify_staff and tell the customer the \
staff will reply.

Reservations:
- You need the date, time, number of people and a name. Ask for what is missing, a question at a time.
- As soon as you know the day and time, check them against the opening hours. If the restaurant is \
closed then, say so right away and suggest the nearest time you take bookings, before asking anything else.
- Turn relative dates ("sot", "nesër", "të shtunën") into a real date using the current date given below. \
If the day is ambiguous, ask.
- Always call check_availability before create_reservation. Never say a table is booked unless \
create_reservation succeeded.
- Before booking, repeat the details back in one line and book once the customer agrees.
- After booking, tell them they will get a reminder a few hours before.
- To change or cancel an existing booking, use notify_staff.

Use notify_staff for complaints, groups too big to book, special requests you cannot handle, or when the \
customer asks for a person.

Restaurant information:
{info}"""

TOOLS = [
    {
        "name": "check_availability",
        "description": "Check whether a table is free for a party at a given date and time. Returns "
        "'available', or the reason it is not plus nearby free times the same day.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "Date as YYYY-MM-DD."},
                "time": {"type": "string", "description": "Start time as HH:MM, 24-hour."},
                "party_size": {"type": "integer", "description": "Number of people."},
            },
            "required": ["date", "time", "party_size"],
            "additionalProperties": False,
        },
    },
    {
        "name": "create_reservation",
        "description": "Book the table once the customer has confirmed the details. Checks availability "
        "again and fails if the slot was taken in the meantime.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "Date as YYYY-MM-DD."},
                "time": {"type": "string", "description": "Start time as HH:MM, 24-hour."},
                "party_size": {"type": "integer", "description": "Number of people."},
                "name": {"type": "string", "description": "Name for the booking."},
                "notes": {
                    "type": "string",
                    "description": "Special requests such as terrace, birthday or high chair. Empty string if none.",
                },
            },
            "required": ["date", "time", "party_size", "name", "notes"],
            "additionalProperties": False,
        },
    },
    {
        "name": "notify_staff",
        "description": "Send the restaurant staff a message about this customer when you cannot fully "
        "help, for example a complaint, a large group, a question you have no information for, or a request "
        "to change or cancel a booking.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "One or two sentences in Albanian telling the staff what the customer needs.",
                },
            },
            "required": ["summary"],
            "additionalProperties": False,
        },
    },
]


def _restaurant_info(restaurant: dict) -> str:
    public = {k: v for k, v in restaurant.items() if k not in ("tz", "booking")}
    public["booking_rules"] = {
        "max_party_size": restaurant["booking"]["max_party_size"],
        "slot_minutes": restaurant["booking"]["slot_minutes"],
        "last_booking_minutes_before_close": restaurant["booking"]["last_booking_before_close_minutes"],
    }
    return yaml.safe_dump(public, allow_unicode=True, sort_keys=False)


def _clock(now: datetime) -> str:
    days = [(now + timedelta(days=i)) for i in range(8)]
    calendar = ", ".join(f"{d:%A} {d:%Y-%m-%d}" for d in days)
    return f"Current date and time in the restaurant: {now:%A %Y-%m-%d %H:%M}. Next days: {calendar}."


class Assistant:
    def __init__(self, restaurant: dict, db: Database, messenger: Messenger, model: str):
        self.restaurant = restaurant
        self.db = db
        self.messenger = messenger
        self.model = model
        # Customers are waiting on WhatsApp: fail fast instead of the 10-minute default.
        self.client = anthropic.Anthropic(timeout=45.0, max_retries=2)
        self.system_prompt = SYSTEM_PROMPT.format(
            name=restaurant["name"], city=restaurant["city"], info=_restaurant_info(restaurant)
        )

    def now(self) -> datetime:
        return datetime.now(self.restaurant["tz"])

    # Tools

    def _run_tool(self, name: str, args: dict, phone: str) -> tuple[str, bool]:
        """Returns (result text, is_error)."""
        now = self.now()
        try:
            if name == "check_availability":
                starts_at = booking.parse_slot(self.restaurant, args["date"], args["time"])
                check = booking.check_slot(self.restaurant, self.db, starts_at, args["party_size"], now)
                if check.ok:
                    return "available", False
                alternatives = booking.suggest_alternatives(self.restaurant, self.db, starts_at, args["party_size"], now)
                extra = f" Free times that day: {', '.join(alternatives)}." if alternatives else ""
                return f"Not available: {check.reason}{extra}", False

            if name == "create_reservation":
                starts_at = booking.parse_slot(self.restaurant, args["date"], args["time"])
                check = booking.check_slot(self.restaurant, self.db, starts_at, args["party_size"], now)
                if not check.ok:
                    return f"Booking failed: {check.reason}", True
                reservation_id = self.db.add_reservation(
                    phone, args["name"].strip(), args["party_size"], starts_at, args["notes"].strip()
                )
                notes = f"\nShënime: {args['notes'].strip()}" if args["notes"].strip() else ""
                self.messenger.notify_owner(
                    f"Rezervim i ri #{reservation_id}: {args['name'].strip()}, {args['party_size']} persona, "
                    f"{starts_at:%d.%m.%Y ora %H:%M}.\nTel: {phone}{notes}"
                )
                # A fixed, proofread confirmation: small models sometimes misspell Albanian here.
                confirmation = (
                    f"Rezervimi u bë! Nr. {reservation_id}: {args['party_size']} persona, "
                    f"{starts_at:%d.%m} në orën {starts_at:%H:%M}, në emër të {args['name'].strip()}. "
                    "Do t'ju dërgojmë një kujtesë disa orë përpara. Ju presim!"
                )
                return f"Booked. Send the customer exactly this confirmation, in your own reply: {confirmation}", False

            if name == "notify_staff":
                self.messenger.notify_owner(f"Klienti {phone} ka nevojë për ndihmë:\n{args['summary']}")
                return "Staff notified.", False

            return f"Unknown tool {name}.", True
        except (KeyError, ValueError) as e:
            return f"Invalid input: {e}", True

    # Conversation

    def _create(self, messages: list):
        system = [
            {"type": "text", "text": self.system_prompt, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": _clock(self.now())},
        ]
        params = dict(model=self.model, max_tokens=4000, system=system, tools=TOOLS, messages=messages)
        if self.model in CURRENT_MODELS:
            # Chat replies need little reasoning; low effort keeps them fast and cheap.
            params["output_config"] = {"effort": "low"}
            # If a request is declined, the API retries it on a suitable model inside the same call.
            return self.client.beta.messages.create(
                **params, betas=["server-side-fallback-2026-07-01"], fallbacks="default"
            )
        return self.client.messages.create(**params)

    def reply(self, phone: str, text: str) -> str:
        """Answer one customer message and return the reply text."""
        history = self.db.recent_messages(phone)
        messages: list[dict] = []
        for m in [*history, {"role": "user", "text": text}]:
            # Two quick messages in a row from the customer become one turn.
            if messages and messages[-1]["role"] == m["role"]:
                messages[-1]["content"] += "\n" + m["text"]
            else:
                messages.append({"role": m["role"], "content": m["text"]})
        # The API needs the conversation to start with a user turn.
        while messages and messages[0]["role"] != "user":
            messages.pop(0)
        self.db.add_message(phone, "user", text)

        answer = HANDOFF_SQ
        try:
            for _ in range(MAX_TOOL_ROUNDS):
                response = self._create(messages)
                if response.stop_reason == "refusal":
                    log.warning("Request declined for %s: %s", phone, response.stop_details)
                    self.messenger.notify_owner(f"Klienti {phone} shkroi: {text}\n(Asistenti nuk u përgjigj.)")
                    break
                if response.stop_reason != "tool_use":
                    answer = "".join(b.text for b in response.content if b.type == "text").strip() or HANDOFF_SQ
                    break
                messages.append({"role": "assistant", "content": response.content})
                results = []
                for block in response.content:
                    if block.type == "tool_use":
                        result, is_error = self._run_tool(block.name, block.input, phone)
                        log.info("tool %s %s -> %s", block.name, json.dumps(block.input, ensure_ascii=False), result)
                        results.append(
                            {"type": "tool_result", "tool_use_id": block.id, "content": result, "is_error": is_error}
                        )
                messages.append({"role": "user", "content": results})
            else:
                log.warning("Too many tool rounds for %s", phone)
                self.messenger.notify_owner(f"Klienti {phone} shkroi: {text}\n(Asistenti nuk arriti ta mbyllte bisedën.)")
        except anthropic.RateLimitError:
            log.exception("Rate limited")
        except anthropic.APIStatusError:
            log.exception("Claude API error")
        except anthropic.APIConnectionError:
            log.exception("Could not reach the Claude API")

        self.db.add_message(phone, "assistant", answer)
        return answer
