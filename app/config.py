"""Settings from environment variables and the restaurant file."""

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    anthropic_model: str
    twilio_account_sid: str
    twilio_auth_token: str
    twilio_whatsapp_from: str
    owner_whatsapp: str
    database_path: str
    restaurant_file: str
    validate_twilio_signature: bool
    public_base_url: str


def load_settings() -> Settings:
    return Settings(
        anthropic_model=os.getenv("ANTHROPIC_MODEL", "claude-opus-5-5"),
        twilio_account_sid=os.getenv("TWILIO_ACCOUNT_SID", ""),
        twilio_auth_token=os.getenv("TWILIO_AUTH_TOKEN", ""),
        twilio_whatsapp_from=os.getenv("TWILIO_WHATSAPP_FROM", ""),
        owner_whatsapp=os.getenv("OWNER_WHATSAPP", ""),
        database_path=os.getenv("DATABASE_PATH", str(ROOT / "velox.db")),
        restaurant_file=os.getenv("RESTAURANT_FILE", str(ROOT / "restaurant.yaml")),
        validate_twilio_signature=os.getenv("VALIDATE_TWILIO_SIGNATURE", "true").lower() == "true",
        public_base_url=os.getenv("PUBLIC_BASE_URL", "").rstrip("/"),
    )


def load_restaurant(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    data["tz"] = ZoneInfo(data.get("timezone", "Europe/Tirane"))
    return data
