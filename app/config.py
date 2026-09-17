"""Runtime settings from environment / .env."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    api_key: str
    base_url: str
    model: str
    db_path: str
    data_dir: str
    dialogues_dir: str
    model_cache_dir: str
    top_k: int
    history_turns: int
    temperature: float
    web_search_enabled: bool = True
    debug: bool = False
    deep_think_max_steps: int = 6
    deep_think_timeout_seconds: float = 180.0
    deep_think_llm_timeout_seconds: float = 60.0
    deep_think_max_tool_chars: int = 1200
    deep_think_max_fetches: int = 2
    deep_think_fetch_enabled: bool = True
    web_search_sources: tuple[str, ...] = ("bocha", "duckduckgo", "so360", "sogou")
    bocha_api_key: str = ""


def load_settings() -> Settings:
    load_dotenv()
    root = Path(__file__).resolve().parent.parent
    return Settings(
        api_key=(os.getenv("LLM_API_KEY") or os.getenv("ZHIPU_API_KEY") or "").strip(),
        base_url=os.getenv("LLM_BASE_URL", "https://api.deepseek.com").strip(),
        model=os.getenv("LLM_MODEL", "deepseek-v4-flash").strip(),
        db_path=os.getenv("DB_PATH", str(root / "data" / "pom.db")),
        data_dir=os.getenv("DATA_DIR", str(root / "data" / "StarRailData")),
        dialogues_dir=os.getenv("DIALOGUES_DIR", str(root / "data" / "dialogues")),
        model_cache_dir=os.getenv("MODEL_CACHE_DIR", str(root / "data" / "models")),
        top_k=int(os.getenv("TOP_K", "6")),
        history_turns=int(os.getenv("HISTORY_TURNS", "20")),
        temperature=float(os.getenv("TEMPERATURE", "0.7")),
        web_search_enabled=os.getenv("WEB_SEARCH_ENABLED", "true").strip().lower()
        in {"1", "true", "yes", "on"},
        debug=os.getenv("DEBUG", "false").strip().lower() in {"1", "true", "yes", "on"},
        deep_think_max_steps=int(os.getenv("DEEP_THINK_MAX_STEPS", "6")),
        deep_think_timeout_seconds=float(
            os.getenv("DEEP_THINK_TIMEOUT_SECONDS", "180")
        ),
        deep_think_llm_timeout_seconds=float(
            os.getenv("DEEP_THINK_LLM_TIMEOUT_SECONDS", "60")
        ),
        deep_think_max_tool_chars=int(os.getenv("DEEP_THINK_MAX_TOOL_CHARS", "1200")),
        deep_think_max_fetches=int(os.getenv("DEEP_THINK_MAX_FETCHES", "2")),
        deep_think_fetch_enabled=os.getenv("DEEP_THINK_FETCH_ENABLED", "true")
        .strip()
        .lower()
        in {"1", "true", "yes", "on"},
        web_search_sources=tuple(
            source.strip()
            for source in os.getenv(
                "WEB_SEARCH_SOURCES", "bocha,duckduckgo,so360,sogou"
            ).split(",")
            if source.strip()
        ),
        bocha_api_key=(os.getenv("BOCHA_API_KEY") or "").strip(),
    )
