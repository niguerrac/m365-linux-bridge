import asyncio
import os
import time
import httpx
from pathlib import Path
from urllib.parse import urlparse, quote
from typing import Dict, Any, List, Optional, Callable
from playwright.async_api import async_playwright
from m365_bridge.config import get_config
from m365_bridge.browser import get_browser_context, navigate_and_handle_prompts
from m365_bridge.sync_db import (
    record_synced_file, get_file_state, list_all_tracked_files,
    get_kv, set_kv, remove_file_record
)

IGNORED_EXTENSIONS = {".tmp", ".crdownload", ".swp", ".bak", ".part"}
IGNORED_PREFIXES = (".~", "~$", ".", "~")

def is_ignored_file(p: Path, excluded_folders: set) -> bool:
    if p.name.startswith(IGNORED_PREFIXES) or p.suffix.lower() in IGNORED_EXTENSIONS:
        return True
    return any(part in excluded_folders for part in p.parts)

async def sync_remote_changes_delta_httpx(
    client: httpx.AsyncClient,
    base_endpoint: str,
    local_drive: Path,
    cfg: Dict[str, Any],
    progress_callback: Optional[Callable[[str, int], None]] = None
) -> int:
    """Sincroniza cambios desde la nube hacia el disco local utilizando la API Delta de OneDrive a máxima velocidad HTTPX."""
    local_drive.mkdir(parents=True, exist_ok=True)
    max_mb = cfg.get("max_file_size_mb", 200) * 1024 * 1024
    excluded = set(cfg.get("excluded_folders", []))

    saved_delta = get_kv("onedrive_delta_link")
    curr_url = saved_delta if saved_delta else f"{base_endpoint}/delta"
    
    if progress_callback:
        mode_text = "Comprobando cambios en la nube..." if saved_delta else "Indexación inicial en la nube..."
        progress_callback(mode_text, -1)

    downloaded = 0
    total_items = 0

    while curr_url:
        try:
            resp = await client.get(
                curr_url,
                headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0"},
                timeout=35.0
            )
        except Exception as err:
            if progress_callback:
                progress_callback(f"Error de red: {err}", downloaded)
            break

        if resp.status_code in (410, 404, 400) and saved_delta:
            # Token delta expirado en Microsoft, reiniciar escaneo delta
            set_kv("onedrive_delta_link", "")
            curr_url = f"{base_endpoint}/delta"
            saved_delta = None
            continue

        if resp.status_code != 200:
            break

        body = resp.json()
        items = body.get("value", [])
        total_items += len(items)

        for item in items:
            name = item.get("name", "")
            if not name or name == "root":
                continue

            parent_path = item.get("parentReference", {}).get("path", "")
            rel_dir = parent_path.split("/drive/root:")[-1].strip("/")
            rel_str = f"{rel_dir}/{name}".strip("/")
            target_path = local_drive / rel_str

            # Borrado remoto
            if "deleted" in item:
                remove_file_record(rel_str)
                continue

            # Carpeta remota
            if item.get("folder"):
                if any(part in excluded for part in target_path.parts):
                    continue
                target_path.mkdir(parents=True, exist_ok=True)
                continue

            # Archivo remoto
            if any(part in excluded for part in target_path.parts):
                continue

            size = item.get("size", 0)
            if size > max_mb:
                continue

            download_url = item.get("@content.downloadUrl") or item.get("@microsoft.graph.downloadUrl")
            last_modified_str = item.get("lastModifiedDateTime")
            
            remote_mtime = None
            if last_modified_str:
                try:
                    from datetime import datetime
                    dt = datetime.fromisoformat(last_modified_str.replace("Z", "+00:00"))
                    remote_mtime = dt.timestamp()
                except Exception:
                    pass

            # Si el archivo local existe con mismo tamaño y fecha igual o más nueva, omitir
            if target_path.exists():
                st = target_path.stat()
                if st.st_size == size:
                    if remote_mtime is not None and st.st_mtime >= (remote_mtime - 2):
                        record_synced_file(
                            rel_path=rel_str,
                            local_mtime=st.st_mtime,
                            local_size=st.st_size,
                            remote_mtime=remote_mtime,
                            remote_size=size,
                            direction="down"
                        )
                        continue
                    elif remote_mtime is None:
                        continue

            if not download_url:
                continue

            if progress_callback:
                progress_callback(f"Descargando: {name}", downloaded)

            target_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = target_path.with_suffix(target_path.suffix + ".tmp")

            try:
                async with client.stream("GET", download_url, follow_redirects=True) as d_resp:
                    if d_resp.status_code == 200:
                        with open(temp_path, "wb") as f:
                            async for chunk in d_resp.aiter_bytes(chunk_size=1024 * 1024):
                                f.write(chunk)
                        temp_path.replace(target_path)
                        if remote_mtime is not None:
                            try:
                                os.utime(target_path, (remote_mtime, remote_mtime))
                            except Exception:
                                pass

                        st = target_path.stat()
                        record_synced_file(
                            rel_path=rel_str,
                            local_mtime=st.st_mtime,
                            local_size=st.st_size,
                            remote_mtime=remote_mtime or st.st_mtime,
                            remote_size=size,
                            direction="down"
                        )
                        downloaded += 1
                        if progress_callback:
                            progress_callback(f"Guardado: {name}", downloaded)
                    else:
                        if temp_path.exists():
                            temp_path.unlink()
            except Exception as err:
                temp_p = target_path.with_suffix(target_path.suffix + ".tmp")
                if temp_p.exists():
                    try:
                        temp_p.unlink()
                    except Exception:
                        pass
                if progress_callback:
                    progress_callback(f"Error {name}: {err}", downloaded)

        # Avanzar paginación de Delta
        if body.get("@odata.nextLink"):
            curr_url = body["@odata.nextLink"]
            if progress_callback:
                progress_callback(f"Indexando nube ({total_items} items)...", downloaded)
        elif body.get("@odata.deltaLink"):
            set_kv("onedrive_delta_link", body["@odata.deltaLink"])
            curr_url = None
            break
        else:
            curr_url = None
            break

    return downloaded

def scan_pending_uploads(local_drive: Path, cfg: Dict[str, Any]) -> List[Path]:
    """Detecta archivos modificados o creados en Linux que requieren subida."""
    excluded = set(cfg.get("excluded_folders", []))
    max_mb = cfg.get("max_file_size_mb", 200) * 1024 * 1024
    pending = []

    if not local_drive.exists():
        return []

    for p in local_drive.rglob("*"):
        if not p.is_file():
            continue
        if is_ignored_file(p, excluded):
            continue
        try:
            stat = p.stat()
            if stat.st_size > max_mb:
                continue
            
            rel_str = str(p.relative_to(local_drive))
            state = get_file_state(rel_str)
            
            if not state:
                pending.append(p)
            else:
                if stat.st_mtime > (state["local_mtime"] + 2) or stat.st_size != state["local_size"]:
                    pending.append(p)
        except Exception:
            pass

    return pending

async def upload_pending_files(
    page,
    base_url: str,
    local_drive: Path,
    pending_files: List[Path],
    progress_callback: Optional[Callable[[str, int], None]] = None
) -> int:
    """Sube archivos locales a la carpeta correspondiente en OneDrive Web."""
    if not pending_files:
        return 0

    tenant_path = urlparse(base_url).path.rstrip("/")
    uploaded = 0

    by_dir: Dict[str, List[Path]] = {}
    for f in pending_files:
        rel_dir = str(f.parent.relative_to(local_drive))
        by_dir.setdefault(rel_dir, []).append(f)

    for rel_dir, files in by_dir.items():
        try:
            if rel_dir == "." or not rel_dir:
                server_rel = f"{tenant_path}/Documents"
                target_url = f"{base_url}/_layouts/15/onedrive.aspx"
            else:
                server_rel = f"{tenant_path}/Documents/{rel_dir}"
                target_url = f"{base_url}/_layouts/15/onedrive.aspx?id={quote(server_rel)}"

            if progress_callback:
                progress_callback(f"Abriendo carpeta {rel_dir} en la nube...", uploaded)

            await page.goto(target_url, wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(2000)

            btn_add = page.locator('button:has-text("Crear o cargar"), button:has-text("Cargar")').first
            if await btn_add.count() == 0:
                continue
            await btn_add.click()
            await page.wait_for_timeout(1000)

            async with page.expect_file_chooser(timeout=10000) as fc_info:
                btn_up = page.locator('button:has-text("Carga de archivos"), button:has-text("Archivos")').first
                await btn_up.click()
            
            fc = await fc_info.value
            paths = [str(f) for f in files]
            
            for f in files:
                if progress_callback:
                    progress_callback(f"Subiendo a OneDrive: {f.name}", uploaded)

            await fc.set_files(paths)
            await page.wait_for_timeout(3000 + 1500 * len(paths))

            replace_btn = page.locator('button:has-text("Reemplazar")').first
            if await replace_btn.count() > 0 and await replace_btn.is_visible():
                await replace_btn.click()
                await page.wait_for_timeout(2500)

            for f in files:
                st = f.stat()
                rel_str = str(f.relative_to(local_drive))
                record_synced_file(
                    rel_path=rel_str,
                    local_mtime=st.st_mtime,
                    local_size=st.st_size,
                    remote_mtime=st.st_mtime,
                    remote_size=st.st_size,
                    direction="up"
                )
                uploaded += 1
                if progress_callback:
                    progress_callback(f"Subido a OneDrive: {f.name}", uploaded)

        except Exception as err:
            if progress_callback:
                progress_callback(f"Error subiendo en {rel_dir}: {err}", uploaded)

    return uploaded

async def run_sync(progress_callback: Optional[Callable[[str, int], None]] = None) -> Dict[str, int]:
    cfg = get_config()
    local_drive = Path(cfg["sync_folder"])
    
    if progress_callback:
        progress_callback("Conectando con Microsoft 365...", 0)

    async with async_playwright() as p:
        context, should_close = await get_browser_context(p)
        try:
            page = await context.new_page()
            await navigate_and_handle_prompts(page, "https://outlook.cloud.microsoft/mail/", timeout=35000)
            
            if progress_callback:
                progress_callback("Abriendo OneDrive...", 0)

            btn = None
            selectors = [
                'button[aria-label="OneDrive"]',
                'button[id="377c982d-9686-450e-9a7c-22aeaf1bc162"]',
                'button[title*="OneDrive"]',
                'button:has-text("OneDrive")',
                'a[aria-label="OneDrive"]'
            ]
            for sel in selectors:
                try:
                    btn = await page.wait_for_selector(sel, timeout=5000)
                    if btn:
                        break
                except Exception:
                    pass

            if not btn:
                raise RuntimeError("No se halló el acceso a OneDrive dentro de la sesión de Microsoft 365.")

            await btn.click()

            if progress_callback:
                progress_callback("Conectando con explorador de archivos...", 0)

            sp_frame = None
            for _ in range(25):
                await page.wait_for_timeout(1000)
                for f in page.frames:
                    if "sharepoint.com" in f.url:
                        sp_frame = f
                        break
                if sp_frame:
                    break
            
            if not sp_frame:
                raise RuntimeError("No se detectó el frame de SharePoint para explorar los archivos.")

            base_url = sp_frame.url.split("/_layouts/")[0].rstrip("/")
            base_endpoint = f"{base_url}/_api/v2.0/me/drive/root"

            # Extraer cookies de sesión de SharePoint
            cookies = await context.cookies()
            sp_cookies = {c["name"]: c["value"] for c in cookies if "sharepoint.com" in c.get("domain", "")}

            # 1. PASO 1: Descargar cambios desde la nube vía HTTPX + API Delta de Microsoft
            async with httpx.AsyncClient(cookies=sp_cookies, timeout=40.0) as http_client:
                downloaded = await sync_remote_changes_delta_httpx(
                    http_client, base_endpoint, local_drive, cfg, progress_callback
                )

            # 2. PASO 2: Detectar y subir archivos nuevos/modificados en local
            pending_uploads = scan_pending_uploads(local_drive, cfg)
            uploaded = 0
            if pending_uploads:
                if progress_callback:
                    progress_callback(f"Subiendo {len(pending_uploads)} archivos locales a OneDrive...", 0)
                uploaded = await upload_pending_files(
                    page, base_url, local_drive, pending_uploads, progress_callback
                )

            return {
                "downloaded": downloaded,
                "uploaded": uploaded,
                "total": downloaded + uploaded
            }
        finally:
            if should_close:
                await context.close()
