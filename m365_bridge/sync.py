import asyncio
import os
import time
import fcntl
import logging
from logging.handlers import RotatingFileHandler
import httpx
from pathlib import Path
from urllib.parse import urlparse, quote
from typing import Dict, Any, List, Optional, Callable, Tuple
from playwright.async_api import async_playwright
from m365_bridge.config import get_config, DATA_DIR
from m365_bridge.browser import get_browser_context, navigate_and_handle_prompts
from m365_bridge.sync_db import (
    record_synced_file, get_file_state, list_all_tracked_files,
    get_kv, set_kv, remove_file_record, get_file_by_remote_id,
    record_synced_folder
)

_sync_logger = None

def get_sync_logger():
    global _sync_logger
    if _sync_logger is None:
        logger = logging.getLogger("m365_bridge.sync")
        logger.setLevel(logging.INFO)
        if not logger.handlers:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            log_file = DATA_DIR / "sync.log"
            handler = RotatingFileHandler(log_file, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
            formatter = logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
            handler.setFormatter(formatter)
            logger.addHandler(handler)
        _sync_logger = logger
    return _sync_logger

class SyncLock:
    def __init__(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.lock_file = DATA_DIR / "sync.lock"
        self._fd = None

    def __enter__(self):
        self._fd = open(self.lock_file, "w")
        try:
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (IOError, BlockingIOError):
            self._fd.close()
            self._fd = None
            raise RuntimeError("Ya hay una sincronización en curso por otro proceso (GUI o Bandeja).")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._fd:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
                self._fd.close()
            except Exception:
                pass
            self._fd = None

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
) -> Tuple[List[str], List[str]]:
    """Sincroniza cambios desde la nube hacia el disco local utilizando la API Delta de OneDrive a máxima velocidad HTTPX."""
    local_drive.mkdir(parents=True, exist_ok=True)
    max_mb = cfg.get("max_file_size_mb", 200) * 1024 * 1024
    excluded = set(cfg.get("excluded_folders", []))

    saved_delta = get_kv("onedrive_delta_link")
    curr_url = saved_delta if saved_delta else f"{base_endpoint}/delta"
    is_initial_full_scan = not bool(saved_delta)
    seen_remote_paths = set()
    
    if progress_callback:
        mode_text = "Comprobando cambios en la nube..." if saved_delta else "Indexación inicial en la nube..."
        progress_callback(mode_text, -1)

    downloaded_files: List[str] = []
    deleted_locally: List[str] = []
    total_items = 0

    while curr_url:
        try:
            resp = await client.get(curr_url, timeout=35.0)
        except Exception as err:
            if progress_callback:
                progress_callback(f"Error de red: {err}", len(downloaded_files))
            break

        if resp.status_code in (400, 401, 403, 404, 410) and saved_delta:
            # Token delta expirado o inválido en Microsoft, reiniciar escaneo delta
            set_kv("onedrive_delta_link", "")
            curr_url = f"{base_endpoint}/delta"
            saved_delta = None
            is_initial_full_scan = True
            seen_remote_paths.clear()
            continue

        if resp.status_code != 200:
            break

        body = resp.json()
        items = body.get("value", [])
        total_items += len(items)

        for item in items:
            item_id = item.get("id")
            name = item.get("name", "")

            # 1. Borrado remoto: el archivo o carpeta fue eliminado en OneDrive
            if "deleted" in item:
                target_rel = None
                if item_id:
                    rec = get_file_by_remote_id(item_id)
                    if rec:
                        target_rel = rec.get("rel_path")
                if not target_rel and name:
                    parent_path = item.get("parentReference", {}).get("path", "")
                    rel_dir = parent_path.split("/drive/root:")[-1].strip("/")
                    target_rel = f"{rel_dir}/{name}".strip("/")

                if target_rel:
                    target_path = local_drive / target_rel
                    if target_path.is_file():
                        try:
                            target_path.unlink()
                            deleted_locally.append(target_rel)
                        except Exception as e:
                            print(f"Error borrando archivo local {target_rel}: {e}")
                    elif target_path.is_dir():
                        try:
                            import shutil
                            shutil.rmtree(target_path, ignore_errors=True)
                            deleted_locally.append(target_rel)
                        except Exception as e:
                            print(f"Error borrando carpeta local {target_rel}: {e}")
                    remove_file_record(target_rel)
                continue

            if not name or name == "root":
                continue

            parent_path = item.get("parentReference", {}).get("path", "")
            rel_dir = parent_path.split("/drive/root:")[-1].strip("/")
            rel_str = f"{rel_dir}/{name}".strip("/")
            target_path = local_drive / rel_str

            if is_initial_full_scan:
                seen_remote_paths.add(rel_str)

            # 2. Carpeta remota
            if item.get("folder"):
                if any(part in excluded for part in target_path.parts):
                    continue
                target_path.mkdir(parents=True, exist_ok=True)
                record_synced_folder(rel_str, item_id)
                continue

            # 3. Archivo remoto
            if any(part in excluded for part in target_path.parts):
                continue

            # Si el archivo fue renombrado o movido en la nube
            if item_id:
                old_rec = get_file_by_remote_id(item_id)
                if old_rec and old_rec.get("rel_path") and old_rec.get("rel_path") != rel_str:
                    old_path = local_drive / old_rec["rel_path"]
                    if old_path.exists():
                        target_path.parent.mkdir(parents=True, exist_ok=True)
                        try:
                            old_path.rename(target_path)
                            remove_file_record(old_rec["rel_path"])
                        except Exception:
                            pass

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
                            direction="down",
                            remote_id=item_id
                        )
                        continue
                    elif remote_mtime is None:
                        continue

            if not download_url:
                continue

            if progress_callback:
                progress_callback(f"Descargando: {name}", len(downloaded_files))

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
                            direction="down",
                            remote_id=item_id
                        )
                        downloaded_files.append(rel_str)
                        if progress_callback:
                            progress_callback(f"Guardado: {name}", len(downloaded_files))
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
                    progress_callback(f"Error {name}: {err}", len(downloaded_files))

        # Avanzar paginación de Delta
        if body.get("@odata.nextLink"):
            curr_url = body["@odata.nextLink"]
            if progress_callback:
                progress_callback(f"Indexando nube ({total_items} items)...", len(downloaded_files))
        elif body.get("@odata.deltaLink"):
            set_kv("onedrive_delta_link", body["@odata.deltaLink"])
            curr_url = None
            break
        else:
            curr_url = None
            break

    # Si se completó un escaneo total, reconciliar archivos rastreados que fueron borrados en la nube
    if is_initial_full_scan and seen_remote_paths:
        all_tracked = list_all_tracked_files()
        for tracked_rel, row in all_tracked.items():
            if row.get("local_size", 0) >= 0 and tracked_rel not in seen_remote_paths:
                local_f = local_drive / tracked_rel
                if local_f.is_file():
                    try:
                        local_f.unlink()
                        deleted_locally.append(tracked_rel)
                    except Exception:
                        pass
                elif local_f.is_dir():
                    try:
                        import shutil
                        shutil.rmtree(local_f, ignore_errors=True)
                        deleted_locally.append(tracked_rel)
                    except Exception:
                        pass
                remove_file_record(tracked_rel)

    return downloaded_files, deleted_locally

def scan_pending_deletions(local_drive: Path) -> List[str]:
    """Detecta archivos que estaban en la base de datos pero fueron borrados localmente en Linux."""
    tracked = list_all_tracked_files()
    deleted = []
    for rel_path in tracked:
        local_path = local_drive / rel_path
        if not local_path.exists():
            deleted.append(rel_path)
    return deleted

async def recycle_remote_files(
    client: httpx.AsyncClient,
    base_url: str,
    tenant_path: str,
    deleted_rel_paths: List[str],
    progress_callback: Optional[Callable[[str, int], None]] = None
) -> List[str]:
    """Envía los archivos eliminados localmente a la Papelera de Reciclaje de OneDrive."""
    if not deleted_rel_paths:
        return []

    try:
        r_ctx = await client.post(
            f"{base_url}/_api/contextinfo",
            headers={"Accept": "application/json;odata=verbose", "User-Agent": "Mozilla/5.0"}
        )
        digest = r_ctx.json().get("d", {}).get("GetContextWebInformation", {}).get("FormDigestValue")
    except Exception:
        digest = None

    recycled: List[str] = []
    for rel_path in deleted_rel_paths:
        if progress_callback:
            progress_callback(f"Papelera nube: {Path(rel_path).name}", len(recycled))
        
        server_rel = f"{tenant_path}/Documents/{rel_path}"
        recycle_url = f"{base_url}/_api/web/GetFileByServerRelativeUrl('{server_rel}')/recycle"
        headers = {
            "Accept": "application/json;odata=verbose",
            "User-Agent": "Mozilla/5.0"
        }
        if digest:
            headers["X-RequestDigest"] = digest

        try:
            r = await client.post(recycle_url, headers=headers)
            # 200 = reciclado exitoso; 404 = ya no existía en la nube
            if r.status_code in (200, 404):
                remove_file_record(rel_path)
                recycled.append(rel_path)
        except Exception:
            pass

    return recycled

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

async def ensure_remote_folder(
    client: httpx.AsyncClient,
    base_url: str,
    tenant_path: str,
    rel_dir: str,
    digest: str,
    ensured_cache: set
):
    """Crea recursivamente las carpetas remotas en SharePoint si no existen."""
    if not rel_dir or rel_dir == ".":
        return
    parts = Path(rel_dir).parts
    current_acc = ""
    for part in parts:
        current_acc = f"{current_acc}/{part}" if current_acc else part
        if current_acc in ensured_cache:
            continue
        server_rel = f"{tenant_path}/Documents/{current_acc}"
        payload = {"__metadata": {"type": "SP.Folder"}, "ServerRelativeUrl": server_rel}
        headers = {
            "Accept": "application/json;odata=verbose",
            "Content-Type": "application/json;odata=verbose",
            "X-RequestDigest": digest
        }
        try:
            await client.post(f"{base_url}/_api/web/folders", json=payload, headers=headers)
        except Exception:
            pass
        ensured_cache.add(current_acc)

async def upload_pending_files(
    client: httpx.AsyncClient,
    base_url: str,
    tenant_path: str,
    local_drive: Path,
    pending_files: List[Path],
    digest: str,
    progress_callback: Optional[Callable[[str, int], None]] = None
) -> List[str]:
    """Sube archivos locales hacia OneDrive mediante la API REST de SharePoint a alta velocidad."""
    if not pending_files:
        return []

    uploaded_files: List[str] = []
    ensured_folders: set = set()
    total = len(pending_files)
    logger = get_sync_logger()

    up_headers = {
        "Accept": "application/json;odata=verbose",
        "Content-Type": "application/octet-stream",
        "X-RequestDigest": digest
    }

    for idx, f in enumerate(pending_files, 1):
        rel_str = str(f.relative_to(local_drive))
        rel_dir = str(f.parent.relative_to(local_drive))
        filename = f.name

        if progress_callback:
            progress_callback(f"Subiendo ({idx}/{total}): {filename}", len(uploaded_files))
        logger.info(f"Subiendo archivo ({idx}/{total}): {rel_str}")

        try:
            # 1. Asegurar que las carpetas remotas existan
            await ensure_remote_folder(client, base_url, tenant_path, rel_dir, digest, ensured_folders)

            # 2. Subir contenido del archivo mediante SharePoint REST API
            folder_server_rel = f"{tenant_path}/Documents" if rel_dir in (".", "") else f"{tenant_path}/Documents/{rel_dir.replace('\\', '/')}"
            up_url = f"{base_url}/_api/web/GetFolderByServerRelativeUrl('{folder_server_rel}')/Files/add(url='{quote(filename)}',overwrite=true)"

            content = f.read_bytes()
            r_up = await client.post(up_url, content=content, headers=up_headers)
            if r_up.status_code in (200, 201):
                d = r_up.json().get("d", {})
                remote_id = d.get("UniqueId")
                st = f.stat()
                record_synced_file(
                    rel_path=rel_str,
                    local_mtime=st.st_mtime,
                    local_size=st.st_size,
                    remote_mtime=st.st_mtime,
                    remote_size=st.st_size,
                    direction="up",
                    remote_id=remote_id
                )
                uploaded_files.append(rel_str)
                logger.info(f"Subido exitosamente: {rel_str} (ID: {remote_id})")
            else:
                logger.warning(f"Error HTTP {r_up.status_code} al subir {rel_str}: {r_up.text[:200]}")
        except Exception as err:
            logger.error(f"Excepción al subir {rel_str}: {err}")
            if progress_callback:
                progress_callback(f"Error al subir {filename}: {err}", len(uploaded_files))

    return uploaded_files

async def run_sync(progress_callback: Optional[Callable[[str, int], None]] = None) -> Dict[str, Any]:
    logger = get_sync_logger()
    logger.info("=== Iniciando ciclo de sincronización ===")

    with SyncLock():
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
                tenant_path = urlparse(base_url).path.rstrip("/")
                base_endpoint = f"{base_url}/_api/v2.0/me/drive/root"

                # Extraer cookies de sesión de SharePoint
                cookies = await context.cookies()
                sp_cookies = {c["name"]: c["value"] for c in cookies if "sharepoint.com" in c.get("domain", "")}

                downloaded_files: List[str] = []
                deleted_locally_files: List[str] = []
                deleted_files: List[str] = []
                uploaded_files: List[str] = []

                client_headers = {
                    "Accept": "application/json",
                    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    "Referer": sp_frame.url
                }

                async with httpx.AsyncClient(cookies=sp_cookies, headers=client_headers, timeout=40.0) as http_client:
                    # Obtener FormDigestValue para operaciones REST de modificación
                    digest = None
                    try:
                        r_ctx = await http_client.post(
                            f"{base_url}/_api/contextinfo",
                            headers={"Accept": "application/json;odata=verbose"}
                        )
                        digest = r_ctx.json().get("d", {}).get("GetContextWebInformation", {}).get("FormDigestValue")
                    except Exception as e:
                        logger.warning(f"No se pudo obtener FormDigestValue: {e}")

                    # 1. PASO 1: Descargar cambios y aplicar borrados desde la nube vía HTTPX + API Delta
                    downloaded_files, deleted_locally_files = await sync_remote_changes_delta_httpx(
                        http_client, base_endpoint, local_drive, cfg, progress_callback
                    )

                    # 2. PASO 2: Detectar y enviar a la papelera remota archivos eliminados en Linux
                    pending_deletions = scan_pending_deletions(local_drive)
                    if pending_deletions:
                        if progress_callback:
                            progress_callback(f"Reciclando {len(pending_deletions)} archivos en la nube...", 0)
                        deleted_files = await recycle_remote_files(
                            http_client, base_url, tenant_path, pending_deletions, progress_callback
                        )

                    # 3. PASO 3: Subir archivos nuevos/modificados en Linux hacia OneDrive vía SharePoint REST
                    pending_uploads = scan_pending_uploads(local_drive, cfg)
                    if pending_uploads and digest:
                        if progress_callback:
                            progress_callback(f"Subiendo {len(pending_uploads)} archivos locales a OneDrive...", 0)
                        uploaded_files = await upload_pending_files(
                            http_client, base_url, tenant_path, local_drive, pending_uploads, digest, progress_callback
                        )

                total_deleted = len(deleted_files) + len(deleted_locally_files)
                summary = {
                    "downloaded": len(downloaded_files),
                    "uploaded": len(uploaded_files),
                    "deleted": total_deleted,
                    "total": len(downloaded_files) + len(uploaded_files) + total_deleted,
                    "downloaded_files": downloaded_files,
                    "uploaded_files": uploaded_files,
                    "deleted_files": deleted_files,
                    "deleted_locally_files": deleted_locally_files,
                    "timestamp": time.strftime("%H:%M:%S"),
                    "error": None
                }
                logger.info(f"Sincronización finalizada exitosamente: ↓{len(downloaded_files)} ↑{len(uploaded_files)} 🗑{total_deleted}")
                return summary
            finally:
                if should_close:
                    await context.close()
