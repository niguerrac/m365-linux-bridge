import sys, os; sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import sys
import os
import subprocess
from pathlib import Path
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QLineEdit, QSpinBox, QFileDialog, QTabWidget,
    QMessageBox, QDialog, QFormLayout
)
from PyQt6.QtGui import QIcon
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from m365_bridge.config import get_config, save_config, BROWSER_PROFILE_DIR, DATA_DIR
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

        # Carpeta local
        folder_layout = QHBoxLayout()
        self.folder_input = QLineEdit(self.cfg.get("sync_folder", ""))
        self.btn_browse = QPushButton("Examinar...")
        self.btn_browse.clicked.connect(self.browse_folder)
        folder_layout.addWidget(self.folder_input)
        folder_layout.addWidget(self.btn_browse)
        layout.addRow("Carpeta Local OneDrive:", folder_layout)

        # Límite de tamaño
        self.max_mb_input = QSpinBox()
        self.max_mb_input.setRange(10, 50000)
        self.max_mb_input.setValue(self.cfg.get("max_file_size_mb", 200))
        self.max_mb_input.setSuffix(" MB")
        layout.addRow("Límite tamaño descarga:", self.max_mb_input)

        # Botones
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
        self.resize(1100, 750)
        if ICON_PATH.exists():
            self.setWindowIcon(QIcon(str(ICON_PATH)))

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)

        # --- BARRA DE HERRAMIENTAS SUPERIOR ---
        toolbar = QHBoxLayout()
        
        self.btn_outlook = QPushButton(" Abrir Outlook")
        self.btn_outlook.setStyleSheet("font-weight: bold; padding: 8px 14px;")
        self.btn_outlook.clicked.connect(self.launch_outlook)
        toolbar.addWidget(self.btn_outlook)

        self.btn_onedrive = QPushButton(" Abrir OneDrive Web")
        self.btn_onedrive.setStyleSheet("font-weight: bold; padding: 8px 14px;")
        self.btn_onedrive.clicked.connect(self.launch_onedrive)
        toolbar.addWidget(self.btn_onedrive)

        toolbar.addSpacing(20)

        self.btn_sync = QPushButton(" Sincronizar Ahora")
        self.btn_sync.setStyleSheet("background-color: #0078D4; color: white; font-weight: bold; padding: 8px 14px; border-radius: 4px;")
        self.btn_sync.clicked.connect(self.start_sync)
        toolbar.addWidget(self.btn_sync)

        self.btn_folder = QPushButton(" Abrir Carpeta Local")
        self.btn_folder.clicked.connect(self.open_local_folder)
        toolbar.addWidget(self.btn_folder)

        toolbar.addStretch()

        self.btn_settings = QPushButton("⚙ Configuración")
        self.btn_settings.clicked.connect(self.open_settings)
        toolbar.addWidget(self.btn_settings)

        main_layout.addLayout(toolbar)

        # --- PANEL CENTRAL DE INFORMACIÓN Y ESTADO ---
        self.info_panel = QWidget()
        info_layout = QVBoxLayout(self.info_panel)
        info_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.status_title = QLabel("M365 Linux Bridge está Activo")
        self.status_title.setStyleSheet("font-size: 20px; font-weight: bold; color: #0078D4;")
        info_layout.addWidget(self.status_title, alignment=Qt.AlignmentFlag.AlignCenter)

        self.status_desc = QLabel(
            "Tu sesión institucional se encuentra vinculada.\n"
            "Tanto el servicio de IA (MCP) como la sincronización local de archivos están habilitados."
        )
        self.status_desc.setStyleSheet("font-size: 13px; color: #555; margin-top: 10px; line-height: 1.4;")
        self.status_desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        info_layout.addWidget(self.status_desc)

        self.sync_status = QLabel("Listo para sincronizar.")
        self.sync_status.setStyleSheet("margin-top: 20px; font-weight: bold;")
        info_layout.addWidget(self.sync_status, alignment=Qt.AlignmentFlag.AlignCenter)

        main_layout.addWidget(self.info_panel)

        self.worker = None

    def launch_outlook(self):
        self.launch_browser_app("https://outlook.cloud.microsoft/mail/")

    def launch_onedrive(self):
        self.launch_browser_app("https://outlook.cloud.microsoft/host/377c982d-9686-450e-9a7c-22aeaf1bc162/7211f19f-262a-42eb-a02e-289956491741")

    def launch_browser_app(self, url):
        port = self.cfg.get("remote_debugging_port", 9222)
        subprocess.run(["pkill", "-f", "m365-bridge.*browser_profile"])
        for lock in ["SingletonLock", "SingletonCookie", "SingletonSocket"]:
            p = BROWSER_PROFILE_DIR / lock
            if p.exists() or p.is_symlink():
                try: p.unlink()
                except Exception: pass

        subprocess.Popen([
            "/usr/bin/chromium",
            f"--user-data-dir={BROWSER_PROFILE_DIR}",
            f"--remote-debugging-port={port}",
            "--no-first-run",
            "--no-default-browser-check",
            f"--app={url}"
        ])

    def open_local_folder(self):
        path = self.cfg.get("sync_folder", str(Path.home() / "M365-Drive"))
        subprocess.Popen(["xdg-open", path])

    def open_settings(self):
        dlg = SettingsDialog(self)
        if dlg.exec():
            self.cfg = get_config()
            self.setWindowTitle(f"M365 Linux Bridge - {self.cfg.get('institution_name', 'Office 365')}")

    def start_sync(self):
        self.btn_sync.setEnabled(False)
        self.sync_status.setText("Sincronizando archivos con OneDrive...")
        self.worker = SyncWorker()
        self.worker.finished.connect(self.on_sync_finished)
        self.worker.error.connect(self.on_sync_error)
        self.worker.start()

    def on_sync_finished(self, count):
        self.btn_sync.setEnabled(True)
        self.sync_status.setText(f"Sincronización finalizada. Archivos actualizados: {count}")
        QMessageBox.information(self, "Sincronización M365", f"¡Sincronización completada!\nArchivos actualizados: {count}")

    def on_sync_error(self, err):
        self.btn_sync.setEnabled(True)
        self.sync_status.setText("Error en la sincronización.")
        QMessageBox.warning(self, "Detalle de sincronización", f"Ocurrió un error al sincronizar:\n{err}")

def main():
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
