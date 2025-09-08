from __future__ import annotations
import os

APP_TITLE = "AI-ассистент для Moodle"

AI_API_BASE = os.getenv("AI_API_BASE_URL", "")
AI_API_KEY = os.getenv("AI_API_KEY", "")

DB_PATH = os.getenv("APP_DB_PATH", "mvp_state.db")
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "60"))
UPLOAD_DIR = os.getenv("APP_UPLOAD_DIR", "uploads")

LLM_API_URL = os.getenv("LLM_API_URL", "http://localhost:8080")
PUBLIC_CALLBACK_BASE = os.getenv("PUBLIC_CALLBACK_BASE", "http://your-host:8008")
WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", "8008"))

PDF_DPI_DEFAULT = 150
PDF_DPI_MIN, PDF_DPI_MAX = 72, 220
