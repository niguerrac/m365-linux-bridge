import asyncio
import os
import httpx
from pathlib import Path
from urllib.parse import quote
from typing import Dict, Any, List, Optional, Callable
from playwright.async_api import async_playwright
from m365_bridge.config import get_config
from m365_bridge.browser import get_browser_context, navigate_and_handle_prompts

async def sync_folder(
    sp_frame,
    base_endpoint: str,
    folder_rel_path: str,
    local_dir: Path,
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

            if is_folder:
                if name in excluded:
                    continue
                sub_rel = f"{folder_rel_path}/{name}".strip("/")
                downloaded += await sync_folder(sp_frame, base_endpoint, sub_rel, target_path, cfg, progress_callback)
            else:
                if not download_url or size > max_mb:
                    continue
                
                # Timestamp remoto
                remote_mtime = None
                if last_modified_str:
                    try:
                        from datetime import datetime
                        dt = datetime.fromisoformat(last_modified_str.replace("Z", "+00:00"))
                        remote_mtime = dt.timestamp()
                    except Exception:
                        pass

                # Si ya existe con mismo tamaño y fecha igual o más reciente, omitir
                if target_path.exists():
                    local_stat = target_path.stat()
                    if local_stat.st_size == size:
                        if remote_mtime is not None:
                            # Tolerancia de 2 segundos para diferencias de filesystem
                            if local_stat.st_mtime >= (remote_mtime - 2):
                                continue
                        else:
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

async def run_sync(progress_callback: Optional[Callable[[str, int], None]] = None) -> int:
    cfg = get_config()
    local_drive = Path(cfg["sync_folder"])
    
    if progress_callback:
        progress_callback("Conectando con Microsoft 365...", 0)

    async with async_playwright() as p:
        context, should_close = await get_browser_context(p)
        try:
            page = await context.new_page()
            
            # Navegar y resolver prompts de sesión
            await navigate_and_handle_prompts(page, "https://outlook.cloud.microsoft/mail/", timeout=35000)
            
            if progress_callback:
                progress_callback("Abriendo OneDrive...", 0)

            # Buscar botón de OneDrive con selectores resilientes
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

            # Esperar a que cargue el frame de SharePoint embebido
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

            # Extraer endpoint dinámico de la URL real del frame de SharePoint
            base_url = sp_frame.url.split("/_layouts/")[0].rstrip("/")
            base_endpoint = f"{base_url}/_api/v2.0/me/drive/root"

            return await sync_folder(sp_frame, base_endpoint, "", local_drive, cfg, progress_callback)
        finally:
            if should_close:
                await context.close()
