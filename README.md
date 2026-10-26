# M365 Linux Bridge

Una solución integral de código abierto para utilizar la suite de **Microsoft 365 (Outlook, OneDrive y MCP para Inteligencia Artificial)** en cualquier distribución **Linux**, superando las políticas estrictas de restricción de consentimiento de administradores en Microsoft Entra ID / Azure AD (*"Need admin approval"*).

---

## 🌟 Características Principales

* **Bypass Nativo de Azure AD:** No requiere permisos de administrador de la institución ni registro previo de aplicaciones OAuth de terceros.
* **Interfaz Integrada (PyQt6-WebEngine):** Outlook y OneDrive corren embebidos en una sola ventana nativa y moderna de escritorio.
* **Sincronización Local de OneDrive en Tiempo Real:** Descarga continua y automática de carpetas institucionales a tu disco local (`~/OneDrive`), con indicador de archivo actual y barra de progreso.
* **Servidor MCP Completo para IA (Lectura y Envío):**
  * `m365_list_emails`: Lista correos recientes con remitente, asunto y fragmento.
  * `m365_search_emails`: Búsqueda de correos por palabras clave, personas o fechas.
  * `m365_send_email`: Redacción y despacho directo de correos desde la cuenta institucional.
  * `m365_list_files`: Explorador de archivos y carpetas de OneDrive.
* **Instalador de MCP en 1 Clic:** Botón directo en la barra de herramientas para registrar el servidor en **Claude Desktop**, **Google Antigravity (AGY)** y **OpenCode**.
* **Icono de Bandeja del Sistema (Tray):** Soporte oficial para KDE Plasma (Wayland) y X11 con acciones rápidas de sincronización.

---

## 📋 Resumen de Trabajo y Estado de la Sesión

### Lo que dejamos listo y operativo:
1. **Autenticación e inicio de sesión persistente:** Vinculado con la cuenta `nilson.guerra@inacapmail.cl` sin requerir reautenticación en cada reinicio.
2. **Descarga masiva de OneDrive:** Sincronizados más de 4.8 GB de documentos, informes y proyectos en `~/OneDrive`.
3. **Barra de herramientas compacta y moderna:** Corregida la alineación visual, dejando el 100% del área de trabajo para la vista web de Microsoft 365.
4. **Envío de correos por IA validado:** Probado con éxito mediante el envío del correo de prueba a `s4wolf64@gmail.com`.
5. **Instalación y accesos en el sistema:** Lanza con `M365 Linux Bridge` desde el menú de aplicaciones de KDE o desde la terminal.

---

## 🚀 Pasos para Retomar el Proyecto o Replicarlo en Otro PC

### 1. Iniciar la aplicación en este equipo:
* Desde el menú de aplicaciones: Busca **"M365 Linux Bridge"**.
* O desde terminal:
  ```bash
  cd ~/m365-linux-bridge
  .venv/bin/python m365_bridge/gui.py
  ```

### 2. Para replicarlo en un equipo nuevo:
```bash
git clone https://github.com/TU_USUARIO/m365-linux-bridge.git
cd m365-linux-bridge
./install.sh
```

### 3. Para publicarlo en GitHub:
```bash
cd ~/m365-linux-bridge
git remote add origin https://github.com/TU_USUARIO/m365-linux-bridge.git
git branch -M main
git push -u origin main
```

---

## 🛣️ Próximos Pasos Sugeridos (Backlog Futuro)
* [ ] Agregar subida bidireccional de archivos locales hacia OneDrive (actualmente es unidireccional de la nube al disco).
* [ ] Empaquetar como paquete AUR (`m365-linux-bridge-bin`) y/o Flatpak.
* [ ] Soporte para calendario y reuniones de Teams vía MCP.

---

## 📄 Licencia
Distribuido bajo la licencia MIT.
