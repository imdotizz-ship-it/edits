"""Runtime configuration, read from environment variables.

All secrets come from the environment (or a .env file you keep out of git);
nothing secret is ever written to the database or to logs.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ENVIRONMENTS = ("test", "production")


def _bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    # "test" and "production" use different databases and export folders so
    # experiments can never overwrite what the public site shows.
    environment: str = "test"
    data_dir: Path = Path("./var")
    game: str = "bf4"
    base_url: str = "https://battlelog.battlefield.com"
    user_agent: str = "BattlelogProfilePanel/0.1 (read-only personal stats sync)"
    request_timeout: float = 15.0
    min_request_interval: float = 2.0  # polite rate limit between Battlelog requests
    # Sync
    auto_sync_minutes: int = 0  # 0 = disabled
    # Auth: SHA-256 hex digests of the tokens, never the tokens themselves.
    admin_token_sha256: str = ""
    viewer_token_sha256: str = ""
    session_secret: str = ""
    # Public site export
    site_cors_origins: list[str] = field(default_factory=list)
    # Writes: disabled by default. Even when True, the AuthorizedWriteAdapter
    # still refuses every operation that is not in its (empty) official registry.
    writes_enabled: bool = False
    # Optional: path template of the dogtag page; UNVERIFIED (see docs).
    dogtags_path: str = "/{game}/soldier/{persona_name}/dogtags/{persona_id}/{platform}/"

    @property
    def env_dir(self) -> Path:
        return self.data_dir / self.environment

    @property
    def db_path(self) -> Path:
        return self.env_dir / "panel.sqlite3"

    @property
    def export_dir(self) -> Path:
        return self.env_dir / "site_export"

    @property
    def capture_dir(self) -> Path:
        return self.env_dir / "captures"

    def ensure_dirs(self) -> None:
        for p in (self.env_dir, self.export_dir, self.capture_dir):
            p.mkdir(parents=True, exist_ok=True)


def load_settings() -> Settings:
    env = os.environ.get("BLPANEL_ENV", "test").strip().lower()
    if env not in ENVIRONMENTS:
        raise ValueError(f"BLPANEL_ENV must be one of {ENVIRONMENTS}, got {env!r}")
    origins = [o.strip() for o in os.environ.get("BLPANEL_SITE_CORS", "").split(",") if o.strip()]
    s = Settings(
        environment=env,
        data_dir=Path(os.environ.get("BLPANEL_DATA_DIR", "./var")),
        game=os.environ.get("BLPANEL_GAME", "bf4"),
        base_url=os.environ.get("BLPANEL_BASE_URL", "https://battlelog.battlefield.com").rstrip("/"),
        request_timeout=float(os.environ.get("BLPANEL_TIMEOUT", "15")),
        min_request_interval=float(os.environ.get("BLPANEL_MIN_INTERVAL", "2")),
        auto_sync_minutes=int(os.environ.get("BLPANEL_AUTO_SYNC_MINUTES", "0")),
        admin_token_sha256=os.environ.get("BLPANEL_ADMIN_TOKEN_SHA256", "").strip().lower(),
        viewer_token_sha256=os.environ.get("BLPANEL_VIEWER_TOKEN_SHA256", "").strip().lower(),
        session_secret=os.environ.get("BLPANEL_SESSION_SECRET", ""),
        site_cors_origins=origins,
        writes_enabled=_bool("BLPANEL_WRITES_ENABLED", False),
    )
    if "BLPANEL_DOGTAGS_PATH" in os.environ:
        s.dogtags_path = os.environ["BLPANEL_DOGTAGS_PATH"]
    return s
