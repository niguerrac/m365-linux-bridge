import sys, os; sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import asyncio
import json
from pathlib import Path
from typing import List, Dict, Any, Optional
from mcp.server.fastmcp import FastMCP
from playwright.async_api import async_playwright
from m365_bridge.browser import get_browser_context, navigate_and_handle_prompts
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
            await navigate_and_handle_prompts(page, "https://outlook.cloud.microsoft/mail/", timeout=35000)
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
            await navigate_and_handle_prompts(page, "https://outlook.cloud.microsoft/mail/", timeout=35000)
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
async def m365_send_email(to: str, subject: str, body: str) -> str:
    """Envía un correo electrónico desde la cuenta institucional de Outlook.
    to: Dirección de correo del destinatario (ej. alumno@inacapmail.cl o correo@gmail.com)
    subject: Asunto del correo
    body: Contenido del mensaje de texto
    """
    async with async_playwright() as p:
        context, should_close = await get_browser_context(p)
        page = None
        try:
            page = await context.new_page()
            await navigate_and_handle_prompts(page, "https://outlook.cloud.microsoft/mail/", timeout=35000)
            await page.wait_for_timeout(2000)

            new_btn = page.locator("button:has-text(\"Nuevo\")").first
            await new_btn.click()
            await page.wait_for_timeout(2500)

            # Escribir destinatario y validar con Enter
            to_input = page.locator("div[aria-label=\"Para\"] input, div[aria-label=\"Para\"]").first
            await to_input.click()
            await page.keyboard.type(to)
            await page.wait_for_timeout(500)
            await page.keyboard.press("Enter")
            await page.keyboard.press("Tab")
            await page.wait_for_timeout(1000)

            # Asunto
            subject_input = page.locator("input[aria-label=\"Asunto\"], input[placeholder*=\"asunto\"]").first
            await subject_input.fill(subject)
            await page.wait_for_timeout(500)

            # Cuerpo
            body_input = page.locator("div[aria-label=\"Cuerpo del mensaje\"], div[role=\"textbox\"]").first
            await body_input.click()
            await page.keyboard.type(body)
            await page.wait_for_timeout(1500)

            # Enviar mediante Ctrl+Enter (atajo nativo de Outlook)
            await page.keyboard.press("Control+Enter")
            await page.wait_for_timeout(4000)

            return f"✓ Correo enviado exitosamente a: {to} | Asunto: {subject}"
        except Exception as e:
            return f"Error al enviar correo: {str(e)}"
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
            await navigate_and_handle_prompts(page, "https://outlook.cloud.microsoft/mail/", timeout=35000)
            await page.wait_for_timeout(2000)
            
            selectors = [
                'button[aria-label="OneDrive"]',
                '#377c982d-9686-450e-9a7c-22aeaf1bc162',
                'button[title*="OneDrive"]',
                'button:has-text("OneDrive")',
                'a[aria-label="OneDrive"]'
            ]
            btn = None
            for sel in selectors:
                try:
                    btn = await page.wait_for_selector(sel, timeout=4000)
                    if btn:
                        break
                except Exception:
                    pass

            if not btn:
                return "No se pudo acceder a la pestaña de OneDrive."
            await btn.click()
            await page.wait_for_timeout(6000)

            sp_frame = None
            for f in page.frames:
                if "sharepoint.com" in f.url:
                    sp_frame = f
                    break
            
            if not sp_frame:
                return "No se pudo conectar con el explorador de archivos de SharePoint."

            base_url = sp_frame.url.split("/_layouts/")[0].rstrip("/")
            api_endpoint = f"{base_url}/_api/v2.0/me/drive/root/children"

            items = await sp_frame.evaluate("""async (url) => {
                const res = await fetch(url, {
                    headers: { "Accept": "application/json" }
                });
                if (!res.ok) return [];
                const data = await res.json();
                return data.value ? data.value.map(i => ({ name: i.name, is_folder: !!i.folder, size: i.size })) : [];
            }""", api_endpoint)

            output = [f"=== Archivos en OneDrive ({len(items)}) ==="]
            for item in items[:30]:
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
