"""Utilidades de la bandeja que no dependen de Qt (testeables sin servidor gráfico).

* ``SingleInstance``   guard de instancia única con ``flock`` (se libera solo si el proceso muere).
* ``choose_qpa_platform`` / ``apply_qpa_platform``  elige la plataforma Qt con fallback a xcb.
* ``wait_until``       espera con reintentos (la bandeja de KDE puede tardar al iniciar sesión).
"""
import errno
import os
import socket
import time
from pathlib import Path
from typing import Callable, Mapping, MutableMapping, Optional

try:
    import fcntl
except ImportError:  # pragma: no cover - solo Linux/macOS
    fcntl = None


class SingleInstance:
    """Lockfile con PID. ``acquire()`` devuelve False si otra instancia viva lo tiene."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._fh = None

    def acquire(self) -> bool:
        if self._fh is not None:
            return True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(self.path, "a+")
        try:
            if fcntl is not None:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            fh.close()
            if e.errno in (errno.EAGAIN, errno.EACCES, errno.EWOULDBLOCK):
                return False
            raise
        fh.seek(0)
        fh.truncate()
        fh.write(str(os.getpid()))
        fh.flush()
        self._fh = fh
        return True

    def owner_pid(self) -> Optional[int]:
        try:
            return int(self.path.read_text().strip())
        except (OSError, ValueError):
            return None

    def release(self) -> None:
        if self._fh is None:
            return
        try:
            if fcntl is not None:
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        finally:
            self._fh.close()
            self._fh = None
        try:
            self.path.unlink()
        except OSError:
            pass


def _wayland_socket_ok(env: Mapping[str, str]) -> bool:
    name = env.get("WAYLAND_DISPLAY")
    if not name:
        return False
    path = Path(name) if os.path.isabs(name) else Path(env.get("XDG_RUNTIME_DIR", "")) / name
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(1)
        try:
            s.connect(str(path))
        finally:
            s.close()
        return True
    except OSError:
        return False


def choose_qpa_platform(env: Mapping[str, str],
                        wayland_ok: Optional[Callable[[Mapping[str, str]], bool]] = None) -> Optional[str]:
    """Valor de QT_QPA_PLATFORM a usar, o None si no hay que tocar nada.

    Respeta un valor ya fijado por el usuario. Con Wayland usable pide ``wayland;xcb`` (Qt prueba
    en orden y cae a xcb si el plugin wayland no carga); sin socket Wayland pero con DISPLAY, ``xcb``.
    """
    if env.get("QT_QPA_PLATFORM"):
        return None
    wayland_ok = wayland_ok or _wayland_socket_ok
    has_x = bool(env.get("DISPLAY"))
    if wayland_ok(env):
        return "wayland;xcb" if has_x else "wayland"
    if has_x:
        return "xcb"
    return None


def apply_qpa_platform(env: Optional[MutableMapping[str, str]] = None, wayland_ok=None) -> Optional[str]:
    env = os.environ if env is None else env
    value = choose_qpa_platform(env, wayland_ok)
    if value:
        env["QT_QPA_PLATFORM"] = value
    return value


def wait_until(predicate: Callable[[], bool], timeout: float = 30.0, interval: float = 1.0,
               sleep: Callable[[float], None] = time.sleep,
               clock: Callable[[], float] = time.monotonic) -> bool:
    """Reintenta ``predicate`` hasta ``timeout`` s. Devuelve True en cuanto se cumple."""
    deadline = clock() + timeout
    while True:
        if predicate():
            return True
        if clock() >= deadline:
            return False
        sleep(interval)
