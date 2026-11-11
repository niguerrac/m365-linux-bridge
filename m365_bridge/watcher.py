import time
import threading
from pathlib import Path
from typing import Callable, Optional
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileSystemEvent
from m365_bridge.config import get_config
from m365_bridge.sync import is_ignored_path
from m365_bridge.selection import get_rules

class DebouncedSyncHandler(FileSystemEventHandler):
    def __init__(self, callback: Callable[[], None], debounce_seconds: float = 3.5):
        super().__init__()
        self.callback = callback
        self.debounce_seconds = debounce_seconds
        self.lock = threading.Lock()
        self.timer: Optional[threading.Timer] = None

    def on_any_event(self, event: FileSystemEvent):
        src_path = Path(event.src_path)
        cfg = get_config()
        excluded = set(cfg.get("excluded_folders", []))
        sync_folder = Path(cfg.get("sync_folder", str(Path.home() / "OneDrive")))

        if is_ignored_path(src_path, excluded, sync_folder, is_dir=event.is_directory):
            return
        try:
            if not get_rules(cfg).is_selected(src_path.relative_to(sync_folder).parts):
                return
        except ValueError:
            pass

        with self.lock:
            if self.timer:
                self.timer.cancel()
            self.timer = threading.Timer(self.debounce_seconds, self._trigger)
            self.timer.daemon = True
            self.timer.start()

    def _trigger(self):
        try:
            self.callback()
        except Exception as e:
            print("[Watcher] Error executing sync callback:", e)

class LocalFolderWatcher:
    def __init__(self, folder_path: Path, sync_callback: Callable[[], None]):
        self.folder_path = folder_path
        self.sync_callback = sync_callback
        self.observer: Optional[Observer] = None
        self.handler = DebouncedSyncHandler(sync_callback)

    def start(self):
        if self.observer and self.observer.is_alive():
            return
        if not self.folder_path.exists():
            self.folder_path.mkdir(parents=True, exist_ok=True)
            
        self.observer = Observer()
        self.observer.schedule(self.handler, str(self.folder_path), recursive=True)
        self.observer.daemon = True
        self.observer.start()
        print(f"[Watcher] Monitoreando cambios locales en: {self.folder_path}")

    def stop(self):
        if self.observer and self.observer.is_alive():
            self.observer.stop()
            self.observer.join(timeout=2.0)
            self.observer = None
            print("[Watcher] Monitoreo detenido.")
