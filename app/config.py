import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except ValueError:
        return default


BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x}

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL_MAIN = os.getenv("GEMINI_MODEL_MAIN", "gemini-2.5-flash").strip()
GEMINI_MODEL_VERIFY = os.getenv("GEMINI_MODEL_VERIFY", "gemini-2.0-flash").strip()
GEMINI_MODEL_FALLBACK = os.getenv("GEMINI_MODEL_FALLBACK", "gemini-2.0-flash").strip()

PUBLIC_URL = os.getenv("PUBLIC_URL", "").strip().rstrip("/")
HOST = os.getenv("HOST", "0.0.0.0")
PORT = _int("PORT", 8080)

DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql://neyro:neyro@localhost:5432/neyro"
).strip()
LOGO_DIR = ROOT / os.getenv("LOGO_DIR", "data/logos")
MINIAPP_DIR = ROOT / "app" / "miniapp"

DEFAULT_DAILY_LIMIT = _int("DEFAULT_DAILY_LIMIT", 500)
POLL_INTERVAL = _int("POLL_INTERVAL", 90)

DEV_AUTH = os.getenv("DEV_AUTH", "").strip() == "1"

LANGUAGES = {
    "ru": "Русский",
    "en": "English",
    "uk": "Українська",
    "es": "Español",
    "de": "Deutsch",
    "fr": "Français",
    "it": "Italiano",
    "pt": "Português",
    "tr": "Türkçe",
    "ar": "العربية",
    "zh": "中文",
    "hi": "हिन्दी",
}

TIMEZONES = [
    "Europe/Moscow",
    "Europe/Kaliningrad",
    "Europe/Kyiv",
    "Europe/Minsk",
    "Europe/London",
    "Europe/Berlin",
    "Asia/Almaty",
    "Asia/Tashkent",
    "Asia/Dubai",
    "Asia/Tbilisi",
    "Asia/Yekaterinburg",
    "Asia/Novosibirsk",
    "Asia/Vladivostok",
    "America/New_York",
    "America/Los_Angeles",
    "UTC",
]

DELAY_MODES = {
    "instant": (0, 0),
    "1-10": (60, 600),
    "10-30": (600, 1800),
    "30-90": (1800, 5400),
}

PACE_MODES = {"as_they_come": 0, "3": 3, "6": 6, "12": 12, "24": 24}

LOGO_DIR.mkdir(parents=True, exist_ok=True)

