"""FastAPI backend for the AI Tutoring Platform.

Wraps ADK agents with get_fast_api_app() using SQLite for local dev.
For production, set SESSION_DB_URI to AlloyDB/PostgreSQL connection string.

Start:
    uvicorn main:app --reload --port 8000
"""

import os
from pathlib import Path

from google.adk.cli.fast_api import get_fast_api_app

# Root of the repo — contains the tutor_platform/ agent package
AGENTS_DIR = str(Path(__file__).parent)

# Session persistence
# Local dev:   SQLite (zero config, file-based)
# Production:  set SESSION_DB_URI=postgresql+asyncpg://user:pass@host/dbname
#              (AlloyDB: SESSION_DB_URI=postgresql+asyncpg://postgres:pass@34.124.206.1:5432/tutor_db)
SESSION_DB_URI = os.environ.get(
    "SESSION_DB_URI",
    "sqlite+aiosqlite:///./sessions.db",
)

app = get_fast_api_app(
    agents_dir=AGENTS_DIR,
    session_service_uri=SESSION_DB_URI,
    # Allow Streamlit frontend (local dev ports)
    allow_origins=[
        "http://localhost:8501",
        "http://127.0.0.1:8501",
        "http://localhost:3000",   # React dev server if used later
        "http://127.0.0.1:3000",
    ],
    web=False,            # Disable ADK's built-in web UI (we have Streamlit)
    auto_create_session=True,  # Create sessions on first run_sse call
)
