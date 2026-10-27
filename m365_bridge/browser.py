import os
import subprocess
from pathlib import Path
from typing import Tuple, Any
from playwright.async_api import async_playwright, BrowserContext
from m365_bridge.config import BROWSER_PROFILE_DIR, get_config

def cleanup_singleton_locks():
    """Limpia bloqueos huérfanos de Chromium verificando si el proceso dueño sigue activo."""
    for lock in ["SingletonLock", "SingletonCookie", "SingletonSocket"]:
        p = BROWSER_PROFILE_DIR / lock
        if p.exists() or p.is_symlink():
            try:
                target = os.readlink(p) if p.is_symlink() else ""
                pid = None
                if "-" in target:
                    parts = target.split("-")
                    if parts[-1].isdigit():
                        pid = int(parts[-1])
                
                is_running = False
                if pid:
                    try:
                        os.kill(pid, 0)
                        is_running = True
                    except OSError:
                        is_running = False
                
                if not is_running:
                    p.unlink()
            except Exception:
                try:
                    p.unlink()
                except Exception:
                    pass

def launch_interactive_login():
    """Lanza una ventana de Chromium con el perfil del motor para login interactivo / 2FA."""
    cleanup_singleton_locks()
    subprocess.Popen([
        "/usr/bin/chromium",
        f"--user-data-dir={BROWSER_PROFILE_DIR}",
        "--no-first-run",
        "--no-default-browser-check",
        "--app=https://outlook.cloud.microsoft/mail/"
    ])

async def navigate_and_handle_prompts(page, target_url: str = "https://outlook.cloud.microsoft/mail/", timeout: int = 35000) -> bool:
    """Navega a la URL y resuelve automáticamente pantallas intermedias de Microsoft
    (¿Confía en su organización?, ¿Mantener la sesión iniciada?, etc.).
    Si detecta solicitud de credenciales o MFA no resueltas, arroja un error explicativo.
    """
    await page.goto(target_url, wait_until="domcontentloaded", timeout=timeout)
    
    for _ in range(8):
        await page.wait_for_timeout(1500)
        curr = page.url
        if "login.microsoftonline.com" in curr or "login.live.com" in curr:
            # Botón Continuar / Sí / Aceptar (idSIButton9)
            btn = await page.query_selector("#idSIButton9")
            if btn:
                try:
                    await btn.click()
                    await page.wait_for_timeout(2000)
                    continue
                except Exception:
                    pass
            
            # Detectar si requiere contraseña o usuario interactivo (sesión expirada)
            has_pw = await page.query_selector("#i0118, input[type='password']")
            has_mfa = await page.query_selector("div[data-viewid*='kmsi'], #idDiv_SAOTCAS_Title")
            if has_pw or has_mfa:
                raise RuntimeError(
                    "La sesión de Microsoft 365 ha caducado o requiere confirmación interactiva / 2FA. "
                    "Por favor inicia sesión con tu cuenta institucional."
                )
        else:
            return True

    if "login.microsoftonline.com" in page.url:
        raise RuntimeError("No se pudo completar el inicio de sesión automático en Microsoft 365.")
    return True

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
