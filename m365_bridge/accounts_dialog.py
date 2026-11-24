"""Diálogo para administrar cuentas M365 (listar, agregar, por defecto, login, quitar)."""
import os
import subprocess
import sys
from pathlib import Path

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem, QPushButton,
    QLabel, QMessageBox, QLineEdit, QFormLayout, QDialogButtonBox,
)
from PyQt6.QtCore import Qt

from m365_bridge import config as cfgmod
from m365_bridge.browser import launch_interactive_login

GUI_SCRIPT = Path(__file__).parent / "gui.py"


def launch_gui_for_account(account: str):
    """Abre una ventana principal de la cuenta en un proceso aparte (cada una usa su puerto CDP)."""
    env = {**os.environ, cfgmod.ACCOUNT_ENV_VAR: account}
    subprocess.Popen([sys.executable, str(GUI_SCRIPT), "--account", account], env=env)


class _AddAccountDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Agregar cuenta Microsoft 365")
        form = QFormLayout(self)
        self.alias = QLineEdit()
        self.alias.setPlaceholderText("Ej: INACAP admin")
        self.email = QLineEdit()
        self.email.setPlaceholderText("correo@dominio.cl")
        form.addRow("Alias:", self.alias)
        form.addRow("Correo:", self.email)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)


class AccountsDialog(QDialog):
    """Administra las cuentas. ``on_change`` se llama tras cualquier cambio del registro."""

    def __init__(self, parent=None, on_change=None):
        super().__init__(parent)
        self.on_change = on_change
        self.setWindowTitle("Cuentas Microsoft 365")
        self.setMinimumSize(460, 320)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Cada cuenta tiene su propio perfil de navegador, carpeta y estado de sync."))
        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(lambda _i: self.open_window())
        lay.addWidget(self.list, 1)

        row = QHBoxLayout()
        for text, slot in (
            ("➕ Agregar…", self.add),
            ("🪟 Abrir ventana", self.open_window),
            ("🔑 Iniciar sesión", self.login),
            ("★ Por defecto", self.make_default),
            ("🗑 Quitar", self.remove),
        ):
            b = QPushButton(text)
            b.clicked.connect(slot)
            row.addWidget(b)
        lay.addLayout(row)
        close = QPushButton("Cerrar")
        close.clicked.connect(self.accept)
        lay.addWidget(close)
        self.refresh()

    def refresh(self):
        self.list.clear()
        active = cfgmod.get_active_account_id()
        for a in cfgmod.list_accounts():
            tags = []
            if a["is_default"]:
                tags.append("por defecto")
            if a["id"] == active:
                tags.append("activa")
            label = f"{a['alias']}  <{a['email'] or 'sin correo'}>"
            if tags:
                label += f"  [{', '.join(tags)}]"
            it = QListWidgetItem(label)
            it.setData(Qt.ItemDataRole.UserRole, a["id"])
            self.list.addItem(it)

    def _selected(self):
        it = self.list.currentItem()
        return it.data(Qt.ItemDataRole.UserRole) if it else None

    def _changed(self):
        self.refresh()
        if self.on_change:
            self.on_change()

    def add(self):
        dlg = _AddAccountDialog(self)
        if not dlg.exec():
            return
        try:
            acc = cfgmod.add_account(dlg.alias.text(), dlg.email.text())
        except ValueError as e:
            QMessageBox.warning(self, "Cuentas", str(e))
            return
        self._changed()
        if QMessageBox.question(self, "Cuentas", "¿Iniciar sesión ahora en esa cuenta?") == QMessageBox.StandardButton.Yes:
            launch_interactive_login(acc)

    def open_window(self):
        acc = self._selected()
        if acc:
            launch_gui_for_account(acc)

    def login(self):
        acc = self._selected()
        if acc:
            launch_interactive_login(acc)

    def make_default(self):
        acc = self._selected()
        if acc:
            cfgmod.set_default_account(acc)
            self._changed()

    def remove(self):
        acc = self._selected()
        if not acc:
            return
        if QMessageBox.question(
            self, "Cuentas",
            "Se quita la cuenta de la lista (los archivos y el perfil en disco no se borran). ¿Continuar?",
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            cfgmod.remove_account(acc)
        except ValueError as e:
            QMessageBox.warning(self, "Cuentas", str(e))
            return
        self._changed()
