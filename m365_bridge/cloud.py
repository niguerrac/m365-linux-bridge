"""Sesión HTTP con el OneDrive de SharePoint reutilizando las cookies del navegador.

El ciclo de sync descubre la URL personal de SharePoint (entrando por Outlook) y la deja en
la base (`sharepoint_base_url`). Acá se usa esa URL para abrir una sesión rápida: entra
directo a OneDrive, toma las cookies y arma un cliente httpx.
"""
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator

import httpx
from playwright.async_api import async_playwright

from m365_bridge.browser import get_browser_context
from m365_bridge.sync_db import get_kv

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


@dataclass
class SharePointSession:
    client: httpx.AsyncClient
    base_url: str

    @property
    def drive_endpoint(self) -> str:
        return f"{self.base_url}/_api/v2.0/me/drive"


@asynccontextmanager
async def open_sharepoint_session() -> AsyncIterator[SharePointSession]:
    base_url = get_kv("sharepoint_base_url")
    if not base_url:
        raise RuntimeError(
            "Todavía no se conoce tu OneDrive: ejecuta una sincronización una vez y vuelve a intentar."
        )
    async with async_playwright() as p:
        context, should_close = await get_browser_context(p)
        page = None
        try:
            page = await context.new_page()
            await page.goto(f"{base_url}/_layouts/15/onedrive.aspx", timeout=45000)
            await page.wait_for_timeout(1500)
            cookies = {
                c["name"]: c["value"]
                for c in await context.cookies()
                if "sharepoint.com" in c.get("domain", "")
            }
            headers = {"Accept": "application/json", "User-Agent": UA, "Referer": page.url}
            async with httpx.AsyncClient(cookies=cookies, headers=headers, timeout=40.0) as client:
                r = await client.get(f"{base_url}/_api/v2.0/me/drive/root?$select=id")
                if r.status_code != 200:
                    raise RuntimeError(
                        f"La sesión de Microsoft 365 no es válida (HTTP {r.status_code}). "
                        "Reautentica desde Configuración."
                    )
                yield SharePointSession(client=client, base_url=base_url)
        finally:
            if page:
                try:
                    await page.close()
                except Exception:
                    pass
            if should_close:
                await context.close()
