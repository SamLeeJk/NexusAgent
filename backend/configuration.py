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
    observability_enabled: bool = False
    otel_endpoint: str = ""
    observability_fault_node: str = ""

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
            observability_enabled=os.getenv("NEXUS_OBSERVABILITY_ENABLED", "false").lower() == "true",
            otel_endpoint=os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", ""),
            observability_fault_node=os.getenv("NEXUS_OBS_FAULT_NODE", ""),
        )
