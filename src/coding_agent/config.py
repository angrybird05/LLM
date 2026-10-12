from functools import lru_cache
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", case_sensitive=False, extra="ignore")

    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000
    APP_ENV: str = "development"
    LOG_LEVEL: str = "INFO"

    DATABASE_URL: str = "sqlite+aiosqlite:///./dev.db"
    REDIS_URL: str = "redis://localhost:6379/0"

    API_KEYS: str = "sk-dev-key-1,sk-dev-key-2"
    AUTH_ENABLED: bool = True
    JWT_SECRET: str = "change-me-in-production-min-32-chars"
    JWT_ALGORITHM: str = "HS256"

    # Audit A2: /tools/execute is limited to read-only tools unless the
    # operator explicitly opts in to letting risky tools run over HTTP.
    TOOLS_EXECUTE_ENABLED: bool = False
    # Audit A6: web_fetch refuses loopback/private/link-local hosts; flip this
    # only for trusted intranet use.
    WEB_FETCH_ALLOW_PRIVATE: bool = False

    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_REQUESTS: int = 100
    RATE_LIMIT_WINDOW: int = 60

    ANTHROPIC_API_KEY: str | None = None
    OPENAI_API_KEY: str | None = None
    OPENROUTER_API_KEY: str | None = None
    DEFAULT_PROVIDER: str = "anthropic"
    DEFAULT_MODEL: str = "claude-sonnet-5"

    # Agent behavior
    AGENT_MAX_ITERATIONS: int = 25
    WORKSPACE_ROOT: str = ""  # empty -> directory the process starts in
    TOOL_OUTPUT_LIMIT: int = 30000  # max chars of tool output sent back to the LLM
    READ_LINE_LIMIT: int = 2000
    PERMISSION_MODE: str = "default"  # default | auto-edit | full-auto

    # Context management: auto-compact history before it outgrows the model window.
    # Estimates use a chars/4 heuristic; provider usage numbers refine the display.
    CONTEXT_TOKEN_LIMIT: int = 200_000   # assumed model context window (tokens)
    COMPACT_THRESHOLD: float = 0.75      # fraction of the limit that triggers compaction
    COMPACT_KEEP_RECENT: int = 16        # recent messages kept verbatim when compacting
    HISTORY_OUTPUT_LIMIT: int = 6000     # chars of each stored tool output resent on later turns

    # Shell execution backend for the `bash` tool
    SHELL_MODE: str = "local"  # local | docker
    SHELL_EXECUTABLE: str = ""  # empty -> auto-detect bash, fall back to system shell
    SHELL_TIMEOUT: int = 120

    DOCKER_HOST: str = "unix:///var/run/docker.sock"
    SANDBOX_TIMEOUT: int = 300
    SANDBOX_MEMORY_LIMIT: str = "512m"
    SANDBOX_CPU_LIMIT: float = 1.0
    SANDBOX_IMAGE: str = "python:3.11-slim"

    UPLOAD_DIR: str = "/app/uploads"
    MAX_UPLOAD_SIZE: int = 10485760
    ALLOWED_EXTENSIONS: str = ".py,.js,.ts,.json,.yaml,.yml,.md,.txt,.csv,.html,.css"

    @property
    def api_keys_list(self) -> list[str]:
        return [k.strip() for k in self.API_KEYS.split(",") if k.strip()]

    @property
    def workspace_path(self) -> Path:
        return Path(self.WORKSPACE_ROOT).resolve() if self.WORKSPACE_ROOT else Path.cwd().resolve()

    @property
    def allowed_extensions_list(self) -> list[str]:
        return [e.strip() for e in self.ALLOWED_EXTENSIONS.split(",") if e.strip()]

    @model_validator(mode="after")
    def _production_guard(self) -> "Settings":
        """Audit A3: refuse production startup on the shipped dev credentials."""
        import secrets

        if self.APP_ENV.lower() not in ("production", "prod"):
            return self
        default_keys = {"sk-dev-key-1", "sk-dev-key-2"}
        weak = [k for k in self.api_keys_list
                if any(secrets.compare_digest(k, d) for d in default_keys)]
        jwt_default = secrets.compare_digest(
            self.JWT_SECRET, "change-me-in-production-min-32-chars")
        problems = []
        if weak:
            problems.append(f"insecure default API keys: {weak}")
        if jwt_default:
            problems.append("JWT_SECRET still the shipped default")
        if problems:
            raise ValueError(
                "Refusing to start: APP_ENV=production with insecure default "
                "credentials (" + "; ".join(problems) + "). Set real API_KEYS "
                "and JWT_SECRET values."
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
