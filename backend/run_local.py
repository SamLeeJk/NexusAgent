"""Explicit SQLite/rule demo launcher. Does not silently replace PostgreSQL in production."""

import os
from pathlib import Path

import uvicorn

base = Path(__file__).resolve().parent
os.environ.setdefault("DATABASE_URL", "sqlite:///" + str(base / "runtime" / "demo.db"))
os.environ.setdefault("NEXUS_DEMO_MODE", "true")
os.environ.setdefault("NEXUS_SEED_DEMO", "true")

if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=int(os.getenv("PORT", "8000")))
