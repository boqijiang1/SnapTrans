"""翻译放大镜：小块玻璃浮窗，浮在目标文字上方，两种显示模式：

- 悬停模式（默认）：翻译后先显示原文快照，鼠标移到某句话上浮现双语对照气泡，
  单击该行只复制该行译文；
- 替换模式：译文按原文的位置、字号、配色原位替换，适合通读整段。

定位时浮窗近乎全透明（可透视下层内容）；按 ↻ 后画布先显示截取的原样快照。
"""

from __future__ import annotations

import time

import numpy as np
from PySide6.QtCore import (
    QEasingCurve,
    QPoint,
    QRect,
    QRectF,
    QSize,
    Qt,
    QThread,
    QTimer,
    Signal,
    QVariantAnimation,
)
from PySide6.QtGui import (
    QCursor,
    QColor,
    QFont,
    QFontMetricsF,
    QGuiApplication,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
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

from . import log
from .config import save_config
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
                items.append({
                    "rect": _box_to_rect(line.box, self._dpr),
                    "src": line.text,
                    "dst": dst,
                    "bg": bg,
                    "fg": fg,
                })
            self.succeeded.emit(self._run_id, {"items": items, "elapsed": time.perf_counter() - t0})
        except TranslatorError as exc:
            self.failed.emit(self._run_id, str(exc))
        except Exception as exc:  # 任何意外都不允许弄崩主线程
            self.failed.emit(self._run_id, f"{type(exc).__name__}: {exc}")


class _LensCanvas(QWidget):
    """先画截取的快照；悬停模式浮出对照气泡，替换模式原位重绘译文。"""

    doubleClicked = Signal()
    lineCopied = Signal(str)

    MODE_HOVER = "hover"
    MODE_REPLACE = "replace"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._bg: QImage | None = None
        self._dpr = 1.0
        self._items: list[dict] = []
        self._font = QFont("Microsoft YaHei UI")
        self._mode = self.MODE_HOVER
        self._hover_index: int | None = None
        self._tip_progress = 0.0
        self._tip_anim = QVariantAnimation(self)
        self._tip_anim.setDuration(140)
        self._tip_anim.setStartValue(0.0)
        self._tip_anim.setEndValue(1.0)
        self._tip_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._tip_anim.valueChanged.connect(self._on_tip_anim)
        self.setMouseTracking(True)

    # ---- 数据 ----
    def set_mode(self, mode: str):
        if mode in (self.MODE_HOVER, self.MODE_REPLACE) and mode != self._mode:
            self._mode = mode
            self._clear_hover()
            self.update()

    @property
    def mode(self) -> str:
        return self._mode

    def set_content(self, bg: QImage | None, dpr: float, items: list[dict]):
        self._bg = bg
        self._dpr = dpr
        self._items = list(items)
        self._clear_hover()
        self.update()

    def clear(self):
        self._bg = None
        self._items = []
        self._clear_hover()
        self.update()

    def has_snapshot(self) -> bool:
        return self._bg is not None

    # ---- 绘制 ----
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
        if self._mode == self.MODE_REPLACE:
            for item in self._items:
                self._draw_replacement(p, item)
        elif self._hover_index is not None and self._hover_index < len(self._items):
            self._draw_hover(p, self._items[self._hover_index])
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

    def _draw_hover(self, p: QPainter, item: dict):
        rect: QRectF = item["rect"]
        progress = max(self._tip_progress, 0.0)

        # 高亮当前块（即时出现，不等动画）
        p.setPen(QPen(QColor(126, 179, 255, 170), 1.4))
        p.setBrush(QColor(126, 179, 255, 26))
        p.drawRoundedRect(rect.adjusted(-2, -2, 2, 2), 4, 4)
        p.setBrush(Qt.NoBrush)

        w, h = float(self.width()), float(self.height())
        main_size = max(12.0, min(rect.height() * 0.9, 18.0))
        main_font = QFont(self._font)
        main_font.setPixelSize(int(main_size))
        src_font = QFont(self._font)
        src_font.setPixelSize(max(9, int(main_size * 0.72)))
        fm_main = QFontMetricsF(main_font)
        fm_src = QFontMetricsF(src_font)

        tip_w = min(max(rect.width() * 1.5, 230.0), w - 16.0)
        inner_w = tip_w - 18.0
        dst_rect = fm_main.boundingRect(QRectF(0, 0, inner_w, 10000.0), Qt.TextWordWrap, item["dst"])
        src_text = fm_src.elidedText(item.get("src", ""), Qt.ElideRight, inner_w)
        content_h = dst_rect.height() + 4.0
        if src_text:
            content_h += fm_src.height() + 2.0
        tip_h = content_h + 14.0

        x = max(6.0, min(rect.left() - 6.0, w - tip_w - 6.0))
        below = True
        y = rect.bottom() + 8.0
        if y + tip_h > h - 6.0:  # 下方放不下就浮到上方
            y = rect.top() - 8.0 - tip_h
            below = False
        y = max(6.0, min(y, max(6.0, h - tip_h - 6.0)))
        dy = (1.0 - progress) * (5.0 if below else -5.0)  # 从块的一侧滑入
        panel = QRectF(x, y + dy, tip_w, tip_h)

        p.setOpacity(progress)
        p.setPen(QPen(QColor(126, 179, 255, 130), 1))
        p.setBrush(QColor(15, 17, 24, 240))
        p.drawRoundedRect(panel, 8, 8)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(126, 179, 255, 180))
        p.drawRoundedRect(QRectF(panel.left() + 5, panel.top() + 6, 3, tip_h - 12), 1.5, 1.5)

        ty = panel.top() + 7.0
        if src_text:
            p.setFont(src_font)
            p.setPen(QColor(234, 240, 255, 150))
            p.drawText(QRectF(panel.left() + 14, ty, inner_w, fm_src.height()),
                       Qt.AlignLeft | Qt.AlignVCenter, src_text)
            ty += fm_src.height() + 2.0
        p.setFont(main_font)
        p.setPen(QColor("#F2F6FF"))
        p.drawText(QRectF(panel.left() + 14, ty, inner_w, dst_rect.height() + 4.0),
                   Qt.AlignLeft | Qt.TextWordWrap, item["dst"])
        p.setOpacity(1.0)

    # ---- 悬停交互 ----
    def mouseMoveEvent(self, e):
        if self._mode == self.MODE_HOVER and self._items:
            pos = e.position()
            idx = None
            for i in range(len(self._items) - 1, -1, -1):
                if self._items[i]["rect"].adjusted(-3, -3, 3, 3).contains(pos):
                    idx = i
                    break
            if idx != self._hover_index:
                self._hover_index = idx
                if idx is not None:
                    self._tip_anim.stop()
                    self._tip_progress = 0.0
                    self._tip_anim.start()
                else:
                    self._tip_progress = 0.0
                self.update()
        super().mouseMoveEvent(e)

    def leaveEvent(self, e):
        self._clear_hover()
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self._mode == self.MODE_HOVER and self._hover_index is not None:
            self.lineCopied.emit(self._items[self._hover_index]["dst"])
        super().mousePressEvent(e)

    def mouseDoubleClickEvent(self, e):
        self.doubleClicked.emit()
        super().mouseDoubleClickEvent(e)

    def _clear_hover(self):
        if self._hover_index is not None or self._tip_progress:
            self._hover_index = None
            self._tip_anim.stop()
            self._tip_progress = 0.0
            self.update()

    def _on_tip_anim(self, value):
        self._tip_progress = float(value)
        self.update()


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
        self._snapshot: QImage | None = None
        self._snapshot_dpr = 1.0
        self._last_status = ""
        self._flash_text = ""
        self._sized_once = False
        self._auto_enabled = bool(cfg.get("auto_translate", True))
        self._captured_geometry: QRect | None = None
        self._auto_timer = QTimer(self)
        self._auto_timer.setSingleShot(True)
        self._auto_timer.setInterval(450)  # 移动停稳 450ms 后自动翻译
        self._auto_timer.timeout.connect(self._auto_refresh)

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
        self.btn_mode = QPushButton("悬停")
        self.btn_mode.setToolTip("切换显示模式：悬停对照（默认）/ 全部原位替换；双击画布也可切换")
        self.btn_refresh = QPushButton("↻ 刷新")
        self.btn_refresh.setToolTip("重新截取浮窗覆盖的区域并翻译（移动/调整大小后按）")
        self.btn_copy = QPushButton("⧉")
        self.btn_copy.setFixedWidth(34)
        self.btn_copy.setToolTip("复制全部译文")
        self.btn_close = QPushButton("✕")
        self.btn_close.setFixedWidth(34)
        self.btn_close.setToolTip("关闭（重新唤出按 Ctrl+Alt+T）")
        for b in (self.btn_mode, self.btn_refresh, self.btn_copy, self.btn_close):
            tlay.addWidget(b)
        self.btn_mode.clicked.connect(self._toggle_mode)
        self.btn_refresh.clicked.connect(self.refresh)
        self.btn_copy.clicked.connect(self._copy)
        self.btn_close.clicked.connect(self.close)
        lay.addWidget(toolbar)

        self.canvas = _LensCanvas()
        self.canvas.setToolTip("悬停：移到句子上看译文，单击复制该行；双击：切换悬停/替换模式")
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

        self.canvas.doubleClicked.connect(self._toggle_mode)
        self.canvas.lineCopied.connect(self._on_line_copied)
        self._apply_mode(str(cfg.get("lens_mode", "hover")), save=False)

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
        self._auto_timer.stop()
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
        try:
            shot = screen.grabWindow(0).toImage()
            g0 = screen.geometry().topLeft()
            local = QRect(canvas_tl, canvas_size).translated(-g0)
            x0, y0 = round(local.x() * dpr), round(local.y() * dpr)
            x1 = min(shot.width(), round((local.x() + local.width()) * dpr))
            y1 = min(shot.height(), round((local.y() + local.height()) * dpr))
            crop = shot.copy(QRect(x0, y0, max(x1 - x0, 1), max(y1 - y0, 1)))
        except Exception as exc:
            self.show()
            self.raise_()
            self._set_status(f"⚠ 截屏失败：{exc}", "panelError")
            return
        self.show()
        self.raise_()

        if crop.isNull() or crop.width() < 2 or crop.height() < 2:
            self._set_status("⚠ 截屏失败：区域无效", "panelError")
            return

        self._captured_geometry = QRect(canvas_tl, canvas_size)
        self._run_id += 1  # 在途的旧结果作废（支持连按刷新）
        run_id = self._run_id
        self._snapshot = crop
        self._snapshot_dpr = dpr
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
            return
        mode_tip = "悬停对照 · " if self.canvas.mode == "hover" else ""
        self._set_status(f"{n} 处 · {mode_tip}{self._cfg.get('model')} · {result['elapsed']:.1f}s", remember=True)

    def _on_failed(self, run_id: int, error: str):
        if run_id != self._run_id:
            return
        hint = "（托盘右键 → 设置检查 API Key）" if ("401" in error or "token" in error.lower()) else ""
        self._set_status("⚠ " + error[:220] + hint, "panelError")

    # ---- 模式 ----
    def _apply_mode(self, mode: str, save: bool = True):
        self.canvas.set_mode(mode)
        self.btn_mode.setText("悬停" if mode == "hover" else "替换")
        if save:
            self._cfg["lens_mode"] = mode
            save_config(self._cfg)

    def _toggle_mode(self):
        if self.canvas.mode == "hover":
            self._apply_mode("replace")
            self._set_status("替换模式 · 译文已原位覆盖，双击画布切回", remember=True)
        else:
            self._apply_mode("hover")
            self._set_status("悬停模式 · 鼠标移到句子上浮现译文，单击复制该行", remember=True)

    # ---- 移动/缩放后：快照失效恢复透视，并自动触发翻译 ----
    def _invalidate_snapshot(self, message: str):
        if not self.isVisible():
            return  # 未显示时的布局/初始定位不算用户操作
        geo = QRect(self.canvas.mapToGlobal(QPoint(0, 0)), self.canvas.size())
        if self._snapshot is not None and geo == self._captured_geometry:
            return  # 几何没变（如 show 引发的伪移动事件），快照仍有效
        self._run_id += 1  # 在途结果作废
        self._captured_geometry = None
        if self.canvas.has_snapshot() or self._items:
            self._snapshot = None
            self._items = []
            self.canvas.clear()
            self._set_status(message)
        if self._auto_enabled:
            self._auto_timer.start()  # 每次移动都重置，停稳后触发

    def moveEvent(self, e):
        super().moveEvent(e)
        self._invalidate_snapshot(
            "跟随移动 · 松手后自动翻译" if self._auto_enabled else "已移动 · 按 ↻ 翻译当前位置"
        )

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._grip.move(self.width() - 18, self.height() - 18)
        self._grip.raise_()
        self._invalidate_snapshot(
            "调整大小 · 松手后自动翻译" if self._auto_enabled else "已调整大小 · 按 ↻ 重新翻译"
        )

    def _auto_refresh(self):
        if not self.isVisible() or not self._auto_enabled:
            return
        if QGuiApplication.mouseButtons() & Qt.LeftButton:
            self._auto_timer.start()  # 还在拖拽（中途停顿），松手再翻译
            return
        log("自动翻译：位置调整触发")
        self.refresh()

    def set_auto_translate(self, enabled: bool):
        self._auto_enabled = enabled
        if not enabled:
            self._auto_timer.stop()

    # ---- 复制 ----
    def _copy(self):
        text = "\n".join(i["dst"] for i in self._items)
        if text:
            self._flash("已复制全部译文 ✓")

    def _on_line_copied(self, text: str):
        QGuiApplication.clipboard().setText(text)
        self._flash("已复制该行 ✓")

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
