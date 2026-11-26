import json
import os
import subprocess
import time
from pathlib import Path
from typing import Tuple, Any
from playwright.async_api import async_playwright, BrowserContext
from typing import Optional
from m365_bridge.config import get_config, browser_profile_dir, resolve_account, account_data_dir

def cleanup_singleton_locks(account: Optional[str] = None):
    """Limpia bloqueos huérfanos de Chromium verificando si el proceso dueño sigue activo."""
    profile_dir = browser_profile_dir(account)
    for lock in ["SingletonLock", "SingletonCookie", "SingletonSocket"]:
        p = profile_dir / lock
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

def find_chromium() -> str:
    """Localiza un Chromium/Chrome del sistema (el login interactivo lo requiere)."""
    import shutil
    for name in ("chromium", "chromium-browser", "google-chrome-stable", "google-chrome", "brave", "brave-browser"):
        path = shutil.which(name)
        if path:
            return path
    raise RuntimeError(
        "No se encontro Chromium. Instalalo (Arch/CachyOS: sudo pacman -S chromium; "
        "Debian/Ubuntu: sudo apt install chromium; Fedora: sudo dnf install chromium)."
    )


def launch_interactive_login(account: Optional[str] = None):
    """Lanza una ventana de Chromium con el perfil de la cuenta para login interactivo / 2FA."""
    cleanup_singleton_locks(account)
    profile_dir = browser_profile_dir(account)
    profile_dir.mkdir(parents=True, exist_ok=True)
    subprocess.Popen([
        find_chromium(),
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        # Permite que la GUI exporte la sesion (cookies) por CDP mientras la ventana sigue abierta.
        f"--remote-debugging-port={get_config(account).get('remote_debugging_port', 9222)}",
        "--app=https://outlook.cloud.microsoft/mail/"
    ])

def is_auth_challenge(url: str, title: str = "") -> bool:
    """Verifica si la URL o el título corresponden a un portal de login o SSO institucional."""
    u = url.lower()
    t = title.lower()
    auth_markers = ("login.microsoftonline.com", "login.live.com", "adfs.", "/adfs/", "login.", "auth.")
    if any(m in u for m in auth_markers):
        return True
    if any(m in t for m in ("iniciar sesión", "sign in", "autenticación", "autenticacion", "adfs")):
        return True
    return False

async def navigate_and_handle_prompts(page, target_url: str = "https://outlook.cloud.microsoft/mail/", timeout: int = 35000) -> bool:
    """Navega a la URL y resuelve automáticamente pantallas intermedias de Microsoft
    (¿Confía en su organización?, ¿Mantener la sesión iniciada?, etc.).
    Si detecta solicitud de credenciales o MFA no resueltas, arroja un error explicativo.
    """
    await page.goto(target_url, wait_until="domcontentloaded", timeout=timeout)
    
    for _ in range(8):
        await page.wait_for_timeout(1500)
        curr = page.url
        title = ""
        try:
            title = await page.title()
        except Exception:
            pass

        if is_auth_challenge(curr, title):
            # Botón Continuar / Sí / Aceptar (idSIButton9)
            btn = await page.query_selector("#idSIButton9")
            if btn:
                try:
                    await btn.click()
                    await page.wait_for_timeout(2000)
                    continue
                except Exception:
                    pass
            
            # Detectar si requiere contraseña, usuario o MFA (sesión expirada o portal SSO)
            has_pw = await page.query_selector("#i0118, input[type='password'], input[name='Password'], #passwordInput")
            has_mfa = await page.query_selector("div[data-viewid*='kmsi'], #idDiv_SAOTCAS_Title")
            has_user_input = await page.query_selector("#userNameInput, input[type='email'], #i0116")
            is_sso = any(k in curr.lower() for k in ("adfs.", "/adfs/"))

            if has_pw or has_mfa or has_user_input or is_sso:
                raise RuntimeError(
                    "La sesión de Microsoft 365 ha caducado o requiere confirmación interactiva / 2FA. "
                    "Por favor inicia sesión con tu cuenta institucional."
                )
        else:
            return True

    curr = page.url
    if is_auth_challenge(curr):
        raise RuntimeError("La sesión de Microsoft 365 ha caducado o requiere confirmación interactiva / 2FA.")
    return True

async def get_browser_context(p, account: Optional[str] = None) -> Tuple[BrowserContext, bool]:
    """Obtiene el contexto del navegador. Retorna (context, should_close).
    Si ya hay una instancia abierta con CDP en el puerto configurado, se conecta a ella.
    De lo contrario, levanta una instancia headless aislada.
    """
    acc = resolve_account(account)
    cfg = get_config(acc)
    port = cfg.get("remote_debugging_port", 9222)
    profile_dir = browser_profile_dir(acc)

    try:
        browser = await p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}", timeout=2500)
        contexts = browser.contexts
        if contexts:
            await import_cookies_into_context(contexts[0], acc)
            return contexts[0], False
    except Exception:
        pass

    cleanup_singleton_locks(acc)
    profile_dir.mkdir(parents=True, exist_ok=True)

    # El lanzador de Chromium (Arch/CachyOS) lee ~/.config/chromium-flags.conf y agrega sus
    # flags; si traen URLs, el modo headless falla con "Multiple targets are not supported".
    # Aislamos esta instancia de la config personal del usuario.
    isolated_cfg = profile_dir.parent / "chromium_cfg"
    isolated_cfg.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "XDG_CONFIG_HOME": str(isolated_cfg)}

    context = await p.chromium.launch_persistent_context(
        user_data_dir=str(profile_dir),
        executable_path=find_chromium(),
        headless=True,
        env=env,
        args=[
            f"--remote-debugging-port={port}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-gpu"
        ]
    )
    await import_cookies_into_context(context, acc)
    return context, True


# --------------------------------------------------------------------------
# Sesion unica: cookies Microsoft compartidas entre el perfil Chromium/Playwright
# (MCP, sync.py, login interactivo) y el QWebEngineProfile de gui.py.
#
# El "almacen comun" es un JSON por cuenta (session_cookies.json, modo 0600) con cookies en
# formato Playwright. El aislamiento entre cuentas se mantiene: cada cuenta tiene su propio JSON.
# --------------------------------------------------------------------------

MS_COOKIE_DOMAINS = (
    "microsoft.com", "microsoftonline.com", "live.com", "office.com",
    "office365.com", "sharepoint.com", "msauth.net", "msftauth.net",
)


def is_ms_domain(domain: str) -> bool:
    d = (domain or "").lstrip(".").lower()
    return any(d == base or d.endswith("." + base) for base in MS_COOKIE_DOMAINS)


def cookies_store_path(account: Optional[str] = None) -> Path:
    return account_data_dir(account) / "session_cookies.json"


def cookie_key(c: dict) -> tuple:
    return (c.get("name", ""), (c.get("domain") or "").lstrip(".").lower(), c.get("path") or "/")


def _expires(c: dict) -> float:
    """expires de Playwright: -1/None = cookie de sesion (la tratamos como la mas duradera)."""
    e = c.get("expires")
    return float("inf") if e is None or e < 0 else float(e)


def is_expired(c: dict, now: Optional[float] = None) -> bool:
    e = c.get("expires")
    return e is not None and e >= 0 and e <= (time.time() if now is None else now)


def filter_ms_cookies(cookies) -> list:
    return [c for c in cookies if c.get("name") and is_ms_domain(c.get("domain", "")) and not is_expired(c)]


def merge_cookies(base: list, incoming: list) -> list:
    """Une dos listas por (name, domain, path). Gana la de expiracion mas lejana; a igualdad, la entrante."""
    merged = {cookie_key(c): c for c in base}
    for c in incoming:
        k = cookie_key(c)
        cur = merged.get(k)
        if cur is None or _expires(c) >= _expires(cur):
            merged[k] = c
    return list(merged.values())


def load_cookie_store(account: Optional[str] = None) -> list:
    try:
        data = json.loads(cookies_store_path(account).read_text(encoding="utf-8"))
        return filter_ms_cookies(data if isinstance(data, list) else [])
    except (OSError, ValueError):
        return []


def save_cookie_store(cookies: list, account: Optional[str] = None) -> Path:
    path = cookies_store_path(account)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(filter_ms_cookies(cookies), f)
    os.replace(tmp, path)
    return path


async def export_browser_cookies(context, account: Optional[str] = None) -> int:
    """Chromium -> almacen comun. Fusiona las cookies MS del contexto. Devuelve cuantas hay en el almacen."""
    fresh = filter_ms_cookies(await context.cookies())
    if not fresh:
        return len(load_cookie_store(account))
    merged = merge_cookies(load_cookie_store(account), fresh)
    save_cookie_store(merged, account)
    return len(merged)


async def import_cookies_into_context(context, account: Optional[str] = None) -> int:
    """Almacen comun -> Chromium. Solo agrega las que faltan o vencen mas tarde. Nunca rompe el flujo."""
    try:
        stored = load_cookie_store(account)
        if not stored:
            return 0
        current = {cookie_key(c): c for c in await context.cookies()}
        to_add = []
        for c in stored:
            cur = current.get(cookie_key(c))
            if cur is None or _expires(c) > _expires(cur):
                to_add.append(_to_playwright(c))
        if to_add:
            await context.add_cookies(to_add)
        return len(to_add)
    except Exception:
        return 0


def _to_playwright(c: dict) -> dict:
    out = {
        "name": c["name"], "value": c.get("value", ""),
        "domain": c["domain"], "path": c.get("path") or "/",
        "httpOnly": bool(c.get("httpOnly")), "secure": bool(c.get("secure")),
        "expires": c.get("expires", -1) if c.get("expires") is not None else -1,
    }
    if c.get("sameSite") in ("Strict", "Lax", "None"):
        out["sameSite"] = c["sameSite"]
    return out


def sync_cookies_from_browser(account: Optional[str] = None) -> int:
    """Version sincrona (para hilos de la GUI): abre el perfil Chromium (o se conecta por CDP si hay
    una ventana abierta), exporta sus cookies MS al almacen comun y devuelve el total. 0 si falla."""
    import asyncio

    async def _run():
        async with async_playwright() as p:
            context, should_close = await get_browser_context(p, account)
            try:
                return await export_browser_cookies(context, account)
            finally:
                if should_close:
                    await context.close()

    try:
        return asyncio.run(_run())
    except Exception:
        return 0


# --- lado QtWebEngine (duck-typed; PyQt6 se importa perezosamente) ---

def qt_cookie_to_dict(c) -> dict:
    exp = -1
    if not c.isSessionCookie():
        exp = float(c.expirationDate().toSecsSinceEpoch())
    return {
        "name": bytes(c.name()).decode("utf-8", "replace"),
        "value": bytes(c.value()).decode("utf-8", "replace"),
        "domain": c.domain(), "path": c.path() or "/",
        "expires": exp, "httpOnly": bool(c.isHttpOnly()), "secure": bool(c.isSecure()),
    }


def dict_to_qt_cookie(d: dict):
    from PyQt6.QtNetwork import QNetworkCookie
    from PyQt6.QtCore import QDateTime
    c = QNetworkCookie(d["name"].encode("utf-8"), d.get("value", "").encode("utf-8"))
    c.setDomain(d["domain"])
    c.setPath(d.get("path") or "/")
    c.setSecure(bool(d.get("secure")))
    c.setHttpOnly(bool(d.get("httpOnly")))
    if d.get("expires") is not None and d["expires"] >= 0:
        c.setExpirationDate(QDateTime.fromSecsSinceEpoch(int(d["expires"])))
    return c


def cookie_origin_url(d: dict) -> str:
    return "https://" + (d.get("domain") or "").lstrip(".") + (d.get("path") or "/")
