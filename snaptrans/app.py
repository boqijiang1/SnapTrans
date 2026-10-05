"""应用装配：DPI、全局热键、托盘、划词翻译、开机自启、设置。"""

from __future__ import annotations

import ctypes
import os
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

import winreg
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

from . import __version__, log
from .bubble import ClipboardBubble
from .config import GLOSSARY_PATH, GLOSSARY_TEMPLATE, load_config, save_config
from .glass import QSS, resolve_font_family
from .lens import LensWindow
from .ocr_engine import OcrEngine
from .settings_dialog import SettingsDialog
from .translator import Translator

WM_HOTKEY = 0x0312
MOD_NOREPEAT = 0x4000
_MODS = {"ctrl": 0x0002, "alt": 0x0001, "shift": 0x0004, "win": 0x0008}
HOTKEY_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
AUTOSTART_NAME = "SnapTrans"


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
    """Win32 RegisterHotKey 多热键管理（无需管理员权限），按名字注册/换绑。"""

    def __init__(self):
        super().__init__()
        self._bindings: dict[int, tuple[int, int]] = {}  # id -> (mods, vk)
        self._callbacks: dict[int, callable] = {}
        self._ids: dict[str, int] = {}

    def add(self, name: str, hotkey: str, callback) -> bool:
        parsed = _parse_hotkey(hotkey)
        if not parsed:
            return False
        hid = self._ids.get(name)
        if hid is None:
            hid = max(self._bindings, default=0) + 1
            self._ids[name] = hid
        self._bindings[hid] = parsed
        self._callbacks[hid] = callback
        return self._register(hid)

    def rebind(self, name: str, hotkey: str) -> bool:
        """换绑已注册的热键（设置页修改热键用）。"""
        hid = self._ids.get(name)
        if hid is None:
            return False
        parsed = _parse_hotkey(hotkey)
        if not parsed:
            return False
        ctypes.windll.user32.UnregisterHotKey(None, hid)
        self._bindings[hid] = parsed
        return self._register(hid)

    def _register(self, hid: int) -> bool:
        mods, vk = self._bindings[hid]
        return bool(ctypes.windll.user32.RegisterHotKey(None, hid, mods | MOD_NOREPEAT, vk))

    def nativeEventFilter(self, eventType, message):
        if eventType == b"windows_generic_MSG":
            try:
                addr = int(message)
            except TypeError:
                addr = message.__int__()
            msg = wintypes.MSG.from_address(addr)
            if msg.message == WM_HOTKEY and msg.wParam in self._callbacks:
                self._callbacks[msg.wParam]()
                return True, 0
        return False, 0


# ---- 开机自启（HKCU 注册表 Run 键，无需管理员权限） ----
def _autostart_command() -> str:
    if getattr(sys, "frozen", False):  # 打包成 exe 后直接指向 exe
        return f'"{sys.executable}"'
    exe = sys.executable
    if exe.lower().endswith("python.exe"):  # 登录启动不带控制台
        pythonw = exe[: -len("python.exe")] + "pythonw.exe"
        if os.path.exists(pythonw):
            exe = pythonw
    launcher = Path(__file__).resolve().parent.parent / "launch.py"
    return f'"{exe}" "{launcher}"'


def _get_autostart() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, HOTKEY_RUN_KEY) as key:
            winreg.QueryValueEx(key, AUTOSTART_NAME)
            return True
    except OSError:
        return False


def _set_autostart(enabled: bool) -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, HOTKEY_RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, AUTOSTART_NAME, 0, winreg.REG_SZ, _autostart_command())
            else:
                try:
                    winreg.DeleteValue(key, AUTOSTART_NAME)
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        return False


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
    def __init__(self, app: QApplication, cfg: dict):
        super().__init__()
        self.app = app
        self.cfg = cfg
        self.engine = OcrEngine(self.cfg.get("invert_threshold", 110))
        self.translator = Translator(self.cfg)
        self.lens: LensWindow | None = None

        self.bubble: ClipboardBubble | None = None
        self.hotkey = _HotkeyFilter()
        self.app.installNativeEventFilter(self.hotkey)
        if not self.hotkey.add("main", self.cfg.get("hotkey", "ctrl+alt+t"), self._summon):
            QTimer.singleShot(
                800,
                lambda: self._tray_msg(
                    "热键注册失败",
                    f"{self.cfg.get('hotkey')} 可能被其他程序占用，请到设置里换一个。",
                ),
            )
        if not self.hotkey.add(
            "clipboard", self.cfg.get("hotkey_clipboard", "ctrl+alt+b"), self._translate_clipboard
        ):
            QTimer.singleShot(
                900,
                lambda: self._tray_msg(
                    "剪贴板热键注册失败",
                    f"{self.cfg.get('hotkey_clipboard')} 可能被占用，请到设置里换一个。",
                ),
            )
        self._ensure_glossary()

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
        act_clip = QAction(f"剪贴板翻译（{self.cfg.get('hotkey_clipboard', 'ctrl+alt+b')}）", menu)
        act_clip.triggered.connect(self._translate_clipboard)
        self.act_auto = QAction("移动后自动翻译", menu)
        self.act_auto.setCheckable(True)
        self.act_auto.setChecked(bool(self.cfg.get("auto_translate", True)))
        self.act_auto.toggled.connect(self._set_auto)
        self.act_follow = QAction("跟随内容变化", menu)
        self.act_follow.setCheckable(True)
        self.act_follow.setChecked(bool(self.cfg.get("follow_content", True)))
        self.act_follow.toggled.connect(self._set_follow)
        self.act_autostart = QAction("开机自启", menu)
        self.act_autostart.setCheckable(True)
        self.act_autostart.setChecked(_get_autostart())
        self.act_autostart.toggled.connect(self._toggle_autostart)
        act_glossary = QAction("打开术语表", menu)
        act_glossary.triggered.connect(self._open_glossary)
        act_set = QAction("设置…", menu)
        act_set.triggered.connect(self.open_settings)
        act_quit = QAction("退出", menu)
        act_quit.triggered.connect(self.app.quit)
        menu.addAction(act_lens)
        menu.addAction(act_clip)
        menu.addSeparator()
        menu.addAction(self.act_auto)
        menu.addAction(self.act_follow)
        menu.addAction(self.act_autostart)
        menu.addSeparator()
        menu.addAction(act_glossary)
        menu.addAction(act_set)
        menu.addSeparator()
        menu.addAction(act_quit)
        self.tray.setContextMenu(menu)
        self.tray.setToolTip(f"SnapTrans v{__version__} 翻译放大镜")
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

    def _set_auto(self, checked: bool):
        self.cfg["auto_translate"] = bool(checked)
        save_config(self.cfg)
        if self.lens is not None:
            self.lens.set_auto_translate(checked)
        self._tray_msg("已保存", "移动后自动翻译：" + ("开" if checked else "关"))

    def _set_follow(self, checked: bool):
        self.cfg["follow_content"] = bool(checked)
        save_config(self.cfg)
        if self.lens is not None:
            self.lens.set_follow_content(checked)
        self._tray_msg("已保存", "跟随内容变化：" + ("开" if checked else "关"))

    def _toggle_autostart(self, checked: bool):
        if _set_autostart(bool(checked)):
            self._tray_msg("已保存", "开机自启：" + ("开" if checked else "关"))
        else:
            self.act_autostart.setChecked(not checked)  # 回滚勾选状态
            self._tray_msg("设置失败", "写入注册表失败，请以普通权限重试。")

    def _open_glossary(self):
        self._ensure_glossary()
        try:
            os.startfile(str(GLOSSARY_PATH))  # noqa: 只在 Windows 运行
        except OSError as exc:
            self._tray_msg("打开失败", str(exc))

    def _ensure_glossary(self):
        if not GLOSSARY_PATH.exists():
            try:
                GLOSSARY_PATH.write_text(GLOSSARY_TEMPLATE, encoding="utf-8")
            except OSError:
                pass

    def _translate_clipboard(self):
        text = QApplication.clipboard().text().strip()
        if not text:
            self._tray_msg("剪贴板是空的", "先复制要翻译的英文文本，再按热键。")
            return
        if self.bubble is None:
            self.bubble = ClipboardBubble(self.translator, self.cfg)
            log("划词翻译气泡已创建")
        self.bubble.summon(text)

    # ---- 设置 ----
    def open_settings(self):
        dialog = SettingsDialog(self.cfg)
        if dialog.exec() == QDialog.Accepted:
            values = dialog.values()
            old = {key: self.cfg.get(key) for key in ("hotkey", "hotkey_clipboard")}
            self.cfg.update(values)
            save_config(self.cfg)
            self.translator.cfg = self.cfg
            failed: list[str] = []
            for name, key, label in (
                ("main", "hotkey", "翻译热键"),
                ("clipboard", "hotkey_clipboard", "剪贴板热键"),
            ):
                if values.get(key) != old.get(key) and not self.hotkey.rebind(name, values[key]):
                    failed.append(f"{label} {values[key]} 可能被占用")
            if failed:
                self._tray_msg("热键注册失败", "；".join(failed) + "，请到设置里换一个。")
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

    cfg = load_config()
    font_family = resolve_font_family(str(cfg.get("font_family", "")))
    app.setFont(QFont(font_family, 10))
    app.setStyleSheet(QSS.replace("__FONT__", font_family))  # 工具界面与译文同一字体

    app.setQuitOnLastWindowClosed(False)  # 托盘常驻，关放大镜不退出
    app.setWindowIcon(_make_icon())

    controller = SnapTransApp(app, cfg)  # 局部变量保持引用存活，防止控制器被垃圾回收
    log(
        f"SnapTrans v{__version__} 已启动 · 自动翻译"
        f"{'开' if controller.cfg.get('auto_translate') else '关'} · 热键 {controller.cfg.get('hotkey')}"
        f" · 字体 {font_family}"
    )
    return app.exec()
