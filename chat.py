"""Chat with the assistant in the terminal, without WhatsApp or Twilio.

Usage: python chat.py
Owner alerts are printed instead of sent. Type "exit" to stop.
"""

import logging

from app.assistant import Assistant
from app.config import load_restaurant, load_settings
from app.db import Database


class ConsoleMessenger:
    def send(self, to: str, body: str) -> None:
        print(f"\n[to {to}] {body}\n")

    def notify_owner(self, body: str) -> None:
        print(f"\n[owner alert] {body}\n")


def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    settings = load_settings()
    restaurant = load_restaurant(settings.restaurant_file)
    db = Database(settings.database_path)
    assistant = Assistant(restaurant, db, ConsoleMessenger(), settings.anthropic_model)
    phone = "+355690000000"  # a pretend customer

    print(f"Chatting with {restaurant['name']} ({settings.anthropic_model}). Type 'exit' to stop.\n")
    while True:
        try:
            text = input("Ju: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if text.lower() in {"exit", "quit"}:
            break
        if text:
            print(f"\n{restaurant['name']}: {assistant.reply(phone, text)}\n")


if __name__ == "__main__":
    main()
