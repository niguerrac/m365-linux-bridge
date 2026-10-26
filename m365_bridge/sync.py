import asyncio
import os
import httpx
from pathlib import Path
from typing import Dict, Any, List, Optional
from playwright.async_api import async_playwright
from m365_bridge.config import get_config
from m365_bridge.browser import get_browser_context

async def sync_folder(sp_frame, folder_rel_path: str, local_dir: Path, cfg: Dict[str, Any]) -> int:
    local_dir.mkdir(parents=True, exist_ok=True)
    endpoint = "https://inacapmailcl-my.sharepoint.com/personal/nilson_guerra_inacapmail_cl/_api/v2.0/me/drive/root/children"
    if folder_rel_path:
        endpoint = f"https://inacapmailcl-my.sharepoint.com/personal/nilson_guerra_inacapmail_cl/_api/v2.0/me/drive/root:/{folder_rel_path}:/children"

    items = await sp_frame.evaluate("""async (url) => {
        try {
            const res = await fetch(url, { headers: { "Accept": "application/json" } });
            if (!res.ok) return [];
            const data = await res.json();
            return data.value ? data.value.map(item => ({
                name: item.name,
                is_folder: !!item.folder,
                size: item.size || 0,
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
            name = item["name"]
            is_folder = item["is_folder"]
            download_url = item["download_url"]
            size = item["size"]
            target_path = local_dir / name

            if is_folder:
                if name in excluded:
                    continue
                sub_rel = f"{folder_rel_path}/{name}".strip("/")
                downloaded += await sync_folder(sp_frame, sub_rel, target_path, cfg)
            else:
                if not download_url or size > max_mb:
                    continue
                if not target_path.exists() or target_path.stat().st_size != size:
                    try:
                        resp = await client.get(download_url, follow_redirects=True)
                        if resp.status_code == 200:
                            target_path.write_bytes(resp.content)
                            downloaded += 1
                    except Exception:
                        pass
    return downloaded

async def run_sync() -> int:
    cfg = get_config()
    local_drive = Path(cfg["sync_folder"])
    
    async with async_playwright() as p:
        context, should_close = await get_browser_context(p)
        try:
            page = await context.new_page()
            await page.goto("https://outlook.cloud.microsoft/mail/", wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(2000)
            btn = await page.wait_for_selector("button[aria-label=\"OneDrive\"]", timeout=10000)
            if not btn:
                return 0
            await btn.click()
            await page.wait_for_timeout(7000)

            sp_frame = None
            for f in page.frames:
                if "sharepoint.com" in f.url:
                    sp_frame = f
                    break
            
            if not sp_frame:
                return 0

            return await sync_folder(sp_frame, "", local_drive, cfg)
        finally:
            if should_close:
                await context.close()
