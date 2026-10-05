"""玻璃态设置窗口：API Key / 模型 / 热键。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .glass import DragBar, GlassCard, enable_acrylic

MODELS = ["glm-4-flash", "glm-4.5-flash", "glm-4-air", "glm-4-plus"]


class SettingsDialog(QDialog):
    def __init__(self, cfg: dict):
        super().__init__()
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setModal(True)
        self.setFixedSize(500, 460)
        self._centered = False

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 20)

        card = GlassCard("solid", radius=18)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(18, 12, 18, 16)
        lay.setSpacing(10)

        title_bar = DragBar()
        tlay = QHBoxLayout(title_bar)
        tlay.setContentsMargins(0, 0, 0, 0)
        tlay.addWidget(QLabel("设置", objectName="panelTitle"))
        tlay.addStretch(1)
        btn_close = QPushButton("✕")
        btn_close.setFixedSize(32, 28)
        btn_close.clicked.connect(self.reject)
        tlay.addWidget(btn_close)
        lay.addWidget(title_bar)

        lay.addWidget(QLabel("API Key（智谱开放平台 open.bigmodel.cn）", objectName="fieldLabel"))
        key_row = QWidget()
        klay = QHBoxLayout(key_row)
        klay.setContentsMargins(0, 0, 0, 0)
        klay.setSpacing(8)
        self.key_edit = QLineEdit(cfg.get("api_key", ""))
        self.key_edit.setEchoMode(QLineEdit.Password)
        self.key_edit.setPlaceholderText("粘贴 API Key")
        toggle = QPushButton("显示")
        toggle.setCheckable(True)
        toggle.setFixedWidth(64)
        toggle.toggled.connect(
            lambda checked: (
                self.key_edit.setEchoMode(QLineEdit.Normal if checked else QLineEdit.Password),
                toggle.setText("隐藏" if checked else "显示"),
            )
        )
        klay.addWidget(self.key_edit, 1)
        klay.addWidget(toggle)
        lay.addWidget(key_row)

        lay.addWidget(QLabel("翻译模型", objectName="fieldLabel"))
        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        self.model_combo.addItems(MODELS)
        self.model_combo.setCurrentText(cfg.get("model", "glm-4-flash"))
        lay.addWidget(self.model_combo)

        lay.addWidget(QLabel("全局热键（修改后立即生效）", objectName="fieldLabel"))
        self.hotkey_edit = QLineEdit(cfg.get("hotkey", "ctrl+alt+t"))
        lay.addWidget(self.hotkey_edit)

        lay.addWidget(QLabel("剪贴板翻译热键（修改后立即生效）", objectName="fieldLabel"))
        self.clip_hotkey_edit = QLineEdit(cfg.get("hotkey_clipboard", "ctrl+alt+b"))
        lay.addWidget(self.clip_hotkey_edit)

        note = QLabel("GLM-4-Flash 免费调用；Key 仅保存在本目录的 config.json 中。", objectName="panelStatus")
        note.setWordWrap(True)
        lay.addWidget(note)
        lay.addStretch(1)

        btn_row = QWidget()
        blay = QHBoxLayout(btn_row)
        blay.setContentsMargins(0, 0, 0, 0)
        blay.addStretch(1)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        save = QPushButton("保存", objectName="primary")
        save.clicked.connect(self.accept)
        blay.addWidget(cancel)
        blay.addWidget(save)
        lay.addWidget(btn_row)

        root.addWidget(card)

    def values(self) -> dict:
        return {
            "api_key": self.key_edit.text().strip(),
            "model": self.model_combo.currentText().strip() or "glm-4-flash",
            "hotkey": self.hotkey_edit.text().strip() or "ctrl+alt+t",
            "hotkey_clipboard": self.clip_hotkey_edit.text().strip() or "ctrl+alt+b",
        }

    def showEvent(self, e):
        super().showEvent(e)
        enable_acrylic(self)
        if not self._centered:
            self._centered = True
            geo = self.screen().availableGeometry()
            self.move(geo.center() - self.rect().center())
