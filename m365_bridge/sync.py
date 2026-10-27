import asyncio
import os
import httpx
from pathlib import Path
from urllib.parse import urlparse, quote
from typing import Dict, Any, List, Optional, Callable
from playwright.async_api import async_playwright
from m365_bridge.config import get_config
from m365_bridge.browser import get_browser_context, navigate_and_handle_prompts
from m365_bridge.sync_db import record_synced_file, get_file_state, list_all_tracked_files

IGNORED_EXTENSIONS = {".tmp", ".crdownload", ".swp", ".bak"}
IGNORED_PREFIXES = (".~", "~$", ".")

def is_ignored_file(p: Path, excluded_folders: set) -> bool:
    if p.name.startswith(IGNORED_PREFIXES) or p.suffix.lower() in IGNORED_EXTENSIONS:
        return True
    return any(part in excluded_folders for part in p.parts)

async def download_remote_folder(
    sp_frame,
    base_endpoint: str,
    folder_rel_path: str,
    local_dir: Path,
    local_drive_root: Path,
    cfg: Dict[str, Any],
    progress_callback: Optional[Callable[[str, int], None]] = None
) -> int:
    local_dir.mkdir(parents=True, exist_ok=True)
    
    if folder_rel_path:
        encoded_path = "/".join(quote(part) for part in folder_rel_path.strip("/").split("/"))
        endpoint = f"{base_endpoint}:/{encoded_path}:/children"
    else:
        endpoint = f"{base_endpoint}/children"

    if progress_callback:
        progress_callback(f"Explorando {folder_rel_path or 'raíz'}...", -1)

    items = await sp_frame.evaluate("""async (url) => {
        try {
            const res = await fetch(url, { headers: { "Accept": "application/json" } });
            if (!res.ok) return [];
            const data = await res.json();
            return data.value ? data.value.map(item => ({
                name: item.name,
                is_folder: !!item.folder,
                size: item.size || 0,
                last_modified: item.lastModifiedDateTime || null,
                download_url: item["@content.downloadUrl"] || item["@microsoft.graph.downloadUrl"]
            })) : [];
        } catch(e) {
            return [];
        }
    }""", endpoint)

    downloaded = 0
    max_mb = cfg.get("max_file_size_mb", 200) * 1024 * 1024
    excluded = set(cfg.get("excluded_folders", []))

    async with httpx.AsyncClient(timeout=60.0) as client:
        for item in items:
            name = item.get("name", "")
            if not name:
                continue

            is_folder = item.get("is_folder", False)
            download_url = item.get("download_url")
            size = item.get("size", 0)
            last_modified_str = item.get("last_modified")
            target_path = local_dir / name
            rel_str = str(target_path.relative_to(local_drive_root))

            if is_folder:
                if name in excluded:
                    continue
                sub_rel = f"{folder_rel_path}/{name}".strip("/")
                downloaded += await download_remote_folder(
                    sp_frame, base_endpoint, sub_rel, target_path, local_drive_root, cfg, progress_callback
                )
            else:
                if not download_url or size > max_mb:
                    continue
                
                remote_mtime = None
                if last_modified_str:
                    try:
                        from datetime import datetime
                        dt = datetime.fromisoformat(last_modified_str.replace("Z", "+00:00"))
                        remote_mtime = dt.timestamp()
                    except Exception:
                        pass

                # Verificar si ya está al día
                if target_path.exists():
                    local_stat = target_path.stat()
                    if local_stat.st_size == size:
                        if remote_mtime is not None and local_stat.st_mtime >= (remote_mtime - 2):
                            record_synced_file(
                                rel_path=rel_str,
                                local_mtime=local_stat.st_mtime,
                                local_size=local_stat.st_size,
                                remote_mtime=remote_mtime,
                                remote_size=size,
                                direction="down"
                            )
                            continue
                        elif remote_mtime is None:
                            continue

                if progress_callback:
                    progress_callback(f"Descargando: {name}", downloaded)
                
                try:
                    temp_path = target_path.with_suffix(target_path.suffix + ".tmp")
                    async with client.stream("GET", download_url, follow_redirects=True) as resp:
                        if resp.status_code == 200:
                            with open(temp_path, "wb") as f:
                                async for chunk in resp.aiter_bytes(chunk_size=1024 * 1024):
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

    return downloaded

def scan_pending_uploads(local_drive: Path, cfg: Dict[str, Any]) -> List[Path]:
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
                # Archivo nuevo local
                pending.append(p)
            else:
                # Modificado localmente después de la última sincronización
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
    if not pending_files:
        return 0

    tenant_path = urlparse(base_url).path.rstrip("/")
    uploaded = 0

    # Agrupar archivos por directorio relativo
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

            # Carga por lotes de archivos en la carpeta
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

            # Resolver diálogo de conflicto si aparece "Reemplazar"
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
                '#377c982d-9686-450e-9a7c-22aeaf1bc162',
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

            # 1. PASO 1: Descargar cambios desde la nube hacia el disco local
            if progress_callback:
                progress_callback("Sincronizando desde la nube...", 0)
            downloaded = await download_remote_folder(
                sp_frame, base_endpoint, "", local_drive, local_drive, cfg, progress_callback
            )

            # 2. PASO 2: Detectar y subir archivos nuevos/modificados en local hacia OneDrive
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
