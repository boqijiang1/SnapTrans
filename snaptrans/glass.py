"""液态玻璃样式：自绘玻璃卡片、Win 亚克力、拖拽条、字体解析与全局 QSS。"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QFrame, QWidget

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


class GlassCard(QFrame):
    """液态玻璃卡片（自绘）：
    - variant='solid'：完整玻璃面（划词气泡、设置窗口）——深色渐变基底 + 顶部光泽 + 对角高光；
    - variant='frame'：玻璃边框包着透明视区（翻译放大镜）——视区保持全透明，截屏零污染。
    """

    def __init__(self, variant: str = "solid", radius: float = 18.0, rim: int = 10, parent=None):
        super().__init__(parent)
        self._variant = variant
        self._radius = radius
        self._rim = rim

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = float(self.width()), float(self.height())
        outer = QRectF(0.5, 0.5, w - 1.5, h - 1.5)
        path = QPainterPath()
        path.addRoundedRect(outer, self._radius, self._radius)

        if self._variant == "solid":
            base = QLinearGradient(0, 0, 0, h)
            base.setColorAt(0, QColor(34, 38, 54, 196))
            base.setColorAt(1, QColor(13, 15, 23, 190))
            p.fillPath(path, base)
        else:
            rim = float(self._rim)
            inner_r = max(self._radius - rim, 6.0)
            viewport = QPainterPath()
            viewport.addRoundedRect(
                QRectF(rim + 0.5, rim + 0.5, w - 2 * rim - 1, h - 2 * rim - 1), inner_r, inner_r
            )
            frame = path.subtracted(viewport)
            base = QLinearGradient(0, 0, 0, h)
            base.setColorAt(0, QColor(255, 255, 255, 64))
            base.setColorAt(1, QColor(150, 170, 215, 34))
            p.fillPath(frame, base)
            # 视区内缘：细暗线定深度，外圈细亮线给反光
            p.setPen(QPen(QColor(0, 0, 0, 45), 1))
            p.setBrush(Qt.NoBrush)
            p.drawPath(viewport)

        # 顶部光泽 + 斜向液态高光
        sheen = QLinearGradient(0, 0, 0, h * 0.5)
        sheen.setColorAt(0, QColor(255, 255, 255, 66))
        sheen.setColorAt(1, QColor(255, 255, 255, 0))
        p.fillPath(path, sheen)
        diag = QLinearGradient(0, 0, h * 0.9, h)
        diag.setColorAt(0, QColor(255, 255, 255, 34))
        diag.setColorAt(0.5, QColor(255, 255, 255, 0))
        p.fillPath(path, diag)

        # 细高光描边：1px、左上亮右下暗，保持轻盈
        border = QLinearGradient(0, 0, w, h)
        border.setColorAt(0, QColor(255, 255, 255, 170))
        border.setColorAt(0.5, QColor(255, 255, 255, 60))
        border.setColorAt(1, QColor(255, 255, 255, 30))
        p.setPen(QPen(QBrush(border), 1))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)

        # 顶缘反光线：玻璃上边缘的"来光"，往下渐隐
        inner_path = QPainterPath()
        inner_path.addRoundedRect(
            QRectF(2, 2, w - 4, h - 4), max(self._radius - 2, 8), max(self._radius - 2, 8)
        )
        edge = QLinearGradient(0, 0, 0, h * 0.45)
        edge.setColorAt(0, QColor(255, 255, 255, 140))
        edge.setColorAt(1, QColor(255, 255, 255, 0))
        p.setPen(QPen(QBrush(edge), 1.2))
        p.drawPath(inner_path)
        p.end()


def resolve_font_family(preferred: str) -> str:
    """解析译文字体：先加载 fonts/ 目录的字体文件，再按名字找系统字体族，
    大小写/空格差异做模糊匹配；都找不到回退微软雅黑。"""
    from PySide6.QtGui import QFontDatabase

    from .config import FONTS_DIR

    loaded: list[str] = []
    try:
        if FONTS_DIR.exists():
            for p in sorted(FONTS_DIR.iterdir()):
                if p.suffix.lower() in (".ttf", ".otf", ".ttc"):
                    fid = QFontDatabase.addApplicationFont(str(p))
                    if fid >= 0:
                        loaded.extend(QFontDatabase.applicationFontFamilies(fid))
    except OSError:
        pass

    def norm(s: str) -> str:
        import re

        return re.sub(r"[\s_\-]", "", s).lower()

    families = set(QFontDatabase.families())
    wanted = [preferred, *loaded] if preferred else list(loaded)
    for cand in wanted:
        if cand in families:
            return cand
    pn = norm(preferred) if preferred else ""
    if pn:
        for fam in families:
            if norm(fam) == pn:
                return fam
    for cand in wanted:
        for fam in families:
            if cand and norm(fam) == norm(cand):
                return fam
    return "Microsoft YaHei UI"


QSS = """
* { font-family: '__FONT__'; color: #F0F4FF; }
QLabel#panelTitle { font-size: 13pt; font-weight: 600; }
QLabel#panelStatus { font-size: 9.5pt; color: rgba(240, 244, 255, 150); }
QLabel#panelError  { font-size: 9.5pt; color: #FFB4A8; }
QLabel#panelBody { font-size: 12pt; background: transparent; }
QLabel#fieldLabel { font-size: 10.5pt; color: rgba(240, 244, 255, 180); }
QFrame#historyCard {
    background: rgba(255, 255, 255, 20);
    border: 1px solid rgba(255, 255, 255, 26);
    border-radius: 10px;
}
QFrame#historyCard:hover {
    background: rgba(255, 255, 255, 36);
    border: 1px solid rgba(150, 195, 255, 110);
}
QLabel#historyDst { font-size: 12pt; }
QLabel#historySrc { font-size: 9pt; color: rgba(240, 244, 255, 118); }
QLabel#historyMeta { font-size: 8.5pt; color: rgba(240, 244, 255, 100); }
QLabel#sectionHeader { font-size: 10.5pt; color: rgba(126, 179, 255, 210); font-weight: 600; }
QToolButton, QPushButton {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 rgba(255, 255, 255, 58), stop:0.5 rgba(255, 255, 255, 28), stop:1 rgba(255, 255, 255, 12));
    border: 1px solid rgba(255, 255, 255, 40);
    border-radius: 10px;
    padding: 4px 12px;
    font-size: 11pt;
}
QToolButton:hover, QPushButton:hover {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 rgba(150, 190, 255, 85), stop:0.5 rgba(110, 160, 255, 48), stop:1 rgba(90, 130, 220, 28));
    border: 1px solid rgba(170, 205, 255, 120);
}
QToolButton:pressed, QPushButton:pressed {
    background: rgba(60, 80, 130, 100);
    border: 1px solid rgba(140, 175, 255, 95);
}
QPushButton#primary {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 rgba(120, 170, 255, 130), stop:1 rgba(80, 120, 230, 90));
    border: 1px solid rgba(160, 195, 255, 170);
}
QPushButton#primary:hover {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 rgba(140, 190, 255, 165), stop:1 rgba(100, 145, 245, 120));
}
QFrame#lensToolbar {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 rgba(32, 36, 50, 218), stop:1 rgba(15, 17, 27, 198));
    border: 1px solid rgba(255, 255, 255, 46);
    border-radius: 10px;
}
QLineEdit, QComboBox {
    background: rgba(255, 255, 255, 30);
    border: 1px solid rgba(255, 255, 255, 52);
    border-radius: 10px;
    padding: 5px 9px;
    font-size: 11pt;
    selection-background-color: rgba(96, 150, 255, 170);
}
QLineEdit:focus, QComboBox:focus { border: 1px solid rgba(150, 195, 255, 175); }
QComboBox QAbstractItemView {
    background: rgba(24, 26, 36, 248);
    border: 1px solid rgba(255, 255, 255, 46);
    border-radius: 8px;
    selection-background-color: rgba(96, 150, 255, 150);
}
QScrollArea { background: transparent; border: none; }
QScrollBar:vertical { background: transparent; width: 8px; margin: 2px; }
QScrollBar::handle:vertical { background: rgba(255, 255, 255, 62); border-radius: 4px; min-height: 28px; }
QScrollBar::handle:vertical:hover { background: rgba(255, 255, 255, 95); }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QMenu { background: rgba(24, 26, 38, 238); border: 1px solid rgba(255, 255, 255, 48); border-radius: 12px; padding: 7px; font-size: 11pt; }
QMenu::item { padding: 7px 26px; border-radius: 8px; }
QMenu::item:selected { background: rgba(126, 179, 255, 82); }
QMenu::separator { height: 1px; background: rgba(255, 255, 255, 36); margin: 5px 10px; }
"""
