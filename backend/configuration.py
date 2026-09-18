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
    retrieval_mode: str = "lexical"
    qdrant_url: str = "http://127.0.0.1:6333"
    qdrant_collection: str = "nexus_knowledge_v1"
    embedding_model: str = (
        "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    )
    embedding_revision: str = "faf4aa4225822f3bc6376869cb1164e8e3feedd0"
    embedding_cache_dir: str = ""
    vector_score_threshold: float = 0.35

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
            retrieval_mode=os.getenv("NEXUS_RETRIEVAL_MODE", "lexical").lower(),
            qdrant_url=os.getenv("NEXUS_QDRANT_URL", "http://127.0.0.1:6333"),
            qdrant_collection=os.getenv(
                "NEXUS_QDRANT_COLLECTION", "nexus_knowledge_v1"
            ),
            embedding_model="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
            embedding_revision="faf4aa4225822f3bc6376869cb1164e8e3feedd0",
            embedding_cache_dir=os.getenv("NEXUS_EMBEDDING_CACHE_DIR", ""),
            vector_score_threshold=float(
                os.getenv("NEXUS_VECTOR_SCORE_THRESHOLD", "0.35")
            ),
        )
