"""Settings, read once from the environment (and `.env` when present).

Every knob has a documented default so `make ingest && make chat` works with nothing but
an OpenRouter key, and `LLM_PROVIDER=mock EMBEDDING_PROVIDER=hash` works with nothing at all
(the CI mode the README describes).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(override=False)

_ROOT = Path(__file__).resolve().parents[2]


def _env(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    return default if value is None or value == "" else value


def _flag(name: str, default: bool) -> bool:
    return _env(name, "1" if default else "0").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    # --- corpus / index ---------------------------------------------------------------
    corpus_dir: Path = field(default_factory=lambda: Path(_env("CORPUS_DIR", str(_ROOT / "corpus"))))
    qdrant_url: str = field(default_factory=lambda: _env("QDRANT_URL"))
    qdrant_api_key: str = field(default_factory=lambda: _env("QDRANT_API_KEY"))
    qdrant_path: Path = field(default_factory=lambda: Path(_env("QDRANT_PATH", str(_ROOT / "data" / "qdrant"))))
    collection: str = field(default_factory=lambda: _env("QDRANT_COLLECTION", "orbitmesh_chunks"))
    # --- embeddings -------------------------------------------------------------------
    embedding_provider: str = field(default_factory=lambda: _env("EMBEDDING_PROVIDER", "fastembed"))
    embedding_model: str = field(default_factory=lambda: _env("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5"))
    model_cache_dir: Path = field(default_factory=lambda: Path(_env("MODEL_CACHE_DIR", str(_ROOT / "data" / "models"))))
    # --- LLM --------------------------------------------------------------------------
    llm_provider: str = field(default_factory=lambda: _env("LLM_PROVIDER", "openrouter"))
    llm_model: str = field(default_factory=lambda: _env("LLM_MODEL", "openai/gpt-4.1-mini"))
    judge_model: str = field(default_factory=lambda: _env("JUDGE_MODEL", "openai/gpt-4.1-mini"))
    openrouter_api_key: str = field(default_factory=lambda: _env("OPENROUTER_API_KEY"))
    openrouter_base_url: str = field(default_factory=lambda: _env("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"))
    llm_timeout_s: float = field(default_factory=lambda: float(_env("LLM_TIMEOUT_S", "60")))
    llm_cache: bool = field(default_factory=lambda: _flag("LLM_CACHE", True))
    llm_cache_dir: Path = field(default_factory=lambda: Path(_env("LLM_CACHE_DIR", str(_ROOT / "data" / "llm_cache"))))
    # --- retrieval --------------------------------------------------------------------
    retrieve_candidates: int = field(default_factory=lambda: int(_env("RETRIEVE_CANDIDATES", "24")))
    retrieve_top_k: int = field(default_factory=lambda: int(_env("RETRIEVE_TOP_K", "8")))
    # --- connectors ---------------------------------------------------------------------
    connectors_dir: Path = field(default_factory=lambda: Path(_env("CONNECTORS_DIR", str(_ROOT / "data" / "connectors"))))
    index_state_path: Path = field(default_factory=lambda: Path(_env("INDEX_STATE_PATH", str(_ROOT / "data" / "index_state.json"))))
    google_api_key: str = field(default_factory=lambda: _env("GOOGLE_API_KEY"))
    # --- sessions / logging -----------------------------------------------------------
    session_dir: Path = field(default_factory=lambda: Path(_env("SESSION_DIR", str(_ROOT / "data" / "sessions"))))
    log_level: str = field(default_factory=lambda: _env("LOG_LEVEL", "INFO"))
    log_content: bool = field(default_factory=lambda: _flag("LOG_CONTENT", False))

    @property
    def root(self) -> Path:
        return _ROOT


def load_settings() -> Settings:
    return Settings()
