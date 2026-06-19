"""Load .env into os.environ — handles BOM, CRLF, export prefix, quoted values."""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def load_dotenv(path: Path | None = None) -> dict[str, str]:
    """Parse .env file and set os.environ. Returns loaded key/value pairs."""
    env_path = path or Path(__file__).resolve().parent / ".env"
    loaded: dict[str, str] = {}

    if not env_path.is_file():
        logger.warning(".env not found: %s", env_path)
        return loaded

    raw: str | None = None
    for encoding in ("utf-8-sig", "utf-8", "utf-16", "latin-1"):
        try:
            raw = env_path.read_text(encoding=encoding)
            break
        except (UnicodeDecodeError, OSError):
            continue

    if raw is None:
        logger.error("Could not read .env (encoding): %s", env_path)
        return loaded

    for line in raw.splitlines():
        line = line.strip().strip("\r")
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            continue
        value = value.strip().strip('"').strip("'")
        loaded[key] = value
        os.environ[key] = value

    if not loaded.get("TELEGRAM_TOKEN"):
        logger.warning(
            ".env loaded from %s (%d keys) but TELEGRAM_TOKEN is empty — "
            "check file is named exactly .env in repo root",
            env_path,
            len(loaded),
        )
    else:
        logger.debug(".env loaded: %s (%d keys)", env_path, len(loaded))

    return loaded


def dotenv_path() -> Path:
    return Path(__file__).resolve().parent / ".env"
