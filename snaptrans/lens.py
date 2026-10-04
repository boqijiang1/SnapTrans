"""翻译放大镜：小块玻璃浮窗，浮在目标文字上方，译文按原文的位置、字号、配色原位替换。

定位时浮窗近乎全透明（可透视下层内容）；按 ↻ 后画布先显示截取的原样快照，
再把每个文本块的中文译文用采样自截图的背景色盖住原文、原位重绘——
排版、字号、明暗与原文一致，看起来就像界面本来就是中文的。
双击浮窗可隐藏译文、对照原文。
"""

from __future__ import annotations

import time

import numpy as np
from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import (
    QCursor,
    QColor,
    QFont,
    QFontMetricsF,
    QGuiApplication,
    QImage,
    QPainter,
    QPainterPath,
)
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizeGrip,
    QVBoxLayout,
    QWidget,
)

from .glass import DragBar
from .ocr_engine import OcrEngine, qimage_to_bgr
from .translator import Translator, TranslatorError

TOOLBAR_H = 34
DEFAULT_SIZE = QSize(460, 240)
MIN_SIZE = QSize(260, 150)


def _box_to_rect(box, dpr: float) -> QRectF:
    """物理像素 4x2 框 → 逻辑像素 QRectF。"""
    x0 = float(box[:, 0].min()) / dpr
    x1 = float(box[:, 0].max()) / dpr
    y0 = float(box[:, 1].min()) / dpr
    y1 = float(box[:, 1].max()) / dpr
    return QRectF(x0, y0, max(x1 - x0, 2.0), max(y1 - y0, 2.0))


def _sample_colors(bgr: np.ndarray, box) -> tuple[QColor, QColor]:
    """从截图采样文本块的背景色，并按亮度给出前景色（复刻原文配色）。"""
    h, w = bgr.shape[:2]
    x0 = max(0, int(box[:, 0].min()))
    x1 = min(w, int(round(box[:, 0].max())) + 1)
    y0 = max(0, int(box[:, 1].min()))
    y1 = min(h, int(round(box[:, 1].max())) + 1)
    if x1 <= x0 or y1 <= y0:
        return QColor("#101218"), QColor("#F2F6FF")
    region = bgr[y0:y1, x0:x1].reshape(-1, 3)
    med = np.median(region, axis=0)  # 文字笔画是少数派，中位数≈背景色
    bg = QColor(int(med[2]), int(med[1]), int(med[0]))
    lum = 0.299 * bg.red() + 0.587 * bg.green() + 0.114 * bg.blue()
    fg = QColor("#16181F") if lum > 140 else QColor("#F2F6FF")
    return bg, fg


class _LensWorker(QThread):
    stage = Signal(int, str)
    succeeded = Signal(int, object)  # run_id, {"items": [...], "elapsed": float}
    failed = Signal(int, str)

    def __init__(self, run_id: int, crop, dpr: float, engine: OcrEngine,
                 translator: Translator, parent=None):
        super().__init__(parent)
        self._run_id = run_id
        self._crop = crop
        self._dpr = dpr
        self._engine = engine
        self._translator = translator

    def run(self):
        t0 = time.perf_counter()
        try:
            self.stage.emit(self._run_id, "识别文字中…")
            bgr = qimage_to_bgr(self._crop)
            ocr_lines = self._engine.recognize(bgr)
            if not ocr_lines:
                self.succeeded.emit(self._run_id, {"items": [], "elapsed": time.perf_counter() - t0})
                return
            self.stage.emit(self._run_id, f"翻译中…（{len(ocr_lines)} 行）")
            translated = self._translator.translate_lines([l.text for l in ocr_lines])
            items = []
            for line, dst in zip(ocr_lines, translated):
                if not dst or dst == line.text:
                    continue  # 纯符号/未翻译的行不动，保留原文
                bg, fg = _sample_colors(bgr, line.box)
                items.append({"rect": _box_to_rect(line.box, self._dpr), "dst": dst, "bg": bg, "fg": fg})
            self.succeeded.emit(self._run_id, {"items": items, "elapsed": time.perf_counter() - t0})
        except TranslatorError as exc:
            self.failed.emit(self._run_id, str(exc))
        except Exception as exc:  # 任何意外都不允许弄崩主线程
            self.failed.emit(self._run_id, f"{type(exc).__name__}: {exc}")


class _LensCanvas(QWidget):
    """先画截取的快照，再把译文按原位重绘；未刷新时完全透明，可透视定位。"""

    doubleClicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._bg: QImage | None = None
        self._dpr = 1.0
        self._items: list[dict] = []
        self._font = QFont("Microsoft YaHei UI")

    def set_content(self, bg: QImage | None, dpr: float, items: list[dict]):
        self._bg = bg
        self._dpr = dpr
        self._items = list(items)
        self.update()

    def clear(self):
        self._bg = None
        self._items = []
        self.update()

    def has_snapshot(self) -> bool:
        return self._bg is not None

    def paintEvent(self, event):
        p = QPainter(self)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(self.rect()), 8, 8)
        p.setClipPath(clip)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        if self._bg is not None:
            img = QImage(self._bg)
            img.setDevicePixelRatio(self._dpr)
            p.drawImage(0, 0, img)
        for item in self._items:
            self._draw_replacement(p, item)
        p.end()

    def _draw_replacement(self, p: QPainter, item: dict):
        rect: QRectF = item["rect"]
        if rect.right() < 0 or rect.bottom() < 0 or rect.x() > self.width() or rect.y() > self.height():
            return
        # 用采样的背景色盖住原文，再把译文画回原位：位置、字号、配色与原文一致
        p.setPen(Qt.NoPen)
        p.setBrush(item["bg"])
        p.drawRoundedRect(rect.adjusted(-1.5, -1.5, 1.5, 1.5), 2, 2)

        text: str = item["dst"]
        size = max(9.0, min(rect.height() * 0.88, 60.0))
        font = QFont(self._font)
        fm = None
        for _ in range(60):  # 中文比原文行还宽时逐级缩小，保持不溢出原排版
            font.setPixelSize(int(size))
            fm = QFontMetricsF(font)
            if fm.horizontalAdvance(text) <= rect.width() or size <= 8.0:
                break
            size -= 1.0
        if fm is None:
            return
        p.setFont(font)
        p.setPen(item["fg"])
        p.drawText(rect, Qt.AlignLeft | Qt.AlignVCenter, text)

    def mouseDoubleClickEvent(self, e):
        self.doubleClicked.emit()
        super().mouseDoubleClickEvent(e)


class LensWindow(QWidget):
    def __init__(self, engine: OcrEngine, translator: Translator, cfg: dict):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setMinimumSize(MIN_SIZE)
        self._engine = engine
        self._translator = translator
        self._cfg = cfg
        self._run_id = 0
        self._worker: _LensWorker | None = None
        self._items: list[dict] = []
        self._saved_items: list[dict] | None = None
        self._snapshot: QImage | None = None
        self._snapshot_dpr = 1.0
        self._last_status = ""
        self._sized_once = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.card = QFrame(objectName="lensCard")
        lay = QVBoxLayout(self.card)
        lay.setContentsMargins(1, 1, 1, 1)
        lay.setSpacing(0)

        toolbar = DragBar()
        toolbar.setObjectName("lensToolbar")
        toolbar.setAttribute(Qt.WA_StyledBackground, True)
        toolbar.setFixedHeight(TOOLBAR_H)
        tlay = QHBoxLayout(toolbar)
        tlay.setContentsMargins(10, 0, 6, 0)
        tlay.setSpacing(6)
        self.title = QLabel("译", objectName="panelTitle")
        self.status = QLabel("", objectName="panelStatus")
        tlay.addWidget(self.title)
        tlay.addWidget(self.status, 1)
        self.btn_refresh = QPushButton("↻ 刷新")
        self.btn_refresh.setToolTip("重新截取浮窗覆盖的区域并翻译（移动/调整大小后按）")
        self.btn_copy = QPushButton("⧉")
        self.btn_copy.setFixedWidth(34)
        self.btn_copy.setToolTip("复制全部译文")
        self.btn_close = QPushButton("✕")
        self.btn_close.setFixedWidth(34)
        self.btn_close.setToolTip("关闭（重新唤出按 Ctrl+Alt+T）")
        for b in (self.btn_refresh, self.btn_copy, self.btn_close):
            tlay.addWidget(b)
        self.btn_refresh.clicked.connect(self.refresh)
        self.btn_copy.clicked.connect(self._copy)
        self.btn_close.clicked.connect(self.close)
        lay.addWidget(toolbar)

        self.canvas = _LensCanvas()
        self.canvas.setToolTip("双击：隐藏/恢复译文，对照原文")
        lay.addWidget(self.canvas, 1)
        root.addWidget(self.card)

        self._grip = QSizeGrip(self)
        self._grip.setFixedSize(16, 16)
        self._grip.setToolTip("拖拽调整大小")

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(30)
        shadow.setOffset(0, 8)
        shadow.setColor(QColor(0, 0, 0, 150))
        self.card.setGraphicsEffect(shadow)

        self.canvas.doubleClicked.connect(self._toggle_original)

    # ---- 唤出 / 刷新 ----
    def summon_at_cursor(self):
        """把浮窗移到鼠标处（光标落在画布区），并自动截取翻译。"""
        pos = QCursor.pos()
        screen = QGuiApplication.screenAt(pos) or QGuiApplication.primaryScreen()
        avail = screen.availableGeometry()
        x = pos.x() - 18
        y = pos.y() - TOOLBAR_H - 24
        x = max(avail.left() + 4, min(x, avail.right() - self.width() - 4))
        y = max(avail.top() + 4, min(y, avail.bottom() - self.height() - 4))
        self.move(x, y)
        if not self.isVisible():
            if not self._sized_once:
                self.resize(DEFAULT_SIZE)
                self._sized_once = True
            self.show()
        self.raise_()
        self.refresh()

    def refresh(self):
        """截取画布正下方的屏幕区域（短暂隐藏自己），交给 OCR+翻译线程。"""
        canvas_tl = self.canvas.mapToGlobal(QPoint(0, 0))
        canvas_size = self.canvas.size()
        if canvas_size.width() < 24 or canvas_size.height() < 24:
            return
        center = canvas_tl + QPoint(canvas_size.width() // 2, canvas_size.height() // 2)
        screen = QGuiApplication.screenAt(center) or QGuiApplication.primaryScreen()
        dpr = float(screen.devicePixelRatio() or 1.0)

        self.hide()
        QApplication.processEvents()
        time.sleep(0.06)  # 等合成器把窗口真正撤下
        shot = screen.grabWindow(0).toImage()
        self.show()
        self.raise_()

        g0 = screen.geometry().topLeft()
        local = QRect(canvas_tl, canvas_size).translated(-g0)
        x0, y0 = round(local.x() * dpr), round(local.y() * dpr)
        x1 = min(shot.width(), round((local.x() + local.width()) * dpr))
        y1 = min(shot.height(), round((local.y() + local.height()) * dpr))
        crop = shot.copy(QRect(x0, y0, max(x1 - x0, 1), max(y1 - y0, 1)))
        if crop.isNull() or crop.width() < 2 or crop.height() < 2:
            return

        self._run_id += 1  # 在途的旧结果作废（支持连按刷新）
        run_id = self._run_id
        self._snapshot = crop
        self._snapshot_dpr = dpr
        self._saved_items = None
        self._items = []
        self.canvas.set_content(crop, dpr, [])  # 先显示原样快照
        self._set_status("⏳ 识别翻译中…")

        worker = _LensWorker(run_id, crop, dpr, self._engine, self._translator, self)
        worker.stage.connect(self._on_stage)
        worker.succeeded.connect(self._on_succeeded)
        worker.failed.connect(self._on_failed)
        worker.finished.connect(worker.deleteLater)
        worker.finished.connect(lambda w=worker: self._forget_worker(w))
        self._worker = worker
        worker.start()

    def _forget_worker(self, worker: _LensWorker):
        if self._worker is worker:
            self._worker = None

    # ---- 结果 ----
    def _on_stage(self, run_id: int, message: str):
        if run_id == self._run_id:
            self._set_status("⏳ " + message)

    def _on_succeeded(self, run_id: int, result: dict):
        if run_id != self._run_id:
            return
        self._items = result["items"]
        self.canvas.set_content(self._snapshot, self._snapshot_dpr, self._items)
        n = len(self._items)
        if n == 0:
            self._set_status("未识别到文字 · 移动浮窗后按 ↻", remember=True)
        else:
            self._set_status(f"{n} 处 · {self._cfg.get('model')} · {result['elapsed']:.1f}s", remember=True)

    def _on_failed(self, run_id: int, error: str):
        if run_id != self._run_id:
            return
        hint = "（托盘右键 → 设置检查 API Key）" if ("401" in error or "token" in error.lower()) else ""
        self._set_status("⚠ " + error[:220] + hint, "panelError")

    def _toggle_original(self):
        if self._snapshot is None:
            return
        if self._items:
            self._saved_items = self._items
            self._items = []
            self.canvas.set_content(self._snapshot, self._snapshot_dpr, [])
            self._set_status("已隐藏译文 · 双击恢复")
        elif self._saved_items:
            self._items = self._saved_items
            self._saved_items = None
            self.canvas.set_content(self._snapshot, self._snapshot_dpr, self._items)
            self._set_status(self._last_status or "")

    # ---- 移动/缩放后使快照失效，恢复透视 ----
    def _invalidate_snapshot(self, message: str):
        if not self.isVisible():
            return  # 未显示时的布局/初始定位不算用户操作
        self._run_id += 1  # 在途结果作废
        if self.canvas.has_snapshot() or self._items:
            self._snapshot = None
            self._items = []
            self._saved_items = None
            self.canvas.clear()
            self._set_status(message)

    def moveEvent(self, e):
        super().moveEvent(e)
        self._invalidate_snapshot("已移动 · 按 ↻ 翻译当前位置")

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._grip.move(self.width() - 18, self.height() - 18)
        self._grip.raise_()
        self._invalidate_snapshot("已调整大小 · 按 ↻ 重新翻译")

    # ---- 杂项 ----
    def _copy(self):
        text = "\n".join(i["dst"] for i in self._items)
        if text:
            QGuiApplication.clipboard().setText(text)
            self._set_status("已复制 ✓")
            QTimer.singleShot(1400, self._restore_status)

    def _restore_status(self):
        if self.status.text() == "已复制 ✓":
            self._set_status(self._last_status or "")

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
