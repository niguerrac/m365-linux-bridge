# 🐧 M365 Linux Bridge

Una solución integral, moderna y de código abierto para utilizar la suite de **Microsoft 365 (Outlook, OneDrive y MCP para Inteligencia Artificial)** en cualquier distribución **Linux**, superando las políticas estrictas de restricción de consentimiento de administradores en Microsoft Entra ID / Azure AD (*"Need admin approval" / "Se necesita aprobación del administrador"*).

---

## 🌟 Características Principales

* **🔓 Bypass Transparente de Azure AD:** No requiere permisos de administrador de la institución ni registro de aplicaciones OAuth de terceros.
* **🖥️ Interfaz Integrada (PyQt6-WebEngine):** Outlook y OneDrive corren embebidos en una sola ventana nativa y moderna de escritorio.
* **📂 Sincronización Bidireccional de OneDrive en Tiempo Real:** 
  * Motor bidireccional automático (Descarga, Subida y Eliminación).
  * API Delta de Microsoft Graph a ultra-alta velocidad (HTTPX) con vigilancia local por `inotify` (Watchdog).
  * Protección contra borrado accidental: los archivos eliminados localmente van a la Papelera de Reciclaje de OneDrive, y los borrados en la nube se limpian en el disco local.
  * Diálogo interactivo de estado de sincronización con pestañas detalladas (Subidos, Descargados, Eliminados o traza de errores).
* **🤖 Servidor MCP Completo para IA (Lectura y Envío):**
  * `m365_list_emails`: Lista correos recientes con remitente, asunto y fragmento.
  * `m365_search_emails`: Búsqueda de correos por palabras clave, personas o fechas.
  * `m365_send_email`: Redacción y despacho directo de correos desde la cuenta institucional.
  * `m365_list_files`: Explorador de archivos y carpetas de OneDrive.
* **⚡ Instalador de MCP en 1 Clic:** Botón directo en la barra de herramientas para registrar el servidor en **Claude Desktop**, **Google Antigravity (AGY)** y **OpenCode**.
* **📌 Icono de Bandeja del Sistema (Tray):** Soporte oficial para KDE Plasma (Wayland) y X11 con acciones rápidas de sincronización.

---

## 📦 Descarga e Instalación

### Opción 1: AppImage Portable (Recomendada - Sin instalación)
Compatible con cualquier distribución (Ubuntu, Debian, Fedora, Arch, CachyOS, openSUSE, etc.):

1. Descarga el ejecutable desde la sección de **[Releases](https://github.com/niguerrac/m365-linux-bridge/releases)**:
   ```bash
   chmod +x M365-Linux-Bridge-x86_64.AppImage
   ./M365-Linux-Bridge-x86_64.AppImage
   ```

---

### Opción 2: Instalación desde el Código Fuente

```bash
git clone https://github.com/niguerrac/m365-linux-bridge.git
cd m365-linux-bridge
./install.sh
```

El script `install.sh`:
* Crea automáticamente el entorno virtual con `uv`.
* Instala las dependencias necesarias (`PyQt6`, `PyQt6-WebEngine`, `playwright`, `mcp`, `httpx`).
* Crea los accesos directos en el menú de aplicaciones de tu escritorio (`M365 Linux Bridge`).

---

## ⚙️ Configuración

Puedes acceder a la configuración gráfica desde el botón **⚙** en la barra superior o editar directamente `~/.config/m365-bridge/config.json`:

```json
{
  "institution_name": "Mi Institución",
  "sync_folder": "/home/usuario/OneDrive",
  "max_file_size_mb": 200,
  "excluded_folders": ["Recordings", "Meetings", "Temporales"]
}
```

---

## 🤖 Conectar con Asistentes de IA (MCP)

Puedes hacer clic en el botón verde **`🤖 MCP / IA`** dentro de la barra de la aplicación para autoinstalarlo, o configurarlo manualmente:

### Google Antigravity (AGY) (`~/.gemini/config/mcp_config.json`)
```json
{
  "mcpServers": {
    "m365": {
      "command": "/ruta/a/m365-linux-bridge/.venv/bin/python",
      "args": ["/ruta/a/m365-linux-bridge/m365_bridge/mcp_server.py"]
    }
  }
}
```

### Claude Desktop (`~/.config/Claude/claude_desktop_config.json`)
```json
{
  "mcpServers": {
    "m365": {
      "command": "/ruta/a/m365-linux-bridge/.venv/bin/python",
      "args": ["/ruta/a/m365-linux-bridge/m365_bridge/mcp_server.py"]
    }
  }
}
```

---

## 🛣️ Próximos Pasos (Roadmap)
* [x] Soporte para subida bidireccional y borrado sincronizado de archivos entre Linux y OneDrive.
* [x] Integración de API Delta de OneDrive e Inotify para sincronizaciones instantáneas con consumo cero de CPU.
* [ ] Empaquetado en AUR (`m365-linux-bridge-bin`) para usuarios de Arch / CachyOS.
* [ ] Integración con el Calendario y reuniones de Microsoft Teams vía MCP.

---

## 📄 Licencia
Este proyecto está bajo la Licencia **MIT**. Consulta el archivo `LICENSE` para más detalles.
