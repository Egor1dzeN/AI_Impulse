"""Load backend/.env before any module reads the environment: backend.sh
sources it, but a server started any other way (a service, an IDE, a bare
`python -m uvicorn`) silently lost settings like ASSISTANT_NOW — every
"tomorrow"/"next week" then shifted to the real date. The file wins over
variables already in the environment: a stale machine-wide GIGA_CHAT_IDE
silently shadowed a fresh key put into .env."""
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=True)
except ImportError:  # python-dotenv is optional; backend.sh still sources .env
    pass
