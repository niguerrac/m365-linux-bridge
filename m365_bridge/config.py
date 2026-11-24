"""Configuración del puente M365 con soporte multi-cuenta.

Layout de ``config.json``
-------------------------
Las claves históricas (``sync_folder``, ``selective_sync``, ``remote_debugging_port``...)
siguen en el nivel superior y pertenecen a la cuenta legacy ``"default"``. Eso mantiene la
compatibilidad con instalaciones de 1 cuenta: no se mueve ningún archivo ni DB.

Se agregan dos claves::

    "default_account": "default",
    "accounts": {
        "default": {"alias": "Principal", "email": ""},
        "inacap":  {"alias": "INACAP", "email": "x@inacap.cl",
                    "sync_folder": "...", "remote_debugging_port": 9223, ...}
    }

Las cuentas no legacy guardan sus ajustes por cuenta (carpeta de sync, puerto CDP, reglas
de sync selectiva...) dentro de su entrada. Los ajustes globales (intervalo, tamaño máximo,
descargas) se comparten.

Datos por cuenta
----------------
* ``default``  -> rutas históricas (``DATA_DIR/browser_profile``, ``DATA_DIR/sync_state.db``...)
* otra cuenta  -> ``DATA_DIR/accounts/<id>/{browser_profile,sync_state.db,sync.log,...}``

Resolución de la cuenta activa (de mayor a menor prioridad): ``use_account()`` (contextvar),
``set_active_account()`` (proceso), variable de entorno ``M365_ACCOUNT``, ``default_account``.
"""
import contextlib
import contextvars
import copy
import json
import os
import re
from pathlib import Path
from typing import Dict, Any, List, Optional

CONFIG_DIR = Path.home() / ".config" / "m365-bridge"
CONFIG_FILE = CONFIG_DIR / "config.json"
DATA_DIR = Path.home() / ".local" / "share" / "m365-bridge"
BROWSER_PROFILE_DIR = DATA_DIR / "browser_profile"  # perfil de la cuenta legacy "default"

LEGACY_ACCOUNT_ID = "default"
ACCOUNT_ENV_VAR = "M365_ACCOUNT"
BASE_DEBUG_PORT = 9222

DEFAULT_CONFIG: Dict[str, Any] = {
    "institution_name": "Mi Institución",
    "sync_folder": str(Path.home() / "M365-Drive"),
    "outlook_url": "https://outlook.cloud.microsoft/mail/",
    "onedrive_url": "https://www.office.com/launch/onedrive",
    "teams_url": "https://teams.microsoft.com/v2/",
    "auto_sync_interval_minutes": 60,
    "max_file_size_mb": 200,
    "excluded_folders": [],
    "remote_debugging_port": 9222,
    "ask_download_location": True,
    "downloads_folder": str(Path.home() / "Descargas")
}

# Ajustes compartidos entre todas las cuentas (el resto es por cuenta).
GLOBAL_KEYS = {
    "auto_sync_interval_minutes", "max_file_size_mb", "ask_download_location",
    "downloads_folder", "default_account", "accounts",
}
# Metadatos de la entrada de cuenta que no son ajustes de config.
ACCOUNT_META_KEYS = {"alias", "email"}

_active_account: Optional[str] = None
_ctx_account: contextvars.ContextVar = contextvars.ContextVar("m365_account", default=None)


# ---------------------------------------------------------------- persistencia

def _load_raw() -> Dict[str, Any]:
    """Lee el JSON crudo y lo normaliza (migra single→multi). Persiste la migración."""
    if not CONFIG_FILE.exists():
        data = copy.deepcopy(DEFAULT_CONFIG)
        _normalize(data)
        _write_raw(data)
        return data
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("config no es un objeto")
    except Exception:
        data = copy.deepcopy(DEFAULT_CONFIG)
        _normalize(data)
        return data
    changed = _normalize(data)
    if changed:
        _write_raw(data)
    return data


def _write_raw(data: Dict[str, Any]):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _normalize(data: Dict[str, Any]) -> bool:
    """Completa defaults y asegura la estructura multi-cuenta. True si cambió algo."""
    changed = False
    for k, v in DEFAULT_CONFIG.items():
        if k not in data:
            data[k] = copy.deepcopy(v)
    accounts = data.get("accounts")
    if not isinstance(accounts, dict):
        accounts = {}
        data["accounts"] = accounts
        changed = True
    if LEGACY_ACCOUNT_ID not in accounts:
        accounts[LEGACY_ACCOUNT_ID] = {"alias": "Principal", "email": ""}
        changed = True
    if data.get("default_account") not in accounts:
        data["default_account"] = LEGACY_ACCOUNT_ID
        changed = True
    return changed


def migrate_single_to_multi(data: Dict[str, Any]) -> Dict[str, Any]:
    """Devuelve una copia de una config single-account con la estructura multi-cuenta."""
    out = copy.deepcopy(data)
    _normalize(out)
    return out


# ------------------------------------------------------------- cuenta activa

def default_account_id() -> str:
    return _load_raw()["default_account"]


def resolve_account(account: Optional[str] = None) -> str:
    """Resuelve el id de cuenta a usar. Lanza KeyError si no existe."""
    raw = _load_raw()
    acc = account or _ctx_account.get() or _active_account or os.environ.get(ACCOUNT_ENV_VAR) \
        or raw["default_account"]
    if acc not in raw["accounts"]:
        raise KeyError(f"Cuenta M365 desconocida: {acc!r}")
    return acc


def get_active_account_id() -> str:
    return resolve_account(None)


def set_active_account(account: Optional[str]):
    """Fija la cuenta activa del proceso (None = volver a la cuenta por defecto)."""
    global _active_account
    if account is not None:
        resolve_account(account)  # valida
    _active_account = account


@contextlib.contextmanager
def use_account(account: Optional[str]):
    """Usa ``account`` dentro del bloque (válido también para tareas asyncio)."""
    if account is not None:
        resolve_account(account)
    token = _ctx_account.set(account)
    try:
        yield
    finally:
        _ctx_account.reset(token)


# --------------------------------------------------------------------- paths

def account_data_dir(account: Optional[str] = None) -> Path:
    acc = resolve_account(account)
    return DATA_DIR if acc == LEGACY_ACCOUNT_ID else DATA_DIR / "accounts" / acc


def browser_profile_dir(account: Optional[str] = None) -> Path:
    acc = resolve_account(account)
    return BROWSER_PROFILE_DIR if acc == LEGACY_ACCOUNT_ID else account_data_dir(acc) / "browser_profile"


def webengine_profile_dir(account: Optional[str] = None) -> Path:
    return account_data_dir(account) / "webengine_profile"


def db_path(account: Optional[str] = None) -> Path:
    return account_data_dir(account) / "sync_state.db"


def log_path(account: Optional[str] = None) -> Path:
    return account_data_dir(account) / "sync.log"


def lock_path(account: Optional[str] = None) -> Path:
    return account_data_dir(account) / "sync.lock"


# ------------------------------------------------------------------- config

def get_config(account: Optional[str] = None) -> Dict[str, Any]:
    """Config efectiva de la cuenta: globales + ajustes de la cuenta.

    Incluye ``account_id``, ``account_alias`` y ``account_email`` (informativos; no se
    persisten en save_config).
    """
    raw = _load_raw()
    acc = resolve_account(account)
    cfg = {k: copy.deepcopy(v) for k, v in raw.items() if k not in ("accounts", "default_account")}
    entry = raw["accounts"][acc]
    if acc != LEGACY_ACCOUNT_ID:
        # los ajustes por cuenta de una cuenta nueva no heredan los de la legacy
        for k in [k for k in cfg if k not in GLOBAL_KEYS]:
            cfg[k] = _account_default(k, acc)
        cfg.pop("selective_sync", None)
        for k, v in entry.items():
            if k not in ACCOUNT_META_KEYS:
                cfg[k] = copy.deepcopy(v)
    cfg["account_id"] = acc
    cfg["account_alias"] = entry.get("alias", acc)
    cfg["account_email"] = entry.get("email", "")
    return cfg


def _account_default(key: str, acc: str):
    if key == "sync_folder":
        return str(Path.home() / f"M365-Drive-{acc}")
    if key == "institution_name":
        return acc
    return copy.deepcopy(DEFAULT_CONFIG.get(key))


def save_config(cfg: Dict[str, Any], account: Optional[str] = None):
    """Guarda ``cfg`` para la cuenta (la activa por defecto), sin tocar las demás."""
    raw = _load_raw()
    acc = resolve_account(account)
    cfg = {k: v for k, v in cfg.items() if k not in ("account_id", "account_alias", "account_email")}
    if acc == LEGACY_ACCOUNT_ID:
        for k, v in cfg.items():
            if k in ("accounts", "default_account"):
                continue
            raw[k] = v
    else:
        entry = raw["accounts"][acc]
        for k, v in cfg.items():
            if k in ("accounts", "default_account") or k in ACCOUNT_META_KEYS:
                continue
            if k in GLOBAL_KEYS:
                raw[k] = v
            else:
                entry[k] = v
    _write_raw(raw)


def ensure_dirs(account: Optional[str] = None):
    acc = resolve_account(account)
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    account_data_dir(acc).mkdir(parents=True, exist_ok=True)
    browser_profile_dir(acc).mkdir(parents=True, exist_ok=True)
    Path(get_config(acc)["sync_folder"]).mkdir(parents=True, exist_ok=True)


# ----------------------------------------------------------------- gestión

def slugify_account_id(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").strip().lower()).strip("-")
    return s


def list_accounts() -> List[Dict[str, Any]]:
    raw = _load_raw()
    out = []
    for acc, entry in raw["accounts"].items():
        cfg = get_config(acc)
        out.append({
            "id": acc,
            "alias": entry.get("alias", acc),
            "email": entry.get("email", ""),
            "is_default": acc == raw["default_account"],
            "sync_folder": cfg["sync_folder"],
            "remote_debugging_port": cfg.get("remote_debugging_port"),
        })
    return out


def get_account(account: Optional[str] = None) -> Dict[str, Any]:
    acc = resolve_account(account)
    return next(a for a in list_accounts() if a["id"] == acc)


def add_account(alias: str, email: str = "", account_id: Optional[str] = None,
                sync_folder: Optional[str] = None, make_default: bool = False) -> str:
    """Registra una cuenta nueva y devuelve su id. No crea carpetas ni abre login."""
    alias = (alias or "").strip()
    acc = slugify_account_id(account_id or alias or email.split("@")[0])
    if not acc:
        raise ValueError("Falta un alias o id para la cuenta")
    raw = _load_raw()
    if acc in raw["accounts"]:
        raise ValueError(f"Ya existe una cuenta con id {acc!r}")
    used_ports = {raw.get("remote_debugging_port", BASE_DEBUG_PORT)}
    used_ports |= {e.get("remote_debugging_port") for e in raw["accounts"].values()}
    port = BASE_DEBUG_PORT + 1
    while port in used_ports:
        port += 1
    entry: Dict[str, Any] = {
        "alias": alias or acc,
        "email": email.strip(),
        "sync_folder": sync_folder or str(Path.home() / f"M365-Drive-{acc}"),
        "remote_debugging_port": port,
    }
    raw["accounts"][acc] = entry
    if make_default:
        raw["default_account"] = acc
    _write_raw(raw)
    return acc


def update_account(account: str, alias: Optional[str] = None, email: Optional[str] = None):
    raw = _load_raw()
    if account not in raw["accounts"]:
        raise KeyError(f"Cuenta M365 desconocida: {account!r}")
    if alias is not None:
        raw["accounts"][account]["alias"] = alias.strip()
    if email is not None:
        raw["accounts"][account]["email"] = email.strip()
    _write_raw(raw)


def set_default_account(account: str):
    raw = _load_raw()
    if account not in raw["accounts"]:
        raise KeyError(f"Cuenta M365 desconocida: {account!r}")
    raw["default_account"] = account
    _write_raw(raw)


def remove_account(account: str):
    """Quita la cuenta del registro (no borra datos en disco). La legacy no se puede quitar."""
    if account == LEGACY_ACCOUNT_ID:
        raise ValueError("La cuenta principal no se puede quitar")
    raw = _load_raw()
    if account not in raw["accounts"]:
        raise KeyError(f"Cuenta M365 desconocida: {account!r}")
    del raw["accounts"][account]
    if raw["default_account"] == account:
        raw["default_account"] = LEGACY_ACCOUNT_ID
    _write_raw(raw)
    global _active_account
    if _active_account == account:
        _active_account = None
