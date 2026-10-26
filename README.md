# M365 Linux Bridge

Una solución integral de código abierto para utilizar la suite de **Microsoft 365 (Outlook, OneDrive y MCP para Inteligencia Artificial)** en cualquier distribución **Linux**, superando las políticas estrictas de restricción de consentimiento de administradores en Microsoft Entra ID / Azure AD (*"Need admin approval"*).

---

## 🌟 Características Principales

* **Bypass Nativo de Azure AD:** No requiere permisos de administrador de la institución ni registro previo de aplicaciones OAuth de terceros.
* **Barra de Herramientas y Panel GUI:** Interfaz gráfica nativa en PyQt6 para abrir Outlook, OneDrive, sincronizar y configurar opciones en un clic.
* **Sincronización Local de OneDrive:** Descarga continua y automática de carpetas institucionales a tu disco local (`~/M365-Drive` o ruta personalizada).
* **Servidor MCP Universal (Model Context Protocol):** Compatible con Google Antigravity (AGY), Claude Desktop, Cursor y OpenCode para que asistentes de IA puedan leer, resumir correos y buscar archivos institucionales.
* **Icono de Bandeja del Sistema (Tray):** Soporte nativo para Wayland y X11 (KDE Plasma, GNOME, XFCE).

---

## 🚀 Instalación Rápida (1 línea)

```bash
git clone https://github.com/tu-usuario/m365-linux-bridge.git
cd m365-linux-bridge
./install.sh
```

---

## ⚙️ Configuración

Puedes acceder a la configuración desde la ventana principal (**"Configuración"**) o editando directamente `~/.config/m365-bridge/config.json`:

```json
{
  "institution_name": "Mi Universidad / Empresa",
  "sync_folder": "/home/usuario/M365-Drive",
  "max_file_size_mb": 200,
  "excluded_folders": ["Recordings", "Meetings"]
}
```

---

## 🤖 Integración con Asistentes de IA (MCP)

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

### Antigravity (AGY) (`~/.gemini/config/mcp_config.json`)
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

## 📄 Licencia
Distribuido bajo la licencia MIT.
