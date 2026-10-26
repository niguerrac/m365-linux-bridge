import sys
from pathlib import Path

# Asegurar que el paquete m365_bridge sea importable
BRIDGE_ROOT = Path(__file__).parent.parent
if str(BRIDGE_ROOT) not in sys.path:
    sys.path.insert(0, str(BRIDGE_ROOT))
