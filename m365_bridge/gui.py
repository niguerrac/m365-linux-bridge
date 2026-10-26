import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from pathlib import Path
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QLineEdit, QSpinBox, QFileDialog, QTabWidget,
    QMessageBox, QDialog, QFormLayout
)
from PyQt6.QtGui import QIcon
from PyQt6.QtCore import Qt, QUrl, QThread, pyqtSignal
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEngineProfile, QWebEnginePage

from m365_bridge.config import get_config, save_config, DATA_DIR
from m365_bridge.sync import run_sync

ICON_PATH = Path(__file__).parent.parent / "icons" / "m365.png"

class SyncWorker(QThread):
    finished = pyqtSignal(int)
    error = pyqtSignal(str)

    def run(self):
        import asyncio
        try:
            count = asyncio.run(run_sync())
            self.finished.emit(count)
        except Exception as e:
            self.error.emit(str(e))

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

        # Perfil persistente embebido para que la sesión de Outlook/OneDrive quede guardada dentro de la app
        profile_storage = DATA_DIR / "webengine_profile"
        profile_storage.mkdir(parents=True, exist_ok=True)
        self.web_profile = QWebEngineProfile("M365Profile", self)
        self.web_profile.setPersistentStoragePath(str(profile_storage))
        self.web_profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.AllowPersistentCookies)

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(4, 4, 4, 4)

        # --- BARRA DE HERRAMIENTAS INTEGRADA ---
        toolbar = QHBoxLayout()
        
        self.btn_outlook = QPushButton(" Outlook (Correo)")
        self.btn_outlook.setStyleSheet("font-weight: bold; padding: 6px 12px;")
        self.btn_outlook.clicked.connect(self.show_outlook)
        toolbar.addWidget(self.btn_outlook)

        self.btn_onedrive = QPushButton(" OneDrive (Archivos)")
        self.btn_onedrive.setStyleSheet("font-weight: bold; padding: 6px 12px;")
        self.btn_onedrive.clicked.connect(self.show_onedrive)
        toolbar.addWidget(self.btn_onedrive)

        toolbar.addSpacing(15)

        self.btn_back = QPushButton("◀")
        self.btn_back.setToolTip("Atrás")
        self.btn_back.clicked.connect(self.go_back)
        toolbar.addWidget(self.btn_back)

        self.btn_reload = QPushButton("⟳")
        self.btn_reload.setToolTip("Recargar")
        self.btn_reload.clicked.connect(self.reload_page)
        toolbar.addWidget(self.btn_reload)

        toolbar.addSpacing(15)

        self.btn_sync = QPushButton(" Sincronizar Ahora")
        self.btn_sync.setStyleSheet("background-color: #0078D4; color: white; font-weight: bold; padding: 6px 12px; border-radius: 4px;")
        self.btn_sync.clicked.connect(self.start_sync)
        toolbar.addWidget(self.btn_sync)

        self.btn_folder = QPushButton(" Abrir Carpeta Local")
        self.btn_folder.clicked.connect(self.open_local_folder)
        toolbar.addWidget(self.btn_folder)

        toolbar.addStretch()

        self.sync_status = QLabel("Listo.")
        self.sync_status.setStyleSheet("color: #666; margin-right: 10px;")
        toolbar.addWidget(self.sync_status)

        self.btn_settings = QPushButton("⚙ Configuración")
        self.btn_settings.clicked.connect(self.open_settings)
        toolbar.addWidget(self.btn_settings)

        main_layout.addLayout(toolbar)

        # --- NAVEGADOR EMBEBIDO (WEBVIEW INTEGRADO) ---
        self.webview = QWebEngineView(self)
        self.web_page = QWebEnginePage(self.web_profile, self.webview)
        self.webview.setPage(self.web_page)
        
        main_layout.addWidget(self.webview)

        # Cargar Outlook por defecto adentro de la app
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

    def start_sync(self):
        self.btn_sync.setEnabled(False)
        self.sync_status.setText("Sincronizando...")
        self.worker = SyncWorker()
        self.worker.finished.connect(self.on_sync_finished)
        self.worker.error.connect(self.on_sync_error)
        self.worker.start()

    def on_sync_finished(self, count):
        self.btn_sync.setEnabled(True)
        self.sync_status.setText(f"Sincronizados: {count}")
        QMessageBox.information(self, "Sincronización M365", f"¡Sincronización completada!\nArchivos actualizados: {count}")

    def on_sync_error(self, err):
        self.btn_sync.setEnabled(True)
        self.sync_status.setText("Error al sincronizar.")
        QMessageBox.warning(self, "Detalle", f"Error:\n{err}")

def main():
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
