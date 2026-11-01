# -*- coding: utf-8 -*-
"""RapidOCR（PP-OCR 模型）兜底识别：系统文字识别用不了时才加载。

它只给整行的方框，词的位置按字符宽度估算（geometry.split_line）。
"""
from __future__ import annotations

import numpy as np
from PIL import Image

from .geometry import WordBox, split_line


class RapidOcr:
    name = "RapidOCR"

    def __init__(self):
        from rapidocr_onnxruntime import RapidOCR
        self._engine = RapidOCR()

    def words(self, img: Image.Image) -> list[WordBox]:
        arr = np.array(img.convert("RGB"))[:, :, ::-1]  # RGB -> BGR
        result, _ = self._engine(arr)
        out = []
        for pts, text, _score in result or []:
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            out.extend(split_line(text, min(xs), min(ys), max(xs), max(ys)))
        return out
