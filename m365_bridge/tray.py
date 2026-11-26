import sys, os; sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import sys
import subprocess
from pathlib import Path
from PyQt6.QtWidgets import QApplication, QSystemTrayIcon, QMenu
from PyQt6.QtGui import QIcon, QActionGroup
from PyQt6.QtCore import QThread, pyqtSignal, QTimer
from m365_bridge.config import (
    get_config, list_accounts, get_active_account_id, set_active_account, ACCOUNT_ENV_VAR,
    browser_profile_dir,
)
from m365_bridge.browser import cleanup_singleton_locks, find_chromium
from m365_bridge.sync import run_sync
from m365_bridge.watcher import LocalFolderWatcher

BRIDGE_DIR = Path(__file__).parent.parent
PYTHON_BIN = BRIDGE_DIR / ".venv" / "bin" / "python"
GUI_SCRIPT = BRIDGE_DIR / "m365_bridge" / "gui.py"
ICON_PATH = BRIDGE_DIR / "icons" / "m365.png"

class SyncWorker(QThread):
    finished = pyqtSignal(object)

    def __init__(self, account=None):
        super().__init__()
        self.account = account or get_active_account_id()

    def run(self):
        import asyncio
        from m365_bridge.sync import get_sync_logger
        logger = get_sync_logger(self.account)
        try:
            res = asyncio.run(run_sync(account=self.account))
            self.finished.emit(res)
        except Exception as e:
            logger.exception(f"[Tray] Error en ciclo de sincronización: {e}")
            self.finished.emit(None)

class M365TrayApp:
    def __init__(self):
        # --account <id> fija la cuenta inicial de la bandeja (si no, M365_ACCOUNT / por defecto)
        if "--account" in sys.argv:
            i = sys.argv.index("--account")
            if i + 1 < len(sys.argv):
                set_active_account(sys.argv[i + 1])
        self.app = QApplication(sys.argv)
        self.app.setQuitOnLastWindowClosed(False)

        self.icon = QIcon(str(ICON_PATH)) if ICON_PATH.exists() else QIcon()
        self.tray = QSystemTrayIcon(self.icon, self.app)
        self.tray.setToolTip("M365 Linux Bridge (Outlook & OneDrive)")

        menu = QMenu()
        
        act_gui = menu.addAction("Abrir Panel Principal")
        act_gui.triggered.connect(self.open_gui)

        menu.addSeparator()

        self.menu_accounts = menu.addMenu("Cuenta")
        self.rebuild_accounts_menu()

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

        act_selective = menu.addAction("Carpetas sincronizadas…")
        act_selective.triggered.connect(self.open_selective_sync)

        menu.addSeparator()

        act_quit = menu.addAction("Salir")
        act_quit.triggered.connect(self.quit_app)

        self.tray.setContextMenu(menu)
        self.tray.show()
        self.worker = None

        # 1. Iniciar vigilante inotify para cambios locales en tiempo real
        cfg = get_config()
        self.watcher = None
        self.start_watcher()

        # 2. Iniciar temporizador periódico para cambios en la nube
        interval_min = cfg.get("auto_sync_interval_minutes", 60)
        if interval_min > 0:
            self.timer = QTimer(self.app)
            self.timer.timeout.connect(self.start_sync_silent)
            self.timer.start(interval_min * 60 * 1000)

    # --- cuentas -------------------------------------------------------
    def start_watcher(self):
        """(Re)inicia el vigilante sobre la carpeta de la cuenta activa."""
        if self.watcher:
            try:
                self.watcher.stop()
            except Exception:
                pass
        cfg = get_config()
        self.sync_folder = Path(cfg.get("sync_folder", str(Path.home() / "OneDrive")))
        self.watcher = LocalFolderWatcher(self.sync_folder, self.start_sync_silent)
        try:
            self.watcher.start()
        except Exception as e:
            print("[Tray] No se pudo iniciar el vigilante inotify:", e)

    def rebuild_accounts_menu(self):
        self.menu_accounts.clear()
        active = get_active_account_id()
        group = QActionGroup(self.menu_accounts)
        group.setExclusive(True)
        for a in list_accounts():
            label = a["alias"] + (f" <{a['email']}>" if a["email"] else "")
            act = self.menu_accounts.addAction(label)
            act.setCheckable(True)
            act.setChecked(a["id"] == active)
            group.addAction(act)
            act.triggered.connect(lambda _checked=False, acc=a["id"]: self.switch_account(acc))
        self.menu_accounts.addSeparator()
        act_sync_all = self.menu_accounts.addAction("Sincronizar todas las cuentas")
        act_sync_all.triggered.connect(self.start_sync_all)
        act_manage = self.menu_accounts.addAction("Administrar cuentas…")
        act_manage.triggered.connect(self.open_accounts)

    def switch_account(self, account):
        if self.worker and self.worker.isRunning():
            self.tray.showMessage("M365 Bridge", "Espera a que termine la sincronización en curso", QSystemTrayIcon.MessageIcon.Warning, 3000)
            self.rebuild_accounts_menu()
            return
        set_active_account(account)
        self.start_watcher()
        self.rebuild_accounts_menu()
        self.tray.setToolTip(f"M365 Linux Bridge - {get_config().get('account_alias', account)}")

    def open_accounts(self):
        from m365_bridge.accounts_dialog import AccountsDialog
        self._accounts_dlg = AccountsDialog(on_change=self.rebuild_accounts_menu)
        self._accounts_dlg.show()

    def start_sync_all(self):
        """Sincroniza cada cuenta, una tras otra."""
        if self.worker and self.worker.isRunning():
            return
        pending = [a["id"] for a in list_accounts()]

        def next_one(_res=None):
            if not pending:
                return
            self.worker = SyncWorker(pending.pop(0))
            self.worker.finished.connect(lambda res: (self.on_sync_done(res, is_silent=True), next_one()))
            self.worker.start()
        next_one()

    def open_gui(self):
        subprocess.Popen([str(PYTHON_BIN), str(GUI_SCRIPT), "--account", get_active_account_id()])

    def _open_chromium_app(self, url: str):
        acc = get_active_account_id()
        cleanup_singleton_locks(acc)
        profile_dir = browser_profile_dir(acc)
        profile_dir.mkdir(parents=True, exist_ok=True)
        subprocess.Popen([
            find_chromium(),
            f"--user-data-dir={profile_dir}",
            f"--class=m365-bridge-{acc}",
            "--no-first-run",
            "--no-default-browser-check",
            f"--app={url}",
        ])

    def open_outlook(self):
        cfg = get_config()
        self._open_chromium_app(cfg["outlook_url"])

    def open_onedrive(self):
        cfg = get_config()
        self._open_chromium_app(cfg["onedrive_url"])

    def open_folder(self):
        subprocess.Popen(["xdg-open", str(self.sync_folder)])

    def open_selective_sync(self):
        from m365_bridge.selective_dialog import SelectiveSyncDialog
        if getattr(self, "_selective_dlg", None) and self._selective_dlg.isVisible():
            self._selective_dlg.raise_()
            return
        self._selective_dlg = SelectiveSyncDialog()
        self._selective_dlg.accepted.connect(self.start_sync_silent)
        self._selective_dlg.show()

    def start_sync_manual(self):
        if self.worker and self.worker.isRunning():
            return
        self.tray.showMessage("M365 Bridge", "Iniciando sincronización bidireccional...", QSystemTrayIcon.MessageIcon.Information, 2500)
        self.worker = SyncWorker(get_active_account_id())
        self.worker.finished.connect(lambda res: self.on_sync_done(res, is_silent=False))
        self.worker.start()

    def start_sync_silent(self):
        if self.worker and self.worker.isRunning():
            return
        self.worker = SyncWorker(get_active_account_id())
        self.worker.finished.connect(lambda res: self.on_sync_done(res, is_silent=True))
        self.worker.start()

    def on_sync_done(self, res, is_silent=False):
        if isinstance(res, dict):
            d = res.get("downloaded", 0)
            u = res.get("uploaded", 0)
            del_c = res.get("deleted", 0)
            if not is_silent or (d > 0 or u > 0 or del_c > 0):
                self.tray.showMessage(
                    "M365 Bridge",
                    f"Sincronización terminada: ↓{d} descargados, ↑{u} subidos, 🗑{del_c} reciclados",
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
