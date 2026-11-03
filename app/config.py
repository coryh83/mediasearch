import json
import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
CONFIG_PATH = DATA_DIR / "config.json"


def default_config() -> dict:
    return {
        "indexers": [],  # [{"name": "...", "url": "http://jackett:9117/api/v2.0/indexers/x/results/torznab/?apikey=..."}]
        "transmission": {"url": "http://transmission:9091", "username": "", "password": ""},
        # Folder paths as seen from INSIDE this app's container (see the volume mounts in docker-compose.yml).
        "folders": {"complete": "/media/downloads/complete", "audiobooks": "/media/audiobooks", "ebooks": "/media/ebooks"},
    }


def load() -> dict:
    cfg = default_config()
    if CONFIG_PATH.exists():
        saved = json.loads(CONFIG_PATH.read_text())
        cfg.update(saved)
        cfg["folders"] = {**default_config()["folders"], **saved.get("folders", {})}
    return cfg


def save(cfg: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    # Holds the Transmission password and Jackett API keys: owner-only read/write.
    fd = os.open(CONFIG_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(json.dumps(cfg, indent=2))
    os.chmod(CONFIG_PATH, 0o600)
