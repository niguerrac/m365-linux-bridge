#!/usr/bin/env bash
set -e

echo "=== Instalando M365 Linux Bridge ==="

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

# Instalar uv si no existe
if ! command -v uv &> /dev/null; then
    echo "Instalando gestor de entornos uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
fi

echo "Configurando entorno virtual Python..."
uv venv
uv pip install playwright "mcp<2" PyQt6 httpx pillow

# Crear accesos de escritorio (.desktop)
APPLICATIONS_DIR="$HOME/.local/share/applications"
mkdir -p "$APPLICATIONS_DIR"

cat << EOF > "$APPLICATIONS_DIR/m365-linux-bridge.desktop"
[Desktop Entry]
Type=Application
Name=M365 Linux Bridge
Comment=Suite de integracion de Outlook, OneDrive y MCP para Linux
Exec=$DIR/.venv/bin/python $DIR/m365_bridge/gui.py
Icon=$DIR/icons/m365.png
Terminal=false
Categories=Office;Network;Email;FileManager;
EOF

# Configurar autostart para el tray
AUTOSTART_DIR="$HOME/.config/autostart"
mkdir -p "$AUTOSTART_DIR"

cat << EOF > "$AUTOSTART_DIR/m365-bridge-tray.desktop"
[Desktop Entry]
Type=Application
Name=M365 Bridge Tray
Comment=Icono de bandeja y sincronizador de Microsoft 365
Exec=$DIR/.venv/bin/python $DIR/m365_bridge/tray.py
Icon=$DIR/icons/m365.png
Terminal=false
Categories=Network;Office;
StartupNotify=false
EOF

echo ""
echo "¡Instalación completada con éxito!"
echo "Puedes iniciar la aplicación buscando 'M365 Linux Bridge' en tu menú de aplicaciones."
