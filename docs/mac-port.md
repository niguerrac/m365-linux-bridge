# 🍎 Guía de Adaptación / Port a macOS (para Agentes de IA y Desarrolladores)

Esta guía técnica está diseñada para que un agente de IA (Codex, Claude Code, Cursor, OpenCode) o un desarrollador adapte **m365-linux-bridge** a un entorno **macOS (Apple Silicon / Intel)** de forma rápida y limpia.

---

## 🎯 Objetivo

El núcleo de sincronización (HTTPX + Graph API Delta), el servidor MCP (`mcp_server.py`) y el motor Playwright son compatibles con macOS de forma nativa. Las únicas diferencias respecto a Linux residen en las rutas del sistema, la apertura de archivos en el Finder y el localizador del binario de Chromium/Chrome.

---

## 🛠️ Paso 1: Dependencias del Sistema en macOS

Se recomienda instalar `uv` y Python 3.11 o 3.12 con [Homebrew](https://brew.sh):

```bash
brew install python@3.12 uv
```

Para crear el entorno virtual e instalar las dependencias:

```bash
cd m365-linux-bridge
uv venv .venv
uv pip install -q --python .venv/bin/python playwright "mcp<2" PyQt6 PyQt6-WebEngine httpx pillow watchdog
.venv/bin/playwright install chromium
```

---

## 💻 Paso 2: Cambios de Código Necesarios

### 1. Localización de Google Chrome / Chromium (`m365_bridge/browser.py`)
En Linux se busca en `/usr/bin/` vía `shutil.which`. En macOS las aplicaciones viven como bundles en `/Applications/`.

En `find_chromium()` dentro de `m365_bridge/browser.py`, agrega el chequeo de rutas de macOS:

```python
import sys, os, shutil

def find_chromium() -> str:
    if sys.platform == "darwin":
        mac_candidates = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
            "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        ]
        for p in mac_candidates:
            if os.path.exists(p):
                return p

    for name in ("chromium", "chromium-browser", "google-chrome-stable", "google-chrome", "brave", "brave-browser"):
        path = shutil.which(name)
        if path:
            return path

    raise RuntimeError("No se encontró Chrome o Chromium en /Applications o en el PATH de macOS.")
```

---

### 2. Abrir carpetas y seleccionar archivos en Finder (`m365_bridge/gui.py`)
En Linux se usa `dolphin --select` o `xdg-open`. En macOS el estándar nativo es el comando `open`:
- `open -R /ruta/archivo`: abre la carpeta en Finder con el archivo seleccionado y destacado.
- `open /ruta/carpeta`: abre el directorio.

En `open_in_file_manager()` dentro de `m365_bridge/gui.py`:

```python
import sys, subprocess
from pathlib import Path

def open_in_file_manager(target_path: Path):
    try:
        p = Path(target_path).resolve()
        target_dir = str(p.parent if p.is_file() or not p.exists() else p)
        file_path = str(p) if p.exists() and not p.is_dir() else None

        # Soporte nativo para macOS (Finder)
        if sys.platform == "darwin":
            if file_path:
                subprocess.Popen(["open", "-R", file_path])
            else:
                subprocess.Popen(["open", target_dir])
            return

        # Linux (Dolphin / DBus / xdg-open)
        if file_path and shutil.which("dolphin"):
            subprocess.Popen(["dolphin", "--select", file_path])
            return
        # ... resto de la lógica de Linux
```

---

## 🤖 Paso 3: Configuración de Codex (MCP) en macOS

Codex lee su configuración global en `~/.codex/config.toml`. Agrega este bloque con las rutas absolutas de tu máquina:

```toml
[mcp_servers.m365]
command = "/Users/TU_USUARIO/m365-linux-bridge/.venv/bin/python"
args = ["/Users/TU_USUARIO/m365-linux-bridge/m365_bridge/mcp_server.py"]
```

*(También puedes usar el botón "Instalar en Codex" directamente desde el diálogo MCP en la ventana de Configuración).*

---

## 🔑 Paso 4: Inicio de Sesión Interactivo (Primera vez)

Para iniciar la sesión institucional y completar el 2FA en el perfil aislado de Playwright:

```bash
.venv/bin/python -c "from m365_bridge.browser import launch_interactive_login; launch_interactive_login()"
```

Inicia sesión en Outlook con tus credenciales institucionales. Una vez cargada la bandeja, cierra la ventana de Chrome y el servidor MCP ya tendrá acceso permanente sin requerir permisos de administrador de Azure AD.
