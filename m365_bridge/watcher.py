import time
import threading
from pathlib import Path
from typing import Callable, Optional
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileSystemEvent
from m365_bridge.config import get_config

IGNORED_EXTENSIONS = {".tmp", ".crdownload", ".swp", ".bak", ".part"}
IGNORED_PREFIXES = (".~", "~$", ".", "~")

class DebouncedSyncHandler(FileSystemEventHandler):
    def __init__(self, callback: Callable[[], None], debounce_seconds: float = 3.5):
        super().__init__()
        self.callback = callback
        self.debounce_seconds = debounce_seconds
        self.lock = threading.Lock()
        self.timer: Optional[threading.Timer] = None

    def on_any_event(self, event: FileSystemEvent):
        if event.is_directory:
            return
        
        filename = Path(event.src_path).name
        if filename.startswith(IGNORED_PREFIXES) or Path(event.src_path).suffix.lower() in IGNORED_EXTENSIONS:
            return

        cfg = get_config()
        excluded = set(cfg.get("excluded_folders", []))
        parts = Path(event.src_path).parts
        if any(ex in parts for ex in excluded):
            return

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
