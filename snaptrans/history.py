"""翻译历史：最近 100 条，持久化到 history.json；日期分组 + 卡片流的玻璃面板。"""

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
    QLineEdit,
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
            "date": time.strftime("%m-%d"),
            "time": time.strftime("%H:%M"),
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


class _EntryCard(QFrame):
    """单条历史的玻璃小卡：译文为主角，原文淡色陪衬；单击复制译文。"""

    copied = Signal(str)

    def __init__(self, entry: dict):
        super().__init__()
        self._dst = entry.get("dst", "")
        self.setObjectName("historyCard")
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("单击复制译文")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 9, 12, 9)
        lay.setSpacing(4)

        dst = QLabel(self._dst, objectName="historyDst")
        dst.setWordWrap(True)
        dst.setTextFormat(Qt.PlainText)
        lay.addWidget(dst)

        src = entry.get("src", "").replace("\n", "  ")
        if src:
            src_label = QLabel(f"{src[:140]}", objectName="historySrc")
            src_label.setWordWrap(True)
            lay.addWidget(src_label)

        meta = QLabel(f"{entry.get('time', '')} · {entry.get('model', '')}", objectName="historyMeta")
        lay.addWidget(meta)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self._dst:
            self.copied.emit(self._dst)
        super().mousePressEvent(e)


class HistoryPanel(QWidget):
    """历史面板：筛选框 + 日期分组 + 卡片流；单击卡片复制译文。"""

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
        lay.setContentsMargins(14, 10, 12, 12)
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

        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("筛选：输入关键词，按原文或译文匹配")
        self.filter_edit.textChanged.connect(lambda _t: self._rebuild())
        lay.addWidget(self.filter_edit)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.list_host = QWidget()
        self.list_lay = QVBoxLayout(self.list_host)
        self.list_lay.setContentsMargins(0, 2, 4, 2)
        self.list_lay.setSpacing(8)
        self.list_lay.addStretch(1)
        self.scroll.setWidget(self.list_host)
        lay.addWidget(self.scroll, 1)

        root.addWidget(card)
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(18)
        shadow.setOffset(0, 4)
        shadow.setColor(QColor(0, 0, 0, 70))
        card.setGraphicsEffect(shadow)

        self.setFixedSize(540, 620)

    # ---- 展示 ----
    def open_panel(self):
        self.filter_edit.clear()
        self._rebuild()
        if not self.isVisible():
            self.show()
        self.raise_()

    def _group_label(self, entry: dict) -> str:
        date = entry.get("date") or entry.get("time", "")[:5]
        today = time.strftime("%m-%d")
        yesterday = time.strftime("%m-%d", time.localtime(time.time() - 86400))
        if date == today:
            return "今天"
        if date == yesterday:
            return "昨天"
        return date or "更早"

    def _rebuild(self):
        keyword = self.filter_edit.text().strip().lower()
        entries = self._history.entries
        if keyword:
            entries = [
                e for e in entries
                if keyword in e.get("dst", "").lower() or keyword in e.get("src", "").lower()
            ]
        self.status.setText(f"{len(entries)} 条")

        while self.list_lay.count() > 1:  # 末尾是 stretch
            item = self.list_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not entries:
            empty = QLabel("没有匹配的翻译记录", objectName="panelStatus")
            empty.setAlignment(Qt.AlignCenter)
            self.list_lay.insertWidget(0, empty)
            return

        row = 0
        last_group = None
        for entry in entries:
            group = self._group_label(entry)
            if group != last_group:  # 日期分组标题
                head = QLabel(group, objectName="sectionHeader")
                self.list_lay.insertWidget(row, head)
                row += 1
                last_group = group
            card = _EntryCard(entry)
            card.copied.connect(self._copy)
            self.list_lay.insertWidget(row, card)
            row += 1

    def _copy(self, text: str):
        QGuiApplication.clipboard().setText(text)
        self.status.setText("已复制译文 ✓")
        self.status.setStyleSheet("color: #9FE6B8;")
        from PySide6.QtCore import QTimer

        QTimer.singleShot(1400, lambda: self.status.setStyleSheet(""))

    def _clear(self):
        self._history.clear()
        self._rebuild()

    def showEvent(self, e):
        super().showEvent(e)
        from .glass import enable_acrylic

        if not self._acrylic_done:
            self._acrylic_done = True
            enable_acrylic(self)
