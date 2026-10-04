"""应用装配：DPI、全局热键、托盘、设置。"""

from __future__ import annotations

import ctypes
import sys
import threading
import time
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter, QObject, QRect, Qt, QTimer
from PySide6.QtGui import (
    QAction,
    QColor,
    QFont,
    QIcon,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPixmap,
    QPen,
)
from PySide6.QtWidgets import QApplication, QDialog, QMenu, QSystemTrayIcon

from .config import load_config, save_config
from .glass import QSS
from .lens import LensWindow
from .ocr_engine import OcrEngine
from .settings_dialog import SettingsDialog
from .translator import Translator

WM_HOTKEY = 0x0312
MOD_NOREPEAT = 0x4000
_MODS = {"ctrl": 0x0002, "alt": 0x0001, "shift": 0x0004, "win": 0x0008}
HOTKEY_ID = 0x5A5A


def log(message: str) -> None:
    print(f"[snaptrans] {message}", flush=True)


def _parse_hotkey(text: str) -> tuple[int, int] | None:
    parts = [p.strip().lower() for p in text.split("+") if p.strip()]
    if not parts:
        return None
    mods = 0
    for p in parts[:-1]:
        if p not in _MODS:
            return None
        mods |= _MODS[p]
    key = parts[-1]
    if len(key) == 1 and key.isalnum():
        vk = ord(key.upper())
    elif key.startswith("f") and key[1:].isdigit() and 1 <= int(key[1:]) <= 12:
        vk = 0x70 + int(key[1:]) - 1
    else:
        return None
    return mods, vk


class _HotkeyFilter(QAbstractNativeEventFilter):
    """用 Win32 RegisterHotKey 实现全局热键（无需管理员权限）。"""

    def __init__(self, hotkey: str, callback):
        super().__init__()
        self.callback = callback
        self._mods: int | None = None
        self._vk: int | None = None
        self.set_hotkey(hotkey)

    def set_hotkey(self, text: str) -> bool:
        parsed = _parse_hotkey(text)
        if not parsed:
            return False
        self._mods, self._vk = parsed
        return True

    def register(self) -> bool:
        if self._mods is None:
            return False
        return bool(
            ctypes.windll.user32.RegisterHotKey(None, HOTKEY_ID, self._mods | MOD_NOREPEAT, self._vk)
        )

    def unregister(self) -> None:
        ctypes.windll.user32.UnregisterHotKey(None, HOTKEY_ID)

    def nativeEventFilter(self, eventType, message):
        if eventType == b"windows_generic_MSG":
            try:
                addr = int(message)
            except TypeError:
                addr = message.__int__()
            msg = wintypes.MSG.from_address(addr)
            if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
                self.callback()
                return True, 0
        return False, 0


def _make_icon() -> QIcon:
    img = QImage(64, 64, QImage.Format_ARGB32)
    img.fill(0)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    grad = QLinearGradient(6, 6, 58, 58)
    grad.setColorAt(0.0, QColor("#5B8CFF"))
    grad.setColorAt(1.0, QColor("#8F6BFF"))
    path = QPainterPath()
    path.addRoundedRect(4, 4, 56, 56, 15, 15)
    p.fillPath(path, grad)
    highlight = QPainterPath()
    highlight.addRoundedRect(4, 4, 56, 28, 15, 15)
    p.fillPath(highlight, QColor(255, 255, 255, 40))
    p.setPen(QPen(QColor(255, 255, 255, 90), 1.4))
    p.drawPath(path)
    font = QFont("Microsoft YaHei UI", 24)
    font.setBold(True)
    p.setFont(font)
    p.setPen(QColor("white"))
    p.drawText(QRect(0, 2, 64, 60), Qt.AlignCenter, "译")
    p.end()
    return QIcon(QPixmap.fromImage(img))


class SnapTransApp(QObject):
    def __init__(self, app: QApplication):
        super().__init__()
        self.app = app
        self.cfg = load_config()
        self.engine = OcrEngine(self.cfg.get("invert_threshold", 110))
        self.translator = Translator(self.cfg)
        self.lens: LensWindow | None = None

        self.hotkey = _HotkeyFilter(self.cfg.get("hotkey", "ctrl+alt+t"), self._summon)
        self.app.installNativeEventFilter(self.hotkey)
        if not self.hotkey.register():
            QTimer.singleShot(
                800,
                lambda: self._tray_msg(
                    "热键注册失败",
                    f"{self.cfg.get('hotkey')} 可能被其他程序占用，请到设置里换一个。",
                ),
            )

        self._make_tray()
        threading.Thread(target=self._preload_ocr, daemon=True, name="ocr-preload").start()
        if not self.cfg.get("api_key"):
            QTimer.singleShot(400, self.open_settings)

    # ---- 托盘 ----
    def _make_tray(self):
        self.tray = QSystemTrayIcon(_make_icon())
        menu = QMenu()
        menu.setAttribute(Qt.WA_TranslucentBackground)
        act_lens = QAction("翻译放大镜", menu)
        act_lens.triggered.connect(self._summon)
        act_set = QAction("设置…", menu)
        act_set.triggered.connect(self.open_settings)
        act_quit = QAction("退出", menu)
        act_quit.triggered.connect(self.app.quit)
        menu.addAction(act_lens)
        menu.addAction(act_set)
        menu.addSeparator()
        menu.addAction(act_quit)
        self.tray.setContextMenu(menu)
        self.tray.setToolTip("SnapTrans 翻译放大镜")
        self.tray.activated.connect(
            lambda reason: self._summon() if reason == QSystemTrayIcon.Trigger else None
        )
        self.tray.show()

    def _tray_msg(self, title: str, text: str):
        self.tray.showMessage(title, text, QSystemTrayIcon.Information, 4000)

    def _preload_ocr(self):
        t0 = time.perf_counter()
        self.engine.preload()
        log(f"OCR 模型已就绪（{time.perf_counter() - t0:.1f}s）")

    # ---- 放大镜 ----
    def _summon(self):
        if self.lens is None:
            self.lens = LensWindow(self.engine, self.translator, self.cfg)
            log("翻译放大镜已创建")
        self.lens.summon_at_cursor()

    # ---- 设置 ----
    def open_settings(self):
        dialog = SettingsDialog(self.cfg)
        if dialog.exec() == QDialog.Accepted:
            values = dialog.values()
            old_hotkey = self.cfg.get("hotkey")
            self.cfg.update(values)
            save_config(self.cfg)
            self.translator.cfg = self.cfg
            if values.get("hotkey") != old_hotkey:
                self.hotkey.unregister()
                self.hotkey.set_hotkey(values["hotkey"])
                if self.hotkey.register():
                    self._tray_msg("已保存", f"热键 {values['hotkey']} 即刻生效。")
                else:
                    self._tray_msg("热键注册失败", f"{values['hotkey']} 可能被占用，请换一个。")
            else:
                self._tray_msg("已保存", "设置已保存。")


def main() -> int:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor DPI aware
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName("SnapTrans")
    app.setApplicationDisplayName("SnapTrans")
    app.setFont(QFont("Microsoft YaHei UI", 10))
    app.setStyleSheet(QSS)
    app.setQuitOnLastWindowClosed(False)  # 托盘常驻，关放大镜不退出
    app.setWindowIcon(_make_icon())

    controller = SnapTransApp(app)  # 局部变量保持引用存活，防止控制器被垃圾回收
    log("SnapTrans 已启动，按 Ctrl+Alt+T 唤出翻译放大镜")
    return app.exec()
