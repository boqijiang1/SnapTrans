"""SnapTrans —— 框选屏幕区域、OCR 识别并翻译成中文的小工具。"""

import sys

__version__ = "0.5.1"


def log(message: str) -> None:
    print(f"[snaptrans] {message}", flush=True)
