import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from pathlib import Path
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QLineEdit, QSpinBox, QFileDialog,
    QMessageBox, QDialog, QFormLayout, QProgressBar, QTextEdit
)
from PyQt6.QtGui import QIcon
from PyQt6.QtCore import Qt, QUrl, QThread, pyqtSignal
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEngineProfile, QWebEnginePage

from m365_bridge.config import get_config, save_config, DATA_DIR
from m365_bridge.sync import run_sync
from m365_bridge.mcp_installer import install_claude_desktop, install_antigravity, install_opencode

ICON_PATH = Path(__file__).parent.parent / "icons" / "m365.png"

class SyncWorker(QThread):
    progress = pyqtSignal(str, int)
    finished = pyqtSignal(int)
    error = pyqtSignal(str)

    def run(self):
        import asyncio
        def on_progress(status_text: str, current_count: int):
            self.progress.emit(status_text, current_count)

        try:
            count = asyncio.run(run_sync(progress_callback=on_progress))
            self.finished.emit(count)
        except Exception as e:
            self.error.emit(str(e))

class McpDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Configuración de MCP para IA (Claude / AGY / OpenCode)")
        self.setMinimumWidth(560)
        
        layout = QVBoxLayout(self)

        title = QLabel("🤖 Servidor MCP para Inteligencia Artificial")
        title.setStyleSheet("font-size: 16px; font-weight: bold; color: #0078D4;")
        layout.addWidget(title)

        desc = QLabel(
            "Permite a modelos de IA leer tus correos de Outlook y buscar archivos en OneDrive "
            "sin requerir permisos del administrador de Azure AD.\n\n"
            "Herramientas incluidas:\n"
            "• m365_list_emails: Obtiene los últimos correos recibidos.\n"
            "• m365_search_emails: Busca correos por palabra clave o remitente.\n"
            "• m365_list_files: Lista documentos y archivos en OneDrive."
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

class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Configuración de M365 Linux Bridge")
        self.setMinimumWidth(450)
        
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

        self.max_mb_input = QSpinBox()
        self.max_mb_input.setRange(10, 50000)
        self.max_mb_input.setValue(self.cfg.get("max_file_size_mb", 200))
        self.max_mb_input.setSuffix(" MB")
        layout.addRow("Límite tamaño descarga:", self.max_mb_input)

        btns = QHBoxLayout()
        btn_save = QPushButton("Guardar")
        btn_save.clicked.connect(self.save)
        btn_cancel = QPushButton("Cancelar")
        btn_cancel.clicked.connect(self.reject)
        btns.addWidget(btn_save)
        btns.addWidget(btn_cancel)
        layout.addRow(btns)

    def browse_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Seleccionar Carpeta para OneDrive", self.folder_input.text())
        if d:
            self.folder_input.setText(d)

    def save(self):
        self.cfg["institution_name"] = self.inst_input.text().strip()
        self.cfg["sync_folder"] = self.folder_input.text().strip()
        self.cfg["max_file_size_mb"] = self.max_mb_input.value()
        save_config(self.cfg)
        self.accept()

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.cfg = get_config()
        self.setWindowTitle(f"M365 Linux Bridge - {self.cfg.get('institution_name', 'Office 365')}")
        self.resize(1200, 800)
        if ICON_PATH.exists():
            self.setWindowIcon(QIcon(str(ICON_PATH)))

        profile_storage = DATA_DIR / "webengine_profile"
        profile_storage.mkdir(parents=True, exist_ok=True)
        self.web_profile = QWebEngineProfile("M365Profile", self)
        self.web_profile.setPersistentStoragePath(str(profile_storage))
        self.web_profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.AllowPersistentCookies)

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(4, 4, 4, 4)

        # --- BARRA DE HERRAMIENTAS PRINCIPAL ---
        toolbar = QHBoxLayout()
        
        self.btn_outlook = QPushButton(" Outlook (Correo)")
        self.btn_outlook.setStyleSheet("font-weight: bold; padding: 6px 12px;")
        self.btn_outlook.clicked.connect(self.show_outlook)
        toolbar.addWidget(self.btn_outlook)

        self.btn_onedrive = QPushButton(" OneDrive (Archivos)")
        self.btn_onedrive.setStyleSheet("font-weight: bold; padding: 6px 12px;")
        self.btn_onedrive.clicked.connect(self.show_onedrive)
        toolbar.addWidget(self.btn_onedrive)

        toolbar.addSpacing(10)

        self.btn_back = QPushButton("◀")
        self.btn_back.setToolTip("Atrás")
        self.btn_back.clicked.connect(self.go_back)
        toolbar.addWidget(self.btn_back)

        self.btn_reload = QPushButton("⟳")
        self.btn_reload.setToolTip("Recargar")
        self.btn_reload.clicked.connect(self.reload_page)
        toolbar.addWidget(self.btn_reload)

        toolbar.addSpacing(10)

        self.btn_sync = QPushButton(" Sincronizar Drive")
        self.btn_sync.setStyleSheet("background-color: #0078D4; color: white; font-weight: bold; padding: 6px 12px; border-radius: 4px;")
        self.btn_sync.clicked.connect(self.start_sync)
        toolbar.addWidget(self.btn_sync)

        self.btn_folder = QPushButton(" Abrir Carpeta")
        self.btn_folder.clicked.connect(self.open_local_folder)
        toolbar.addWidget(self.btn_folder)

        toolbar.addSpacing(10)

        self.btn_mcp = QPushButton("🤖 MCP / IA")
        self.btn_mcp.setStyleSheet("background-color: #107C41; color: white; font-weight: bold; padding: 6px 12px; border-radius: 4px;")
        self.btn_mcp.clicked.connect(self.open_mcp_dialog)
        toolbar.addWidget(self.btn_mcp)

        toolbar.addStretch()

        self.btn_settings = QPushButton("⚙ Configuración")
        self.btn_settings.clicked.connect(self.open_settings)
        toolbar.addWidget(self.btn_settings)

        main_layout.addLayout(toolbar)

        # --- BARRA DE PROGRESO DE DESCARGA / SINCRONIZACIÓN ---
        self.sync_bar_container = QWidget()
        sync_layout = QHBoxLayout(self.sync_bar_container)
        sync_layout.setContentsMargins(8, 2, 8, 2)

        self.sync_status_label = QLabel("Listo.")
        self.sync_status_label.setStyleSheet("color: #444; font-size: 12px;")
        sync_layout.addWidget(self.sync_status_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedHeight(12)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setRange(0, 0) # Indeterminado por defecto
        self.progress_bar.setVisible(False)
        sync_layout.addWidget(self.progress_bar)

        main_layout.addWidget(self.sync_bar_container)

        # --- NAVEGADOR EMBEBIDO (WEBVIEW) ---
        self.webview = QWebEngineView(self)
        self.web_page = QWebEnginePage(self.web_profile, self.webview)
        self.webview.setPage(self.web_page)
        main_layout.addWidget(self.webview)

        self.show_outlook()
        self.worker = None

    def show_outlook(self):
        url = self.cfg.get("outlook_url", "https://outlook.cloud.microsoft/mail/")
        self.webview.setUrl(QUrl(url))

    def show_onedrive(self):
        url = self.cfg.get("onedrive_url", "https://www.office.com/launch/onedrive")
        self.webview.setUrl(QUrl(url))

    def go_back(self):
        self.webview.back()

    def reload_page(self):
        self.webview.reload()

    def open_local_folder(self):
        import subprocess
        path = self.cfg.get("sync_folder", str(Path.home() / "OneDrive"))
        subprocess.Popen(["xdg-open", path])

    def open_settings(self):
        dlg = SettingsDialog(self)
        if dlg.exec():
            self.cfg = get_config()
            self.setWindowTitle(f"M365 Linux Bridge - {self.cfg.get('institution_name', 'Office 365')}")

    def open_mcp_dialog(self):
        dlg = McpDialog(self)
        dlg.exec()

    def start_sync(self):
        self.btn_sync.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0) # Animación de carga activa
        self.sync_status_label.setText("Iniciando sincronización con OneDrive...")

        self.worker = SyncWorker()
        self.worker.progress.connect(self.on_sync_progress)
        self.worker.finished.connect(self.on_sync_finished)
        self.worker.error.connect(self.on_sync_error)
        self.worker.start()

    def on_sync_progress(self, msg, count):
        self.sync_status_label.setText(f"Sincronizando: {msg} (Descargados: {max(count, 0)})")

    def on_sync_finished(self, count):
        self.btn_sync.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.sync_status_label.setText(f"✓ Sincronización completada. Total archivos actualizados: {count}")
        QMessageBox.information(self, "Sincronización M365", f"¡Sincronización completada!\nArchivos actualizados: {count}")

    def on_sync_error(self, err):
        self.btn_sync.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.sync_status_label.setText("⚠ Error durante la sincronización.")
        QMessageBox.warning(self, "Detalle", f"Error:\n{err}")

def main():
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
