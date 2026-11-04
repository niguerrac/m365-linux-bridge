"""Deteccion de aceleracion grafica y fallback automatico a render por software.

Debe importarse y ejecutarse ANTES de importar PyQt6: QtWebEngine (Chromium) lee
estas variables de entorno al inicializarse. En equipos sin EGL funcional
(tablets/GPU antiguas, VMs, drivers incompletos) Chromium aborta con
'Failed to get system egl display'; en ese caso forzamos render por software.

Control manual:
  M365_SOFTWARE_RENDER=1  fuerza software
  M365_SOFTWARE_RENDER=0  desactiva el fallback automatico
"""
import ctypes
import sys
import os


def _egl_available() -> bool:
    """True si se puede inicializar un display EGL en este sistema."""
    try:
        egl = ctypes.CDLL("libEGL.so.1")
    except OSError:
        return False
    try:
        egl.eglGetDisplay.restype = ctypes.c_void_p
        egl.eglGetDisplay.argtypes = [ctypes.c_void_p]
        egl.eglInitialize.restype = ctypes.c_uint
        egl.eglInitialize.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int),
        ]
        display = egl.eglGetDisplay(None)
        if not display:
            return False
        major, minor = ctypes.c_int(), ctypes.c_int()
        return bool(egl.eglInitialize(display, ctypes.byref(major), ctypes.byref(minor)))
    except Exception:
        return False


def apply_render_fallback() -> bool:
    """Configura render por software si hace falta. Devuelve True si lo activo."""
    forced = os.environ.get("M365_SOFTWARE_RENDER")
    if forced == "0":
        return False
    if forced != "1" and _egl_available():
        return False

    os.environ.setdefault("QT_QUICK_BACKEND", "software")
    flags = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
    if "--disable-gpu" not in flags:
        os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (flags + " --disable-gpu").strip()
    # Evita que libva cargue un driver del sistema contra el libstdc++ empaquetado.
    os.environ.setdefault("LIBVA_DRIVER_NAME", "dummy")
    print("[m365-bridge] EGL no disponible: usando render por software.", file=sys.stderr)
    return True
