"""Buscar e instalar actualizaciones desde los releases de GitHub.

Tres formas de instalación, cada una se actualiza distinto:
- "installed": instalada con el one-liner de curl (carpeta gestionada por install.sh, con
  el archivo marcador INSTALL_MARKER). Se actualiza re-ejecutando install.sh: hace checkout
  del último tag, reinstala dependencias y reinicia la bandeja.
- "dev": un clon de git propio (sin marcador). install.sh hace `git pull --ff-only` sobre la
  rama actual; si hay cambios sin commitear, se niega.
- "appimage": se descarga el AppImage del último release y reemplaza al actual.
"""
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from m365_bridge import __version__

REPO = "niguerrac/m365-linux-bridge"
LATEST_API = f"https://api.github.com/repos/{REPO}/releases/latest"
INSTALL_SH_URL = f"https://raw.githubusercontent.com/{REPO}/main/install.sh"
APPIMAGE_ASSET = "M365-Linux-Bridge-x86_64.AppImage"
INSTALL_MARKER = ".m365-install"
APP_DIR = Path(__file__).resolve().parent.parent
CHECK_CACHE_SECONDS = 6 * 3600


def parse_version(v: str) -> Tuple[int, ...]:
    nums = re.findall(r"\d+", v or "")
    return tuple(int(n) for n in nums[:3]) or (0,)


def is_newer(remote: str, local: str = __version__) -> bool:
    return parse_version(remote) > parse_version(local)


def install_mode() -> str:
    if os.environ.get("APPIMAGE"):
        return "appimage"
    if (APP_DIR / INSTALL_MARKER).exists():
        return "installed"
    return "dev"


MODE_LABEL = {"appimage": "AppImage", "installed": "instalada", "dev": "copia de desarrollo (git)"}


def fetch_latest_release(timeout: float = 8.0) -> Dict[str, Any]:
    req = urllib.request.Request(
        LATEST_API,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "m365-linux-bridge-updater"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.load(r)
    asset = next((a for a in data.get("assets", []) if a.get("name") == APPIMAGE_ASSET), None)
    return {
        "version": data.get("tag_name", "").lstrip("v"),
        "tag": data.get("tag_name", ""),
        "url": data.get("html_url", ""),
        "notes": data.get("body") or "",
        "appimage_url": asset.get("browser_download_url") if asset else None,
    }


def check_for_update(use_cache: bool = True) -> Optional[Dict[str, Any]]:
    """Devuelve la info del release si es más nuevo que la versión actual, si no None.
    Cachea la consulta (la API pública de GitHub tiene límite por IP)."""
    from m365_bridge.sync_db import get_kv, set_kv
    info = None
    if use_cache:
        try:
            cached = json.loads(get_kv("update_check_cache") or "{}")
            if cached and time.time() - cached.get("ts", 0) < CHECK_CACHE_SECONDS:
                info = cached["info"]
        except Exception:
            info = None
    if info is None:
        info = fetch_latest_release()
        set_kv("update_check_cache", json.dumps({"ts": time.time(), "info": info}))
    return info if info.get("version") and is_newer(info["version"]) else None


def run_update(info: Dict[str, Any], log: Callable[[str], None] = print) -> None:
    """Instala la actualización. Lanza RuntimeError con un mensaje claro si falla."""
    mode = install_mode()
    if mode == "appimage":
        _update_appimage(info, log)
        return
    env = {**os.environ, "M365_INSTALL_DIR": str(APP_DIR), "M365_RESTART_TRAY": "1"}
    cmd = ["bash", "-c", f"set -o pipefail; curl -fsSL '{INSTALL_SH_URL}' | bash"]
    log(f"Ejecutando instalador sobre {APP_DIR}…")
    proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    tail = []
    for line in proc.stdout:
        line = line.rstrip()
        tail = (tail + [line])[-15:]
        log(line)
    if proc.wait() != 0:
        raise RuntimeError("El instalador falló:\n" + "\n".join(tail))


def _update_appimage(info: Dict[str, Any], log: Callable[[str], None]) -> None:
    url = info.get("appimage_url")
    target = Path(os.environ["APPIMAGE"])
    if not url:
        raise RuntimeError("El release no trae AppImage para descargar.")
    log(f"Descargando {APPIMAGE_ASSET} {info.get('tag')}…")
    fd, tmp = tempfile.mkstemp(prefix=".m365-update-", suffix=".AppImage", dir=str(target.parent))
    os.close(fd)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "m365-linux-bridge-updater"})
        with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
            while chunk := r.read(1024 * 1024):
                f.write(chunk)
        os.chmod(tmp, os.stat(tmp).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        os.replace(tmp, target)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    log(f"AppImage reemplazado: {target}")


def restart_command() -> list:
    """Comando para relanzar la ventana principal después de actualizar."""
    if install_mode() == "appimage":
        return [os.environ["APPIMAGE"]]
    return [sys.executable, str(APP_DIR / "m365_bridge" / "gui.py")]
