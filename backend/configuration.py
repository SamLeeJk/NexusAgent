import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")


@dataclass
class Config:
    database_url: str
    demo_mode: bool = False
    seed_demo: bool = False
    secure_cookie: bool = False
    api_key: str = ""
    model: str = "gpt-4o-mini"
    proposal_ttl: int = 900

    @classmethod
    def environment(cls):
        return cls(
            database_url=os.getenv(
                "DATABASE_URL",
                "postgresql+psycopg://nexus:nexus_local@127.0.0.1:55432/nexus",
            ),
            demo_mode=os.getenv("NEXUS_DEMO_MODE", "false").lower() == "true",
            seed_demo=os.getenv("NEXUS_SEED_DEMO", "false").lower() == "true",
            secure_cookie=os.getenv("NEXUS_SECURE_COOKIE", "false").lower() == "true",
            api_key=os.getenv("OPENAI_API_KEY", ""),
            model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        )
