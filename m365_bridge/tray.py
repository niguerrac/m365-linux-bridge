import sys, os; sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import sys
import subprocess
from pathlib import Path
from PyQt6.QtWidgets import QApplication, QSystemTrayIcon, QMenu
from PyQt6.QtGui import QIcon
from PyQt6.QtCore import QThread, pyqtSignal
from m365_bridge.config import get_config
from m365_bridge.sync import run_sync

BRIDGE_DIR = Path(__file__).parent.parent
PYTHON_BIN = BRIDGE_DIR / ".venv" / "bin" / "python"
GUI_SCRIPT = BRIDGE_DIR / "m365_bridge" / "gui.py"
ICON_PATH = BRIDGE_DIR / "icons" / "m365.png"

class SyncWorker(QThread):
    finished = pyqtSignal(object)

    def run(self):
        import asyncio
        try:
            res = asyncio.run(run_sync())
            self.finished.emit(res)
        except Exception:
            self.finished.emit(None)

class M365TrayApp:
    def __init__(self):
        self.app = QApplication(sys.argv)
        self.app.setQuitOnLastWindowClosed(False)

        self.icon = QIcon(str(ICON_PATH)) if ICON_PATH.exists() else QIcon()
        self.tray = QSystemTrayIcon(self.icon, self.app)
        self.tray.setToolTip("M365 Linux Bridge (Outlook & OneDrive)")

        menu = QMenu()
        
        act_gui = menu.addAction("Abrir Panel Principal")
        act_gui.triggered.connect(self.open_gui)

        menu.addSeparator()

        act_outlook = menu.addAction("Abrir Outlook")
        act_outlook.triggered.connect(self.open_outlook)

        act_onedrive = menu.addAction("Abrir OneDrive")
        act_onedrive.triggered.connect(self.open_onedrive)

        menu.addSeparator()

        act_sync = menu.addAction("Sincronizar OneDrive Ahora")
        act_sync.triggered.connect(self.start_sync)

        act_folder = menu.addAction("Abrir Carpeta Local")
        act_folder.triggered.connect(self.open_folder)

        menu.addSeparator()

        act_quit = menu.addAction("Salir")
        act_quit.triggered.connect(self.app.quit)

        self.tray.setContextMenu(menu)
        self.tray.show()
        self.worker = None

    def open_gui(self):
        subprocess.Popen([str(PYTHON_BIN), str(GUI_SCRIPT)])

    def open_outlook(self):
        cfg = get_config()
        subprocess.Popen(["/usr/bin/chromium", f"--app={cfg['outlook_url']}"])

    def open_onedrive(self):
        cfg = get_config()
        subprocess.Popen(["/usr/bin/chromium", f"--app={cfg['onedrive_url']}"])

    def open_folder(self):
        cfg = get_config()
        subprocess.Popen(["xdg-open", cfg.get("sync_folder", str(Path.home() / "M365-Drive"))])

    def start_sync(self):
        self.tray.showMessage("M365 Bridge", "Iniciando sincronización...", QSystemTrayIcon.MessageIcon.Information, 3000)
        self.worker = SyncWorker()
        self.worker.finished.connect(self.on_sync_done)
        self.worker.start()

    def on_sync_done(self, res):
        if isinstance(res, dict):
            d = res.get("downloaded", 0)
            u = res.get("uploaded", 0)
            self.tray.showMessage(
                "M365 Bridge",
                f"Sincronización terminada: ↓{d} descargados, ↑{u} subidos",
                QSystemTrayIcon.MessageIcon.Information,
                4000
            )
        elif isinstance(res, int) and res >= 0:
            self.tray.showMessage("M365 Bridge", f"Sincronización terminada ({res} archivos)", QSystemTrayIcon.MessageIcon.Information, 4000)
        else:
            self.tray.showMessage("M365 Bridge", "Error en sincronización", QSystemTrayIcon.MessageIcon.Warning, 4000)

    def run(self):
        return self.app.exec()

def main():
    app = M365TrayApp()
    sys.exit(app.run())

if __name__ == "__main__":
    main()
