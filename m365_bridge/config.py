import json
import os
from pathlib import Path
from typing import Dict, Any, List

CONFIG_DIR = Path.home() / ".config" / "m365-bridge"
CONFIG_FILE = CONFIG_DIR / "config.json"
DATA_DIR = Path.home() / ".local" / "share" / "m365-bridge"
BROWSER_PROFILE_DIR = DATA_DIR / "browser_profile"

DEFAULT_CONFIG: Dict[str, Any] = {
    "institution_name": "Mi Institución",
    "sync_folder": str(Path.home() / "M365-Drive"),
    "outlook_url": "https://outlook.cloud.microsoft/mail/",
    "onedrive_url": "https://www.office.com/launch/onedrive",
    "auto_sync_interval_minutes": 60,
    "max_file_size_mb": 200,
    "excluded_folders": ["Recordings", "Meetings", "Temporales"],
    "remote_debugging_port": 9222
}

def ensure_dirs():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    BROWSER_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    sync_dir = Path(get_config()["sync_folder"])
    sync_dir.mkdir(parents=True, exist_ok=True)

def get_config() -> Dict[str, Any]:
    if not CONFIG_FILE.exists():
        save_config(DEFAULT_CONFIG)
        return DEFAULT_CONFIG.copy()
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            # Combinar con claves por defecto si faltan
            for k, v in DEFAULT_CONFIG.items():
                if k not in data:
                    data[k] = v
            return data
    except Exception:
        return DEFAULT_CONFIG.copy()

def save_config(cfg: Dict[str, Any]):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
