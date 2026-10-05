"""划词翻译气泡：读取剪贴板文本 → GLM 翻译 → 玻璃气泡显示原文/译文对照。"""

from __future__ import annotations

import time

from PySide6.QtCore import QRect, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QCursor, QGuiApplication, QColor
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from . import log
from .glass import DragBar, GlassCard, enable_acrylic, resolve_font_family
from .translator import Translator, TranslatorError


class TranslateWorker(QThread):
    partial = Signal(int, str)  # run_id, 已完成行的拼接
    done = Signal(int, object)  # run_id, {"text": ..., "elapsed": ...}
    fail = Signal(int, str)

    def __init__(self, run_id: int, text: str, translator: Translator, parent=None):
        super().__init__(parent)
        self._run_id = run_id
        self._text = text
        self._translator = translator

    def run(self):
        t0 = time.perf_counter()
        try:
            lines = self._text.replace("\r\n", "\n").split("\n")
            best: list[str | None] = [None] * len(lines)

            def on_line(i: int, dst: str):
                best[i] = dst
                self.partial.emit(self._run_id, "\n".join(b for b in best if b))

            translated = self._translator.translate_lines_streaming(lines, on_line)
            self.done.emit(self._run_id, {"text": "\n".join(translated), "elapsed": time.perf_counter() - t0})
        except TranslatorError as exc:
            self.fail.emit(self._run_id, str(exc))
        except Exception as exc:
            self.fail.emit(self._run_id, f"{type(exc).__name__}: {exc}")


class ClipboardBubble(QWidget):
    WIDTH = 460
    finished = Signal(str, str)  # 原文, 译文（历史记录用）

    def __init__(self, translator: Translator, cfg: dict):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._translator = translator
        self._cfg = cfg
        self._run_id = 0
        self._worker = None
        self._last_translation = ""
        self._last_status = ""
        self._flash_text = ""
        self._acrylic_done = False

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 18)
        self.card = GlassCard("solid", radius=18)
        lay = QVBoxLayout(self.card)
        lay.setContentsMargins(14, 10, 10, 12)
        lay.setSpacing(8)

        header = DragBar()
        hlay = QHBoxLayout(header)
        hlay.setContentsMargins(0, 0, 0, 0)
        hlay.setSpacing(8)
        self.title = QLabel("划词翻译", objectName="panelTitle")
        self.status = QLabel("", objectName="panelStatus")
        hlay.addWidget(self.title)
        hlay.addWidget(self.status, 1)
        self.btn_copy = QPushButton("⧉ 复制")
        self.btn_copy.setToolTip("复制译文")
        self.btn_close = QPushButton("✕")
        self.btn_close.setFixedWidth(34)
        self.btn_close.setToolTip("关闭")
        self.btn_copy.clicked.connect(self._copy)
        self.btn_close.clicked.connect(self.close)
        hlay.addWidget(self.btn_copy)
        hlay.addWidget(self.btn_close)
        lay.addWidget(header)

        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet("background: rgba(255,255,255,30); border: none;")
        lay.addWidget(line)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        body = QWidget()
        body_lay = QVBoxLayout(body)
        body_lay.setContentsMargins(0, 2, 6, 2)
        body_lay.setSpacing(10)
        self.orig = QLabel(objectName="panelStatus")
        self.orig.setWordWrap(True)
        self.orig.setTextFormat(Qt.PlainText)
        self.orig.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.trans = QLabel(objectName="panelBody")
        self.trans.setWordWrap(True)
        self.trans.setTextFormat(Qt.PlainText)
        self.trans.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.trans.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        body_lay.addWidget(self.orig)
        body_lay.addWidget(self.trans)
        body_lay.addStretch(1)
        self.scroll.setWidget(body)
        lay.addWidget(self.scroll, 1)
        root.addWidget(self.card)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(18)
        shadow.setOffset(0, 4)
        shadow.setColor(QColor(0, 0, 0, 70))
        self.card.setGraphicsEffect(shadow)

        self.setFixedWidth(self.WIDTH)

        family = resolve_font_family(str(cfg.get("font_family", "")))
        self.orig.setStyleSheet(f"font-family: \"{family}\"; font-size: 9pt;")
        self.trans.setStyleSheet(f"font-family: \"{family}\"; font-size: 12pt;")

    # ---- 唤出 ----
    def summon(self, text: str):
        pos = QCursor.pos()
        screen = QGuiApplication.screenAt(pos) or QGuiApplication.primaryScreen()
        avail = screen.availableGeometry()
        x = max(avail.left() + 8, min(pos.x() - 20, avail.right() - self.width() - 8))
        y = max(avail.top() + 8, min(pos.y() - 20, avail.bottom() - 240))
        self.move(x, y)
        if not self.isVisible():
            self.show()
        self.raise_()
        self._start(text)

    def _start(self, text: str):
        self._run_id += 1
        run_id = self._run_id
        self._last_translation = ""
        self.orig.setText(text)
        self.trans.setText("")
        self._set_status("⏳ 翻译中…")
        self._fit_height()
        worker = TranslateWorker(run_id, text, self._translator, self)
        worker.partial.connect(self._on_partial)
        worker.done.connect(self._on_done)
        worker.fail.connect(self._on_fail)
        worker.finished.connect(worker.deleteLater)
        worker.finished.connect(lambda w=worker: self._forget(w))
        self._worker = worker
        worker.start()

    def _on_partial(self, run_id: int, text: str):
        if run_id == self._run_id:
            self.trans.setText(text)

    def _forget(self, worker: TranslateWorker):
        if self._worker is worker:
            self._worker = None

    # ---- 结果 ----
    def _on_done(self, run_id: int, result: dict):
        if run_id != self._run_id:
            return
        self._last_translation = result["text"]
        self.trans.setText(result["text"].strip() or "（无译文）")
        self._set_status(f"{self._cfg.get('model')} · {result['elapsed']:.1f}s", remember=True)
        self._fit_height()
        self.finished.emit(self.orig.text(), self._last_translation)

    def _on_fail(self, run_id: int, error: str):
        if run_id != self._run_id:
            return
        log(f"划词翻译失败：{error}")
        hint = "（托盘右键 → 设置检查 API Key）" if ("401" in error or "token" in error.lower()) else ""
        self._set_status("⚠ " + error[:200] + hint, "panelError")

    # ---- 布局 ----
    def _fit_height(self):
        screen = self.screen() or QGuiApplication.primaryScreen()
        avail_h = screen.availableGeometry().height()
        inner_w = self.width() - 64
        h = 104
        for label in (self.orig, self.trans):
            text = label.text()
            if not text:
                continue
            rect = label.fontMetrics().boundingRect(QRect(0, 0, inner_w, 10000), Qt.TextWordWrap, text)
            h += rect.height() + 16
        self.setFixedHeight(int(min(max(h, 150), avail_h * 0.55)))

    # ---- 复制 ----
    def _copy(self):
        if self._last_translation:
            QGuiApplication.clipboard().setText(self._last_translation)
            self._flash("已复制译文 ✓")

    def _flash(self, text: str):
        self._flash_text = text
        self._set_status(text)
        QTimer.singleShot(1400, self._restore_status)

    def _restore_status(self):
        if self._flash_text and self.status.text() == self._flash_text:
            self._set_status(self._last_status or "")

    # ---- 杂项 ----
    def _set_status(self, text: str, object_name: str = "panelStatus", remember: bool = False):
        if remember:
            self._last_status = text
        self.status.setText(text)
        if self.status.objectName() != object_name:
            self.status.setObjectName(object_name)
            self.status.style().unpolish(self.status)
            self.status.style().polish(self.status)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.close()
        super().keyPressEvent(e)

    def showEvent(self, e):
        super().showEvent(e)
        if not self._acrylic_done:
            self._acrylic_done = True
            enable_acrylic(self)
