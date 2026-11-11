"""Panel "Carpetas sincronizadas": elegir qué carpetas de OneDrive viven en este equipo."""
import asyncio
import queue
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox,
    QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
)

from m365_bridge.config import get_config
from m365_bridge.selection import (
    SelectionRules, apply_selection, diff_selection, folder_local_size, get_rules,
)

REL_ROLE = Qt.ItemDataRole.UserRole
PLACEHOLDER = "__cargando__"
CHECK = {"on": Qt.CheckState.Checked, "off": Qt.CheckState.Unchecked, "partial": Qt.CheckState.PartiallyChecked}

# Workers que siguen vivos al cerrar el panel (p. ej. todavía conectando): se guardan acá
# para que Qt no destruya un QThread en ejecución.
_lingering: List[QThread] = []

# (nombre, tamaño en bytes, cantidad de hijos)
FolderInfo = Tuple[str, int, int]


def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}".replace(".", ",")
        n /= 1024
    return str(n)


class CloudFolderWorker(QThread):
    """Mantiene UNA sesión con OneDrive mientras el panel está abierto y atiende pedidos de
    listado de subcarpetas (carga perezosa al expandir)."""
    loaded = pyqtSignal(str, list)
    failed = pyqtSignal(str, str)

    def __init__(self):
        super().__init__()
        self.q: "queue.Queue[Optional[str]]" = queue.Queue()

    def request(self, rel: str):
        self.q.put(rel)

    def stop(self):
        self.q.put(None)

    def run(self):
        asyncio.run(self._main())

    async def _main(self):
        from m365_bridge.cloud import open_sharepoint_session
        from m365_bridge.sync import list_remote_children
        try:
            async with open_sharepoint_session() as s:
                while True:
                    rel = await asyncio.to_thread(self.q.get)
                    if rel is None:
                        return
                    try:
                        items = await list_remote_children(s.client, s.drive_endpoint, rel)
                        kids = sorted(
                            ((i["name"], i.get("size", 0), (i.get("folder") or {}).get("childCount", 0))
                             for i in items if i.get("folder")),
                            key=lambda k: k[0].lower(),
                        )
                        self.loaded.emit(rel, kids)
                    except Exception as e:
                        self.failed.emit(rel, str(e))
        except Exception as e:
            self.failed.emit("", str(e))


class SelectiveSyncDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Carpetas sincronizadas")
        self.resize(720, 560)
        self.cfg = get_config()
        self.local_drive = Path(self.cfg["sync_folder"])
        self.rules: SelectionRules = get_rules(self.cfg).copy()
        self.items: Dict[str, QTreeWidgetItem] = {}
        self.requested: set = set()
        self._updating = False
        self.result_summary: Optional[dict] = None

        layout = QVBoxLayout(self)
        intro = QLabel(
            "Marca las carpetas de OneDrive que quieres en este equipo. "
            "Lo desmarcado se borra de aquí pero <b>sigue en la nube</b>. "
            "Las carpetas nuevas de la nube se sincronizan solas."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Filtrar carpetas cargadas…")
        self.search.textChanged.connect(self.apply_filter)
        layout.addWidget(self.search)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Carpeta", "Tamaño en la nube", "En este equipo"])
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setStretchLastSection(False)
        self.tree.itemExpanded.connect(self.on_expand)
        self.tree.itemChanged.connect(self.on_item_changed)
        layout.addWidget(self.tree, 1)

        self.status = QLabel("Conectando con OneDrive…")
        self.status.setStyleSheet("color: #555;")
        layout.addWidget(self.status)

        pat_row = QHBoxLayout()
        pat_row.addWidget(QLabel("Ignorar siempre carpetas llamadas:"))
        self.patterns = QLineEdit(", ".join(self.cfg.get("excluded_folders", [])))
        self.patterns.setPlaceholderText("ej.: node_modules, Copias")
        self.patterns.setToolTip("Por NOMBRE, en cualquier nivel. Separadas por coma.")
        pat_row.addWidget(self.patterns, 1)
        layout.addLayout(pat_row)

        btns = QHBoxLayout()
        btns.addStretch()
        self.btn_apply = QPushButton("Aplicar")
        self.btn_apply.setStyleSheet("background-color: #0078D4; color: white; font-weight: bold; padding: 5px 14px;")
        self.btn_apply.clicked.connect(self.apply)
        btn_cancel = QPushButton("Cancelar")
        btn_cancel.clicked.connect(self.reject)
        btns.addWidget(self.btn_apply)
        btns.addWidget(btn_cancel)
        layout.addLayout(btns)

        self.worker = CloudFolderWorker()
        self.worker.loaded.connect(self.on_loaded)
        self.worker.failed.connect(self.on_failed)
        self.worker.start()
        self.worker.request("")

    # ------------------------------------------------------------------ árbol
    def on_loaded(self, parent_rel: str, kids: List[FolderInfo]):
        parent = self.tree.invisibleRootItem() if not parent_rel else self.items.get(parent_rel)
        if parent is None:
            return
        self._updating = True
        for i in reversed(range(parent.childCount())):
            if parent.child(i).data(0, REL_ROLE) == PLACEHOLDER:
                parent.removeChild(parent.child(i))
        for name, size, child_count in kids:
            rel = f"{parent_rel}/{name}" if parent_rel else name
            item = QTreeWidgetItem([name, human_size(size), "✓" if (self.local_drive / rel).is_dir() else "—"])
            item.setData(0, REL_ROLE, rel)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(0, CHECK[self.rules.state(rel)])
            item.setTextAlignment(1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            item.setTextAlignment(2, Qt.AlignmentFlag.AlignCenter)
            if child_count:
                ph = QTreeWidgetItem(["Cargando…"])
                ph.setData(0, REL_ROLE, PLACEHOLDER)
                item.addChild(ph)
            parent.addChild(item)
            self.items[rel] = item
        self._updating = False
        if not parent_rel:
            self.status.setText(f"{len(kids)} carpetas en la raíz de OneDrive. Expande para ver subcarpetas.")
            self.tree.resizeColumnToContents(1)
            self.tree.resizeColumnToContents(2)
        self.apply_filter()

    def on_failed(self, rel: str, msg: str):
        if not rel:
            self.status.setText(f"⚠️ {msg}")
            self.status.setStyleSheet("color: #b00020;")
        else:
            self.requested.discard(rel)
            item = self.items.get(rel)
            if item:
                for i in range(item.childCount()):
                    if item.child(i).data(0, REL_ROLE) == PLACEHOLDER:
                        item.child(i).setText(0, f"⚠️ No se pudo cargar: {msg[:80]}")

    def on_expand(self, item: QTreeWidgetItem):
        rel = item.data(0, REL_ROLE)
        if rel and rel != PLACEHOLDER and rel not in self.requested:
            self.requested.add(rel)
            self.worker.request(rel)

    def on_item_changed(self, item: QTreeWidgetItem, column: int):
        if self._updating or column != 0:
            return
        rel = item.data(0, REL_ROLE)
        if not rel or rel == PLACEHOLDER:
            return
        selected = item.checkState(0) != Qt.CheckState.Unchecked
        self.rules.set_selected(rel, selected)
        self.refresh_states()

    def refresh_states(self):
        self._updating = True
        for rel, item in self.items.items():
            item.setCheckState(0, CHECK[self.rules.state(rel)])
        self._updating = False
        n_off = len(self.rules.excluded)
        self.status.setText(
            "Se sincroniza todo." if not n_off else f"{n_off} carpeta(s) desmarcada(s). Pulsa Aplicar para guardar."
        )
        self.status.setStyleSheet("color: #555;")

    def apply_filter(self):
        text = self.search.text().strip().lower()

        def visit(item: QTreeWidgetItem) -> bool:
            any_child = False
            for i in range(item.childCount()):
                any_child = visit(item.child(i)) or any_child
            if item is self.tree.invisibleRootItem():
                return any_child
            rel = item.data(0, REL_ROLE)
            match = not text or (rel != PLACEHOLDER and text in item.text(0).lower())
            item.setHidden(not (match or any_child))
            return match or any_child

        visit(self.tree.invisibleRootItem())

    # ------------------------------------------------------------------ aplicar
    def apply(self):
        old = get_rules(get_config())
        to_delete, to_fetch = diff_selection(old, self.rules, self.local_drive)
        patterns = [p.strip() for p in self.patterns.text().split(",") if p.strip()]

        if to_delete:
            lines, total = [], 0
            for rel in to_delete:
                if rel.endswith("/*"):
                    folder = self.local_drive / rel[:-2]
                    files = [f for f in folder.iterdir() if f.is_file()]
                    size = sum(f.stat().st_size for f in files)
                    lines.append(f"• {rel[:-2]} — solo sus archivos sueltos ({len(files)}, {human_size(size)})")
                else:
                    size, count = folder_local_size(self.local_drive / rel)
                    lines.append(f"• {rel} ({count} archivos, {human_size(size)})")
                total += size
            shown = "\n".join(lines[:15]) + (f"\n… y {len(lines) - 15} más" if len(lines) > 15 else "")
            ok = QMessageBox.warning(
                self, "Se borrará de este equipo",
                "Estas carpetas se BORRARÁN de este equipo y dejarán de sincronizarse. "
                f"Siguen intactas en la nube.\n\n{shown}\n\nLiberas {human_size(total)}. ¿Continuar?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if ok != QMessageBox.StandardButton.Yes:
                return

        try:
            self.result_summary = apply_selection(self.rules, patterns)
        except RuntimeError as e:
            QMessageBox.information(
                self, "Sincronización en curso",
                f"{e}\n\nEspera a que termine el ciclo actual y vuelve a pulsar Aplicar.",
            )
            return
        self.accept()

    def done(self, r):
        self.worker.stop()
        if not self.worker.wait(3000):
            _lingering.append(self.worker)
            self.worker.finished.connect(lambda w=self.worker: _lingering.remove(w) if w in _lingering else None)
        super().done(r)
