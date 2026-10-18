# -*- coding: utf-8 -*-
"""暖词 · 取词翻译助手（主程序）

四个能力：
  1. 取词翻译     —— 鼠标下取词，小浮窗显示词典释义
  2. 鼠标自动取词 —— 悬停即查，跟随鼠标的小浮窗
  3. 快捷查词     —— 全局热键弹出查词窗（默认 Ctrl+Alt+D）
  4. 翻译选中内容 —— 全局热键翻译选中文字并列出句中生词（默认 Ctrl+Alt+T）

运行：venv313\\Scripts\\pythonw.exe app.py
"""
from __future__ import annotations

import ctypes

# 必须在导入界面库之前设置，否则会被界面库的默认 DPI 设置抢先（导致鼠标坐标与文字接口坐标错位）
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # 每显示器感知
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

import ctypes.wintypes
import hashlib
import html as html_mod
import json
import os
import re
import sys
import threading
import time
import urllib.parse
from html.parser import HTMLParser
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

import requests  # noqa: E402
import webview  # noqa: E402

from dict_engine import DictEngine, Entry  # noqa: E402
from capture_client import CaptureClient  # noqa: E402
from text_capture import set_clipboard_text, get_clipboard_text, copy_selection  # noqa: E402
import translate as translator  # noqa: E402

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
winmm = ctypes.windll.winmm

WINDOW_TITLES = {
    "float": "取词浮窗",
    "lookup": "暖词 · 快捷查词",
    "translate": "暖词 · 翻译选中内容",
    "vocab": "暖词 · 生词本",
}
VOCAB_PATH = BASE / "生词本.json"


# ---------------- 日志 ----------------
LOG_DIR = BASE / "logs"
LOG_FILE = LOG_DIR / "运行日志.txt"


def log(msg: str) -> None:
    line = f"[{time.strftime('%m-%d %H:%M:%S')}] {msg}"
    try:
        print(line, flush=True)
    except Exception:
        pass
    try:
        LOG_DIR.mkdir(exist_ok=True)
        if LOG_FILE.exists() and LOG_FILE.stat().st_size > 512 * 1024:
            LOG_FILE.replace(LOG_FILE.with_suffix(".old.txt"))
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ---------------- 单实例 ----------------
def single_instance() -> bool:
    kernel32.CreateMutexW(None, False, "WarmWordHelper_SingleInstance")
    return kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS


# ---------------- 配置 ----------------
DEFAULT_CONFIG = {
    "dictionaries": [
        {
            "path": r"C:\词典\oaldpe.mdx",
            "name": "牛津高阶",
        },
        {
            "path": r"C:\词典\麦克米伦高阶英汉双解.mdx",
            "name": "麦克米伦英汉双解",
        },
        {
            "path": r"C:\词典\oaldZhEn.mdx",
            "name": "牛津汉英",
        },
    ],
    "hotkeys": {
        "lookup": "ctrl+alt+d",
        "translate": "ctrl+alt+t",
        "capture": "ctrl+alt+a",
        "toggle_auto": "ctrl+alt+s",
        "add_vocab": "ctrl+shift+a",
        "quit": "ctrl+alt+q",
    },
    "auto_capture": True,
    "capture_only_english": True,
    "hover_delay_ms": 600,
    "online_translate": True,
    "autostart": True,
    "hide_tray_icon": False,
    "accent": "us",
}

CONFIG_PATH = BASE / "config.json"


# ---------------- 生词本 ----------------
def load_vocab() -> list[dict]:
    try:
        data = json.loads(VOCAB_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_vocab(items: list[dict]) -> None:
    try:
        VOCAB_PATH.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as e:
        log(f"[生词本] 保存失败 {e!r}")


def add_vocab(word: str) -> bool:
    word = (word or "").strip()
    if not word:
        return False
    items = load_vocab()
    if any(i.get("word", "").lower() == word.lower() for i in items):
        log(f"[生词本] 已存在 {word}")
        return False
    brief = ""
    if ENGINE is not None:
        e = ENGINE.lookup(word)[:1]
        if e:
            brief = plain_text(e[0].html)[:140].replace("\n", " ")
    items.insert(0, {"word": word, "brief": brief,
                     "added_at": time.strftime("%Y-%m-%d %H:%M")})
    save_vocab(items)
    log(f"[生词本] 已加入 {word}（共 {len(items)} 个）")
    return True


def remove_vocab(word: str) -> bool:
    items = load_vocab()
    left = [i for i in items if i.get("word", "").lower() != (word or "").lower()]
    if len(left) == len(items):
        return False
    save_vocab(left)
    log(f"[生词本] 已移除 {word}（剩 {len(left)} 个）")
    return True


def export_vocab() -> str:
    import csv
    items = load_vocab()
    if not items:
        return ""
    out_dir = BASE / "导出"
    out_dir.mkdir(exist_ok=True)
    path = out_dir / f"生词本_{time.strftime('%Y%m%d_%H%M')}.csv"
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["单词", "释义", "加入时间"])
        for i in items:
            w.writerow([i.get("word", ""), i.get("brief", ""), i.get("added_at", "")])
    log(f"[生词本] 已导出 {path}")
    return str(path)


def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            user_cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            cfg = json.loads(json.dumps(DEFAULT_CONFIG))
            for k, v in user_cfg.items():
                if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                    cfg[k].update(v)
                else:
                    cfg[k] = v
            return cfg
        except Exception as e:
            log(f"[配置] 读取失败，用默认: {e!r}")
    CONFIG_PATH.write_text(
        json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2), encoding="utf-8")
    return json.loads(json.dumps(DEFAULT_CONFIG))


def save_config() -> None:
    try:
        CONFIG_PATH.write_text(
            json.dumps(CONFIG, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        log(f"[配置] 保存失败: {e!r}")


# ---------------- 释义排版规范化 ----------------
class _Cleaner(HTMLParser):
    """把词典原始 HTML 压成干净的富文本：块级转行、保留粗斜体、去掉脚本/图片/样式。"""

    BLOCK = {
        "div", "p", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
        "table", "section", "article", "header", "footer", "blockquote",
        "dt", "dd", "ul", "ol", "center", "html", "body",
    }
    KEEP = {"b": "b", "strong": "b", "i": "i", "em": "i", "u": "u",
            "sub": "sub", "sup": "sup"}
    DROP = {"script", "style", "img", "link", "input", "iframe", "audio",
            "video", "svg", "meta", "head", "table"}  # table 多是导航/排版干扰，整块丢弃
    DROP_SKIP = {"script", "style", "svg", "iframe", "audio", "video", "head", "table"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag, attrs):
        t = tag.lower()
        if t in self.DROP:
            if t in self.DROP_SKIP:
                self.skip_depth += 1
            return
        if self.skip_depth:
            return
        if t == "br":
            self.parts.append("<br>")
        elif t in self.KEEP:
            self.parts.append(f"<{self.KEEP[t]}>")
        elif t in ("span", "font", "small"):
            # 被丢弃的内联标签之间补空格，避免相邻词性标签粘成 "verbnoun"
            if self.parts and not self.parts[-1].endswith(("<br>", " ")):
                self.parts.append(" ")
        elif t in self.BLOCK:
            self.parts.append("<br>")

    def handle_endtag(self, tag):
        t = tag.lower()
        if t in self.DROP:
            if t in self.DROP_SKIP and self.skip_depth:
                self.skip_depth -= 1
            return
        if self.skip_depth:
            return
        if t in self.KEEP:
            self.parts.append(f"</{self.KEEP[t]}>")

    def handle_data(self, data):
        if self.skip_depth:
            return
        data = data.replace("\x00", "")
        data = re.sub(r"[ \t\r\f\v]+", " ", data)
        if data.strip():
            self.parts.append(html_mod.escape(data, quote=False))
        elif data and self.parts:
            last = self.parts[-1]
            if not last.endswith(("<br>", " ", "&nbsp;")) and last.strip():
                self.parts.append(" ")


POS_RE = re.compile(
    r"^(noun|verb|adjective|adverb|preposition|conjunction|pronoun|determiner|"
    r"interjection|numeral|article|auxiliary|abbr\.?|phrasal verb|idiom)\b", re.I)
NUM_RE = re.compile(r"^(\d+[a-z]?|\(\d+\))$")


def enrich_html(text: str) -> str:
    """给纯文本释义加语义标记：中文释义、音标、词性、编号单独着色，提升可读性。"""
    if not text:
        return text
    segs = text.split("<br>")
    out = []
    cn_re = re.compile(r"([\u4e00-\u9fff][\u4e00-\u9fff，。、；：！？（）《》〈〉…—·“”‘’、\s]*)")
    phon_re = re.compile(r"(?<![A-Za-z>])(/[^/<>]{2,42}/|\[[^\[\]<>]{2,30}\])")
    for seg in segs:
        seg = phon_re.sub(r'<span class="phon">\1</span>', seg)
        seg = cn_re.sub(r'<span class="cn">\1</span>', seg)
        # 中文和英文之间补空格，避免「language词典」粘在一起
        seg = re.sub(r'(<span class="cn">[^<]{0,200}</span>)', r' \1 ', seg)
        seg = re.sub(r" {2,}", " ", seg).strip()
        # 行首词性、纯编号做成小标签，层次更分明
        m = POS_RE.match(seg)
        if m:
            seg = f'<span class="pos">{m.group(1)}</span>' + seg[m.end():]
        elif NUM_RE.match(seg):
            seg = f'<span class="num">{NUM_RE.match(seg).group(1)}</span>'
        out.append(seg)
    return "<br>".join(out)


def clean_html(raw: str) -> str:
    try:
        p = _Cleaner()
        p.feed(raw or "")
        text = "".join(p.parts)
    except Exception:
        text = re.sub(r"<[^>]+>", " ", (raw or "").replace("\x00", ""))
    text = re.sub(r"(<br>\s*){2,}", "<br>", text)
    text = re.sub(r"^(<br>\s*)+", "", text)
    text = re.sub(r"(\s*<br>)+$", "", text)
    # 相邻内联标签之间补空格，避免 "verb noun" 粘成 "verbnoun"
    text = re.sub(r"</(b|i|u|sub|sup)>\s*<(b|i|u|sub|sup)>", r"</\1> <\2>", text)
    text = re.sub(r"</(b|i|u|sub|sup)>(?=\S)", r"</\1> ", text)
    return enrich_html(text.strip())


_TAG_RE = re.compile(r"<[^>]+>")


def plain_text(raw: str) -> str:
    t = clean_html(raw).replace("<br>", "\n")
    t = _TAG_RE.sub("", t)
    return re.sub(r"[ \t]+", " ", t).strip()


# ---------------- 热键解析 ----------------
VK_MAP = {chr(ord("a") + i): 0x41 + i for i in range(26)}
VK_MAP.update({str(i): 0x30 + i for i in range(10)})
VK_MAP.update({f"f{i}": 0x70 + i - 1 for i in range(1, 13)})
VK_MAP.update({"space": 0x20, "tab": 0x09, "esc": 0x1B, "escape": 0x1B})

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN = 0x0001, 0x0002, 0x0004, 0x0008
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312


def parse_hotkey(spec: str):
    parts = [p.strip().lower() for p in spec.split("+")]
    mods, vk = 0, None
    for p in parts:
        if p in ("ctrl", "control"):
            mods |= MOD_CONTROL
        elif p in ("alt", "menu"):
            mods |= MOD_ALT
        elif p == "shift":
            mods |= MOD_SHIFT
        elif p in ("win", "super"):
            mods |= MOD_WIN
        elif p in VK_MAP:
            vk = VK_MAP[p]
    if vk is None:
        raise ValueError(f"热键里没有主键: {spec}")
    return mods | MOD_NOREPEAT, vk


# ---------------- 全局状态 ----------------
class AppState:
    def __init__(self):
        self.lock = threading.RLock()
        self.auto_capture = True
        self.float_visible = False
        self.float_pinned = False
        self.float_word = ""
        self.quit = False
        self.hotkey_tid = 0
        self.hotkey_status: dict[str, bool] = {}
        self.pending: tuple[int, int] | None = None   # 待取词坐标（由界面轮询取走）
        self.pending_task: str | None = None          # 待执行任务（translate/capture）
        self.result: dict | None = None               # 后台取词完成的结果
        self.capture_busy = False                     # 是否正在后台取词
        self.float_xy: tuple[int, int] | None = None  # 浮窗对应的取词坐标


STATE = AppState()
ENGINE: DictEngine | None = None
CAPTURE: CaptureClient | None = None
WINDOWS: dict = {}
CONFIG: dict = {}
TRAY_ICON = None


# ---------------- 发音 ----------------
VOICE_CACHE = BASE / "cache"


def _play_mp3(path: Path) -> bool:
    alias = "warmword_voice"
    winmm.mciSendStringW(f"close {alias}", None, 0, None)
    r = winmm.mciSendStringW(f'open "{path}" type mpegvideo alias {alias}', None, 0, None)
    if r != 0:
        log(f"[发音] 打开音频失败 code={r}")
        return False
    winmm.mciSendStringW(f"play {alias}", None, 0, None)
    return True


def speak(word: str, accent: str = "us") -> bool:
    """有道发音（英/美），失败返回 False。"""
    word = (word or "").strip()
    if not word:
        return False
    voice_type = "2" if accent == "us" else "1"
    key = hashlib.md5(f"{word}|{voice_type}".encode("utf-8")).hexdigest()
    VOICE_CACHE.mkdir(exist_ok=True)
    path = VOICE_CACHE / f"{key}.mp3"
    if not path.exists():
        try:
            r = requests.get(
                "https://dict.youdao.com/dictvoice",
                params={"audio": word, "type": voice_type},
                headers=translator.HEADERS, timeout=8)
            if r.status_code != 200 or len(r.content) < 800:
                log(f"[发音] 没拿到音频 {word} code={r.status_code} size={len(r.content)}")
                return False
            path.write_bytes(r.content)
        except Exception as e:
            log(f"[发音] 下载失败 {word}: {e!r}")
            return False
    # 清缓存：最多 300 个文件
    try:
        files = sorted(VOICE_CACHE.glob("*.mp3"), key=lambda p: p.stat().st_mtime)
        for old in files[:-300]:
            old.unlink()
    except Exception:
        pass
    return _play_mp3(path)


# ---------------- 开机自启 ----------------
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
AUTOSTART_NAME = "暖词取词助手"


def autostart_enabled() -> bool:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, AUTOSTART_NAME)
            return True
    except OSError:
        return False


def set_autostart(on: bool) -> bool:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            if on:
                target = f'"{BASE / "venv313" / "Scripts" / "pythonw.exe"}" "{BASE / "app.py"}"'
                winreg.SetValueEx(k, AUTOSTART_NAME, 0, winreg.REG_SZ, target)
            else:
                try:
                    winreg.DeleteValue(k, AUTOSTART_NAME)
                except OSError:
                    pass
        log(f"[自启] {'已开启' if on else '已关闭'}")
        return True
    except Exception as e:
        log(f"[自启] 设置失败: {e!r}")
        return False


def ensure_autostart() -> None:
    """首次运行按配置开启自启；之后以注册表实际状态为准。"""
    if CONFIG.get("autostart", True) and not autostart_enabled():
        set_autostart(True)


# ---------------- 窗口 ----------------
def round_window(win_name: str) -> None:
    """Windows 11 原生圆角（失败忽略）。"""
    try:
        hwnd = user32.FindWindowW(None, WINDOW_TITLES[win_name])
        if hwnd:
            val = ctypes.c_int(2)  # DWMWCP_ROUND
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(val), 4)
    except Exception:
        pass


def push(win_name: str, func: str, data) -> None:
    win = WINDOWS.get(win_name)
    if win is None:
        return
    try:
        win.evaluate_js(f"{func}({json.dumps(data, ensure_ascii=False)})")
    except Exception as e:
        log(f"[推送] {win_name} 失败: {e!r}")


def entry_dicts(entries: list[Entry], limit: int | None = None) -> list[dict]:
    out = []
    for e in entries:
        h = clean_html(e.html)
        if limit and len(h) > limit:
            h = h[:limit] + "…（内容较长，点「详情」看完整释义）"
        out.append({"dict": e.dict_name, "html": h})
    return out


def float_height_limit() -> int:
    """逻辑坐标下的高度上限（界面库用逻辑单位）。"""
    sh = user32.GetSystemMetrics(1)
    return int(sh * 0.35 / SCALE)


def to_dip(v: float) -> int:
    """物理像素 -> 界面逻辑单位。"""
    return int(v / SCALE)


OFFSCREEN = (-4000, -4000)
FLOAT_HWND = {"hwnd": 0}
ENGLISH_RE = re.compile(r"[A-Za-z]")


def float_hwnd() -> int:
    if not FLOAT_HWND["hwnd"]:
        FLOAT_HWND["hwnd"] = user32.FindWindowW(None, WINDOW_TITLES["float"]) or 0
    return FLOAT_HWND["hwnd"]


def mouse_in_float() -> bool:
    """鼠标是否停在浮窗范围内（按窗口矩形判断，比逐点命中更可靠）。"""
    hwnd = float_hwnd()
    if not hwnd:
        return False
    try:
        r = ctypes.wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        if r.right - r.left <= 0:
            return False
        pos = ctypes.wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pos))
        return (r.left - 4 <= pos.x <= r.right + 4 and
                r.top - 4 <= pos.y <= r.bottom + 4)
    except Exception:
        return False


def log_window_state(name: str) -> None:
    """记录窗口真实位置和可见性（便于从日志判断窗口是否真的弹出来了）。"""
    try:
        hwnd = user32.FindWindowW(None, WINDOW_TITLES[name])
        if not hwnd:
            log(f"[{name}] 窗口不存在")
            return
        r = ctypes.wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        vis = bool(user32.IsWindowVisible(hwnd))
        log(f"[{name}] 实际位置 ({r.left},{r.top}) 大小 {r.right-r.left}x{r.bottom-r.top} 可见={vis}")
    except Exception as e:
        log(f"[{name}] 状态读取失败 {e!r}")


def force_redraw(name: str) -> None:
    """强制窗口整块重绘（移入屏幕后清掉边缘残影）。"""
    try:
        hwnd = user32.FindWindowW(None, WINDOW_TITLES[name])
        if not hwnd:
            return
        user32.InvalidateRect(hwnd, None, True)
        user32.RedrawWindow(hwnd, None, None,
                            0x0001 | 0x0004 | 0x0080 | 0x0100)  # INVALIDATE|ERASE|ALLCHILDREN|UPDATENOW
    except Exception:
        pass


def position_window(name: str, x_dip: int, y_dip: int, show: bool = True) -> bool:
    """用系统接口直接定位窗口（界面库的移动在某些状态下不生效）。逻辑单位。"""
    hwnd = user32.FindWindowW(None, WINDOW_TITLES[name])
    if not hwnd:
        win = WINDOWS.get(name)
        if win is None:
            return False
        try:
            win.move(x_dip, y_dip)
            if show:
                win.show()
            return True
        except Exception:
            return False
    SWP_NOSIZE = 0x0001
    SWP_NOZORDER = 0x0004
    SWP_NOACTIVATE = 0x0010
    SWP_SHOWWINDOW = 0x0040
    flags = SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | (SWP_SHOWWINDOW if show else 0)
    user32.SetWindowPos(hwnd, 0, int(x_dip * SCALE), int(y_dip * SCALE), 0, 0, flags)
    return True


def hide_float_window() -> None:
    """把浮窗挪到屏幕外（不能隐藏，隐藏后页面定时器会停）。"""
    position_window("float", OFFSCREEN[0], OFFSCREEN[1], show=True)
    with STATE.lock:
        STATE.float_visible = False


def show_float_near_cursor(word: str, entries: list[Entry],
                           x: int | None = None, y: int | None = None,
                           push_content: bool = True) -> None:
    win = WINDOWS.get("float")
    if win is None:
        return
    if push_content:
        push("float", "renderFloat", {
            "word": word,
            "loading": False,
            "entries": entry_dicts(entries),
            "status": "",
        })
    if x is None or y is None:
        pos = ctypes.wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pos))
        x, y = pos.x, pos.y
    try:
        w, h = win.get_current_size()
    except Exception:
        w, h = 440, 360
    sw = to_dip(user32.GetSystemMetrics(0))
    sh = to_dip(user32.GetSystemMetrics(1))
    px, py = to_dip(x) + 18, to_dip(y) + 22
    if px + w > sw:
        px = max(0, to_dip(x) - w - 12)
    if py + h > sh:
        py = max(0, to_dip(y) - h - 12)
    try:
        win.move(int(px), int(py))
        win.show()
        round_window("float")
    except Exception as e:
        log(f"[浮窗] 显示失败: {e!r}")
    position_window("float", px, py, show=True)
    force_redraw("float")
    with STATE.lock:
        STATE.float_visible = True
        STATE.float_word = word
        STATE.float_xy = (x, y)


FOREGROUND_HOLDER = {"hwnd": None}


def remember_focus() -> None:
    FOREGROUND_HOLDER["hwnd"] = user32.GetForegroundWindow()


def restore_focus() -> None:
    hwnd = FOREGROUND_HOLDER["hwnd"]
    if hwnd:
        try:
            user32.SetForegroundWindow(hwnd)
        except Exception:
            pass


def center_window(win, aspect_y: float = 0.30) -> tuple[int, int]:
    """把窗口放到屏幕中央（逻辑坐标），返回位置。"""
    try:
        w, h = win.get_current_size()
    except Exception:
        w, h = 600, 660
    sw = to_dip(user32.GetSystemMetrics(0))
    sh = to_dip(user32.GetSystemMetrics(1))
    x = max(0, (sw - w) // 2)
    y = max(0, int((sh - h) * aspect_y))
    return x, y


def show_lookup(word: str = "") -> None:
    win = WINDOWS.get("lookup")
    if win is None:
        return
    try:
        x, y = center_window(win, 0.22)
        win.show()                       # 先让它显示出来
        win.evaluate_js("window.focusSearch && window.focusSearch()")
        position_window("lookup", x, y, show=True)   # 再定位（否则会被界面库恢复回旧位置）
        force_redraw("lookup")
        round_window("lookup")
        log(f"[查词窗] 已显示于 ({x},{y})")
    except Exception as e:
        log(f"[查词窗] 显示失败: {e!r}")
    log_window_state("lookup")
    if word and ENGINE is not None:
        push("lookup", "renderLookup",
             {"word": word, "entries": entry_dicts(ENGINE.lookup(word)[:6])})


def show_vocab() -> None:
    win = WINDOWS.get("vocab")
    if win is None:
        return
    push("vocab", "renderVocab", {"items": load_vocab()})
    try:
        x, y = center_window(win, 0.16)
        win.show()
        position_window("vocab", x, y, show=True)
        force_redraw("vocab")
        round_window("vocab")
        log("[生词本] 窗口已打开")
    except Exception as e:
        log(f"[生词本] 显示失败: {e!r}")


def show_translate(text: str) -> None:
    win = WINDOWS.get("translate")
    if win is None:
        return
    push("translate", "renderTranslate",
         {"original": text, "loading": True, "translated": None, "words": []})
    try:
        x, y = center_window(win, 0.25)
        win.show()
        position_window("translate", x, y, show=True)
        force_redraw("translate")
        round_window("translate")
        log(f"[翻译窗] 已显示于 ({x},{y})")
    except Exception as e:
        log(f"[翻译窗] 显示失败: {e!r}")
    log_window_state("translate")

    def worker():
        translated = None
        if CONFIG.get("online_translate", True):
            translated = translator.translate(text)
        items = []
        if ENGINE is not None:
            seen = set()
            for w in re.findall(r"[A-Za-z][A-Za-z']+", text):
                lw = w.lower()
                if len(lw) < 2 or lw in seen:
                    continue
                seen.add(lw)
                entries = ENGINE.lookup(lw)[:1]
                if not entries:
                    continue
                brief = plain_text(entries[0].html)[:60]
                items.append({"word": lw, "brief": brief, "html": ""})
                if len(items) >= 12:
                    break
        push("translate", "renderTranslate", {
            "original": text,
            "loading": False,
            "translated": translated,
            "words": items,
        })

    threading.Thread(target=worker, daemon=True).start()


# ---------------- JS 接口 ----------------
class JsApi:
    def search(self, word: str) -> dict:
        word = (word or "").strip()
        if not word or ENGINE is None:
            return {"word": word, "entries": []}
        return {"word": word, "entries": entry_dicts(ENGINE.lookup(word)[:6])}

    def suggest(self, prefix: str) -> list[str]:
        if ENGINE is None:
            return []
        return ENGINE.suggest((prefix or "").strip(), 8)

    def word_detail(self, word: str) -> str:
        if ENGINE is None:
            return "词典还没加载完"
        entries = ENGINE.lookup((word or "").strip())[:4]
        return "".join(
            f'<div style="margin-bottom:8px"><div class="w-brief">{e.dict_name}</div>'
            f'{clean_html(e.html)}</div>'
            for e in entries
        ) or "没有找到释义"

    def translate_text(self, text: str) -> str | None:
        return translator.translate(text)

    def speak(self, word: str, accent: str = "us") -> bool:
        return speak(word, accent)

    def pin_float(self, pinned: bool) -> bool:
        """钉住浮窗：不再自动收起（之前漏了这个方法，按钮点了没反应）。"""
        with STATE.lock:
            STATE.float_pinned = bool(pinned)
        log(f"[浮窗] {'钉住' if pinned else '取消钉住'}")
        return True

    def resize_float(self, height: int) -> None:
        win = WINDOWS.get("float")
        if win is None:
            return
        h = max(150, min(int(height / SCALE), float_height_limit()))
        try:
            win.resize(440, h)
        except Exception:
            pass

    def close_window(self, which: str) -> None:
        win = WINDOWS.get(which)
        if win is None:
            return
        position_window(which, OFFSCREEN[0], OFFSCREEN[1], show=True)
        if which == "float":
            with STATE.lock:
                STATE.float_visible = False
                STATE.float_pinned = False

    def poll_hover(self):
        """界面每 120 毫秒调一次：在主线程执行取词和待办任务。"""
        self._poll_count = getattr(self, "_poll_count", 0) + 1
        if self._poll_count in (1, 1000) or self._poll_count % 5000 == 0:
            log(f"[轮询] 第 {self._poll_count} 次")
        try:
            return self._poll_hover_inner()
        except Exception as e:
            log(f"[轮询] 异常: {e!r}")
            return None

    def _poll_hover_inner(self):
        # 1. 后台取词有结果就先交付
        with STATE.lock:
            res = STATE.result
            STATE.result = None
            task = STATE.pending_task
            STATE.pending_task = None
            req = STATE.pending
            busy = STATE.capture_busy
            if req is not None and not busy:
                STATE.pending = None
                STATE.capture_busy = True
                start_capture = req
            else:
                start_capture = None
        if res:
            log(f"[取词] 识别到: {res['word']}")
            show_float_near_cursor(res["word"], res["entries"],
                                   res["x"], res["y"], push_content=False)
            return {"word": res["word"], "loading": False, "status": "",
                    "pinned": STATE.float_pinned,
                    "entries": entry_dicts(res["entries"], limit=700)}
        if task:
            threading.Thread(target=self._task_worker, args=(task,), daemon=True).start()
        if start_capture:
            threading.Thread(target=self._capture_worker,
                             args=start_capture, daemon=True).start()
        return None

    def _capture_worker(self, x: int, y: int) -> None:
        """后台线程：屏幕识别取词 + 查词典（不阻塞界面）。"""
        try:
            word = None
            try:
                if CAPTURE is not None:
                    word = CAPTURE.word_at(x, y)
            except Exception as e:
                log(f"[取词] 失败: {e!r}")
            if not word:
                return
            word = word.strip()
            if not word or len(word) > 40 or not re.search(r"[A-Za-z\u4e00-\u9fff]", word):
                return
            # 按欧陆的习惯：取词只认英文词，中文不弹窗（中文查词走快捷查词窗）
            if CONFIG.get("capture_only_english", True) and not ENGLISH_RE.search(word):
                log(f"[取词] 跳过非英文词「{word}」")
                return
            if ENGINE is None:
                return
            with STATE.lock:
                if STATE.float_visible and STATE.float_word == word:
                    return
            entries = ENGINE.lookup(word)[:1]
            if not entries and re.search(r"[A-Za-z]", word):
                # 识别可能有偏差：先试前缀候选，再试拼写最近(编辑距离)的词
                for cand in list(ENGINE.suggest(word[:4], 3)) + ENGINE.fuzzy(word):
                    entries = ENGINE.lookup(cand)[:1]
                    if entries:
                        word = cand
                        log(f"[取词] 按「{cand}」纠正后命中")
                        break
            if not entries:
                return
            with STATE.lock:
                STATE.result = {"word": word, "entries": entries, "x": x, "y": y}
        finally:
            with STATE.lock:
                STATE.capture_busy = False

    def _task_worker(self, task: str) -> None:
        try:
            self._run_task(task)
        except Exception as e:
            log(f"[任务] {task} 失败: {e!r}")

    def _run_task(self, task: str) -> None:
        if task == "translate":
            remember_focus()
            text = None
            try:
                if CAPTURE is not None:
                    text = CAPTURE.selection()
            except Exception as e:
                log(f"[翻译] 读选区失败: {e!r}")
            if not text:  # 选区接口读不到时，模拟一次复制再读剪贴板
                before = get_clipboard_text() or ""
                copy_selection()
                time.sleep(0.18)
                after = get_clipboard_text() or ""
                if after and after != before:
                    text = after
            if text:
                show_translate(text.strip()[:2000])
            else:
                log("[翻译] 没读到选中内容")
        elif task == "capture":
            pos = ctypes.wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(pos))
            word = None
            try:
                if CAPTURE is not None:
                    word = CAPTURE.word_at(pos.x, pos.y)
            except Exception as e:
                log(f"[取词] 手动取词失败: {e!r}")
            if not word or not re.search(r"[A-Za-z\u4e00-\u9fff]", word):
                log("[取词] 鼠标下没有文字")
                return
            remember_focus()
            entries = ENGINE.lookup(word.strip())[:4] if ENGINE else []
            if entries:
                show_float_near_cursor(word.strip(), entries, pos.x, pos.y)
        elif task == "add_vocab":
            word = None
            with STATE.lock:
                if STATE.float_visible and STATE.float_word:
                    word = STATE.float_word
            if not word:
                pos = ctypes.wintypes.POINT()
                user32.GetCursorPos(ctypes.byref(pos))
                try:
                    if CAPTURE is not None:
                        word = CAPTURE.word_at(pos.x, pos.y)
                except Exception as e:
                    log(f"[生词本] 取词失败 {e!r}")
            if word and ENGLISH_RE.search(word):
                add_vocab(word.strip())
            else:
                log("[生词本] 鼠标下没有英文词")

    def open_lookup(self, word: str = "") -> None:
        self.close_window("float")
        show_lookup(word)

    def copy_text(self, text: str) -> None:
        set_clipboard_text(text or "")

    # ---------- 生词本 ----------
    def add_vocab(self, word: str) -> bool:
        return add_vocab(word)

    def remove_vocab(self, word: str) -> bool:
        return remove_vocab(word)

    def list_vocab(self) -> list[dict]:
        return load_vocab()

    def export_vocab(self) -> str:
        return export_vocab()

    def speak_word(self, word: str, accent: str = "us") -> bool:
        return speak(word, accent)


API = JsApi()


# ---------------- 热键 ----------------
def hotkey_thread():
    try:
        STATE.hotkey_tid = kernel32.GetCurrentThreadId()
        reg = {}
        for idx, (action, spec) in enumerate(CONFIG["hotkeys"].items(), start=1):
            mods, vk = parse_hotkey(spec)
            ok = user32.RegisterHotKey(None, idx, mods, vk)
            STATE.hotkey_status[spec] = bool(ok)
            log(f"[热键] {spec} -> {action} {'注册成功' if ok else '注册失败（可能被其他软件占用）'}")
            if ok:
                reg[idx] = action
        MSG = ctypes.wintypes.MSG()
        while not STATE.quit:
            r = user32.GetMessageW(ctypes.byref(MSG), None, 0, 0)
            if r in (0, -1):
                break
            if MSG.message == WM_HOTKEY and MSG.wParam in reg:
                on_hotkey(reg[MSG.wParam])
    except Exception as e:
        log(f"[热键线程异常] {e!r}")


def on_hotkey(action: str):
    log(f"[热键触发] {action}")
    if action == "lookup":
        show_lookup()
    elif action == "translate":
        with STATE.lock:
            STATE.pending_task = "translate"
    elif action == "capture":
        with STATE.lock:
            STATE.pending_task = "capture"
    elif action == "add_vocab":
        with STATE.lock:
            STATE.pending_task = "add_vocab"
    elif action == "quit":
        log("[退出] 收到退出快捷键")
        STATE.quit = True
        os._exit(0)
    elif action == "toggle_auto":
        toggle_auto()


def do_capture_now():
    with STATE.lock:
        STATE.pending_task = "capture"


def _lookup_and_show(word: str):
    entries = ENGINE.lookup(word)[:4] if ENGINE else []
    if entries:
        show_float_near_cursor(word, entries)


HOST_HWND = {"hwnd": 0}


def host_window() -> int:
    """创建一个从不显示的宿主窗口，界面窗口挂在它下面就不会出现在任务栏。"""
    if HOST_HWND["hwnd"]:
        return HOST_HWND["hwnd"]
    try:
        # 句柄是 64 位，必须声明类型，否则会被截断导致创建失败
        kernel32.GetModuleHandleW.restype = ctypes.c_void_p
        kernel32.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
        user32.CreateWindowExW.restype = ctypes.c_void_p
        user32.CreateWindowExW.argtypes = [
            ctypes.c_ulong, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_ulong,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
        ]
        hinst = kernel32.GetModuleHandleW(None)
        hwnd = user32.CreateWindowExW(0, "STATIC", "WarmWordHost", 0,
                                      0, 0, 0, 0, None, None, hinst, None)
        HOST_HWND["hwnd"] = int(hwnd or 0)
        log(f"[任务栏] 宿主窗口 hwnd={HOST_HWND['hwnd']}")
    except Exception as e:
        log(f"[任务栏] 宿主窗口创建失败 {e!r}")
        HOST_HWND["hwnd"] = 0
    return HOST_HWND["hwnd"]


def detach_from_taskbar(name: str) -> bool:
    """把窗口从任务栏彻底摘掉：挂在隐藏宿主下 + 工具窗口样式 + 强制任务栏刷新。"""
    hwnd = user32.FindWindowW(None, WINDOW_TITLES[name])
    if not hwnd:
        return False
    try:
        user32.GetWindowLongPtrW.restype = ctypes.c_void_p
        user32.GetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int]
        user32.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
        host = host_window()
        if host:
            user32.SetWindowLongPtrW(hwnd, -8, ctypes.c_void_p(host))  # GWLP_HWNDPARENT
        ex = int(user32.GetWindowLongPtrW(hwnd, -20) or 0)
        # 关键：清掉「应用窗口」标志（WinForms 默认会加，它优先于工具窗口，导致任务栏照显示）
        ex = (ex | 0x00000080 | 0x08000000) & ~0x00040000
        user32.SetWindowLongPtrW(hwnd, -20, ctypes.c_void_p(ex))
        return True
    except Exception as e:
        log(f"[任务栏] {name} 脱钩失败 {e!r}")
        return False


def make_tool_window(name: str) -> bool:
    """让窗口成为「工具窗口」：不出现在任务栏、不进 Alt+Tab、不抢焦点。"""
    hwnd = user32.FindWindowW(None, WINDOW_TITLES[name])
    if not hwnd:
        return False
    try:
        GWL_EXSTYLE = -20
        WS_EX_TOOLWINDOW = 0x00000080
        WS_EX_NOACTIVATE = 0x08000000
        user32.GetWindowLongPtrW.restype = ctypes.c_void_p
        user32.GetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int]
        user32.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
        ex = int(user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE) or 0)
        if not (ex & WS_EX_TOOLWINDOW) or (ex & 0x00040000):
            ex = (ex | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE) & ~0x00040000
            user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, ctypes.c_void_p(ex))
        # 通知系统刷新窗口框架，任务栏立刻撤掉图标
        user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0,
                            0x0001 | 0x0002 | 0x0004 | 0x0020)  # NOSIZE|NOMOVE|NOZORDER|FRAMECHANGED
        return True
    except Exception as e:
        log(f"[窗口] 设置工具窗口失败 {name}: {e!r}")
        return False


def init_windows():
    """窗口初始化：先隐藏状态下挂好「不进任务栏」的样式，再显示到屏幕外待命。"""
    FLOAT_HWND["hwnd"] = 0
    names = ("float", "lookup", "translate", "vocab")
    for name in names:
        if WINDOWS.get(name) is None:
            continue
        try:
            make_tool_window(name)
            detach_from_taskbar(name)
        except Exception as e:
            log(f"[窗口] {name} 样式设置失败: {e!r}")
    for name in names:
        if WINDOWS.get(name) is None:
            continue
        try:
            position_window(name, OFFSCREEN[0], OFFSCREEN[1], show=True)
            round_window(name)
        except Exception as e:
            log(f"[窗口] {name} 就位失败: {e!r}")
    log("[窗口] 四个窗口就位（屏幕外待命，已从任务栏摘除）")


def toggle_auto():
    with STATE.lock:
        STATE.auto_capture = not STATE.auto_capture
    log(f"[自动取词] {'开启' if STATE.auto_capture else '关闭'}")
    try:
        if TRAY_ICON is not None:
            TRAY_ICON.update_menu()
    except Exception:
        pass


# ---------------- 自动取词 ----------------
def own_window_at(x: int, y: int) -> bool:
    try:
        hwnd = user32.WindowFromPoint(ctypes.wintypes.POINT(x, y))
        if not hwnd:
            return False
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value == os.getpid()
    except Exception:
        return False


def hover_thread():
    last_pos = None
    idle_since = time.time()
    hide_stamp = 0.0
    while not STATE.quit:
        time.sleep(0.12)
        pos = ctypes.wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pos))
        cur = (pos.x, pos.y)

        with STATE.lock:
            visible = STATE.float_visible
            pinned = STATE.float_pinned
            word_xy = STATE.float_xy
        if visible and not pinned:
            near_word = bool(
                word_xy and abs(pos.x - word_xy[0]) <= 110 and abs(pos.y - word_xy[1]) <= 80)
            if mouse_in_float() or near_word:
                hide_stamp = 0.0  # 鼠标在浮窗上或还停在取的那个词上：不收起
            else:
                if hide_stamp == 0.0:
                    hide_stamp = time.time()
                elif time.time() - hide_stamp > 3.5:
                    API.close_window("float")
                    hide_stamp = 0.0

        with STATE.lock:
            if not STATE.auto_capture:
                last_pos = cur
                continue

        if cur != last_pos:
            last_pos = cur
            idle_since = time.time()
            continue
        if time.time() - idle_since < CONFIG.get("hover_delay_ms", 600) / 1000.0:
            continue
        idle_since = time.time() + 2.0

        if own_window_at(pos.x, pos.y):
            continue
        if ENGINE is None:
            continue
        # 只登记坐标，真正的取词由界面定时回调在主线程执行
        with STATE.lock:
            STATE.pending = (pos.x, pos.y)
            STATE.float_word = ""


# ---------------- 自检 ----------------
def self_check() -> str:
    lines = ["暖词自检报告", f"时间: {time.strftime('%Y-%m-%d %H:%M:%S')}", ""]
    if ENGINE is None:
        lines.append("词典: 还没加载完")
    else:
        total = sum(len(b) for b in ENGINE.books)
        lines.append(f"词典: {len(ENGINE.books)} 本，共 {total:,} 条")
        for b in ENGINE.books:
            lines.append(f"  · {b.name}: {len(b):,} 条")
        t0 = time.time()
        hit = ENGINE.lookup("test")
        lines.append(f"查询测试: test -> {len(hit)} 条，{(time.time()-t0)*1000:.0f} 毫秒")
    lines.append("")
    if STATE.hotkey_status:
        for spec, ok in STATE.hotkey_status.items():
            lines.append(f"热键 {spec}: {'正常' if ok else '注册失败（被占用）'}")
    else:
        lines.append("热键: 还没注册")
    lines.append("")
    lines.append(f"自动取词: {'开' if STATE.auto_capture else '关'}")
    lines.append(f"开机自启: {'开' if autostart_enabled() else '关'}")
    t0 = time.time()
    ok_voice = speak("test", "us")
    lines.append(f"发音: {'正常' if ok_voice else '不可用'} ({(time.time()-t0):.1f} 秒)")
    t0 = time.time()
    tr = translator.translate("This is a self check.")
    lines.append(f"联网翻译: {'正常' if tr else '不可用'} ({(time.time()-t0):.1f} 秒)")
    lines.append("")
    lines.append(f"日志文件: {LOG_FILE}")
    report = "\n".join(lines)
    try:
        (BASE / "自检报告.txt").write_text(report, encoding="utf-8")
    except Exception:
        pass
    log("[自检]\n" + report)
    return report


# ---------------- 触发文件 ----------------
def trigger_file_thread():
    path = BASE / "触发.txt"
    while not STATE.quit:
        time.sleep(0.3)
        if not path.exists():
            continue
        try:
            raw = path.read_text(encoding="utf-8").strip()
            path.unlink()
        except Exception:
            continue
        action, _, rest = raw.partition("\n")
        action, rest = action.strip(), rest.strip()
        if action == "translate" and rest:
            show_translate(rest[:2000])
        elif action == "dump":
            out = []
            for name, win in WINDOWS.items():
                try:
                    val = win.evaluate_js(
                        "JSON.stringify({text: document.body.innerText.slice(0, 5000),"
                        " html: (document.getElementById('inner')||document.body).innerHTML.slice(0, 6000),"
                        " h: Math.round(document.body.getBoundingClientRect().height),"
                        " ticks: (window.__tickCount||-1), polls: (window.__pollCount||-1)})")
                    out.append(f"=== {name} ===\n{val}")
                except Exception as e:
                    out.append(f"=== {name} === <{e!r}>")
            (BASE / "测试_窗口内容.txt").write_text("\n".join(out), encoding="utf-8")
        elif action == "selfcheck":
            self_check()
        elif action == "speak":
            speak(rest or "hello", CONFIG.get("accent", "us"))
        elif action == "pin":
            API.pin_float(rest != "off")
        elif action == "closefloat":
            API.close_window("float")
        elif action == "vocab":
            show_vocab()
        elif action == "addvocab":
            if rest:
                add_vocab(rest)
            else:
                with STATE.lock:
                    STATE.pending_task = "add_vocab"
        elif action.startswith("probe"):
            parts = action.split()
            out = {"raw": raw}
            if len(parts) >= 3:
                try:
                    x, y = int(parts[1]), int(parts[2])
                    out["result"] = CAPTURE.probe_at(x, y) if CAPTURE else "无取词服务"
                except Exception as e:
                    out["error"] = repr(e)
            (BASE / "探测结果.txt").write_text(
                json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        elif action in ("lookup", "translate", "capture", "toggle_auto"):
            on_hotkey(action)


# ---------------- 托盘 ----------------
def make_tray_icon():
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([2, 2, 62, 62], radius=16, fill=(250, 246, 238, 255),
                        outline=(176, 106, 63, 255), width=3)
    font = None
    for fp in [r"C:\Windows\Fonts\msyhbd.ttc", r"C:\Windows\Fonts\msyh.ttc",
               r"C:\Windows\Fonts\simhei.ttf"]:
        if os.path.exists(fp):
            try:
                font = ImageFont.truetype(fp, 34)
                break
            except Exception:
                continue
    if font is not None:
        d.text((32, 30), "词", font=font, fill=(176, 106, 63, 255), anchor="mm")
    return img


def tray_thread():
    global TRAY_ICON
    import pystray
    from pystray import MenuItem as Item

    # 允许完全隐藏托盘图标（此时用 Ctrl+Alt+Q 退出程序）
    if CONFIG.get("hide_tray_icon"):
        log("[托盘] 配置为隐藏托盘图标；退出请按 Ctrl+Alt+Q")
        return

    def on_lookup(icon, item):
        show_lookup()

    def on_translate(icon, item):
        with STATE.lock:
            STATE.pending_task = "translate"

    def on_capture(icon, item):
        with STATE.lock:
            STATE.pending_task = "capture"

    def on_vocab(icon, item):
        show_vocab()

    def on_hide_tray(icon, item):
        CONFIG["hide_tray_icon"] = True
        save_config()
        log("[托盘] 已隐藏托盘图标（退出按 Ctrl+Alt+Q，想恢复把 config.json 里 hide_tray_icon 改 false）")
        try:
            icon.stop()
        except Exception:
            pass

    def on_toggle(icon, item):
        toggle_auto()

    def on_autostart(icon, item):
        set_autostart(not autostart_enabled())
        try:
            icon.update_menu()
        except Exception:
            pass

    def on_selfcheck(icon, item):
        threading.Thread(target=self_check, daemon=True).start()

    def on_quit(icon, item):
        STATE.quit = True
        try:
            icon.stop()
        except Exception:
            pass
        os._exit(0)

    menu = pystray.Menu(
        Item("快捷查词 (Ctrl+Alt+D)", on_lookup, default=True),
        Item("翻译选中内容 (Ctrl+Alt+T)", on_translate),
        Item("鼠标处取词 (Ctrl+Alt+A)", on_capture),
        pystray.Menu.SEPARATOR,
        Item("生词本 (收藏用 Ctrl+Shift+A)", on_vocab),
        pystray.Menu.SEPARATOR,
        Item("鼠标自动取词 (Ctrl+Alt+S)", on_toggle, checked=lambda item: STATE.auto_capture),
        Item("开机自启", on_autostart, checked=lambda item: autostart_enabled()),
        pystray.Menu.SEPARATOR,
        Item("自检（生成报告）", on_selfcheck),
        Item("隐藏托盘图标（退出用 Ctrl+Alt+Q）", on_hide_tray),
        Item("退出", on_quit),
    )
    TRAY_ICON = pystray.Icon("warmword", make_tray_icon(), "暖词 · 取词助手", menu)
    TRAY_ICON.run()


# ---------------- 启动 ----------------
def engine_thread():
    global ENGINE
    books = [(d["path"], d.get("name")) for d in CONFIG["dictionaries"]]
    ENGINE = DictEngine(books)
    names = "、".join(f"{b.name}（{len(b):,} 条）" for b in ENGINE.books)
    log(f"[词典就绪] {names}")


def main():
    global CONFIG, CAPTURE, SCALE, FLOAT_WIDTH
    # 缩放比：按系统 DPI（96 为一倍）
    try:
        hdc = user32.GetDC(None)
        dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)  # LOGPIXELSX
        user32.ReleaseDC(None, hdc)
        SCALE = round(dpi / 96.0, 2)
    except Exception:
        SCALE = 1.0
    SCALE = max(1.0, SCALE)
    FLOAT_WIDTH = int(440 * SCALE)
    _aware = ctypes.c_int()
    try:
        ctypes.windll.shcore.GetProcessDpiAwareness(None, ctypes.byref(_aware))
    except Exception:
        pass
    log(f"[显示] 屏幕 {user32.GetSystemMetrics(0)}x{user32.GetSystemMetrics(1)}，"
        f"缩放 {SCALE}，DPI 感知级别 {_aware.value}")

    CONFIG = load_config()
    STATE.auto_capture = bool(CONFIG.get("auto_capture", True))

    if not single_instance():
        log("[启动] 已有实例在跑，唤出查词窗后退出")
        try:
            (BASE / "触发.txt").write_text("lookup", encoding="utf-8")
        except Exception:
            pass
        return

    log("=" * 40)
    log("[启动] 暖词取词助手")

    threading.Thread(target=engine_thread, daemon=True).start()
    threading.Thread(target=hotkey_thread, daemon=True).start()
    threading.Thread(target=hover_thread, daemon=True).start()
    threading.Thread(target=trigger_file_thread, daemon=True).start()
    threading.Thread(target=tray_thread, daemon=True).start()

    ensure_autostart()
    CAPTURE = CaptureClient()  # 取词交给独立小进程（文字接口只能在进程主线程用）

    float_html = (BASE / "ui" / "float.html").read_text(encoding="utf-8")
    lookup_html = (BASE / "ui" / "lookup.html").read_text(encoding="utf-8")
    translate_html = (BASE / "ui" / "translate.html").read_text(encoding="utf-8")

    vocab_html = (BASE / "ui" / "vocab.html").read_text(encoding="utf-8")

    WINDOWS["float"] = webview.create_window(
        WINDOW_TITLES["float"], html=float_html, js_api=API,
        width=440, height=360, frameless=True, easy_drag=True,
        on_top=True, hidden=True)
    WINDOWS["lookup"] = webview.create_window(
        WINDOW_TITLES["lookup"], html=lookup_html, js_api=API,
        width=600, height=660, on_top=True, hidden=True)
    WINDOWS["translate"] = webview.create_window(
        WINDOW_TITLES["translate"], html=translate_html, js_api=API,
        width=620, height=560, on_top=True, hidden=True)
    WINDOWS["vocab"] = webview.create_window(
        WINDOW_TITLES["vocab"], html=vocab_html, js_api=API,
        width=760, height=780, on_top=True, hidden=True)

    threading.Timer(1.0, init_windows).start()
    # 窗口每次显示时再兜底一次，避免任务栏出现图标
    for _name, _win in WINDOWS.items():
        def _on_shown(n=_name):
            make_tool_window(n)
            detach_from_taskbar(n)
        try:
            _win.events.shown += _on_shown
        except Exception:
            pass
    # 关闭按钮 = 收起窗口（挪到屏幕外），程序继续在托盘运行
    def _close_to_tray(name):
        def handler():
            try:
                position_window(name, OFFSCREEN[0], OFFSCREEN[1], show=True)
                if name == "float":
                    with STATE.lock:
                        STATE.float_visible = False
                        STATE.float_pinned = False
                log(f"[窗口] 关闭 {name} → 收起（程序仍在托盘）")
            except Exception as e:
                log(f"[窗口] 收起 {name} 失败 {e!r}")
            return False  # 不让界面库真正销毁窗口
        return handler

    for _name, _win in WINDOWS.items():
        try:
            _win.events.closing += _close_to_tray(_name)
        except Exception:
            pass
    webview.start()
    # 界面线程退出后仍保持托盘运行，直到用户从托盘退出
    while not STATE.quit:
        time.sleep(0.5)


if __name__ == "__main__":
    main()
