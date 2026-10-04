"""RapidOCR 封装：深底浅字自动反色，输出按阅读顺序整理的文本行 + QImage 转换工具。"""

from __future__ import annotations

import threading
from dataclasses import dataclass

import numpy as np
from PySide6.QtGui import QImage


@dataclass
class OcrLine:
    text: str
    box: np.ndarray  # 4x2 物理像素坐标
    score: float


def qimage_to_bgr(img: QImage) -> np.ndarray:
    """QImage → HxWx3 BGR ndarray（RapidOCR 入参格式），自动处理行对齐填充。"""
    converted = img.convertToFormat(QImage.Format_BGR888)
    h, w = converted.height(), converted.width()
    stride = converted.bytesPerLine()
    buf = np.frombuffer(converted.constBits(), dtype=np.uint8)
    buf = buf[: stride * h]
    return buf.reshape(h, stride)[:, : w * 3].reshape(h, w, 3).copy()


class OcrEngine:
    """懒加载 RapidOCR；recognize() 可在任意线程调用（内部加锁）。"""

    def __init__(self, invert_threshold: int = 110):
        self._invert_threshold = invert_threshold
        self._ocr = None
        self._lock = threading.Lock()

    @property
    def ready(self) -> bool:
        return self._ocr is not None

    def preload(self) -> None:
        with self._lock:
            if self._ocr is None:
                from rapidocr_onnxruntime import RapidOCR

                self._ocr = RapidOCR()

    def recognize(self, image_bgr: np.ndarray) -> list[OcrLine]:
        import cv2

        self.preload()
        img = image_bgr
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        if int(gray.mean()) < self._invert_threshold:  # 深色主题：反色后识别更稳
            img = cv2.bitwise_not(img)
        raw, _ = self._ocr(img)
        lines: list[OcrLine] = []
        for item in raw or []:
            text = str(item[1]).strip()
            if text:
                lines.append(OcrLine(text, np.asarray(item[0], dtype=np.float64), float(item[2])))
        return _reading_order(lines)


def _reading_order(lines: list[OcrLine]) -> list[OcrLine]:
    """垂直重叠的框归入同一视觉行；行内水平间距过大的（独立按钮等）不合并，保持逐块对应。"""
    if not lines:
        return []

    def top(l: OcrLine) -> float:
        return float(l.box[:, 1].min())

    def bottom(l: OcrLine) -> float:
        return float(l.box[:, 1].max())

    def left(l: OcrLine) -> float:
        return float(l.box[:, 0].min())

    def right(l: OcrLine) -> float:
        return float(l.box[:, 0].max())

    lines.sort(key=lambda l: (top(l), left(l)))
    rows: list[list[OcrLine]] = []
    for line in lines:
        h = max(bottom(line) - top(line), 1.0)
        for row in rows:
            ref = row[-1]
            ref_h = max(bottom(ref) - top(ref), 1.0)
            overlap = min(bottom(line), bottom(ref)) - max(top(line), top(ref))
            if overlap > 0.45 * min(h, ref_h):
                row.append(line)
                break
        else:
            rows.append([line])

    merged: list[OcrLine] = []
    for row in rows:
        row.sort(key=left)
        row_h = sorted(bottom(l) - top(l) for l in row)[len(row) // 2]
        gap_limit = max(row_h * 1.2, 24.0)  # 间距超过它就当独立的块（如分开的按钮）
        group: list[OcrLine] = [row[0]]
        for nxt in row[1:]:
            if left(nxt) - right(group[-1]) > gap_limit:
                merged.append(_merge_group(group))
                group = [nxt]
            else:
                group.append(nxt)
        merged.append(_merge_group(group))
    merged.sort(key=lambda l: (top(l), left(l)))
    return _dedup_overlap(merged)


def _dedup_overlap(lines: list[OcrLine], threshold: float = 0.55) -> list[OcrLine]:
    """丢掉和已保留块重叠过大的框（OCR 偶尔对同一文字重复出框，会导致译文叠在一起）。"""

    def rect(l: OcrLine) -> tuple[float, float, float, float]:
        return (
            float(l.box[:, 0].min()),
            float(l.box[:, 1].min()),
            float(l.box[:, 0].max()),
            float(l.box[:, 1].max()),
        )

    kept: list[tuple[OcrLine, float, tuple[float, float, float, float]]] = []
    for line in lines:
        x0, y0, x1, y1 = rect(line)
        area = max((x1 - x0) * (y1 - y0), 1.0)
        dup = False
        for _, k_area, (kx0, ky0, kx1, ky1) in kept:
            inter_w = min(x1, kx1) - max(x0, kx0)
            inter_h = min(y1, ky1) - max(y0, ky0)
            if inter_w > 0 and inter_h > 0 and (inter_w * inter_h) / min(area, k_area) > threshold:
                dup = True
                break
        if not dup:
            kept.append((line, area, (x0, y0, x1, y1)))
    return [line for line, _, _ in kept]


def _merge_group(group: list[OcrLine]) -> OcrLine:
    if len(group) == 1:
        return group[0]
    return OcrLine(
        text=" ".join(l.text for l in group),
        box=np.vstack([l.box for l in group]),
        score=min(l.score for l in group),
    )
