"""SnapTrans 行为回归测试

把踩过坑的关键行为固化下来，防止改动时悄悄弄坏：
  1. 翻译协议解析（离线）
  2. 流式行解析器（离线）
  3. 术语表解析与注入（离线）
  4. 字体回退链 + 统一字号（离线）
  5. 透屏截取纯净度（真实窗口：截到的必须是底层内容而非浮窗自身）
  6. 隐形命中区（悬停模式下译文块像素可被鼠标命中，防系统穿透）
  7. 悬停自动气泡（真实 OCR + API：光标先停靠在句子上，译文到位自动浮现）
  8. 真实光标命中（DPI 修正后的 SetCursorPos 走系统路径）

运行：python tests/run_regression.py
说明：第 7 项走真实 API（需要 config.json 里的有效 Key），并会短暂移动系统光标。
"""

import json
import os
import sys
import time
import ctypes

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

RESULTS = []


def record(name, ok, detail=""):
    RESULTS.append((name, ok))
    tag = "PASS" if ok else ("SKIP" if ok is None else "FAIL")
    print(f"[{tag}] {name}" + (f" — {detail}" if detail else ""), flush=True)


# ---------------------------------------------------------------- 离线部分
def check_parse_reply():
    from snaptrans.translator import _parse_reply

    ok = (
        _parse_reply("1. 复制\n2. 设置", 2) == ["复制", "设置"]
        and _parse_reply("**1. 复制**\n2、设置", 2) == ["复制", "设置"]
        and _parse_reply("```text\n1. 复制\n2. 设置\n```", 2) == ["复制", "设置"]
    )
    record("翻译协议解析", ok)


def check_stream_emitter():
    from snaptrans.translator import Translator

    tr = Translator({"api_key": "x"})
    lines = ["Copy", "Settings"]
    idx = [[0], [1]]
    results = [None, None]
    emitted = set()
    calls = []
    cb = lambda i, d: calls.append((i, d))
    tr._emit_stream_line("1. 复制", lines, idx, emitted, results, cb)
    tr._emit_stream_line("2、设置", lines, idx, emitted, results, cb)
    ok = results == ["复制", "设置"] and calls == [(0, "复制"), (1, "设置")]
    # 重复编号 / 越界编号必须被安全忽略
    tr._emit_stream_line("1. 重复", lines, idx, emitted, results, cb)
    tr._emit_stream_line("99. 越界", lines, idx, emitted, results, cb)
    ok = ok and len(calls) == 2
    record("流式行解析器", ok)


def check_glossary():
    import tempfile

    from snaptrans.translator import Translator

    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write("# 注释\nZCode = ZCode\nMCP -> MCP\nsandbox\t沙箱\n坏行\n\n")
        path = f.name
    tr = Translator({"api_key": "x"}, glossary_path=path)
    prompt = tr._system_prompt()
    ok = "ZCode = ZCode" in prompt and "MCP = MCP" in prompt and "sandbox = 沙箱" in prompt and "坏行" not in prompt
    record("术语表解析与注入", ok)


def check_font_and_size():
    from PySide6.QtCore import QRectF

    from snaptrans.glass import resolve_font_family
    from snaptrans.lens import _LensCanvas

    fam = resolve_font_family("Microsoft YaHei UI")
    ok = fam == "Microsoft YaHei UI"
    c = _LensCanvas()
    c.set_items([
        {"rect": QRectF(0, 0, 100, 20), "dst": "a"},
        {"rect": QRectF(0, 30, 100, 22), "dst": "b"},
        {"rect": QRectF(0, 60, 100, 24), "dst": "c"},
    ])
    ok = ok and abs(c._base_size - 18.7) < 0.01
    record("字体回退链与统一字号", ok)


# ---------------------------------------------------------------- 真实窗口部分
def make_env():
    from PySide6.QtWidgets import QApplication, QWidget
    from PySide6.QtGui import QColor, QPainter, QFont
    from PySide6.QtCore import Qt, QRect

    app = QApplication.instance() or QApplication(sys.argv)
    from snaptrans.glass import QSS, resolve_font_family

    from snaptrans.config import load_config

    cfg = load_config()
    app.setStyleSheet(QSS.replace("__FONT__", resolve_font_family(str(cfg.get("font_family", "")))))

    from snaptrans.lens import LensWindow
    from snaptrans.ocr_engine import OcrEngine
    from snaptrans.translator import Translator

    bg = QWidget()
    bg.setWindowFlags(bg.windowFlags() | 0x00000001)  # 置顶，避免被桌面窗口盖住
    bg.setAutoFillBackground(True)
    pal = bg.palette()
    pal.setColor(bg.backgroundRole(), QColor(255, 0, 255))  # 品红：避免与常见深色应用混淆
    bg.setPalette(pal)
    bg.setGeometry(100, 100, 560, 360)

    def paint_bg(event):
        p = QPainter(bg)
        p.fillRect(bg.rect(), QColor(255, 0, 255))
        p.setFont(QFont("Arial", 18))
        p.setPen(QColor(255, 255, 255))
        p.drawText(QRect(40, 90, 480, 40), Qt.AlignVCenter, "Copy   Settings   Export")
        p.drawText(QRect(40, 150, 480, 40), Qt.AlignVCenter, "This plugin provides AI translation")
        p.end()

    bg.paintEvent = paint_bg
    bg.show()
    app.processEvents()
    time.sleep(0.8)
    app.processEvents()

    cfg = dict(cfg)
    cfg["auto_translate"] = False
    cfg["follow_content"] = False
    lens = LensWindow(OcrEngine(), Translator(cfg), cfg)
    lens.resize(520, 300)
    lens.show()
    app.processEvents()
    lens.move(120, 120)
    app.processEvents()
    time.sleep(0.5)
    app.processEvents()
    return app, bg, lens, cfg


def refresh_until_items(lens, max_attempts=4):
    for _ in range(max_attempts):
        lens.refresh()
        deadline = time.time() + 20
        while time.time() < deadline:
            app = QApplication.instance()
            app.processEvents()
            if lens._worker is None:
                break
            time.sleep(0.05)
        if lens._items:
            return True
        time.sleep(0.5)
    return False


def gui_checks():
    from PySide6.QtWidgets import QApplication

    app, bg, lens, cfg = make_env()
    dpr = lens.screen().devicePixelRatio()

    # ---- 5. 透屏截取纯净度 ----
    ok, detail = False, ""
    for _ in range(4):  # 底层窗口首绘有竞态，允许重试
        bg.raise_()
        lens.raise_()
        r = lens._capture_silent()
        if r is None:
            time.sleep(0.5)
            continue
        crop = r[0]
        c = crop.pixelColor(crop.width() // 2, crop.height() - 20)  # 取画布底部（避开文字）
        ok = c.red() > 180 and c.green() < 90 and c.blue() > 180  # 品红主导
        detail = f"中心像素 RGB=({c.red()},{c.green()},{c.blue()})"
        if ok:
            break
        time.sleep(0.5)
    if not ok:
        detail += "（可能被其他顶层窗口遮挡，跳过 GUI 后续断言）"
        record("透屏截取纯净度", False, detail)
        record("悬停自动气泡（译文到位即浮现）", None, "环境干扰，跳过")
        record("真实光标命中", None, "环境干扰，跳过")
        record("隐形命中区像素", None, "环境干扰，跳过")
        return
    record("透屏截取纯净度", True, detail)

    # ---- 7. 悬停自动气泡：第一次刷新学习位置，光标停靠后第二次刷新走缓存秒回 ----
    ok = refresh_until_items(lens)
    detail = lens.status.text()[:40]
    if not ok or not lens._items:
        record("悬停自动气泡（译文到位即浮现）", False, detail or "无译文块")
        record("真实光标命中", False, "无译文块")
        record("隐形命中区像素", False, "无译文块")
        return

    c = lens._items[0]["rect"].center()
    g = lens.canvas.mapToGlobal(c)
    ctypes.windll.user32.SetCursorPos(round(g.x() * dpr), round(g.y() * dpr))
    time.sleep(0.15)
    app.processEvents()

    lens.refresh()  # 第二次：行级缓存全命中，译文即时返回
    deadline = time.time() + 15
    while time.time() < deadline:
        app.processEvents()
        if lens._worker is None:
            break
        time.sleep(0.05)
    # 光标全程未动，译文到位后气泡应自动浮现
    t0 = time.time()
    while time.time() - t0 < 0.6:
        app.processEvents()
        time.sleep(0.02)
    auto = lens.canvas._hover_index == 0
    record("悬停自动气泡（译文到位即浮现）", auto, lens.status.text()[:40])

    # ---- 8. 真实光标命中（真实 WM_MOUSEMOVE 走系统路径） ----
    lens.canvas._hover_index = None
    lens.canvas.update()
    app.processEvents()
    ctypes.windll.user32.SetCursorPos(round(g.x() * dpr) + 12, round(g.y() * dpr))
    time.sleep(0.08)
    app.processEvents()
    ctypes.windll.user32.SetCursorPos(round(g.x() * dpr), round(g.y() * dpr))
    t0 = time.time()
    while time.time() - t0 < 0.5:
        app.processEvents()
        time.sleep(0.02)
    record("真实光标命中", lens.canvas._hover_index == 0)

    # ---- 6. 隐形命中区（画布抓图里译文块像素 alpha>0，可被系统命中） ----
    lens.canvas._hover_index = None
    lens.canvas.update()
    app.processEvents()
    img = lens.canvas.grab().toImage()
    c = lens._items[0]["rect"].center()
    alpha = img.pixelColor(int(c.x()), int(c.y())).alpha()
    record("隐形命中区像素", alpha > 0, f"alpha={alpha}")


def check_truncation():
    from snaptrans.translator import Translator, _split_truncation

    clean, cut = _split_truncation("manufacture…")
    assert clean == "manufacture" and cut
    clean2, cut2 = _split_truncation("man...")
    assert clean2 == "man" and cut2
    clean3, cut3 = _split_truncation("hello world")
    assert clean3 == "hello world" and not cut3

    tr = Translator({"api_key": "x"})
    tr._request_batch = lambda lines: ["制造" if l == "manufacture" else l for l in lines]
    out = tr.translate_lines(["manufacture…", "hello"])
    assert out[0].endswith("…") and "制造" in out[0], out  # 截断特征保留
    assert out[1] == "hello", out

    # 模型原样返回英文碎片时，原文（含省略号）保持可见而非臆译
    tr2 = Translator({"api_key": "x"})
    tr2._request_batch = lambda lines: [l for l in lines]  # 模拟模型原样返回
    out2 = tr2.translate_lines(["man…"])
    assert out2 == ["man…"], out2
    record("截断行识别与省略号守卫", True)


if __name__ == "__main__":
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)  # 真实平台：GUI 检查需要，离线检查无害
    check_parse_reply()
    check_stream_emitter()
    check_glossary()
    check_truncation()
    check_font_and_size()
    try:
        gui_checks()
    except Exception as exc:
        record("GUI 回归（异常终止）", False, f"{type(exc).__name__}: {exc}")
    failed = [n for n, ok in RESULTS if ok is False]
    print(f"\n=== {len(RESULTS) - len(failed)}/{len(RESULTS)} 通过 ===", flush=True)
    if failed:
        print("失败项：", "、".join(failed), flush=True)
    sys.exit(1 if failed else 0)
