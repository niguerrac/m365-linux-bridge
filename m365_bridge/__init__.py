import sys
from pathlib import Path

# Asegurar que el paquete m365_bridge sea importable
BRIDGE_ROOT = Path(__file__).parent.parent
if str(BRIDGE_ROOT) not in sys.path:
    sys.path.insert(0, str(BRIDGE_ROOT))

# Versión de la app. Al publicar un release, el tag vX.Y.Z debe coincidir (lo verifica el CI).
__version__ = "1.0.4"
