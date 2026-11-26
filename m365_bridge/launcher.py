"""Lanzador de la app desde el menú: asegura la bandeja y luego abre la GUI.

tray.py tiene guard de instancia única, así que lanzarlo de más es inocuo.
"""
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main():
    try:
        subprocess.Popen(
            [sys.executable, str(HERE / "tray.py")],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as e:
        print(f"[Launcher] No se pudo lanzar la bandeja: {e}", file=sys.stderr)
    os.execv(sys.executable, [sys.executable, str(HERE / "gui.py"), *sys.argv[1:]])


if __name__ == "__main__":
    main()
