"""玻璃态样式：Win 亚克力模糊背景 + 全局 QSS + 可拖拽条。"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget

_ACCENT_ENABLE_ACRYLICBLURBEHIND = 4
_WCA_ACCENT_POLICY = 19


class _ACCENT_POLICY(ctypes.Structure):
    _fields_ = [
        ("AccentState", ctypes.c_int),
        ("AccentFlags", ctypes.c_int),
        ("GradientColor", ctypes.c_uint),  # ABGR
        ("AnimationId", ctypes.c_int),
    ]


class _WCA_DATA(ctypes.Structure):
    _fields_ = [
        ("Attribute", ctypes.c_int),
        ("Data", ctypes.c_void_p),
        ("SizeOfData", ctypes.c_size_t),
    ]


def enable_acrylic(widget: QWidget, gradient_abgr: int = 0x991E1614) -> bool:
    """给窗口开启深色亚克力模糊。需在窗口 show 之后（winId 有效时）调用。"""
    try:
        hwnd = int(widget.winId())
    except Exception:
        return False
    accent = _ACCENT_POLICY(_ACCENT_ENABLE_ACRYLICBLURBEHIND, 2, gradient_abgr, 0)
    data = _WCA_DATA(
        _WCA_ACCENT_POLICY,
        ctypes.cast(ctypes.pointer(accent), ctypes.c_void_p),
        ctypes.sizeof(accent),
    )
    try:
        ok = ctypes.windll.user32.SetWindowCompositionAttribute(wintypes.HWND(hwnd), ctypes.byref(data))
        return bool(ok)
    except Exception:
        return False


class DragBar(QWidget):
    """无标题栏窗口的拖拽区：按住空白处即可移动窗口，子控件不受影响。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._offset = None

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._offset = e.globalPosition().toPoint() - self.window().frameGeometry().topLeft()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._offset is not None and e.buttons() & Qt.LeftButton:
            self.window().move(e.globalPosition().toPoint() - self._offset)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        self._offset = None
        super().mouseReleaseEvent(e)


QSS = """
* { font-family: 'Microsoft YaHei UI', 'Microsoft YaHei'; color: #EAF0FF; }
QFrame#glassCard {
    background: rgba(18, 20, 30, 150);
    border: 1px solid rgba(255, 255, 255, 36);
    border-radius: 14px;
}
QFrame#lensCard {
    background: rgba(10, 12, 18, 26);   /* 极浅 tint：画布区近乎全透明，截屏无影响 */
    border: 1px solid rgba(126, 179, 255, 90);
    border-radius: 10px;
}
QFrame#lensToolbar {
    background: rgba(12, 14, 20, 205);
    border: none;
    border-radius: 8px;
}
QLabel#panelTitle { font-size: 11pt; font-weight: 600; }
QLabel#panelStatus { font-size: 8.5pt; color: rgba(234, 240, 255, 145); }
QLabel#panelError  { font-size: 8.5pt; color: #FFB4A8; }
QLabel#panelBody { font-size: 11pt; background: transparent; }
QLabel#fieldLabel { font-size: 9.5pt; color: rgba(234, 240, 255, 175); }
QToolButton {
    background: rgba(255, 255, 255, 22);
    border: 1px solid rgba(255, 255, 255, 32);
    border-radius: 8px;
    padding: 3px 10px;
    font-size: 9.5pt;
}
QToolButton:hover { background: rgba(126, 179, 255, 60); border-color: rgba(126, 179, 255, 130); }
QToolButton:pressed { background: rgba(126, 179, 255, 95); }
QPushButton {
    background: rgba(255, 255, 255, 22);
    border: 1px solid rgba(255, 255, 255, 32);
    border-radius: 8px;
    padding: 5px 14px;
}
QPushButton:hover { background: rgba(255, 255, 255, 40); }
QPushButton#primary { background: rgba(96, 150, 255, 110); border-color: rgba(126, 179, 255, 150); }
QPushButton#primary:hover { background: rgba(96, 150, 255, 150); }
QLineEdit, QComboBox {
    background: rgba(255, 255, 255, 24);
    border: 1px solid rgba(255, 255, 255, 42);
    border-radius: 8px;
    padding: 5px 8px;
    selection-background-color: rgba(96, 150, 255, 160);
}
QLineEdit:focus, QComboBox:focus { border-color: rgba(126, 179, 255, 160); }
QComboBox QAbstractItemView {
    background: rgba(24, 26, 36, 245);
    border: 1px solid rgba(255, 255, 255, 40);
    selection-background-color: rgba(96, 150, 255, 140);
}
QScrollArea { background: transparent; border: none; }
QScrollBar:vertical { background: transparent; width: 8px; margin: 2px; }
QScrollBar::handle:vertical { background: rgba(255, 255, 255, 55); border-radius: 4px; min-height: 28px; }
QScrollBar::handle:vertical:hover { background: rgba(255, 255, 255, 85); }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QMenu { background: rgba(24, 26, 36, 242); border: 1px solid rgba(255, 255, 255, 40); border-radius: 10px; padding: 6px; }
QMenu::item { padding: 6px 24px; border-radius: 6px; }
QMenu::item:selected { background: rgba(126, 179, 255, 75); }
QMenu::separator { height: 1px; background: rgba(255, 255, 255, 32); margin: 4px 8px; }
"""
