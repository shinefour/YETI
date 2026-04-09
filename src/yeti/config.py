"""YETI configuration — loaded from environment variables."""

from pathlib import Path

from pydantic_settings import BaseSettings

_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"
if not _ENV_FILE.exists():
    _ENV_FILE = None


class Settings(BaseSettings):
    # --- Core ---
    app_name: str = "YETI"
    debug: bool = False
    host: str = "0.0.0.0"
    port: int = 8000

    # --- AI Model Keys ---
    anthropic_api_key: str = ""
    openai_api_key: str = ""

    # --- LiteLLM ---
    litellm_default_model: str = "claude-sonnet-4-20250514"
    litellm_fast_model: str = "claude-haiku-4-5-20251001"
    litellm_local_model: str = "ollama/llama3"

    # --- Ollama ---
    ollama_base_url: str = "http://ollama:11434"

    # --- Telegram ---
    telegram_bot_token: str = ""
    telegram_allowed_chat_id: int = 0

    # --- Redis ---
    redis_url: str = "redis://redis:6379/0"

    # --- MemPalace ---
    mempalace_url: str = "http://mempalace:3100"

    # --- ChromaDB ---
    chromadb_url: str = "http://chromadb:8001"

    # --- Integration credentials ---
    microsoft_client_id: str = ""
    microsoft_client_secret: str = ""
    microsoft_tenant_id: str = ""

    jira_url: str = ""
    jira_email: str = ""
    jira_api_token: str = ""

    notion_api_key: str = ""

    slack_bot_token: str = ""

    model_config = {
        "env_prefix": "YETI_",
        "env_file": str(_ENV_FILE),
    }


settings = Settings()
