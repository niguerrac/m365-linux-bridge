#!/usr/bin/env bash
# Instalador y actualizador de M365 Linux Bridge.
#
# Instalar o actualizar (mismo comando):
#   curl -fsSL https://raw.githubusercontent.com/niguerrac/m365-linux-bridge/main/install.sh | bash
#
# Variables opcionales:
#   M365_INSTALL_DIR   carpeta de instalación (por defecto ~/.local/share/m365-linux-bridge)
#   M365_VERSION       tag a instalar, ej. v1.0.4 (por defecto el último release)
#   M365_NO_DESKTOP=1  no crear accesos en el menú ni autostart (para pruebas)
#   M365_RESTART_TRAY=1  reiniciar la bandeja al terminar (lo usa el botón Actualizar)
#
# Si se ejecuta como ./install.sh dentro de un clon de git, instala ESE clon tal cual
# (modo desarrollo, no toca git).
set -euo pipefail

REPO_URL="https://github.com/niguerrac/m365-linux-bridge.git"
MARKER=".m365-install"

echo "=== M365 Linux Bridge: instalación / actualización ==="

command -v git >/dev/null || { echo "ERROR: falta git (Arch: sudo pacman -S git | Debian/Ubuntu: sudo apt install git)"; exit 1; }

SCRIPT_PATH="${BASH_SOURCE[0]:-}"
if [ -z "${M365_INSTALL_DIR:-}" ] && [ -n "$SCRIPT_PATH" ] && [ -f "$SCRIPT_PATH" ] \
   && [ -d "$(dirname "$SCRIPT_PATH")/m365_bridge" ]; then
    # ./install.sh desde un clon: instalar ese clon sin tocar git
    DIR="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)"
    echo "Instalando el clon local: $DIR"
else
    DIR="${M365_INSTALL_DIR:-$HOME/.local/share/m365-linux-bridge}"
    if [ ! -d "$DIR/.git" ]; then
        echo "Descargando en $DIR…"
        mkdir -p "$(dirname "$DIR")"
        git clone -q "$REPO_URL" "$DIR"
        touch "$DIR/$MARKER"
    fi
    cd "$DIR"
    git fetch -q --tags origin
    if [ -f "$DIR/$MARKER" ]; then
        TAG="${M365_VERSION:-$(git tag -l 'v*' --sort=-v:refname | head -n1)}"
        [ -n "$TAG" ] || { echo "ERROR: el repositorio no tiene releases (tags v*)"; exit 1; }
        echo "Versión: $TAG"
        git -c advice.detachedHead=false checkout -q --force "$TAG"
    else
        # Clon de desarrollo: solo avanzar la rama actual si está limpia
        if ! git diff --quiet || ! git diff --cached --quiet; then
            echo "ERROR: $DIR es un clon de desarrollo con cambios sin commitear; no se actualiza."
            echo "       Haz commit o stash y vuelve a intentar."
            exit 1
        fi
        BRANCH="$(git rev-parse --abbrev-ref HEAD)"
        echo "Clon de desarrollo en la rama $BRANCH: git pull --ff-only"
        git pull -q --ff-only origin "$BRANCH"
    fi
fi
cd "$DIR"

# Gestor de entornos uv
if ! command -v uv >/dev/null 2>&1; then
    if [ -x "$HOME/.local/bin/uv" ]; then
        export PATH="$HOME/.local/bin:$PATH"
    else
        echo "Instalando gestor de entornos uv…"
        curl -LsSf https://astral.sh/uv/install.sh | sh
        export PATH="$HOME/.local/bin:$PATH"
    fi
fi

# Requisito del sistema: Chromium (lo usa el login interactivo de Microsoft)
if ! command -v chromium >/dev/null && ! command -v chromium-browser >/dev/null && ! command -v google-chrome-stable >/dev/null; then
    echo "AVISO: no se encontró Chromium. Instálalo antes de iniciar sesión:"
    echo "  Arch/CachyOS: sudo pacman -S chromium | Debian/Ubuntu: sudo apt install chromium | Fedora: sudo dnf install chromium"
fi

echo "Configurando entorno Python…"
[ -x "$DIR/.venv/bin/python" ] || uv venv -q "$DIR/.venv"
uv pip install -q --python "$DIR/.venv/bin/python" playwright "mcp<2" PyQt6 PyQt6-WebEngine httpx pillow watchdog

VERSION="$("$DIR/.venv/bin/python" -c 'import sys; sys.path.insert(0, "."); from m365_bridge import __version__; print(__version__)' 2>/dev/null || true)"
[ -n "$VERSION" ] || VERSION="$(echo "${TAG:-?}" | sed 's/^v//')"

if [ "${M365_NO_DESKTOP:-0}" != "1" ]; then
    APPLICATIONS_DIR="$HOME/.local/share/applications"
    AUTOSTART_DIR="$HOME/.config/autostart"
    ICON_DIR="$HOME/.local/share/icons/hicolor/64x64/apps"
    mkdir -p "$APPLICATIONS_DIR" "$AUTOSTART_DIR" "$ICON_DIR"
    # Icono registrado en el tema: Icon=<nombre> funciona en cualquier menú/lanzador
    # (con ruta absoluta algunos solo muestran el genérico)
    cp "$DIR/icons/m365.png" "$ICON_DIR/m365-linux-bridge.png"
    command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q -t "$HOME/.local/share/icons/hicolor" 2>/dev/null || true

    cat << EOF > "$APPLICATIONS_DIR/m365-linux-bridge.desktop"
[Desktop Entry]
Type=Application
Name=M365 Linux Bridge
Comment=Suite de integración de Outlook, OneDrive y MCP para Linux
Exec=$DIR/.venv/bin/python $DIR/m365_bridge/gui.py
Icon=m365-linux-bridge
Terminal=false
Categories=Office;Network;Email;FileManager;
EOF

    cat << EOF > "$AUTOSTART_DIR/m365-bridge-tray.desktop"
[Desktop Entry]
Type=Application
Name=M365 Bridge Tray
Comment=Icono de bandeja y sincronizador de Microsoft 365
Exec=$DIR/.venv/bin/python $DIR/m365_bridge/tray.py
Icon=m365-linux-bridge
Terminal=false
Categories=Network;Office;
StartupNotify=false
EOF
    command -v update-desktop-database >/dev/null && update-desktop-database -q "$APPLICATIONS_DIR" || true
fi

if [ "${M365_RESTART_TRAY:-0}" = "1" ] && pgrep -f "$DIR/m365_bridge/tray.py" >/dev/null; then
    echo "Reiniciando la bandeja…"
    pkill -f "$DIR/m365_bridge/tray.py" || true
    sleep 1
    setsid nohup "$DIR/.venv/bin/python" "$DIR/m365_bridge/tray.py" >/dev/null 2>&1 < /dev/null &
fi

echo ""
echo "¡Listo! M365 Linux Bridge v$VERSION en $DIR"
echo "Búscala como 'M365 Linux Bridge' en el menú de aplicaciones."
echo "Para actualizar más adelante, vuelve a ejecutar el mismo comando (o usa Configuración → Buscar actualizaciones)."
