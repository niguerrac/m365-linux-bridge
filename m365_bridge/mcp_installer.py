import json
from pathlib import Path
from typing import Tuple

BRIDGE_DIR = Path(__file__).parent.parent
PYTHON_BIN = BRIDGE_DIR / ".venv" / "bin" / "python"
MCP_SERVER_SCRIPT = BRIDGE_DIR / "m365_bridge" / "mcp_server.py"

def install_claude_desktop() -> Tuple[bool, str]:
    config_path = Path.home() / ".config" / "Claude" / "claude_desktop_config.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    data = {}
    if config_path.exists():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}
    
    if "mcpServers" not in data:
        data["mcpServers"] = {}
    
    data["mcpServers"]["m365"] = {
        "command": str(PYTHON_BIN),
        "args": [str(MCP_SERVER_SCRIPT)]
    }
    
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return True, str(config_path)

def install_antigravity() -> Tuple[bool, str]:
    config_path = Path.home() / ".gemini" / "config" / "mcp_config.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    data = {}
    if config_path.exists():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}
            
    if "mcpServers" not in data:
        data["mcpServers"] = {}
        
    data["mcpServers"]["m365"] = {
        "command": str(PYTHON_BIN),
        "args": [str(MCP_SERVER_SCRIPT)]
    }
    
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return True, str(config_path)

def install_opencode() -> Tuple[bool, str]:
    config_path = Path.home() / ".config" / "opencode" / "opencode.json"
    if not config_path.exists():
        return False, "Archivo opencode.json no encontrado"
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if "mcp" not in data:
            data["mcp"] = {}
        data["mcp"]["m365"] = {
            "type": "local",
            "command": [str(PYTHON_BIN), str(MCP_SERVER_SCRIPT)]
        }
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return True, str(config_path)
    except Exception as e:
        return False, str(e)
