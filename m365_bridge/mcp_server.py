import asyncio
import json
from pathlib import Path
from typing import List, Dict, Any, Optional
from mcp.server.fastmcp import FastMCP
from playwright.async_api import async_playwright
from m365_bridge.browser import get_browser_context
from m365_bridge.config import get_config

mcp = FastMCP("m365_linux_bridge")

@mcp.tool()
async def m365_list_emails(count: int = 10) -> str:
    """Obtiene los últimos correos recibidos en la bandeja de entrada institucional."""
    async with async_playwright() as p:
        context, should_close = await get_browser_context(p)
        page = None
        try:
            page = await context.new_page()
            await page.goto("https://outlook.cloud.microsoft/mail/", wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_selector('div[role="listbox"], div[role="option"], div[data-convid]', timeout=25000)
            await page.wait_for_timeout(2000)

            emails = await page.evaluate('''(count) => {
                const items = Array.from(document.querySelectorAll('div[data-convid], div[role="option"]'));
                const results = [];
                for (let item of items.slice(0, count)) {
                    const text = item.innerText.split('\\n').filter(t => t.trim().length > 0);
                    if (text.length >= 2) {
                        results.push({
                            sender: text[0] || 'Desconocido',
                            subject: text[1] || 'Sin asunto',
                            preview: text.slice(2, 4).join(' - ')
                        });
                    }
                }
                return results;
            }''', count)

            if not emails:
                return "No se encontraron correos en la vista actual."

            output = [f"=== Últimos correos M365 ({len(emails)}) ==="]
            for i, email in enumerate(emails, 1):
                output.append(f"{i}. De: {email['sender']}\n   Asunto: {email['subject']}\n   Vista previa: {email['preview']}\n")
            return "\n".join(output)
        finally:
            if page:
                await page.close()
            if should_close:
                await context.close()

@mcp.tool()
async def m365_search_emails(query: str) -> str:
    """Busca correos en Outlook institucional por palabra clave, remitente o tema."""
    async with async_playwright() as p:
        context, should_close = await get_browser_context(p)
        page = None
        try:
            page = await context.new_page()
            await page.goto("https://outlook.cloud.microsoft/mail/", wait_until="domcontentloaded", timeout=30000)
            search_input = await page.wait_for_selector('input[aria-label*="Buscar"], input[placeholder*="Buscar"]', timeout=20000)
            await search_input.fill(query)
            await page.keyboard.press("Enter")
            await page.wait_for_timeout(4000)

            emails = await page.evaluate('''() => {
                const items = Array.from(document.querySelectorAll('div[data-convid], div[role="option"]'));
                const results = [];
                for (let item of items.slice(0, 10)) {
                    const text = item.innerText.split('\\n').filter(t => t.trim().length > 0);
                    if (text.length >= 2) {
                        results.push({
                            sender: text[0] || 'Desconocido',
                            subject: text[1] || 'Sin asunto',
                            preview: text.slice(2, 4).join(' - ')
                        });
                    }
                }
                return results;
            }''')

            if not emails:
                return f"No se encontraron correos para la búsqueda: '{query}'."

            output = [f"=== Resultados de búsqueda para '{query}' ({len(emails)}) ==="]
            for i, email in enumerate(emails, 1):
                output.append(f"{i}. De: {email['sender']}\n   Asunto: {email['subject']}\n   Vista previa: {email['preview']}\n")
            return "\n".join(output)
        finally:
            if page:
                await page.close()
            if should_close:
                await context.close()

@mcp.tool()
async def m365_list_files() -> str:
    """Lista los archivos y documentos disponibles en el OneDrive institucional."""
    async with async_playwright() as p:
        context, should_close = await get_browser_context(p)
        page = None
        try:
            page = await context.new_page()
            await page.goto("https://outlook.cloud.microsoft/mail/", wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(2000)
            btn = await page.wait_for_selector("button[aria-label=\"OneDrive\"]", timeout=10000)
            if not btn:
                return "No se pudo acceder a la pestaña de OneDrive."
            await btn.click()
            await page.wait_for_timeout(7000)

            sp_frame = None
            for f in page.frames:
                if "sharepoint.com" in f.url:
                    sp_frame = f
                    break
            
            if not sp_frame:
                return "No se pudo conectar con el explorador de archivos."

            items = await sp_frame.evaluate("""async () => {
                const res = await fetch("https://inacapmailcl-my.sharepoint.com/personal/nilson_guerra_inacapmail_cl/_api/v2.0/me/drive/root/children", {
                    headers: { "Accept": "application/json" }
                });
                const data = await res.json();
                return data.value ? data.value.map(i => ({ name: i.name, is_folder: !!i.folder, size: i.size })) : [];
            }""")

            output = [f"=== Archivos en OneDrive ({len(items)}) ==="]
            for item in items[:25]:
                t = "[Carpeta]" if item["is_folder"] else "[Archivo]"
                output.append(f"{t} {item['name']}")
            return "\n".join(output)
        finally:
            if page:
                await page.close()
            if should_close:
                await context.close()

if __name__ == "__main__":
    mcp.run()
