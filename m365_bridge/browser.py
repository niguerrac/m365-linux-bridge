import os
import subprocess
from pathlib import Path
from typing import Tuple, Any
from playwright.async_api import async_playwright, BrowserContext
from m365_bridge.config import BROWSER_PROFILE_DIR, get_config

def cleanup_singleton_locks():
    """Limpia bloqueos huérfanos de Chromium si no hay navegador corriendo"""
    res = subprocess.run(["pgrep", "-f", "m365-bridge.*browser_profile"], capture_output=True, text=True)
    if not res.stdout.strip():
        for lock in ["SingletonLock", "SingletonCookie", "SingletonSocket"]:
            p = BROWSER_PROFILE_DIR / lock
            if p.exists() or p.is_symlink():
                try:
                    p.unlink()
                except Exception:
                    pass

async def get_browser_context(p) -> Tuple[BrowserContext, bool]:
    """Obtiene el contexto del navegador. Retorna (context, should_close).
    Si ya hay una instancia abierta con CDP en el puerto configurado, se conecta a ella.
    De lo contrario, levanta una instancia headless aislada.
    """
    cfg = get_config()
    port = cfg.get("remote_debugging_port", 9222)

    try:
        browser = await p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}", timeout=2500)
        contexts = browser.contexts
        if contexts:
            return contexts[0], False
    except Exception:
        pass

    cleanup_singleton_locks()

    context = await p.chromium.launch_persistent_context(
        user_data_dir=str(BROWSER_PROFILE_DIR),
        executable_path="/usr/bin/chromium",
        headless=True,
        args=[
            f"--remote-debugging-port={port}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-gpu"
        ]
    )
    return context, True
