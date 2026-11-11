"""Sincronización selectiva: qué carpetas de OneDrive se sincronizan con este dispositivo.

Reglas por RUTA (no por nombre) guardadas en ``config["selective_sync"]``::

    {"excluded_paths": ["Magister 2024"], "included_paths": ["Magister 2024/Tesis"]}

Gana la regla con el prefijo más largo, comparando por segmentos de ruta. Sin regla, la
carpeta se sincroniza (las carpetas nuevas de la nube entran solas).

Deseleccionar una carpeta BORRA su copia local (sigue en la nube). Para que eso no se
interprete como "el usuario borró estos archivos" y se reciclen en OneDrive, primero se
quitan sus registros de la base de sync y recién después se borra el disco.
"""
import json
import shutil
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

Parts = Tuple[str, ...]

# Carpetas que antes se excluían por nombre por defecto. Desde la sync selectiva se tratan
# como carpetas normales: el usuario las excluye desde el panel si no las quiere.
LEGACY_NAME_EXCLUSIONS = ("Recordings", "Meetings", "Temporales")
BACKFILL_KV_KEY = "selective_backfill_queue"


def to_parts(rel_path: Any) -> Parts:
    if isinstance(rel_path, (tuple, list)):
        return tuple(p for p in rel_path if p and p != ".")
    s = str(rel_path).replace("\\", "/").strip("/")
    return tuple(p for p in s.split("/") if p and p != ".")


def to_rel(parts: Iterable[str]) -> str:
    return "/".join(parts)


class SelectionRules:
    def __init__(self, excluded: Iterable[Any] = (), included: Iterable[Any] = ()):
        self.excluded = {to_parts(p) for p in excluded if to_parts(p)}
        self.included = {to_parts(p) for p in included if to_parts(p)}

    @classmethod
    def from_config(cls, cfg: Dict[str, Any]) -> "SelectionRules":
        sel = cfg.get("selective_sync") or {}
        return cls(sel.get("excluded_paths", []), sel.get("included_paths", []))

    def to_config(self) -> Dict[str, List[str]]:
        return {
            "excluded_paths": sorted(to_rel(p) for p in self.excluded),
            "included_paths": sorted(to_rel(p) for p in self.included),
        }

    def copy(self) -> "SelectionRules":
        r = SelectionRules()
        r.excluded = set(self.excluded)
        r.included = set(self.included)
        return r

    def is_selected(self, rel_path: Any) -> bool:
        parts = to_parts(rel_path)
        for n in range(len(parts), 0, -1):
            prefix = parts[:n]
            if prefix in self.included:
                return True
            if prefix in self.excluded:
                return False
        return True

    def set_selected(self, rel_path: Any, selected: bool) -> None:
        """Marca/desmarca una carpeta. Pisa las reglas de sus subcarpetas (como al
        tildar una carpeta en cualquier cliente de sync)."""
        parts = to_parts(rel_path)
        if not parts:
            raise ValueError("La raíz no se puede desmarcar")
        n = len(parts)
        self.excluded = {p for p in self.excluded if p[:n] != parts}
        self.included = {p for p in self.included if p[:n] != parts}
        if self.is_selected(parts) != selected:
            (self.included if selected else self.excluded).add(parts)

    def state(self, rel_path: Any) -> str:
        """'on' | 'off' | 'partial' (alguna subcarpeta tiene el estado contrario)."""
        parts = to_parts(rel_path)
        mine = self.is_selected(parts)
        n = len(parts)
        for p in self.excluded | self.included:
            if len(p) > n and p[:n] == parts and self.is_selected(p) != mine:
                return "partial"
        return "on" if mine else "off"

    def rule_paths(self) -> List[Parts]:
        return sorted(self.excluded | self.included)


def get_rules(cfg: Optional[Dict[str, Any]] = None) -> SelectionRules:
    if cfg is None:
        from m365_bridge.config import get_config
        cfg = get_config()
    return SelectionRules.from_config(cfg)


def is_path_selected(rel_path: Any, cfg: Optional[Dict[str, Any]] = None) -> bool:
    return get_rules(cfg).is_selected(rel_path)


# --------------------------------------------------------------------------- cambios

def diff_selection(old: SelectionRules, new: SelectionRules, local_drive: Path) -> Tuple[List[str], List[str]]:
    """Devuelve (carpetas locales a borrar, carpetas a traer de la nube).

    - A borrar: carpetas que EXISTEN en el disco, estaban seleccionadas y ya no. Se
      devuelve la más alta de cada rama (no sus hijas). Si una carpeta desmarcada tiene
      una subcarpeta que sigue marcada, se devuelve como "ruta/*": solo sus archivos
      sueltos, y se sigue bajando por las subcarpetas.
    - A traer: rutas con regla en alguno de los dos sets que pasaron de no seleccionadas
      a seleccionadas.
    """
    to_delete: List[str] = []

    def walk(dir_path: Path, parts: Parts):
        try:
            children = sorted(c for c in dir_path.iterdir() if c.is_dir() and not c.is_symlink())
        except OSError:
            return
        for child in children:
            cp = parts + (child.name,)
            if old.is_selected(cp) and not new.is_selected(cp):
                keeps_child = any(len(r) > len(cp) and r[:len(cp)] == cp and new.is_selected(r)
                                  for r in new.rule_paths())
                if not keeps_child:
                    to_delete.append(to_rel(cp))
                    continue
                to_delete.append(to_rel(cp) + "/*")
            # Solo hace falta bajar si debajo hay alguna regla que cambie algo
            if any(len(r) > len(cp) and r[:len(cp)] == cp for r in old.rule_paths() + new.rule_paths()):
                walk(child, cp)

    if local_drive.exists():
        walk(local_drive, ())

    to_fetch = sorted({
        to_rel(p) for p in set(old.rule_paths()) | set(new.rule_paths())
        if not old.is_selected(p) and new.is_selected(p)
    })
    # Quitar las que ya quedan cubiertas por una ancestra en la misma lista
    fetch_parts = [to_parts(p) for p in to_fetch]
    to_fetch = [to_rel(p) for p in fetch_parts
                if not any(len(q) < len(p) and p[:len(q)] == q for q in fetch_parts)]
    return to_delete, to_fetch


def folder_local_size(path: Path) -> Tuple[int, int]:
    """(bytes, cantidad de archivos) de una carpeta local."""
    total = count = 0
    for f in path.rglob("*"):
        try:
            if f.is_file() and not f.is_symlink():
                total += f.stat().st_size
                count += 1
        except OSError:
            pass
    return total, count


def queue_backfill(rel_paths: Iterable[str]) -> None:
    from m365_bridge.sync_db import get_kv, set_kv
    current = json.loads(get_kv(BACKFILL_KV_KEY) or "[]")
    for p in rel_paths:
        if p not in current:
            current.append(p)
    set_kv(BACKFILL_KV_KEY, json.dumps(current, ensure_ascii=False))


def pending_backfill() -> List[str]:
    from m365_bridge.sync_db import get_kv
    return json.loads(get_kv(BACKFILL_KV_KEY) or "[]")


def done_backfill(rel_path: str) -> None:
    from m365_bridge.sync_db import get_kv, set_kv
    current = [p for p in json.loads(get_kv(BACKFILL_KV_KEY) or "[]") if p != rel_path]
    set_kv(BACKFILL_KV_KEY, json.dumps(current, ensure_ascii=False))


def apply_selection(new_rules: SelectionRules, excluded_patterns: Optional[List[str]] = None) -> Dict[str, Any]:
    """Aplica una nueva selección: borra las copias locales de lo desmarcado (sin tocar
    la nube), guarda la configuración y encola la descarga de lo nuevo.

    Toma el SyncLock: lanza RuntimeError si hay una sincronización en curso.
    """
    from m365_bridge.config import get_config, save_config
    from m365_bridge.sync import SyncLock
    from m365_bridge.sync_db import remove_records_under

    with SyncLock():
        cfg = get_config()
        local_drive = Path(cfg["sync_folder"])
        old_rules = SelectionRules.from_config(cfg)
        to_delete, to_fetch = diff_selection(old_rules, new_rules, local_drive)

        from m365_bridge.sync_db import remove_file_record
        deleted = []
        for rel in to_delete:
            if rel.endswith("/*"):
                # Carpeta desmarcada con una hija que sigue marcada: solo sus archivos sueltos
                folder = local_drive / rel[:-2]
                for f in folder.iterdir():
                    if f.is_file() or f.is_symlink():
                        f_rel = to_rel(to_parts(rel[:-2]) + (f.name,))
                        remove_file_record(f_rel)
                        f.unlink(missing_ok=True)
                deleted.append(rel)
                continue
            # 1) Olvidar en la base: así el próximo ciclo no lo toma como borrado del usuario
            remove_records_under(rel)
            # 2) Recién ahora, liberar el disco
            target = local_drive / rel
            if target.is_dir():
                shutil.rmtree(target, ignore_errors=True)
            deleted.append(rel)

        cfg["selective_sync"] = new_rules.to_config()
        if excluded_patterns is not None:
            cfg["excluded_folders"] = [p for p in excluded_patterns if p]
        save_config(cfg)
        if to_fetch:
            queue_backfill(to_fetch)

    return {"deleted_local": deleted, "queued_download": to_fetch}


def migrate_config(cfg: Dict[str, Any]) -> bool:
    """Primera vez con sync selectiva: las exclusiones por nombre heredadas
    (Recordings/Meetings/Temporales) pasan a ser carpetas normales y se encolan para
    bajarlas. Devuelve True si cambió la configuración."""
    if "selective_sync" in cfg:
        return False
    old = list(cfg.get("excluded_folders", []))
    revived = [n for n in old if n in LEGACY_NAME_EXCLUSIONS]
    cfg["excluded_folders"] = [n for n in old if n not in LEGACY_NAME_EXCLUSIONS]
    cfg["selective_sync"] = {"excluded_paths": [], "included_paths": []}
    if revived:
        queue_backfill(revived)
    return True
