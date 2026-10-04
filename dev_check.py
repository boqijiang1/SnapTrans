"""开发自检（不弹真窗口）：
1. 翻译协议解析与缓存逻辑
2. OCR 深底反色冒烟测试
3. 翻译放大镜离屏渲染，输出 dev_lens.png

运行：python dev_check.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np


def check_translator():
    from snaptrans.translator import Translator, _parse_reply

    assert _parse_reply("1. 复制\n2. 设置\n3. 保存", 3) == ["复制", "设置", "保存"]
    assert _parse_reply("**1. 复制**\n2、设置\n3）保存", 3) == ["复制", "设置", "保存"]
    assert _parse_reply("```text\n1. 复制\n2. 设置\n3. 保存\n```", 3) == ["复制", "设置", "保存"]

    cfg = {"api_key": "x", "api_base": "http://localhost:1", "model": "glm-4-flash"}
    tr = Translator(cfg)

    def fake_post(prompt: str) -> str:
        n = len(prompt.splitlines())
        return "\n".join(f"{i + 1}. 译{i + 1}" for i in range(n))

    tr._post = fake_post
    res = tr.translate_lines(["Copy", "Settings", "123"])
    assert res == ["译1", "译2", "123"], res
    res2 = tr.translate_lines(["Copy", "Paste"])
    assert res2 == ["译1", "译1"], res2  # Copy 命中缓存，Paste 是本轮第 1 行
    print("[ok] translator protocol & cache")


def check_ocr():
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (640, 160), (16, 18, 26))
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 36)
    except OSError:
        font = ImageFont.load_default()
    d.text((30, 50), "Copy  Settings  Save", fill=(235, 240, 255), font=font)
    bgr = np.asarray(img)[:, :, ::-1].copy()

    from snaptrans.ocr_engine import OcrEngine

    engine = OcrEngine()
    engine.preload()
    lines = engine.recognize(bgr)
    texts = [l.text for l in lines]
    print("[ocr]", texts)
    joined = " ".join(texts).upper()
    assert "COPY" in joined and "SETTINGS" in joined, texts
    print("[ok] ocr (dark background auto-invert)")


def check_widgets():
    from PySide6.QtCore import QRect, QRectF, Qt
    from PySide6.QtGui import QColor, QFont, QImage, QPainter
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    from snaptrans.glass import QSS

    app.setStyleSheet(QSS)

    from snaptrans.lens import LensWindow
    from snaptrans.ocr_engine import OcrEngine
    from snaptrans.translator import Translator

    # 伪造一张"截图"：深色背景上两行英文
    bg = QImage(458, 206, QImage.Format_ARGB32)
    bg.fill(QColor("#1E222C"))
    p = QPainter(bg)
    p.setFont(QFont("Arial", 15))
    p.setPen(QColor("#E6EBF5"))
    p.drawText(QRect(20, 12, 260, 28), Qt.AlignVCenter, "Copy    Settings    Export")
    p.drawText(QRect(20, 60, 420, 24), Qt.AlignVCenter, "This plugin provides AI powered translation.")
    p.end()

    lens = LensWindow(OcrEngine(), Translator({"api_key": "x"}), {"model": "glm-4-flash"})
    lens.resize(460, 240)
    lens.canvas.set_content(
        bg,
        1.0,
        [
            {
                "rect": QRectF(20, 12, 260, 28),
                "dst": "复制    设置    导出",
                "bg": QColor("#1E222C"),
                "fg": QColor("#F2F6FF"),
            },
            {
                "rect": QRectF(20, 60, 420, 24),
                "dst": "这个插件提供 AI 驱动的翻译功能。",
                "bg": QColor("#1E222C"),
                "fg": QColor("#F2F6FF"),
            },
        ],
    )
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dev_lens.png")
    lens.grab().save(out)
    print("[ok] lens rendered -> dev_lens.png")


if __name__ == "__main__":
    failures = 0
    for name, fn in (("translator", check_translator), ("ocr", check_ocr), ("widgets", check_widgets)):
        try:
            fn()
        except Exception as exc:
            failures += 1
            print(f"[FAIL] {name}: {type(exc).__name__}: {exc}")
    sys.exit(1 if failures else 0)
