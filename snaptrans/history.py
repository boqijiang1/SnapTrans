"""翻译历史：最近 100 条，持久化到 history.json；玻璃态历史面板。"""

from __future__ import annotations

import json
import time

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .config import _app_dir
from .glass import DragBar, GlassCard

HISTORY_PATH = _app_dir() / "history.json"


class History:
    MAX = 100

    def __init__(self):
        self.entries: list[dict] = []
        try:
            if HISTORY_PATH.exists():
                data = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    self.entries = data[: self.MAX]
        except (OSError, ValueError):
            pass

    def add(self, src: str, dst: str, model: str) -> None:
        if not dst.strip():
            return
        self.entries.insert(0, {
            "time": time.strftime("%m-%d %H:%M"),
            "src": src.strip()[:300],
            "dst": dst.strip()[:2000],
            "model": model,
        })
        self.entries = self.entries[: self.MAX]
        self._save()

    def clear(self) -> None:
        self.entries = []
        self._save()

    def _save(self) -> None:
        try:
            HISTORY_PATH.write_text(
                json.dumps(self.entries, ensure_ascii=False, indent=1), encoding="utf-8"
            )
        except OSError:
            pass


class _EntryRow(QFrame):
    """单条历史：上行是时间/模型，下行是译文；单击整行复制译文。"""

    copied = Signal(str)

    def __init__(self, entry: dict, index: int):
        super().__init__()
        self._dst = entry.get("dst", "")
        self.setObjectName("historyRow")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 6, 10, 8)
        lay.setSpacing(3)
        head = QLabel(f"{entry.get('time', '')} · {entry.get('model', '')}", objectName="panelStatus")
        body = QLabel(entry.get("dst", ""), objectName="panelBody")
        body.setWordWrap(True)
        body.setTextFormat(Qt.PlainText)
        src = entry.get("src", "").replace("\n", " ")
        if src:
            src_label = QLabel(f"原文：{src[:120]}", objectName="panelStatus")
            src_label.setWordWrap(True)
            lay.addWidget(src_label)
        lay.addWidget(head)
        lay.addWidget(body)
        if index % 2 == 1:
            self.setStyleSheet("QFrame#historyRow { background: rgba(255,255,255,14); border-radius: 8px; }")

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self._dst:
            self.copied.emit(self._dst)
        super().mousePressEvent(e)


class HistoryPanel(QWidget):
    """历史面板：单击条目复制译文，可清空。"""

    def __init__(self, history: History):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._history = history
        self._acrylic_done = False

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 18)
        card = GlassCard("solid", radius=18)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(14, 10, 10, 12)
        lay.setSpacing(8)

        header = DragBar()
        hlay = QHBoxLayout(header)
        hlay.setContentsMargins(0, 0, 0, 0)
        hlay.setSpacing(8)
        self.title = QLabel("翻译历史", objectName="panelTitle")
        self.status = QLabel("", objectName="panelStatus")
        hlay.addWidget(self.title)
        hlay.addWidget(self.status, 1)
        btn_clear = QPushButton("清空")
        btn_clear.setToolTip("清空全部历史")
        btn_close = QPushButton("✕")
        btn_close.setFixedWidth(34)
        btn_clear.clicked.connect(self._clear)
        btn_close.clicked.connect(self.close)
        hlay.addWidget(btn_clear)
        hlay.addWidget(btn_close)
        lay.addWidget(header)

        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet("background: rgba(255,255,255,30); border: none;")
        lay.addWidget(line)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.list_host = QWidget()
        self.list_lay = QVBoxLayout(self.list_host)
        self.list_lay.setContentsMargins(0, 2, 6, 2)
        self.list_lay.setSpacing(6)
        self.list_lay.addStretch(1)
        self.scroll.setWidget(self.list_host)
        lay.addWidget(self.scroll, 1)

        hint = QLabel("单击条目复制译文 · 数据保存在本机 history.json", objectName="panelStatus")
        lay.addWidget(hint)

        root.addWidget(card)
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(18)
        shadow.setOffset(0, 4)
        shadow.setColor(QColor(0, 0, 0, 70))
        card.setGraphicsEffect(shadow)

        self.setFixedSize(500, 560)

    # ---- 展示 ----
    def open_panel(self):
        self._rebuild()
        if not self.isVisible():
            self.show()
        self.raise_()

    def _rebuild(self):
        while self.list_lay.count() > 1:  # 末尾是 stretch
            item = self.list_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        entries = self._history.entries
        self.status.setText(f"{len(entries)} 条")
        if not entries:
            empty = QLabel("还没有翻译记录", objectName="panelStatus")
            self.list_lay.insertWidget(0, empty)
            return
        for i, entry in enumerate(entries):
            row = _EntryRow(entry, i)
            row.copied.connect(self._copy)
            self.list_lay.insertWidget(i, row)

    def _copy(self, text: str):
        QGuiApplication.clipboard().setText(text)
        self._flash("已复制该条译文 ✓")

    def _clear(self):
        self._history.clear()
        self._rebuild()

    def _flash(self, text: str):
        self.status.setText(text)
        self.status.setStyleSheet("color: #9FE6B8;")
        from PySide6.QtCore import QTimer

        QTimer.singleShot(1400, lambda: self.status.setStyleSheet(""))

    def showEvent(self, e):
        super().showEvent(e)
        from .glass import enable_acrylic

        if not self._acrylic_done:
            self._acrylic_done = True
            enable_acrylic(self)
