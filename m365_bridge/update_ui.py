"""UI de actualizaciones: aviso al abrir (se descarta o se desvanece solo) y diálogo que
instala la versión nueva y ofrece reiniciar."""
import subprocess
from typing import Any, Dict, Optional

from PyQt6.QtCore import QPropertyAnimation, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication, QDialog, QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel,
    QMessageBox, QProgressBar, QPushButton, QTextEdit, QVBoxLayout, QWidget,
)

from m365_bridge import __version__
from m365_bridge.updater import check_for_update, restart_command, run_update

DISMISSED_KEY = "update_dismissed_version"


class UpdateCheckWorker(QThread):
    result = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, use_cache: bool = True, parent=None):
        super().__init__(parent)
        self.use_cache = use_cache

    def run(self):
        try:
            self.result.emit(check_for_update(use_cache=self.use_cache))
        except Exception as e:
            self.error.emit(str(e))


class UpdateRunWorker(QThread):
    line = pyqtSignal(str)
    done = pyqtSignal(str)  # "" = ok, si no el mensaje de error

    def __init__(self, info: Dict[str, Any], parent=None):
        super().__init__(parent)
        self.info = info

    def run(self):
        try:
            run_update(self.info, self.line.emit)
            self.done.emit("")
        except Exception as e:
            self.done.emit(str(e) or type(e).__name__)


class UpdateDialog(QDialog):
    def __init__(self, info: Dict[str, Any], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Actualizar M365 Linux Bridge")
        self.resize(560, 360)
        self.info = info
        lay = QVBoxLayout(self)
        self.title = QLabel(f"Actualizando de <b>v{__version__}</b> a <b>v{info['version']}</b>…")
        lay.addWidget(self.title)
        self.bar = QProgressBar()
        self.bar.setRange(0, 0)
        lay.addWidget(self.bar)
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setStyleSheet("font-family: monospace; font-size: 11px;")
        lay.addWidget(self.log, 1)
        row = QHBoxLayout()
        row.addStretch()
        self.btn_restart = QPushButton("Reiniciar ahora")
        self.btn_restart.setStyleSheet("background-color: #0078D4; color: white; font-weight: bold; padding: 5px 12px;")
        self.btn_restart.clicked.connect(self.restart)
        self.btn_restart.setVisible(False)
        self.btn_close = QPushButton("Cerrar")
        self.btn_close.setEnabled(False)
        self.btn_close.clicked.connect(self.accept)
        row.addWidget(self.btn_restart)
        row.addWidget(self.btn_close)
        lay.addLayout(row)

        self.worker = UpdateRunWorker(info, self)
        self.worker.line.connect(self.log.append)
        self.worker.done.connect(self.on_done)
        self.worker.start()

    def on_done(self, err: str):
        self.bar.setRange(0, 1)
        self.bar.setValue(1)
        self.btn_close.setEnabled(True)
        if err:
            self.title.setText("⚠️ No se pudo actualizar")
            self.log.append("\n" + err)
            return
        self.title.setText(f"✓ Actualizada a v{self.info['version']}. Reinicia la app para usarla.")
        self.btn_close.setText("Más tarde")
        self.btn_restart.setVisible(True)

    def restart(self):
        subprocess.Popen(restart_command(), start_new_session=True)
        QApplication.quit()

    def reject(self):
        if self.worker.isRunning():
            return  # no cerrar a medio instalar
        super().reject()


def start_update(info: Dict[str, Any], parent: Optional[QWidget] = None):
    UpdateDialog(info, parent).exec()


class UpdateToast(QFrame):
    """Aviso flotante en la esquina inferior derecha. Se descarta con ✕ / "Ahora no" (no
    vuelve a aparecer para esa versión) y se desvanece solo a los 15 segundos."""

    VISIBLE_MS = 15000

    def __init__(self, info: Dict[str, Any], parent: QWidget):
        super().__init__(parent)
        self.info = info
        self.setObjectName("updateToast")
        self.setStyleSheet(
            "#updateToast { background: #1f2937; border-radius: 8px; }"
            "#updateToast QLabel { color: white; }"
            "#updateToast QPushButton { padding: 4px 10px; border-radius: 4px; }"
        )
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 10, 10, 10)
        msg = QLabel(f"⬆️ Hay una versión nueva: <b>v{info['version']}</b> (tienes v{__version__})")
        lay.addWidget(msg)
        btn_update = QPushButton("Actualizar")
        btn_update.setStyleSheet("background-color: #0078D4; color: white; font-weight: bold;")
        btn_update.clicked.connect(self.do_update)
        btn_later = QPushButton("Ahora no")
        btn_later.setStyleSheet("background: transparent; color: #cbd5e1;")
        btn_later.clicked.connect(self.dismiss)
        lay.addWidget(btn_update)
        lay.addWidget(btn_later)

        self.effect = QGraphicsOpacityEffect(self)
        self.effect.setOpacity(1.0)
        self.setGraphicsEffect(self.effect)
        self.fade = QPropertyAnimation(self.effect, b"opacity", self)
        self.fade.setDuration(800)
        self.fade.setStartValue(1.0)
        self.fade.setEndValue(0.0)
        self.fade.finished.connect(self.close)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.fade.start)

    def popup(self):
        self.adjustSize()
        self.reposition()
        self.show()
        self.raise_()
        self.timer.start(self.VISIBLE_MS)

    def reposition(self):
        p = self.parentWidget()
        if p:
            self.move(p.width() - self.width() - 16, p.height() - self.height() - 16)

    def enterEvent(self, e):
        # Con el mouse encima no se desvanece
        self.timer.stop()
        self.fade.stop()
        self.effect.setOpacity(1.0)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.timer.start(4000)
        super().leaveEvent(e)

    def dismiss(self):
        from m365_bridge.sync_db import set_kv
        set_kv(DISMISSED_KEY, self.info["version"])
        self.close()

    def do_update(self):
        self.close()
        start_update(self.info, self.parentWidget())


def check_on_startup(window: QWidget):
    """Consulta (con caché) si hay versión nueva y, si no fue descartada, muestra el aviso."""
    worker = UpdateCheckWorker(use_cache=True, parent=window)

    def on_result(info):
        if not info:
            return
        from m365_bridge.sync_db import get_kv
        if get_kv(DISMISSED_KEY) == info["version"]:
            return
        window._update_toast = UpdateToast(info, window)
        window._update_toast.popup()

    worker.result.connect(on_result)
    worker.error.connect(lambda e: None)  # sin red no molestamos al abrir
    worker.start()
    window._update_check_worker = worker
