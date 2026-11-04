"""Runtime settings, read from the environment (and an optional .env file)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
STORE_TZ = ZoneInfo("America/Campo_Grande")  # Mato Grosso do Sul: UTC-4, no DST

load_dotenv(ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    base_url: str
    api_key: str
    model: str
    temperature: float
    reasoning_effort: str | None  # 'none' turns off qwen3's hidden reasoning on Ollama
    data_dir: Path
    runtime_dir: Path
    store_now: datetime | None  # fixed clock for reproducible runs; None = real time

    def now(self) -> datetime:
        return self.store_now or datetime.now(STORE_TZ)


def load_settings() -> Settings:
    fixed = os.getenv("STORE_NOW")
    store_now = None
    if fixed:
        store_now = datetime.fromisoformat(fixed)
        if store_now.tzinfo is None:
            store_now = store_now.replace(tzinfo=STORE_TZ)
    return Settings(
        base_url=os.getenv("LLM_BASE_URL", "http://localhost:11434/v1"),
        api_key=os.getenv("LLM_API_KEY", "ollama"),
        model=os.getenv("LLM_MODEL", "qwen3:8b"),
        temperature=float(os.getenv("LLM_TEMPERATURE", "0.2")),
        reasoning_effort=os.getenv("LLM_REASONING_EFFORT", "none") or None,
        data_dir=Path(os.getenv("DATA_DIR", str(ROOT / "data"))),
        runtime_dir=Path(os.getenv("RUNTIME_DIR", str(ROOT / "runtime"))),
        store_now=store_now,
    )
