"""Test config. Loads backend/.env so tests hit the real Neon DB, matching production shape."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)
# Force test app_env so nothing production-flagged runs.
os.environ.setdefault("APP_ENV", "test")
