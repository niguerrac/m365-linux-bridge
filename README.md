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
* **⚡ Instalador de MCP en 1 Clic:** Botón en Configuración para registrar el servidor en **Claude Desktop**, **Google Antigravity (AGY)** y **OpenCode**.
* **📌 Icono de Bandeja del Sistema (Tray):** Soporte oficial para KDE Plasma (Wayland) y X11 con acciones rápidas de sincronización.

---

## 📦 Descarga e Instalación

### Opción 1: Instalador de una línea (Recomendada)

```bash
curl -fsSL https://raw.githubusercontent.com/niguerrac/m365-linux-bridge/main/install.sh | bash
```

* Instala el **último release** en `~/.local/share/m365-linux-bridge` (entorno con `uv`, dependencias incluidas).
* Crea el acceso **M365 Linux Bridge** con su icono en el menú de aplicaciones y deja la **bandeja** (sync automático) en el inicio de sesión.
* **Para actualizar, vuelve a ejecutar el mismo comando.** La app también avisa al abrir cuando hay una versión nueva, y en **Configuración → Buscar actualizaciones** muestra la versión actual y actualiza con un clic.

Variables opcionales: `M365_INSTALL_DIR` (otra carpeta), `M365_VERSION=v1.0.4` (fijar una versión).

---

### Requisitos del sistema

* **git** y **Chromium** (o Chrome/Brave): Chromium se usa para el inicio de sesión interactivo de Microsoft.
  * Arch / CachyOS: `sudo pacman -S git chromium` · Debian / Ubuntu: `sudo apt install git chromium` · Fedora: `sudo dnf install git chromium`
* Solo para el AppImage: **FUSE 2** (ver abajo).

---

### Opción 2: AppImage portable (sin instalación)

Descarga `M365-Linux-Bridge-x86_64.AppImage` desde **[Releases](https://github.com/niguerrac/m365-linux-bridge/releases)**:
```bash
chmod +x M365-Linux-Bridge-x86_64.AppImage
./M365-Linux-Bridge-x86_64.AppImage
```
El AppImage trae la ventana principal (sin bandeja ni sync automático) y se actualiza solo desde **Configuración → Buscar actualizaciones**. No se agrega al menú por sí mismo: usa Gear Lever o AppImageLauncher si lo quieres ahí.

> **⚠️ Si ves `dlopen(): error loading libfuse.so.2`:** los AppImage necesitan FUSE 2, que muchas distros ya no instalan por defecto (Arch, CachyOS, Fedora, Ubuntu 22.04+). Soluciones:
>
> | Distro | Comando |
> |---|---|
> | Arch / CachyOS | `sudo pacman -S fuse2` |
> | Ubuntu / Debian | `sudo apt install libfuse2` (en Ubuntu 24.04: `libfuse2t64`) |
> | Fedora | `sudo dnf install fuse-libs` |
>
> Sin instalar nada, también puedes ejecutarlo así: `APPIMAGE_EXTRACT_AND_RUN=1 ./M365-Linux-Bridge-x86_64.AppImage`

---

### Opción 3: Desarrollo (clon de git)

```bash
git clone https://github.com/niguerrac/m365-linux-bridge.git
cd m365-linux-bridge
./install.sh
```

Instala ese clon tal cual. El botón **Buscar actualizaciones** hace `git pull --ff-only` sobre la rama actual (se niega si hay cambios sin commitear).

### Publicar un release

1. Subir `__version__` en `m365_bridge/__init__.py`.
2. `git tag vX.Y.Z && git push origin vX.Y.Z` — el CI verifica que el tag coincida con `__version__`, arma el AppImage y publica el release. Las instalaciones existentes lo ven en su próximo chequeo.

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

## 🩺 Solución de problemas

| Síntoma | Causa y solución |
|---|---|
| `dlopen(): error loading libfuse.so.2` | Falta FUSE 2 (solo AppImage). `sudo pacman -S fuse2` / `sudo apt install libfuse2`. |
| La ventana no abre o se cierra con `EGL not available` / `Failed to create RHI` | Tu GPU/driver no inicializa EGL. Desde v1.0.2 la app lo detecta y usa **render por software** automáticamente. Para forzarlo: `M365_SOFTWARE_RENDER=1`; para desactivar el fallback: `M365_SOFTWARE_RENDER=0`. |
| `No se encontro Chromium` al iniciar sesión | Instala Chromium (ver *Requisitos del sistema*). |
| El sync dice que la sesión no está iniciada | Inicia sesión en la pestaña de Outlook de la propia app: el sync reutiliza esa sesión (puerto de depuración local `9222`, solo localhost). Si lo ejecutas sin la ventana abierta, se abrirá un Chromium aparte para iniciar sesión una vez. |

---

## 🛣️ Próximos Pasos (Roadmap)
* [x] Soporte para subida bidireccional y borrado sincronizado de archivos entre Linux y OneDrive.
* [x] Integración de API Delta de OneDrive e Inotify para sincronizaciones instantáneas con consumo cero de CPU.
* [ ] Empaquetado en AUR (`m365-linux-bridge-bin`) para usuarios de Arch / CachyOS.
* [ ] Integración con el Calendario y reuniones de Microsoft Teams vía MCP.

---

## 📄 Licencia
Este proyecto está bajo la Licencia **MIT**. Consulta el archivo `LICENSE` para más detalles.
