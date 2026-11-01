# -*- coding: utf-8 -*-
"""Windows 自带的文字识别（Windows.Media.Ocr）：每个词单独给出方框，约几十毫秒，不用下载模型。

Win10 / Win11 都有。优先用英语识别；系统没装英语识别时用系统语言（简体中文的识别也认英文）。
想装英语识别：设置 → 时间和语言 → 语言 → 添加英语（只勾「基本键入」「光学字符识别」即可）。
"""
from __future__ import annotations

import asyncio

from PIL import Image

from .geometry import WordBox


class WinOcr:
    name = "系统文字识别"

    def __init__(self):
        from winrt.windows.globalization import Language
        from winrt.windows.media.ocr import OcrEngine

        engine, lang = None, ""
        for tag in ("en-US", "en-GB", "en"):
            try:
                language = Language(tag)
                if OcrEngine.is_language_supported(language):
                    engine = OcrEngine.try_create_from_language(language)
                    lang = tag
                    break
            except Exception:
                continue
        if engine is None:
            engine = OcrEngine.try_create_from_user_profile_languages()
            if engine is not None:
                lang = engine.recognizer_language.language_tag
        if engine is None:
            raise RuntimeError("系统里没有可用的文字识别语言")
        self.engine = engine
        self.language = lang
        self.max_side = int(getattr(OcrEngine, "max_image_dimension", 0) or 2600)
        self._loop = asyncio.new_event_loop()

    @staticmethod
    def _bitmap(img: Image.Image):
        from winrt.windows.graphics.imaging import BitmapAlphaMode, BitmapPixelFormat, SoftwareBitmap
        from winrt.windows.storage.streams import DataWriter

        data = img.convert("RGBA").tobytes("raw", "BGRA")
        writer = DataWriter()
        try:
            writer.write_bytes(data)
        except TypeError:  # 老版本绑定只认列表
            writer.write_bytes(list(data))
        return SoftwareBitmap.create_copy_from_buffer(
            writer.detach_buffer(), BitmapPixelFormat.BGRA8, img.width, img.height,
            BitmapAlphaMode.PREMULTIPLIED)

    async def _recognize(self, bitmap):
        return await self.engine.recognize_async(bitmap)

    def words(self, img: Image.Image) -> list[WordBox]:
        """识别图片里的词，方框坐标相对于图片。"""
        if max(img.size) > self.max_side:
            img = img.crop((0, 0, min(img.width, self.max_side), min(img.height, self.max_side)))
        result = self._loop.run_until_complete(self._recognize(self._bitmap(img)))
        out = []
        for line in result.lines:
            for w in line.words:
                r = w.bounding_rect
                out.append(WordBox(w.text, r.x, r.y, r.x + r.width, r.y + r.height))
        return out
