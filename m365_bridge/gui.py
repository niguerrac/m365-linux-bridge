import sys, os, re, shutil, subprocess
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Debe ir antes de importar PyQt6 (QtWebEngine lee estas variables al iniciar).
from m365_bridge.render_fallback import apply_render_fallback
apply_render_fallback()

# Expone el navegador embebido por CDP (solo localhost) para que el sync reutilice su sesion
# en vez de abrir un segundo Chromium que pida login otra vez.
from m365_bridge.config import get_config as _get_config
os.environ.setdefault("QTWEBENGINE_REMOTE_DEBUGGING", str(_get_config().get("remote_debugging_port", 9222)))

from pathlib import Path
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QLineEdit, QSpinBox, QFileDialog,
    QMessageBox, QDialog, QFormLayout, QProgressBar, QTextEdit, QSizePolicy,
    QTabWidget, QListWidget, QListWidgetItem, QCheckBox
)
from PyQt6.QtGui import QIcon, QDesktopServices
from PyQt6.QtCore import Qt, QUrl, QThread, pyqtSignal, QStandardPaths, QTimer
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import (
    QWebEngineProfile, QWebEnginePage, QWebEngineDownloadRequest,
    QWebEnginePermission, QWebEngineSettings
)

from m365_bridge.config import get_config, save_config, DATA_DIR
from m365_bridge import __version__
from m365_bridge.sync import run_sync
from m365_bridge.browser import launch_interactive_login
from m365_bridge.mcp_installer import install_claude_desktop, install_antigravity, install_opencode

ICON_PATH = Path(__file__).parent.parent / "icons" / "m365.png"

class SyncWorker(QThread):
    progress = pyqtSignal(str, int)
    finished = pyqtSignal(object)
    error = pyqtSignal(str)

    def run(self):
        import asyncio
        def on_progress(status_text: str, current_count: int):
            self.progress.emit(status_text, current_count)

        try:
            res = asyncio.run(run_sync(progress_callback=on_progress))
            self.finished.emit(res)
        except Exception as e:
            from m365_bridge.sync import get_sync_logger
            get_sync_logger().exception("Fallo el ciclo de sincronizacion")
            self.error.emit(f"{type(e).__name__}: {e}")

class SyncDetailsDialog(QDialog):
    def __init__(self, sync_result=None, error_msg=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Detalles de Sincronización M365")
        self.setMinimumWidth(580)
        self.setMinimumHeight(420)

        layout = QVBoxLayout(self)

        if error_msg:
            title = QLabel("⚠ Error durante la sincronización")
            title.setStyleSheet("font-size: 15px; font-weight: bold; color: #d13438;")
            layout.addWidget(title)

            desc = QLabel("Detalle técnico del error reportado por el sistema:")
            desc.setStyleSheet("color: #666; font-size: 11px;")
            layout.addWidget(desc)

            err_box = QTextEdit()
            err_box.setPlainText(error_msg)
            err_box.setReadOnly(True)
            err_box.setStyleSheet("background-color: #fff0f0; border: 1px solid #f5c2c7; color: #842029; font-family: monospace; padding: 8px;")
            layout.addWidget(err_box)

            btn_fix = QPushButton("🔑 Abrir Navegador para Reautenticar Sesión")
            btn_fix.setStyleSheet("background-color: #0078D4; color: white; font-weight: bold; padding: 8px 14px; border-radius: 4px;")
            btn_fix.clicked.connect(launch_interactive_login)
            layout.addWidget(btn_fix)
        elif sync_result:
            timestamp = sync_result.get("timestamp", "")
            d_list = sync_result.get("downloaded_files", [])
            u_list = sync_result.get("uploaded_files", [])
            del_remote_list = sync_result.get("deleted_files", [])
            del_local_list = sync_result.get("deleted_locally_files", [])
            total_del = len(del_remote_list) + len(del_local_list)

            title = QLabel(f"✓ Sincronización finalizada ({timestamp})")
            title.setStyleSheet("font-size: 15px; font-weight: bold; color: #107C41;")
            layout.addWidget(title)

            summary_lbl = QLabel(
                f"📥 Descargados: {len(d_list)}   |   📤 Subidos: {len(u_list)}   |   🗑️ Eliminados: {total_del}"
            )
            summary_lbl.setStyleSheet("font-size: 12px; color: #333; font-weight: bold; margin-bottom: 6px;")
            layout.addWidget(summary_lbl)

            tabs = QTabWidget()

            # Pestaña Subidos
            list_up = QListWidget()
            for f in u_list:
                list_up.addItem(f"📤 {f}")
            if not u_list:
                item = QListWidgetItem("Ningún archivo local nuevo para subir.")
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
                list_up.addItem(item)
            tabs.addTab(list_up, f"📤 Subidos ({len(u_list)})")

            # Pestaña Descargados
            list_down = QListWidget()
            for f in d_list:
                list_down.addItem(f"📥 {f}")
            if not d_list:
                item = QListWidgetItem("Ningún archivo nuevo en la nube para descargar.")
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
                list_down.addItem(item)
            tabs.addTab(list_down, f"📥 Descargados ({len(d_list)})")

            # Pestaña Eliminados (ambas direcciones)
            list_del = QListWidget()
            for f in del_local_list:
                list_del.addItem(f"💻 [Eliminado en PC]: {f} (borrado desde la nube)")
            for f in del_remote_list:
                list_del.addItem(f"☁️ [Reciclado en nube]: {f} (borrado desde tu PC)")
            if not del_local_list and not del_remote_list:
                item = QListWidgetItem("Ningún archivo eliminado en esta pasada.")
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
                list_del.addItem(item)
            tabs.addTab(list_del, f"🗑️ Eliminados ({total_del})")

            # Pestaña Registro / Log
            log_box = QTextEdit()
            log_box.setReadOnly(True)
            log_box.setStyleSheet("font-family: monospace; font-size: 11px; background-color: #fdfdfd; padding: 6px;")
            log_file = DATA_DIR / "sync.log"
            if log_file.exists():
                try:
                    lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
                    log_box.setPlainText("\n".join(lines[-250:]))
                except Exception as e:
                    log_box.setPlainText(f"Error al leer sync.log: {e}")
            else:
                log_box.setPlainText("El archivo sync.log aún no se ha generado.")
            tabs.addTab(log_box, "📋 Registro (Log)")

            layout.addWidget(tabs)
        else:
            info = QLabel("Aún no se ha completado ninguna sincronización en esta sesión.")
            info.setStyleSheet("color: #666; font-size: 13px; padding: 10px;")
            layout.addWidget(info)

            # Mostrar log disponible aunque no haya sync en esta sesión
            log_file = DATA_DIR / "sync.log"
            if log_file.exists():
                lbl = QLabel("Último registro de eventos (sync.log):")
                lbl.setStyleSheet("font-weight: bold; font-size: 12px; margin-top: 6px;")
                layout.addWidget(lbl)
                log_box = QTextEdit()
                log_box.setReadOnly(True)
                log_box.setStyleSheet("font-family: monospace; font-size: 11px; background-color: #fdfdfd; padding: 6px;")
                try:
                    lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
                    log_box.setPlainText("\n".join(lines[-250:]))
                except Exception as e:
                    log_box.setPlainText(f"Error al leer sync.log: {e}")
                layout.addWidget(log_box)

        btn_row = QHBoxLayout()
        btn_open_log = QPushButton("📄 Abrir archivo sync.log")
        btn_open_log.setStyleSheet("padding: 6px 12px; color: #0078D4; font-size: 11px;")
        def open_external_log():
            lf = DATA_DIR / "sync.log"
            if lf.exists():
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(lf)))
        btn_open_log.clicked.connect(open_external_log)
        btn_row.addWidget(btn_open_log)

        btn_row.addStretch()

        btn_close = QPushButton("Cerrar")
        btn_close.setStyleSheet("padding: 6px 16px; font-weight: bold;")
        btn_close.clicked.connect(self.accept)
        btn_row.addWidget(btn_close)

        layout.addLayout(btn_row)

class McpDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Configuración de MCP para IA (Claude / AGY / OpenCode)")
        self.setMinimumWidth(580)
        
        layout = QVBoxLayout(self)

        title = QLabel("🤖 Servidor MCP para Inteligencia Artificial")
        title.setStyleSheet("font-size: 16px; font-weight: bold; color: #0078D4;")
        layout.addWidget(title)

        desc = QLabel(
            "Permite a modelos de IA leer, buscar correos, ENVIAR correos y listar archivos en OneDrive "
            "sin requerir permisos de administrador de Azure AD.\n\n"
            "Herramientas disponibles para la IA:\n"
            "• m365_list_emails: Obtiene los últimos correos recibidos.\n"
            "• m365_search_emails: Busca correos por tema o remitente.\n"
            "• m365_send_email: Redacta y envía correos (to, subject, body).\n"
            "• m365_list_files: Lista documentos y carpetas en OneDrive."
        )
        desc.setStyleSheet("color: #444; line-height: 1.4;")
        layout.addWidget(desc)

        layout.addSpacing(10)
        lbl_install = QLabel("Instalación automática en 1 clic:")
        lbl_install.setStyleSheet("font-weight: bold;")
        layout.addWidget(lbl_install)

        btns_layout = QHBoxLayout()
        
        self.btn_claude = QPushButton(" Instalar en Claude Desktop")
        self.btn_claude.setStyleSheet("padding: 8px 12px; font-weight: bold;")
        self.btn_claude.clicked.connect(self.do_install_claude)
        btns_layout.addWidget(self.btn_claude)

        self.btn_agy = QPushButton(" Instalar en Antigravity (AGY)")
        self.btn_agy.setStyleSheet("padding: 8px 12px; font-weight: bold;")
        self.btn_agy.clicked.connect(self.do_install_agy)
        btns_layout.addWidget(self.btn_agy)

        self.btn_opencode = QPushButton(" Instalar en OpenCode")
        self.btn_opencode.setStyleSheet("padding: 8px 12px; font-weight: bold;")
        self.btn_opencode.clicked.connect(self.do_install_opencode)
        btns_layout.addWidget(self.btn_opencode)

        layout.addLayout(btns_layout)

        layout.addSpacing(15)
        lbl_manual = QLabel("O configuración manual JSON:")
        lbl_manual.setStyleSheet("font-weight: bold;")
        layout.addWidget(lbl_manual)

        bridge_root = Path(__file__).parent.parent
        json_example = (
            '{\n'
            '  "mcpServers": {\n'
            '    "m365": {\n'
            f'      "command": "{bridge_root}/.venv/bin/python",\n'
            f'      "args": ["{bridge_root}/m365_bridge/mcp_server.py"]\n'
            '    }\n'
            '  }\n'
            '}'
        )
        txt = QTextEdit()
        txt.setPlainText(json_example)
        txt.setReadOnly(True)
        txt.setMaximumHeight(110)
        layout.addWidget(txt)

        btn_close = QPushButton("Cerrar")
        btn_close.clicked.connect(self.accept)
        layout.addWidget(btn_close, alignment=Qt.AlignmentFlag.AlignRight)

    def do_install_claude(self):
        ok, path = install_claude_desktop()
        if ok:
            QMessageBox.information(self, "Claude Desktop", f"¡Servidor MCP registrado en Claude Desktop con éxito!\n\nArchivo: {path}")

    def do_install_agy(self):
        ok, path = install_antigravity()
        if ok:
            QMessageBox.information(self, "Antigravity (AGY)", f"¡Servidor MCP registrado en Antigravity con éxito!\n\nArchivo: {path}")

    def do_install_opencode(self):
        ok, path = install_opencode()
        if ok:
            QMessageBox.information(self, "OpenCode", f"¡Servidor MCP registrado en OpenCode con éxito!\n\nArchivo: {path}")
        else:
            QMessageBox.warning(self, "OpenCode", f"No se pudo registrar: {path}")

class M365WebPage(QWebEnginePage):
    """Página WebEngine personalizada para soportar enlaces emergentes, permisos de medios (Teams) y descargas."""
    def __init__(self, profile, parent=None):
        super().__init__(profile, parent)
        # Manejo moderno de permisos (Qt 6.5+)
        if hasattr(self, "permissionRequested"):
            self.permissionRequested.connect(self.on_permission_requested)
        # Compatibilidad con API anterior de permisos de características
        if hasattr(self, "featurePermissionRequested"):
            self.featurePermissionRequested.connect(self.on_feature_permission_requested)

    def on_permission_requested(self, permission: QWebEnginePermission):
        # Auto-conceder permisos para llamadas y reuniones (micrófono, cámara, notificaciones, pantalla)
        permission.grant()

    def on_feature_permission_requested(self, security_origin, feature):
        # Auto-conceder en API legacy
        self.setFeaturePermission(
            security_origin,
            feature,
            QWebEnginePage.PermissionPolicy.PermissionGrantedByUser
        )




def open_in_file_manager(target_path: Path):
    """Abre el gestor de archivos seleccionando el archivo si es posible, o abriendo la carpeta contenedora."""
    try:
        p = Path(target_path).resolve()
        target_dir = str(p.parent if p.is_file() or not p.exists() else p)
        file_path = str(p) if p.exists() and not p.is_dir() else None

        # 1. Intentar con dolphin --select (gestor nativo en KDE / CachyOS)
        if file_path and shutil.which("dolphin"):
            subprocess.Popen(["dolphin", "--select", file_path])
            return

        # 2. Intentar con dbus FileManager1 ShowItems (estándar freedesktop)
        if file_path and shutil.which("busctl"):
            res = subprocess.run([
                "busctl", "--user", "call", "org.freedesktop.FileManager1",
                "/org/freedesktop/FileManager1", "org.freedesktop.FileManager1",
                "ShowItems", "as", "1", f"file://{file_path}", ""
            ], capture_output=True, timeout=1)
            if res.returncode == 0:
                return

        # 3. Abrir la carpeta directamente con dolphin si existe
        if shutil.which("dolphin"):
            subprocess.Popen(["dolphin", target_dir])
            return

        # 4. Fallback a xdg-open
        subprocess.Popen(["xdg-open", target_dir])
    except Exception as e:
        print(f"Error abriendo gestor de archivos: {e}")

class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Configuración de M365 Linux Bridge")
        self.setMinimumWidth(480)
        
        self.cfg = get_config()
        layout = QFormLayout(self)

        self.inst_input = QLineEdit(self.cfg.get("institution_name", ""))
        layout.addRow("Nombre de Institución:", self.inst_input)

        folder_layout = QHBoxLayout()
        self.folder_input = QLineEdit(self.cfg.get("sync_folder", ""))
        self.btn_browse = QPushButton("Examinar...")
        self.btn_browse.clicked.connect(self.browse_folder)
        folder_layout.addWidget(self.folder_input)
        folder_layout.addWidget(self.btn_browse)
        layout.addRow("Carpeta Local OneDrive:", folder_layout)

        down_layout = QHBoxLayout()
        default_down = str(Path.home() / "Descargas")
        self.down_input = QLineEdit(self.cfg.get("downloads_folder", default_down))
        self.btn_browse_down = QPushButton("Examinar...")
        self.btn_browse_down.clicked.connect(self.browse_downloads_folder)
        down_layout.addWidget(self.down_input)
        down_layout.addWidget(self.btn_browse_down)
        layout.addRow("Carpeta de Descargas Web:", down_layout)

        self.ask_down_chk = QCheckBox("Preguntar siempre dónde guardar cada archivo descargado")
        self.ask_down_chk.setChecked(self.cfg.get("ask_download_location", True))
        layout.addRow("", self.ask_down_chk)

        self.max_mb_input = QSpinBox()
        self.max_mb_input.setRange(10, 50000)
        self.max_mb_input.setValue(self.cfg.get("max_file_size_mb", 200))
        self.max_mb_input.setSuffix(" MB")
        layout.addRow("Límite tamaño descarga automática:", self.max_mb_input)

        self.interval_input = QSpinBox()
        self.interval_input.setRange(0, 1440)
        self.interval_input.setValue(self.cfg.get("auto_sync_interval_minutes", 60))
        self.interval_input.setSuffix(" min (0 = desactivado)")
        layout.addRow("Intervalo sincro periódica:", self.interval_input)

        self.btn_reauth = QPushButton("🔑 Reautenticar Motor en Navegador")
        self.btn_reauth.setStyleSheet("padding: 6px 10px; font-weight: bold;")
        self.btn_reauth.setToolTip("Abre una ventana de Chromium para ingresar credenciales o 2FA si la sesión expiró.")
        self.btn_reauth.clicked.connect(self.reauthenticate)
        layout.addRow("Sesión de Fondo (Playwright/MCP):", self.btn_reauth)

        from m365_bridge.updater import MODE_LABEL, install_mode
        ver_row = QHBoxLayout()
        self.version_label = QLabel(f"v{__version__} · {MODE_LABEL[install_mode()]}")
        self.btn_check_update = QPushButton("Buscar actualizaciones")
        self.btn_check_update.clicked.connect(self.check_updates)
        ver_row.addWidget(self.version_label, 1)
        ver_row.addWidget(self.btn_check_update)
        layout.addRow("Versión:", ver_row)

        self.btn_selective = QPushButton("📁 Elegir carpetas…")
        self.btn_selective.setStyleSheet("padding: 6px 10px;")
        self.btn_selective.setToolTip("Elegir qué carpetas de OneDrive se sincronizan en este equipo")
        self.btn_selective.clicked.connect(self.open_selective_sync)
        layout.addRow("Carpetas sincronizadas:", self.btn_selective)

        self.btn_mcp = QPushButton("🤖 MCP / IA…")
        self.btn_mcp.setStyleSheet("background-color: #107C41; color: white; font-weight: bold; padding: 6px 10px; border-radius: 4px;")
        self.btn_mcp.clicked.connect(self.open_mcp_dialog)
        layout.addRow("Integración con IA:", self.btn_mcp)

        btns = QHBoxLayout()
        btn_save = QPushButton("Guardar")
        btn_save.clicked.connect(self.save)
        btn_cancel = QPushButton("Cancelar")
        btn_cancel.clicked.connect(self.reject)
        btns.addWidget(btn_save)
        btns.addWidget(btn_cancel)
        layout.addRow(btns)

    def reauthenticate(self):
        launch_interactive_login()
        QMessageBox.information(
            self,
            "Reautenticación M365",
            "Se ha abierto una ventana de Chromium.\n\n"
            "Inicia sesión con tu cuenta institucional y completa cualquier verificación de dos factores (2FA).\n"
            "Cuando cargue tu bandeja de entrada de Outlook, cierra la ventana del navegador."
        )

    def browse_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Seleccionar Carpeta para OneDrive", self.folder_input.text())
        if d:
            self.folder_input.setText(d)

    def browse_downloads_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Seleccionar Carpeta para Descargas", self.down_input.text())
        if d:
            self.down_input.setText(d)

    def open_selective_sync(self):
        main = self.parent()
        if main is not None and hasattr(main, "open_selective_sync"):
            main.open_selective_sync()
        else:
            from m365_bridge.selective_dialog import SelectiveSyncDialog
            SelectiveSyncDialog(self).exec()

    def open_mcp_dialog(self):
        McpDialog(self).exec()

    def check_updates(self):
        from m365_bridge.update_ui import UpdateCheckWorker, start_update
        self.btn_check_update.setEnabled(False)
        self.btn_check_update.setText("Buscando…")

        def done():
            self.btn_check_update.setEnabled(True)
            self.btn_check_update.setText("Buscar actualizaciones")

        def on_result(info):
            done()
            if not info:
                self.version_label.setText(self.version_label.text().split("  ")[0] + "  ✓ al día")
                return
            ok = QMessageBox.question(
                self, "Actualización disponible",
                f"Hay una versión nueva: v{info['version']} (tienes v{__version__}).\n\n¿Actualizar ahora?",
            )
            if ok == QMessageBox.StandardButton.Yes:
                start_update(info, self)

        def on_error(msg):
            done()
            QMessageBox.warning(self, "Actualizaciones", f"No se pudo consultar GitHub:\n{msg}")

        self._upd_worker = UpdateCheckWorker(use_cache=False, parent=self)
        self._upd_worker.result.connect(on_result)
        self._upd_worker.error.connect(on_error)
        self._upd_worker.start()

    def save(self):
        # Releer: el panel de carpetas pudo guardar cambios mientras este diálogo estaba abierto
        self.cfg = get_config()
        self.cfg["institution_name"] = self.inst_input.text().strip()
        self.cfg["sync_folder"] = self.folder_input.text().strip()
        self.cfg["downloads_folder"] = self.down_input.text().strip()
        self.cfg["ask_download_location"] = self.ask_down_chk.isChecked()
        self.cfg["max_file_size_mb"] = self.max_mb_input.value()
        self.cfg["auto_sync_interval_minutes"] = self.interval_input.value()
        save_config(self.cfg)
        self.accept()

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.cfg = get_config()
        self.setWindowTitle(f"M365 Linux Bridge v{__version__} - {self.cfg.get('institution_name', 'Office 365')}")
        self.resize(1200, 800)
        if ICON_PATH.exists():
            self.setWindowIcon(QIcon(str(ICON_PATH)))

        profile_storage = DATA_DIR / "webengine_profile"
        profile_storage.mkdir(parents=True, exist_ok=True)
        self.web_profile = QWebEngineProfile("M365Profile", self)
        self.web_profile.setPersistentStoragePath(str(profile_storage))
        self.web_profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.AllowPersistentCookies)
        self.web_profile.setPersistentPermissionsPolicy(QWebEngineProfile.PersistentPermissionsPolicy.StoreOnDisk)
        self.web_profile.downloadRequested.connect(self.on_download_requested)
        self._active_downloads = []

        # Limpiar QtWebEngine del User-Agent para que Microsoft 365, SharePoint y OneDrive
        # reconozcan el navegador como Google Chrome estándar y permitan la previsualización / edición web
        # de documentos (Word, Excel, PowerPoint, imágenes, PDFs) en la misma ventana sin forzar descargas.
        orig_ua = self.web_profile.httpUserAgent()
        clean_ua = re.sub(r'QtWebEngine/\S+\s*', '', orig_ua)
        self.web_profile.setHttpUserAgent(clean_ua)

        # Habilitar soporte multimedia, WebRTC, plugins y previsualizaciones web completas
        web_settings = self.web_profile.settings()
        web_settings.setAttribute(QWebEngineSettings.WebAttribute.ScreenCaptureEnabled, True)
        web_settings.setAttribute(QWebEngineSettings.WebAttribute.WebRTCPublicInterfacesOnly, False)
        web_settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanAccessClipboard, True)
        web_settings.setAttribute(QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, False)
        web_settings.setAttribute(QWebEngineSettings.WebAttribute.PluginsEnabled, True)
        web_settings.setAttribute(QWebEngineSettings.WebAttribute.PdfViewerEnabled, True)
        web_settings.setAttribute(QWebEngineSettings.WebAttribute.LocalStorageEnabled, True)
        web_settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows, True)
        web_settings.setAttribute(QWebEngineSettings.WebAttribute.AllowWindowActivationFromJavaScript, True)
        web_settings.setAttribute(QWebEngineSettings.WebAttribute.FullScreenSupportEnabled, True)

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # --- BARRA DE HERRAMIENTAS COMPACTA ---
        toolbar_container = QWidget()
        toolbar_container.setStyleSheet("background-color: #f3f3f3; border-bottom: 1px solid #ddd;")
        toolbar = QHBoxLayout(toolbar_container)
        toolbar.setContentsMargins(8, 6, 8, 6)
        toolbar.setSpacing(6)
        
        self.btn_outlook = QPushButton(" Outlook (Correo)")
        self.btn_outlook.setStyleSheet("font-weight: bold; padding: 5px 10px;")
        self.btn_outlook.clicked.connect(self.show_outlook)
        toolbar.addWidget(self.btn_outlook)

        self.btn_onedrive = QPushButton(" OneDrive (Archivos)")
        self.btn_onedrive.setStyleSheet("font-weight: bold; padding: 5px 10px;")
        self.btn_onedrive.clicked.connect(self.show_onedrive)
        toolbar.addWidget(self.btn_onedrive)

        self.btn_teams = QPushButton(" Teams")
        self.btn_teams.setStyleSheet("font-weight: bold; padding: 5px 10px;")
        self.btn_teams.clicked.connect(self.show_teams)
        toolbar.addWidget(self.btn_teams)

        toolbar.addSpacing(6)

        self.btn_back = QPushButton("◀")
        self.btn_back.setToolTip("Atrás")
        self.btn_back.setFixedWidth(30)
        self.btn_back.clicked.connect(self.go_back)
        toolbar.addWidget(self.btn_back)

        self.btn_reload = QPushButton("⟳")
        self.btn_reload.setToolTip("Recargar")
        self.btn_reload.setFixedWidth(30)
        self.btn_reload.clicked.connect(self.reload_page)
        toolbar.addWidget(self.btn_reload)

        toolbar.addSpacing(10)

        self.btn_sync = QPushButton(" Sincronizar Drive")
        self.btn_sync.setStyleSheet("background-color: #0078D4; color: white; font-weight: bold; padding: 5px 12px; border-radius: 4px;")
        self.btn_sync.clicked.connect(self.start_sync)
        toolbar.addWidget(self.btn_sync)

        self.btn_folder = QPushButton(" Abrir Carpeta")
        self.btn_folder.setStyleSheet("padding: 5px 10px;")
        self.btn_folder.clicked.connect(self.open_local_folder)
        toolbar.addWidget(self.btn_folder)


        toolbar.addStretch()

        # Indicador de estado interactivo clicable
        self.last_sync_result = None
        self.last_sync_error = None

        self.btn_status = QPushButton("Listo para sincronizar")
        self.btn_status.setFlat(True)
        self.btn_status.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_status.setStyleSheet(
            "color: #0078D4; font-size: 11px; font-weight: bold; margin-right: 6px; "
            "border: 1px solid #cce4f7; border-radius: 3px; padding: 3px 8px; background-color: #f8fbff;"
        )
        self.btn_status.setToolTip("Haz clic aquí para ver los archivos subidos, descargados o el detalle del error")
        self.btn_status.clicked.connect(self.show_sync_details)
        toolbar.addWidget(self.btn_status)

        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedWidth(100)
        self.progress_bar.setFixedHeight(8)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setVisible(False)
        toolbar.addWidget(self.progress_bar)

        self.btn_settings = QPushButton("⚙")
        self.btn_settings.setToolTip("Configuración")
        self.btn_settings.setFixedWidth(32)
        self.btn_settings.clicked.connect(self.open_settings)
        toolbar.addWidget(self.btn_settings)

        main_layout.addWidget(toolbar_container)

        # --- NAVEGADOR EMBEBIDO (WEBVIEW) OCUPA TODO EL ESPACIO RESTANTE ---
        self._last_browse_url = None
        self.webview = QWebEngineView(self)
        self.web_page = M365WebPage(self.web_profile, self.webview)
        if hasattr(self.web_page, "fullScreenRequested"):
            self.web_page.fullScreenRequested.connect(lambda req: req.accept())
        if hasattr(self.web_page, "windowCloseRequested"):
            self.web_page.windowCloseRequested.connect(self.on_window_close_requested)
        if hasattr(self.web_page, "newWindowRequested"):
            self.web_page.newWindowRequested.connect(self.on_new_window_requested)
        self.webview.setPage(self.web_page)
        self.webview.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.webview.urlChanged.connect(self.on_url_changed)
        main_layout.addWidget(self.webview, 1)

        self.show_outlook()
        self.worker = None

        # Temporizador de sincronización periódica en segundo plano
        self.auto_sync_timer = QTimer(self)
        self.auto_sync_timer.timeout.connect(self.trigger_auto_sync)
        self.update_auto_sync_timer()

        # Aviso de versión nueva al abrir (no bloquea; sin red no dice nada)
        from m365_bridge.update_ui import check_on_startup
        QTimer.singleShot(3000, lambda: check_on_startup(self))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        toast = getattr(self, "_update_toast", None)
        if toast is not None and toast.isVisible():
            toast.reposition()

    def show_outlook(self):
        url = self.cfg.get("outlook_url", "https://outlook.cloud.microsoft/mail/")
        self.webview.setUrl(QUrl(url))

    def show_onedrive(self):
        url = self.cfg.get("onedrive_url", "https://www.office.com/launch/onedrive")
        self.webview.setUrl(QUrl(url))

    def show_teams(self):
        url = self.cfg.get("teams_url", "https://teams.microsoft.com/v2/")
        self.webview.setUrl(QUrl(url))

    def on_new_window_requested(self, request):
        url = request.requestedUrl()
        if url.isValid() and url.toString() not in ("", "about:blank"):
            self.webview.setUrl(url)

    def on_url_changed(self, url: QUrl):
        u = url.toString()
        is_editor = any(m in u for m in (":w:/r/", ":x:/r/", ":p:/r/", "WopiFrame", "Doc.aspx"))
        if not is_editor and any(m in u for m in ("sharepoint.com", "office.com", "onedrive.live.com", "onedrive.com")):
            self._last_browse_url = u

    def go_back(self):
        hist = self.webview.history()
        if hist.canGoBack():
            back_item = hist.backItem()
            if back_item:
                back_u = back_item.url().toString()
                if any(m in back_u for m in (":w:/r/", ":x:/r/", ":p:/r/")):
                    items = hist.backItems(2)
                    if len(items) >= 2:
                        hist.goToItem(items[0])
                        return
            self.webview.back()
        elif getattr(self, "_last_browse_url", None):
            self.webview.setUrl(QUrl(self._last_browse_url))
        else:
            self.show_onedrive()

    def reload_page(self):
        self.webview.reload()

    def on_window_close_requested(self):
        self.go_back()

    def open_local_folder(self):
        path = self.cfg.get("sync_folder", str(Path.home() / "OneDrive"))
        open_in_file_manager(Path(path))

    def update_auto_sync_timer(self):
        interval_min = self.cfg.get("auto_sync_interval_minutes", 60)
        if interval_min > 0:
            self.auto_sync_timer.start(interval_min * 60 * 1000)
        else:
            self.auto_sync_timer.stop()

    def trigger_auto_sync(self):
        if (self.worker is None or not self.worker.isRunning()) and self.btn_sync.isEnabled():
            self.start_sync()

    def open_settings(self):
        dlg = SettingsDialog(self)
        if dlg.exec():
            self.cfg = get_config()
            self.setWindowTitle(f"M365 Linux Bridge v{__version__} - {self.cfg.get('institution_name', 'Office 365')}")
            self.update_auto_sync_timer()

    def open_selective_sync(self):
        from m365_bridge.selective_dialog import SelectiveSyncDialog
        dlg = SelectiveSyncDialog(self)
        if dlg.exec() and dlg.result_summary is not None:
            res = dlg.result_summary
            if res["deleted_local"] or res["queued_download"]:
                self.btn_status.setText(
                    f"Carpetas: −{len(res['deleted_local'])} / +{len(res['queued_download'])}"
                )
            if self.btn_sync.isEnabled():
                self.start_sync()

    def open_mcp_dialog(self):
        dlg = McpDialog(self)
        dlg.exec()

    def show_sync_details(self):
        dlg = SyncDetailsDialog(sync_result=self.last_sync_result, error_msg=self.last_sync_error, parent=self)
        dlg.exec()

    def start_sync(self):
        self.btn_sync.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)
        self.btn_status.setText("Conectando...")
        self.btn_status.setStyleSheet("color: #0078D4; font-size: 11px; font-weight: bold; margin-right: 6px; border: 1px solid #cce4f7; border-radius: 3px; padding: 3px 8px; background-color: #f8fbff;")

        self.worker = SyncWorker()
        self.worker.progress.connect(self.on_sync_progress)
        self.worker.finished.connect(self.on_sync_finished)
        self.worker.error.connect(self.on_sync_error)
        self.worker.start()

    def on_sync_progress(self, msg, count):
        self.btn_status.setText(msg[:48])

    def on_sync_finished(self, res):
        self.btn_sync.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.last_sync_result = res
        self.last_sync_error = None
        if isinstance(res, dict):
            d = res.get("downloaded", 0)
            u = res.get("uploaded", 0)
            del_c = res.get("deleted", 0)
            self.btn_status.setText(f"✓ Sincronizado (↓{d} ↑{u} 🗑{del_c})")
            self.btn_status.setStyleSheet(
                "color: #107C41; font-size: 11px; font-weight: bold; margin-right: 6px; "
                "border: 1px solid #c3e6cb; border-radius: 3px; padding: 3px 8px; background-color: #f2f9f4;"
            )
        else:
            self.btn_status.setText(f"✓ Sincronizado ({res})")

    def on_sync_error(self, err):
        self.btn_sync.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.last_sync_error = str(err)
        err_lower = str(err).lower()

        if "ya hay una sincronización en curso" in err_lower:
            self.btn_status.setText("⏳ Sincronización en curso (Bandeja)")
            self.btn_status.setStyleSheet(
                "color: #8a6d3b; font-size: 11px; font-weight: bold; margin-right: 6px; "
                "border: 1px solid #faebcc; border-radius: 3px; padding: 3px 8px; background-color: #fcf8e3;"
            )
            return

        self.btn_status.setText("⚠ Error (clic para ver detalle)")
        self.btn_status.setStyleSheet(
            "color: #d13438; font-size: 11px; font-weight: bold; margin-right: 6px; "
            "border: 1px solid #f5c2c7; border-radius: 3px; padding: 3px 8px; background-color: #fff0f0;"
        )
        if "sesión" in err_lower or "autenticación" in err_lower or "2fa" in err_lower:
            reply = QMessageBox.question(
                self,
                "Autenticación Requerida",
                f"{err}\n\n¿Deseas abrir la ventana del navegador para iniciar sesión ahora?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.Yes:
                launch_interactive_login()

    def on_download_requested(self, download: QWebEngineDownloadRequest):
        suggested = download.suggestedFileName() or "archivo_descargado"
        cfg = get_config()
        downloads_dir = Path(cfg.get("downloads_folder", str(Path.home() / "Descargas")))
        downloads_dir.mkdir(parents=True, exist_ok=True)

        ask_location = cfg.get("ask_download_location", True)

        if ask_location:
            initial_path = str(downloads_dir / suggested)
            save_path, _ = QFileDialog.getSaveFileName(
                self,
                "Guardar archivo descargado",
                initial_path,
                "Todos los archivos (*.*)"
            )
            if not save_path:
                download.cancel()
                return
            target_path = Path(save_path)
            target_dir = str(target_path.parent)
            target_filename = target_path.name
        else:
            target_filename = suggested
            target = downloads_dir / target_filename
            stem = target.stem
            suffix = target.suffix
            counter = 1
            while target.exists():
                target_filename = f"{stem} ({counter}){suffix}"
                target = downloads_dir / target_filename
                counter += 1
            target_dir = str(downloads_dir)

        download.setDownloadDirectory(target_dir)
        download.setDownloadFileName(target_filename)
        download.accept()

        self._active_downloads.append(download)

        # Mostrar progreso en barra de herramientas
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.btn_status.setText(f"Descargando: {target_filename[:20]}...")
        self.btn_status.setStyleSheet(
            "color: #0078D4; font-size: 11px; font-weight: bold; margin-right: 6px; "
            "border: 1px solid #cce4f7; border-radius: 3px; padding: 3px 8px; background-color: #f8fbff;"
        )

        final_path = Path(target_dir) / target_filename

        def on_received_bytes_changed():
            received = download.receivedBytes()
            total = download.totalBytes()
            if total > 0:
                pct = int((received / total) * 100)
                self.progress_bar.setValue(pct)
                self.btn_status.setText(f"Descargando ({pct}%): {target_filename[:18]}")
            else:
                self.progress_bar.setRange(0, 0)
                self.btn_status.setText(f"Descargando: {target_filename[:20]}")

        def on_state_changed(state):
            if state == QWebEngineDownloadRequest.DownloadState.DownloadCompleted:
                self.progress_bar.setVisible(False)
                self.btn_status.setText(f"✓ Descarga lista: {target_filename[:18]}")
                self.btn_status.setStyleSheet(
                    "color: #107C41; font-size: 11px; font-weight: bold; margin-right: 6px; "
                    "border: 1px solid #c3e6cb; border-radius: 3px; padding: 3px 8px; background-color: #f2f9f4;"
                )
                if download in self._active_downloads:
                    self._active_downloads.remove(download)

                reply = QMessageBox.information(
                    self,
                    "Descarga Completada",
                    f"Se ha descargado correctamente:\n\n{final_path}\n\n¿Deseas abrir la carpeta contenedora?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.Yes
                )
                if reply == QMessageBox.StandardButton.Yes:
                    open_in_file_manager(final_path)

            elif state == QWebEngineDownloadRequest.DownloadState.DownloadInterrupted:
                self.progress_bar.setVisible(False)
                self.btn_status.setText(f"⚠ Error de descarga: {target_filename[:16]}")
                self.btn_status.setStyleSheet(
                    "color: #d13438; font-size: 11px; font-weight: bold; margin-right: 6px; "
                    "border: 1px solid #f5c2c7; border-radius: 3px; padding: 3px 8px; background-color: #fff0f0;"
                )
                if download in self._active_downloads:
                    self._active_downloads.remove(download)
                QMessageBox.warning(
                    self,
                    "Descarga Interrumpida",
                    f"No se pudo descargar {target_filename}.\n\nMotivo: {download.interruptReasonString()}"
                )

            elif state == QWebEngineDownloadRequest.DownloadState.DownloadCancelled:
                self.progress_bar.setVisible(False)
                self.btn_status.setText("Descarga cancelada")
                if download in self._active_downloads:
                    self._active_downloads.remove(download)

        download.receivedBytesChanged.connect(on_received_bytes_changed)
        download.stateChanged.connect(on_state_changed)

def main():
    app = QApplication(sys.argv)
    app.setDesktopFileName("m365-linux-bridge")
    if ICON_PATH.exists():
        app.setWindowIcon(QIcon(str(ICON_PATH)))
    win = MainWindow()
    win.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
