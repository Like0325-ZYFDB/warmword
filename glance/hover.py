# -*- coding: utf-8 -*-
"""鼠标取词的节奏控制（纯逻辑，不碰系统接口，可单独测试）。

每隔几十毫秒喂一次鼠标状态，返回要做的事：
  ("capture", x, y)  在这个位置取词
  ("hide",)          收起浮窗

规则（参考欧路词典）：
- 按住取词键（默认 Shift）模式：一按下立刻取词；按住移动时，鼠标停 60 毫秒就取新词
- 悬停模式：鼠标停住 hover_delay 毫秒后取词
- 鼠标还在当前词上：不重复取词，浮窗不闪
- 浮窗显示时，鼠标在词上、浮窗上、或两者之间的通道里都不收起；离开后 0.45 秒收起
- 按着鼠标左键（在拖动、选文字）时不取词
"""
from __future__ import annotations

from dataclasses import dataclass, field

MODIFIER_SETTLE = 0.06   # 按住取词键时，鼠标停多久取新词
LEAVE_GRACE = 0.45       # 离开词和浮窗多久后收起
JITTER = 3               # 小于这个像素的抖动算没动


@dataclass
class Inputs:
    now: float
    pos: tuple[int, int]
    modifier: bool = False        # 取词键单独按着
    lbutton: bool = False         # 鼠标左键按着
    in_own_window: bool = False   # 鼠标在我们自己的窗口上
    esc: bool = False


@dataclass
class HoverController:
    mode: str = "modifier"        # modifier / hover / off
    hover_delay: float = 0.45
    # 浮窗状态（由主程序在显示 / 收起后回填）
    visible: bool = False
    pinned: bool = False
    word_box: tuple[int, int, int, int] | None = None
    card_rect: tuple[int, int, int, int] | None = None

    _last_pos: tuple[int, int] | None = None
    _still_since: float = 0.0
    _fired_at: tuple[int, int] | None = None
    _mod_was_down: bool = False
    _leave_since: float | None = None
    _pending: list = field(default_factory=list)

    def shown(self, word_box, card_rect) -> None:
        self.visible = True
        self.word_box = tuple(word_box) if word_box else None
        self.card_rect = tuple(card_rect) if card_rect else None
        self._leave_since = None

    def hidden(self) -> None:
        self.visible = False
        self.pinned = False
        self.word_box = None
        self.card_rect = None
        self._leave_since = None

    # ---------- 判断 ----------
    @staticmethod
    def _inside(rect, pos, pad=0) -> bool:
        if not rect:
            return False
        l, t, r, b = rect
        return l - pad <= pos[0] <= r + pad and t - pad <= pos[1] <= b + pad

    def _in_bridge(self, pos) -> bool:
        """词和浮窗之间的通道：从词移到浮窗上的途中不收起。"""
        if not (self.word_box and self.card_rect):
            return False
        wl, wt, wr, wb = self.word_box
        cl, ct, cr, cb = self.card_rect
        left, right = min(wl, cl), max(wr, cr)
        top, bottom = (wb, ct) if ct >= wb else (cb, wt)
        return left <= pos[0] <= right and top - 2 <= pos[1] <= bottom + 2

    def keeps_open(self, pos) -> bool:
        return (self._inside(self.card_rect, pos, 6) or self._inside(self.word_box, pos, 4)
                or self._in_bridge(pos))

    # ---------- 主循环 ----------
    def tick(self, i: Inputs) -> list[tuple]:
        out: list[tuple] = []
        pos = i.pos
        moved = self._last_pos is None or (abs(pos[0] - self._last_pos[0]) > JITTER or
                                           abs(pos[1] - self._last_pos[1]) > JITTER)
        if moved:
            self._last_pos = pos
            self._still_since = i.now

        # 1. 收起
        if self.visible and not self.pinned:
            if i.esc:
                out.append(("hide",))
            elif self.keeps_open(pos) or i.in_own_window:
                self._leave_since = None
            elif self._leave_since is None:
                self._leave_since = i.now
            elif i.now - self._leave_since >= LEAVE_GRACE:
                out.append(("hide",))
                self._leave_since = None
        elif self.visible and self.pinned and i.esc:
            out.append(("hide",))

        # 2. 取词
        mod_pressed = i.modifier and not self._mod_was_down
        self._mod_was_down = i.modifier
        if self.mode == "off" or i.lbutton or i.in_own_window:
            return out
        if self.visible and self._inside(self.word_box, pos, 1):
            return out  # 还在当前词上
        if self._inside(self.card_rect, pos, 2):
            return out

        fire = False
        if self.mode == "modifier":
            if mod_pressed:
                fire = True
            elif (i.modifier and i.now - self._still_since >= MODIFIER_SETTLE
                  and self._fired_at != self._last_pos):
                fire = True
        elif self.mode == "hover":
            if i.now - self._still_since >= self.hover_delay and self._fired_at != self._last_pos:
                fire = True
        if fire:
            self._fired_at = self._last_pos
            out.append(("capture", pos[0], pos[1]))
        return out
