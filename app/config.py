import os
import secrets
from pathlib import Path


def _load_secret_key(data_dir: Path) -> str:
    """Use SECRET_KEY from env, else persist a generated one inside the data dir."""
    key = os.environ.get("SECRET_KEY")
    if key:
        return key
    key_file = data_dir / ".secret_key"
    if key_file.exists():
        return key_file.read_text().strip()
    key = secrets.token_urlsafe(48)
    key_file.write_text(key)
    key_file.chmod(0o600)
    return key


class Config:
    def __init__(self, **overrides):
        self.DATA_DIR = Path(overrides.pop("DATA_DIR", os.environ.get("DATA_DIR", "/data")))
        self.DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.UPLOAD_DIR = self.DATA_DIR / "uploads"
        self.BRANDING_DIR = self.DATA_DIR / "branding"
        self.UPLOAD_DIR.mkdir(exist_ok=True)
        self.BRANDING_DIR.mkdir(exist_ok=True)

        self.SECRET_KEY = _load_secret_key(self.DATA_DIR)
        self.SQLALCHEMY_DATABASE_URI = f"sqlite:///{self.DATA_DIR / 'inventory.db'}"
        self.SQLALCHEMY_ENGINE_OPTIONS = {"connect_args": {"timeout": 30}}
        self.MAX_CONTENT_LENGTH = int(os.environ.get("MAX_UPLOAD_MB", "64")) * 1024 * 1024
        self.SESSION_COOKIE_HTTPONLY = True
        self.SESSION_COOKIE_SAMESITE = "Lax"
        self.REMEMBER_COOKIE_SAMESITE = "Lax"
        self.WTF_CSRF_TIME_LIMIT = None
        self.JSON_SORT_KEYS = False
        self.TESTING = False
        # Migrations/CLI runs shouldn't start the indexer and maintenance threads
        self.SKIP_BACKGROUND = os.environ.get("SKIP_BACKGROUND") == "1"

        for k, v in overrides.items():
            setattr(self, k, v)
