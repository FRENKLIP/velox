# Velox WhatsApp demo

A WhatsApp assistant for a made-up Tirana restaurant, "Taverna Demo". Restaurant owners message it to see what Velox can do for them.

It:

- answers questions about the menu, prices, hours, address and parking, in Albanian or English
- takes table reservations, checking opening hours and free tables
- sends the owner a WhatsApp message for every new booking
- reminds customers 3 hours before their booking (reply 1 to confirm, 2 to cancel)
- passes anything it can't handle to the staff

## How it works

1. A customer writes to the WhatsApp number.
2. Twilio sends the message to `POST /whatsapp` on this server.
3. The server replies to Twilio right away, then asks Claude for an answer in the background. Claude can call three tools: `check_availability`, `create_reservation` and `notify_staff`.
4. The answer goes back to the customer through the Twilio API.
5. Every 5 minutes a background job sends the due reminders.

| File | What it does |
| --- | --- |
| `restaurant.yaml` | Menu, hours, address and booking rules. Change this per client. |
| `app/main.py` | Web server, Twilio webhook, reminders |
| `app/assistant.py` | The Claude conversation and tools |
| `app/booking.py` | Opening hours, time slots and table availability |
| `app/db.py` | SQLite storage for messages and reservations |
| `app/messaging.py` | Sending WhatsApp messages through Twilio |

## Model and cost

The demo uses Claude Haiku (`claude-haiku-4-5`), the cheapest Claude model: roughly a cent per booking conversation at published prices. To try a stronger model for harder conversations, set `ANTHROPIC_MODEL=claude-sonnet-5-5` or `claude-opus-5-5` in `.env`.

## Run it locally

You need Python 3.11+, a [Claude API key](https://console.anthropic.com) and a free [Twilio](https://www.twilio.com) account.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env   # then fill in the values
pytest                 # runs the tests, no keys needed
python chat.py         # chat with the bot in the terminal, only needs ANTHROPIC_API_KEY
uvicorn app.main:app --reload --port 8000
```

### Connect WhatsApp (Twilio sandbox)

1. In the Twilio console open **Messaging > Try it out > Send a WhatsApp message** and follow the steps to join the sandbox from your phone (you send a code like `join something-word` to the sandbox number). The owner's phone must join too.
2. Make your local server reachable from the internet, for example with [ngrok](https://ngrok.com): `ngrok http 8000`.
3. In the sandbox settings, set **When a message comes in** to `https://<your-ngrok-url>/whatsapp` (method POST).
4. Put the same base URL in `PUBLIC_BASE_URL` in `.env`, so the server can check that requests really come from Twilio.
5. Send the sandbox number a message such as "A keni tavolinë për 4 nesër në 20:00?".

Twilio's newer WhatsApp trial number (the "join twilio-trial" one) refuses free-form messages sent through the API (error 21654, "ContentSid Required"). With `REPLY_IN_WEBHOOK=true` (the default) the bot answers inside Twilio's request instead (TwiML), which should get around this; still to be confirmed on a live trial account. Owner alerts and reminders still go through the API, so on the trial number they only show up as errors in the log; they work once we use our own WhatsApp sender.

Note: WhatsApp only lets a business message a customer freely within 24 hours of the customer's last message. Reminders sent later than that need an approved message template once we move to our own number. For the demo, test bookings made the same day.

## Deploy

Render or Railway both work: create a web service from this repository, set the start command to `uvicorn app.main:app --host 0.0.0.0 --port $PORT`, add the variables from `.env.example`, and point the Twilio webhook at `https://<service-url>/whatsapp`. SQLite lives on the service's disk, so use a persistent disk or move to Postgres before real clients.

## Change the restaurant

Edit `restaurant.yaml`: name, address, hours, menu, extra info and booking rules. The assistant only states facts from this file and sends everything else to the staff.
