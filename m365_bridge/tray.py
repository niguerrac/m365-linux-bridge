import sys, os; sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import sys
import subprocess
from pathlib import Path
from PyQt6.QtWidgets import QApplication, QSystemTrayIcon, QMenu
from PyQt6.QtGui import QIcon
from PyQt6.QtCore import QThread, pyqtSignal, QTimer
from m365_bridge.config import get_config
from m365_bridge.sync import run_sync
from m365_bridge.watcher import LocalFolderWatcher

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
        act_sync.triggered.connect(self.start_sync_manual)

        act_folder = menu.addAction("Abrir Carpeta Local")
        act_folder.triggered.connect(self.open_folder)

        menu.addSeparator()

        act_quit = menu.addAction("Salir")
        act_quit.triggered.connect(self.quit_app)

        self.tray.setContextMenu(menu)
        self.tray.show()
        self.worker = None

        # 1. Iniciar vigilante inotify para cambios locales en tiempo real
        cfg = get_config()
        self.sync_folder = Path(cfg.get("sync_folder", str(Path.home() / "OneDrive")))
        self.watcher = LocalFolderWatcher(self.sync_folder, self.start_sync_silent)
        try:
            self.watcher.start()
        except Exception as e:
            print("[Tray] No se pudo iniciar el vigilante inotify:", e)

        # 2. Iniciar temporizador periódico para cambios en la nube
        interval_min = cfg.get("auto_sync_interval_minutes", 60)
        if interval_min > 0:
            self.timer = QTimer(self.app)
            self.timer.timeout.connect(self.start_sync_silent)
            self.timer.start(interval_min * 60 * 1000)

    def open_gui(self):
        subprocess.Popen([str(PYTHON_BIN), str(GUI_SCRIPT)])

    def open_outlook(self):
        cfg = get_config()
        subprocess.Popen(["/usr/bin/chromium", f"--app={cfg['outlook_url']}"])

    def open_onedrive(self):
        cfg = get_config()
        subprocess.Popen(["/usr/bin/chromium", f"--app={cfg['onedrive_url']}"])

    def open_folder(self):
        subprocess.Popen(["xdg-open", str(self.sync_folder)])

    def start_sync_manual(self):
        if self.worker and self.worker.isRunning():
            return
        self.tray.showMessage("M365 Bridge", "Iniciando sincronización bidireccional...", QSystemTrayIcon.MessageIcon.Information, 2500)
        self.worker = SyncWorker()
        self.worker.finished.connect(lambda res: self.on_sync_done(res, is_silent=False))
        self.worker.start()

    def start_sync_silent(self):
        if self.worker and self.worker.isRunning():
            return
        self.worker = SyncWorker()
        self.worker.finished.connect(lambda res: self.on_sync_done(res, is_silent=True))
        self.worker.start()

    def on_sync_done(self, res, is_silent=False):
        if isinstance(res, dict):
            d = res.get("downloaded", 0)
            u = res.get("uploaded", 0)
            if not is_silent or (d > 0 or u > 0):
                self.tray.showMessage(
                    "M365 Bridge",
                    f"Sincronización terminada: ↓{d} descargados, ↑{u} subidos",
                    QSystemTrayIcon.MessageIcon.Information,
                    4000
                )
        elif not is_silent:
            if isinstance(res, int) and res >= 0:
                self.tray.showMessage("M365 Bridge", f"Sincronización terminada ({res} archivos)", QSystemTrayIcon.MessageIcon.Information, 4000)
            else:
                self.tray.showMessage("M365 Bridge", "Error en sincronización", QSystemTrayIcon.MessageIcon.Warning, 4000)

    def quit_app(self):
        if hasattr(self, "watcher") and self.watcher:
            self.watcher.stop()
        self.app.quit()

    def run(self):
        return self.app.exec()

def main():
    app = M365TrayApp()
    sys.exit(app.run())

if __name__ == "__main__":
    main()
