# -*- coding: utf-8 -*-
"""暖词 · 取词翻译助手（主程序）

四个能力：
  1. 取词翻译     —— 鼠标下取词，小浮窗显示词典释义
  2. 鼠标自动取词 —— 悬停即查，跟随鼠标的小浮窗
  3. 快捷查词     —— 全局热键弹出查词窗（默认 Ctrl+Alt+D）
  4. 翻译选中内容 —— 全局热键翻译选中文字并列出句中生词（默认 Ctrl+Alt+T）

运行：venv\\Scripts\\pythonw.exe app.py（或双击 启动.bat）
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

import sys  # noqa: E402

if "--capture-service" in sys.argv:  # 打包版：同一个 exe 兼任取词服务子进程
    import capture_service
    capture_service.main()
    raise SystemExit(0)

import ctypes.wintypes
import hashlib
import html as html_mod
import json
import os
import re
import threading
import time
import urllib.parse
from html.parser import HTMLParser
from pathlib import Path

from paths import BASE, DATA, FROZEN, PORTABLE, RES, migrate_old_data  # noqa: E402

sys.path.insert(0, str(BASE))

import requests  # noqa: E402
import webview  # noqa: E402

from dict_engine import DictEngine, Entry  # noqa: E402
from dict_render import clean_ref, rewrite_html  # noqa: E402
from capture_client import CaptureClient  # noqa: E402
from text_capture import set_clipboard_text, copy_selection_text  # noqa: E402
import translate as translator  # noqa: E402
from wordlist import pick_new_words  # noqa: E402
from tts import SapiSpeaker  # noqa: E402

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
winmm = ctypes.windll.winmm

WINDOW_TITLES = {
    "float": "取词浮窗",
    "lookup": "暖词 · 快捷查词",
    "translate": "暖词 · 翻译选中内容",
    "vocab": "暖词 · 生词本",
    "settings": "暖词 · 设置",
}

HOTKEY_LABELS = {
    "lookup": "快捷查词",
    "translate": "翻译选中内容",
    "capture": "立即取词",
    "toggle_auto": "自动取词开关",
    "add_vocab": "收藏生词",
    "quit": "退出程序",
}
VOCAB_PATH = DATA / "生词本.json"
HISTORY_PATH = DATA / "查词历史.json"
DICT_DIR = DATA / "词库"            # 下载的 ECDICT 放这里
INDEX_DIR = DATA / "cache" / "index"  # ECDICT csv 转出来的 sqlite 索引
ECDICT_URL = "https://raw.githubusercontent.com/skywind3000/ECDICT/master/ecdict.csv"


# ---------------- 日志 ----------------
LOG_DIR = DATA / "logs"
LOG_FILE = LOG_DIR / "运行日志.txt"


def log(msg: str) -> None:
    line = f"[{time.strftime('%m-%d %H:%M:%S')}] {msg}"
    try:
        print(line, flush=True)
    except Exception:
        pass
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
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


QUIT_EVENT = "WarmWordHelper_QuitEvent_v1"
SHOW_EVENT = "WarmWordHelper_ShowLookup_v1"


def signal_event(name: str) -> bool:
    try:
        kernel32.OpenEventW.restype = ctypes.c_void_p
        h = kernel32.OpenEventW(0x0002, False, name)  # EVENT_MODIFY_STATE
        if not h:
            return False
        kernel32.SetEvent(ctypes.c_void_p(h))
        kernel32.CloseHandle(ctypes.c_void_p(h))
        return True
    except Exception:
        return False


def ask_old_instance_quit() -> bool:
    """通知正在跑的旧实例退出（用于托盘「重启程序」）。"""
    return signal_event(QUIT_EVENT)


def show_watcher() -> None:
    """再次双击启动时，新实例发信号过来：唤出查词窗。"""
    try:
        kernel32.CreateEventW.restype = ctypes.c_void_p
        h = kernel32.CreateEventW(None, False, False, SHOW_EVENT)
        while not STATE.quit:
            if kernel32.WaitForSingleObject(ctypes.c_void_p(h), 1000) == 0:
                log("[启动] 收到再次启动的信号，唤出查词窗")
                show_lookup()
    except Exception as e:
        log(f"[启动] 唤出信号监听失败 {e!r}")


def quit_watcher() -> None:
    """旧实例监听退出信号：收到就自己退出，让新实例接管。"""
    try:
        kernel32.CreateEventW.restype = ctypes.c_void_p
        h = kernel32.CreateEventW(None, False, False, QUIT_EVENT)
        while not STATE.quit:
            r = kernel32.WaitForSingleObject(ctypes.c_void_p(h), 1000)
            if r == 0:
                log("[重启] 收到重启通知，旧实例退出")
                os._exit(0)
    except Exception as e:
        log(f"[重启] 监听失败 {e!r}")


# ---------------- 配置 ----------------
DEFAULT_CONFIG = {
    "dictionaries": [],
    # 避开常见冲突：Ctrl+Alt+A 是 QQ 截图，Ctrl+Shift+A 是钉钉截图 / Chrome 搜索标签页
    "hotkeys": {
        "lookup": "ctrl+alt+d",
        "translate": "ctrl+alt+t",
        "capture": "ctrl+alt+e",
        "toggle_auto": "ctrl+alt+s",
        "add_vocab": "ctrl+alt+b",
        "quit": "ctrl+alt+q",
    },
    "auto_capture": True,
    "capture_modifier": "",          # ""=停住就取词；"ctrl"/"shift"/"alt"=按住该键才取词
    "capture_only_english": True,
    "hover_delay_ms": 600,
    "online_translate": True,
    "baidu_appid": "",
    "baidu_key": "",
    "autostart": True,
    "hide_tray_icon": False,
    "accent": "us",
    "render_mode": "rich",           # rich=词典原版排版（带样式、图片、发音）；simple=简洁文字
    "font_scale": 1.0,               # 释义字号倍数
    "auto_speak": False,             # 取词弹窗时自动读出单词
    "debug": False,
}
CAPTURE_MODIFIERS = {"ctrl": 0x11, "shift": 0x10, "alt": 0x12}

CONFIG_PATH = DATA / "config.json"


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


def vocab_words() -> set[str]:
    return {i.get("word", "").lower() for i in load_vocab()}


def brief_of(entry: Entry, limit: int = 140) -> str:
    """简短释义：去掉开头重复的词头，换行压成空格。"""
    text = plain_text(entry.html).replace("\n", " ")
    head = (entry.headword or "").strip()
    if head and text.lower().startswith(head.lower()):
        text = text[len(head):].lstrip(" ·:：")
    return re.sub(r"\s{2,}", " ", text)[:limit]


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
            brief = brief_of(e[0])
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


def load_history() -> list[str]:
    try:
        data = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
        return [w for w in data if isinstance(w, str)] if isinstance(data, list) else []
    except Exception:
        return []


def add_history(word: str) -> None:
    """查词历史：最近的在前，去重，最多 60 个。"""
    word = (word or "").strip()
    if not word or len(word) > 60:
        return
    items = [w for w in load_history() if w.lower() != word.lower()]
    items.insert(0, word)
    try:
        HISTORY_PATH.write_text(json.dumps(items[:60], ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        log(f"[历史] 保存失败 {e!r}")


EXPORT_DIR = DATA / "导出"


def export_vocab() -> str:
    import csv
    items = load_vocab()
    if not items:
        return ""
    out_dir = EXPORT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"生词本_{time.strftime('%Y%m%d_%H%M')}.csv"
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["单词", "释义", "加入时间"])
        for i in items:
            w.writerow([i.get("word", ""), i.get("brief", ""), i.get("added_at", "")])
    log(f"[生词本] 已导出 {path}")
    return str(path)


def export_anki() -> str:
    """导出成 Anki 可直接导入的文本：正面单词，背面释义（HTML），制表符分隔。"""
    items = load_vocab()
    if not items:
        return ""
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = EXPORT_DIR / f"生词本_Anki_{time.strftime('%Y%m%d_%H%M')}.txt"
    lines = ["#separator:tab", "#html:true", "#tags column:3"]
    for i in items:
        word = i.get("word", "")
        back = html_mod.escape(i.get("brief", ""))
        if ENGINE is not None:
            e = ENGINE.lookup(word)[:1]
            if e:
                back = clean_html(e[0].html)[:3000]
        back = back.replace("\t", " ").replace("\n", "<br>")
        lines.append(f"{word}\t{back}\t暖词")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log(f"[生词本] 已导出 Anki 文件 {path}")
    return str(path)


def load_config() -> dict:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if CONFIG_PATH.exists():
        try:
            user_cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            for k, v in user_cfg.items():
                if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                    cfg[k].update(v)
                else:
                    cfg[k] = v
            return cfg
        except Exception as e:
            # 不要直接覆盖：先把坏掉的配置留个备份，方便找回
            backup = CONFIG_PATH.with_name("config.broken.json")
            log(f"[配置] 读取失败，改用默认，原文件备份为 {backup.name}: {e!r}")
            try:
                CONFIG_PATH.replace(backup)
            except Exception:
                pass
    try:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        log(f"[配置] 写入默认配置失败: {e!r}")
    return cfg


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
        # 整句英文例句（大写开头、句号结尾、至少 4 个词）：标出来，页面会在后面加朗读按钮
        eng = re.split(r"[\u4e00-\u9fff]", _TAG_RE.sub("", seg), maxsplit=1)[0].strip()
        if (re.match(r"^[A-Z\"'‘“]", eng) and len(re.findall(r"[A-Za-z]{2,}", eng)) >= 4
                and re.search(r"[.?!][\"'’”]?$", eng)):
            seg = f'<span class="ex">{seg}</span>'
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


# ---------------- 提示 ----------------
_LAST_NOTES: dict[str, float] = {}


def notify(msg: str, key: str | None = None, interval: float = 6.0) -> None:
    """托盘气泡提示（同一条提示 interval 秒内只弹一次）。失败的操作一定要让用户看得到。"""
    log(f"[提示] {msg}")
    k = key or msg
    now = time.time()
    if now - _LAST_NOTES.get(k, 0) < interval:
        return
    _LAST_NOTES[k] = now
    try:
        if TRAY_ICON is not None and getattr(TRAY_ICON, "HAS_NOTIFICATION", False):
            TRAY_ICON.notify(msg, "暖词")
    except Exception as e:
        log(f"[提示] 气泡失败 {e!r}")


# ---------------- 热键解析 ----------------
VK_MAP = {chr(ord("a") + i): 0x41 + i for i in range(26)}
VK_MAP.update({str(i): 0x30 + i for i in range(10)})
VK_MAP.update({f"f{i}": 0x70 + i - 1 for i in range(1, 13)})
VK_MAP.update({"space": 0x20, "tab": 0x09, "esc": 0x1B, "escape": 0x1B})

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN = 0x0001, 0x0002, 0x0004, 0x0008
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312
WM_APP_RELOAD_HOTKEYS = 0x8001


def hotkey_text(action: str) -> str:
    """快捷键的显示文字，如 Ctrl+Alt+D（界面和托盘菜单都用它，改键后自动跟着变）。"""
    spec = CONFIG.get("hotkeys", {}).get(action, "")
    if not spec:
        return "未设置"
    names = {"ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "win": "Win"}
    return "+".join(names.get(p, p.upper()) for p in spec.split("+"))


def hotkey_texts() -> dict:
    return {a: hotkey_text(a) for a in HOTKEY_LABELS}


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
VOICE_CACHE = DATA / "cache"


_PLAY_LOCK = threading.Lock()
SAPI = SapiSpeaker(log)
MCI_FORMATS = (".mp3", ".wav", ".wma")


def _play_mp3(path: Path) -> bool:
    alias = "warmword_voice"
    with _PLAY_LOCK:
        winmm.mciSendStringW(f"close {alias}", None, 0, None)
        r = winmm.mciSendStringW(f'open "{path}" type mpegvideo alias {alias}', None, 0, None)
        if r != 0:
            log(f"[发音] 打开音频失败 code={r} {path.name}")
            return False
        winmm.mciSendStringW(f"play {alias}", None, 0, None)
    return True


def _trim_cache(folder: Path, pattern: str, keep: int = 300) -> None:
    try:
        files = sorted(folder.glob(pattern), key=lambda p: p.stat().st_mtime)
        for old in files[:-keep]:
            old.unlink()
    except Exception:
        pass


def speak(text: str, accent: str = "us") -> bool:
    """读单词或例句：先用有道在线发音（真人/高质量合成），拿不到就用 Windows 自带语音。"""
    text = re.sub(r"\s+", " ", (text or "")).strip()[:300]
    if not text:
        return False
    if _speak_online(text, accent):
        return True
    log(f"[发音] 在线发音不可用，改用系统语音：{text[:40]}")
    return SAPI.speak(text, accent)


def play_resource(book_path: str, ref: str) -> dict:
    """播放词典 mdd 里的发音（sound:// 链接）。MCI 放不了的格式交给页面用浏览器播放。"""
    import base64
    book = ENGINE.book(book_path) if ENGINE is not None else None
    if book is None or not hasattr(book, "resources"):
        return {"ok": False, "why": "这本词典没有发音资源"}
    data = book.resources().get(ref)
    if not data:
        log(f"[发音] 词典里找不到音频 {ref}（mdd 文件要和 mdx 放在同一个文件夹、同名）")
        return {"ok": False, "why": "词典里找不到这个音频"}
    ext = Path(clean_ref(ref)).suffix.lower()
    if ext in MCI_FORMATS:
        folder = VOICE_CACHE / "res"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / (hashlib.md5(data).hexdigest() + ext)
        if not path.exists():
            path.write_bytes(data)
        _trim_cache(folder, "*.*")
        return {"ok": _play_mp3(path)}
    if ext in (".ogg", ".oga", ".opus", ".m4a", ".aac", ".flac", ".spx"):
        from dict_render import mime_of
        return {"ok": False, "data": base64.b64encode(data).decode(), "mime": mime_of(ref)}
    return {"ok": False, "why": f"不支持的音频格式 {ext}"}


def _speak_online(word: str, accent: str = "us") -> bool:
    """有道发音（英/美；单词和句子都行），失败返回 False。"""
    voice_type = "2" if accent == "us" else "1"
    key = hashlib.md5(f"{word}|{voice_type}".encode("utf-8")).hexdigest()
    VOICE_CACHE.mkdir(parents=True, exist_ok=True)
    path = VOICE_CACHE / f"{key}.mp3"
    if not path.exists():
        try:
            r = requests.get(
                "https://dict.youdao.com/dictvoice",
                params={"audio": word, "type": voice_type},
                headers=translator.HEADERS, timeout=(3, 8))
            if r.status_code != 200 or len(r.content) < 800:
                log(f"[发音] 没拿到音频 {word} code={r.status_code} size={len(r.content)}")
                return False
            path.write_bytes(r.content)
        except Exception as e:
            log(f"[发音] 下载失败 {word}: {e!r}")
            return False
    _trim_cache(VOICE_CACHE, "*.mp3")  # 清缓存：最多 300 个文件
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


def autostart_command() -> str:
    """开机自启要执行的命令：用当前正在运行的解释器（以前写死 venv313，和 启动.bat 建的 venv 对不上）。"""
    if FROZEN:
        return f'"{sys.executable}"'
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    if pythonw.exists():
        exe = pythonw
    return f'"{exe}" "{BASE / "app.py"}"'


def autostart_value() -> str | None:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            return winreg.QueryValueEx(k, AUTOSTART_NAME)[0]
    except OSError:
        return None


def set_autostart(on: bool) -> bool:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            if on:
                winreg.SetValueEx(k, AUTOSTART_NAME, 0, winreg.REG_SZ, autostart_command())
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
    """按配置开启自启；注册表里的路径过期了（换过目录/虚拟环境）就改成当前路径。"""
    if CONFIG.get("autostart", True) and autostart_value() != autostart_command():
        set_autostart(True)


def change_autostart(on: bool) -> None:
    """托盘和设置里切换自启：同时写回配置，否则下次启动又被 ensure_autostart 改回去。"""
    set_autostart(on)
    CONFIG["autostart"] = bool(on)
    save_config()


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


def push_hotkeys() -> None:
    """把当前快捷键文字推给各页面（页面里的提示文字跟着改键变化）。"""
    data = hotkey_texts()
    for name in WINDOWS:
        if name == "settings":
            continue  # 设置页自己按 get_settings 的数据渲染，没有 setHotkeys，推了只会报 ReferenceError
        push(name, "setHotkeys", data)


BOOK_CSS_REFS: dict[str, list[str]] = {}  # 每本词典用到的样式表


def book_resources(path: str):
    book = ENGINE.book(path) if ENGINE is not None else None
    if book is None or not hasattr(book, "resources"):
        return None
    try:
        return book.resources()
    except Exception as e:
        log(f"[资源] {Path(path).name} 加载失败 {e!r}")
        return None


def entry_dicts(entries: list[Entry], limit: int | None = None) -> list[dict]:
    """把词条整理成页面要的格式。

    mode=rich：词典原版 HTML（脚本已去掉），页面放进 Shadow DOM 并套上词典自带样式
    mode=ecdict：ECDICT 结构化释义
    mode=simple：压成简洁文字（设置里选「简洁」时）
    """
    out = []
    rich = CONFIG.get("render_mode", "rich") != "simple"
    for e in entries:
        item = {"dict": e.dict_name, "book": e.source, "head": e.headword}
        if e.kind == "ecdict":
            item.update(mode="ecdict", html=e.html)
        elif rich:
            res = book_resources(e.source)
            html, css_refs, _has_sound = rewrite_html(e.html, res)
            refs = BOOK_CSS_REFS.setdefault(e.source, [])
            refs.extend(r for r in css_refs if r not in refs)
            item.update(mode="rich", html=html,
                        css_id=hashlib.md5("|".join(refs).encode("utf-8")).hexdigest()[:8] if refs else "")
        else:
            h = clean_html(e.html)
            if limit and len(h) > limit:  # 只在换行处截断，不把标签切坏
                cut = h.rfind("<br>", 0, limit)
                h = h[:cut if cut > 0 else limit] + "<br>…（内容较长，点「详情」看完整释义）"
            item.update(mode="simple", html=h)
        out.append(item)
    return out


def book_css(path: str) -> str:
    res = book_resources(path)
    if res is None:
        return ""
    parts = []
    for ref in BOOK_CSS_REFS.get(path, []):
        try:
            parts.append(res.css(ref))
        except Exception as e:
            log(f"[资源] 样式表 {ref} 读取失败 {e!r}")
    return "\n".join(parts)


def prefs() -> dict:
    return {"font_scale": float(CONFIG.get("font_scale", 1.0) or 1.0),
            "render_mode": CONFIG.get("render_mode", "rich"),
            "accent": CONFIG.get("accent", "us")}


def push_prefs() -> None:
    data = prefs()
    for name in WINDOWS:
        push(name, "WW.setPrefs", data)


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


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", ctypes.wintypes.RECT),
                ("rcWork", ctypes.wintypes.RECT), ("dwFlags", ctypes.c_ulong)]


def work_area_at(x: int, y: int) -> tuple[int, int, int, int]:
    """坐标所在显示器的工作区（物理像素，不含任务栏）。多显示器时按鼠标所在的那块屏算。"""
    try:
        user32.MonitorFromPoint.restype = ctypes.c_void_p
        user32.MonitorFromPoint.argtypes = [ctypes.wintypes.POINT, ctypes.c_ulong]
        user32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(MONITORINFO)]
        hmon = user32.MonitorFromPoint(ctypes.wintypes.POINT(int(x), int(y)), 2)  # NEAREST
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        if hmon and user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            r = mi.rcWork
            return r.left, r.top, r.right, r.bottom
    except Exception as e:
        log(f"[窗口] 取显示器信息失败 {e!r}")
    return 0, 0, user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)


def cursor_pos() -> tuple[int, int]:
    pos = ctypes.wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(pos))
    return pos.x, pos.y


def window_size_px(name: str, default_dip: tuple[int, int]) -> tuple[int, int]:
    hwnd = user32.FindWindowW(None, WINDOW_TITLES[name])
    if hwnd:
        r = ctypes.wintypes.RECT()
        if user32.GetWindowRect(hwnd, ctypes.byref(r)) and r.right > r.left:
            return r.right - r.left, r.bottom - r.top
    return int(default_dip[0] * SCALE), int(default_dip[1] * SCALE)


def place_window_px(name: str, x: int, y: int) -> bool:
    """按物理像素放窗口（不改大小、不抢焦点）。"""
    hwnd = user32.FindWindowW(None, WINDOW_TITLES[name])
    if not hwnd:
        return position_window(name, to_dip(x), to_dip(y), show=True)
    user32.SetWindowPos(hwnd, 0, int(x), int(y), 0, 0, 0x0001 | 0x0004 | 0x0010 | 0x0040)
    return True


def show_float_near_cursor(word: str, entries: list[Entry],
                           x: int | None = None, y: int | None = None,
                           push_content: bool = True, status: str = "") -> None:
    win = WINDOWS.get("float")
    if win is None:
        return
    if push_content:
        push("float", "renderFloat", {
            "word": word,
            "loading": False,
            "entries": entry_dicts(entries),
            "status": status,
        })
    if x is None or y is None:
        x, y = cursor_pos()
    w, h = window_size_px("float", (440, 360))
    left, top, right, bottom = work_area_at(x, y)
    gap_x, gap_y = int(18 * SCALE), int(22 * SCALE)
    px, py = x + gap_x, y + gap_y
    if px + w > right:
        px = max(left, x - w - gap_x)
    if py + h > bottom:
        py = max(top, y - h - gap_y)
    try:
        win.show()
        round_window("float")
    except Exception as e:
        log(f"[浮窗] 显示失败: {e!r}")
    place_window_px("float", px, py)
    force_redraw("float")
    with STATE.lock:
        STATE.float_visible = True
        STATE.float_word = word
        STATE.float_xy = (x, y)


FOREGROUND_HOLDER = {"hwnd": None}


def own_hwnd(hwnd) -> bool:
    if not hwnd:
        return False
    pid = ctypes.c_ulong()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value == os.getpid()


def remember_focus() -> None:
    hwnd = user32.GetForegroundWindow()
    if hwnd and not own_hwnd(hwnd):
        FOREGROUND_HOLDER["hwnd"] = hwnd


def restore_focus() -> None:
    hwnd = FOREGROUND_HOLDER["hwnd"]
    if hwnd:
        try:
            user32.SetForegroundWindow(hwnd)
        except Exception:
            pass


def activate_window(name: str) -> None:
    """把窗口真正切到前台并拿到键盘焦点（查词窗弹出来就能直接打字、按 Esc）。"""
    hwnd = user32.FindWindowW(None, WINDOW_TITLES[name])
    if not hwnd:
        return
    try:
        fg = user32.GetForegroundWindow()
        me = kernel32.GetCurrentThreadId()
        fg_tid = user32.GetWindowThreadProcessId(fg, None) if fg else 0
        attached = bool(fg_tid and fg_tid != me and user32.AttachThreadInput(me, fg_tid, True))
        try:
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
            user32.SetFocus(hwnd)
        finally:
            if attached:
                user32.AttachThreadInput(me, fg_tid, False)
    except Exception as e:
        log(f"[窗口] 激活 {name} 失败 {e!r}")


def present(name: str, aspect_y: float, default_dip: tuple[int, int]) -> None:
    """在鼠标所在的显示器上居中显示窗口，并切到前台。"""
    win = WINDOWS.get(name)
    if win is None:
        return
    remember_focus()
    cx, cy = cursor_pos()
    left, top, right, bottom = work_area_at(cx, cy)
    try:
        win.show()
    except Exception as e:
        log(f"[{name}] 显示失败: {e!r}")
    w, h = window_size_px(name, default_dip)
    x = left + max(0, (right - left - w) // 2)
    y = top + max(0, int((bottom - top - h) * aspect_y))
    place_window_px(name, x, y)
    force_redraw(name)
    round_window(name)
    activate_window(name)
    log_window_state(name)


def show_lookup(word: str = "") -> None:
    if WINDOWS.get("lookup") is None:
        return
    present("lookup", 0.22, (600, 660))
    push("lookup", "focusSearch", None)
    if word and ENGINE is not None:
        push("lookup", "renderLookup", API.search(word))


def show_vocab() -> None:
    if WINDOWS.get("vocab") is None:
        return
    push("vocab", "renderVocab", {"items": load_vocab()})
    present("vocab", 0.16, (760, 780))


def settings_payload() -> dict:
    """设置窗口要显示的全部状态。"""
    counts = {}
    if ENGINE is not None:
        for b in ENGINE.books:
            counts[str(b.path)] = len(b)
    dicts = []
    for d in CONFIG.get("dictionaries", []):
        path = str(d.get("path", ""))
        dicts.append({
            "path": path,
            "name": d.get("name") or Path(path).stem,
            "count": counts.get(path, 0),
            "ok": bool(counts.get(path)) or Path(path).exists(),
            "missing": not Path(path).exists(),
        })
    hotkeys = {}
    for action, spec in CONFIG.get("hotkeys", {}).items():
        hotkeys[action] = {"spec": spec, "ok": STATE.hotkey_status.get(action)}
    return {
        "hotkeys": hotkeys,
        "dictionaries": dicts,
        "auto_capture": bool(CONFIG.get("auto_capture", True)),
        "capture_modifier": CONFIG.get("capture_modifier", "") or "",
        "render_mode": CONFIG.get("render_mode", "rich"),
        "font_scale": float(CONFIG.get("font_scale", 1.0) or 1.0),
        "auto_speak": bool(CONFIG.get("auto_speak", False)),
        "baidu_appid": CONFIG.get("baidu_appid", ""),
        "baidu_key": CONFIG.get("baidu_key", ""),
        "data_dir": str(DATA),
        "ecdict": dict(ECDICT_STATE),
        "capture_only_english": bool(CONFIG.get("capture_only_english", True)),
        "hover_delay_ms": int(CONFIG.get("hover_delay_ms", 600)),
        "online_translate": bool(CONFIG.get("online_translate", True)),
        "accent": CONFIG.get("accent", "us"),
        "autostart": autostart_enabled(),
        "hide_tray_icon": bool(CONFIG.get("hide_tray_icon", False)),
        "config_path": str(CONFIG_PATH),
        "engine_ready": ENGINE is not None,
    }


def show_settings() -> None:
    if WINDOWS.get("settings") is None:
        return
    push("settings", "renderSettings", settings_payload())
    present("settings", 0.08, (780, 820))


def show_translate(text: str) -> None:
    if WINDOWS.get("translate") is None:
        return
    push("translate", "renderTranslate",
         {"original": text, "loading": True, "translated": None, "words": []})
    present("translate", 0.25, (620, 560))

    def worker():
        translated, source = None, ""
        if CONFIG.get("online_translate", True):
            translated = translator.translate(text)
            source = translator.last_source()
        items = []
        if ENGINE is not None:
            def brief(w):
                e = ENGINE.lookup(w)[:1]
                return brief_of(e[0], 60) if e else ""
            items = pick_new_words(text, brief, rank=ENGINE.rank, saved=vocab_words())
        push("translate", "renderTranslate", {
            "original": text,
            "loading": False,
            "translated": translated,
            "source": source,
            "online": bool(CONFIG.get("online_translate", True)),
            "words": items,
        })

    threading.Thread(target=worker, daemon=True).start()


# ---------------- JS 接口 ----------------
class JsApi:
    def search(self, word: str, record: bool = False) -> dict:
        word = (word or "").strip()
        if not word:
            return {"word": word, "entries": []}
        if ENGINE is None:
            return {"word": word, "entries": [], "status": "词典还在加载，请稍等几秒"}
        entries = ENGINE.lookup(word)[:8]
        if entries and record:
            add_history(word)
        out = {"word": word, "entries": entry_dicts(entries)}
        if not entries:
            if not ENGINE.books:
                out["status"] = "还没有可用的词典：托盘右键「设置」里添加词典，或一键下载 ECDICT"
            elif re.search(r"[A-Za-z]", word):
                out["did_you_mean"] = ENGINE.fuzzy(word, limit=6)  # 拼写纠错：dictionery→dictionary
        return out

    def get_hotkeys(self) -> dict:
        return hotkey_texts()

    def get_accent(self) -> str:
        return CONFIG.get("accent", "us")

    def get_prefs(self) -> dict:
        return prefs()

    def set_font_scale(self, scale: float) -> dict:
        try:
            CONFIG["font_scale"] = round(max(0.8, min(1.8, float(scale))), 2)
        except Exception:
            pass
        save_config()
        push_prefs()
        return prefs()

    def recent(self) -> list[str]:
        return load_history()[:24]

    def clear_history(self) -> bool:
        try:
            HISTORY_PATH.unlink()
        except Exception:
            pass
        return True

    def book_css(self, path: str) -> str:
        return book_css(path)

    def play_sound(self, book: str, ref: str) -> dict:
        try:
            return play_resource(book, ref)
        except Exception as e:
            log(f"[发音] 播放词典音频失败 {e!r}")
            return {"ok": False, "why": "播放失败"}

    def speak_text(self, text: str, accent: str = "") -> bool:
        return speak(text, accent or CONFIG.get("accent", "us"))

    def suggest(self, prefix: str) -> list[str]:
        if ENGINE is None:
            return []
        return ENGINE.suggest((prefix or "").strip(), 8)

    def word_detail(self, word: str) -> list[dict]:
        if ENGINE is None:
            return []
        return entry_dicts(ENGINE.lookup((word or "").strip())[:4])

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

    def pick_dictionary(self):
        """打开文件对话框选一本词典（.mdx，或 ECDICT 的 .csv / .db）。"""
        win = WINDOWS.get("settings")
        if win is None:
            return {"error": "设置窗口还没准备好"}
        try:
            import webview as _wv
            files = win.create_file_dialog(
                _wv.OPEN_DIALOG, allow_multiple=False,
                file_types=("词典文件 (*.mdx;*.csv;*.db)", "所有文件 (*.*)"))
            if not files:
                return None
            path = files[0] if isinstance(files, (list, tuple)) else files
            p = Path(path)
            if p.suffix.lower() not in (".mdx", ".csv", ".db"):
                return {"error": "请选择 .mdx 词典，或 ECDICT 的 ecdict.csv / stardict.db"}
            name = "ECDICT" if p.suffix.lower() in (".csv", ".db") else p.stem
            return {"path": str(p), "name": name, "count": 0, "ok": True, "missing": False}
        except Exception as e:
            log(f"[设置] 选词典失败 {e!r}")
            return {"error": f"选择文件失败：{e}"}

    def get_settings(self) -> dict:
        return settings_payload()

    def save_settings(self, payload: dict) -> dict:
        payload = payload or {}
        # 1. 快捷键先校验，不合法就整体不写
        incoming = payload.get("hotkeys") or {}
        for action, spec in incoming.items():
            if action not in HOTKEY_LABELS:
                continue
            try:
                mods, vk = parse_hotkey(spec or "")
            except Exception:
                mods, vk = 0, None
            # parse_hotkey 会带上 MOD_NOREPEAT，这里只看真正的修饰键；只带 Shift 会吃掉正常打字
            if not vk or not (mods & (MOD_CONTROL | MOD_ALT | MOD_WIN)):
                return {"ok": False,
                        "error": f"「{HOTKEY_LABELS.get(action, action)}」的快捷键不合法：至少要 Ctrl / Alt / Win 之一加一个按键"}
        specs = {}
        for action, spec in incoming.items():
            if action in HOTKEY_LABELS and spec:
                key = "+".join(sorted((spec or "").lower().split("+")))
                if key in specs:
                    return {"ok": False, "error": f"「{HOTKEY_LABELS[specs[key]]}」和「{HOTKEY_LABELS[action]}」用了同一个快捷键"}
                specs[key] = action
        # 2. 词典：找不到的文件照样保存（列表里标红），不再整体拒绝
        new_dicts = payload.get("dictionaries")
        if new_dicts is not None:
            cleaned, seen = [], set()
            for d in new_dicts:
                path = str((d or {}).get("path", "")).strip()
                if not path or path in seen:
                    continue
                seen.add(path)
                cleaned.append({"path": path,
                                "name": (d or {}).get("name") or Path(path).stem})
            new_dicts = cleaned

        old_hotkeys = dict(CONFIG.get("hotkeys", {}))
        old_dicts = [dict(d) for d in CONFIG.get("dictionaries", [])]

        if incoming:
            CONFIG.setdefault("hotkeys", {}).update(
                {k: v for k, v in incoming.items() if k in HOTKEY_LABELS})
        if new_dicts is not None:
            CONFIG["dictionaries"] = new_dicts
        for key in ("auto_capture", "capture_only_english", "online_translate", "hide_tray_icon",
                    "auto_speak"):
            if key in payload:
                CONFIG[key] = bool(payload[key])
        if "hover_delay_ms" in payload:
            try:
                CONFIG["hover_delay_ms"] = max(300, min(2000, int(payload["hover_delay_ms"])))
            except Exception:
                pass
        if payload.get("accent") in ("us", "uk"):
            CONFIG["accent"] = payload["accent"]
        if payload.get("render_mode") in ("rich", "simple"):
            CONFIG["render_mode"] = payload["render_mode"]
        if "font_scale" in payload:
            try:
                CONFIG["font_scale"] = round(max(0.8, min(1.8, float(payload["font_scale"]))), 2)
            except Exception:
                pass
        if "capture_modifier" in payload and payload["capture_modifier"] in ("", *CAPTURE_MODIFIERS):
            CONFIG["capture_modifier"] = payload["capture_modifier"]
        for key in ("baidu_appid", "baidu_key"):
            if key in payload:
                CONFIG[key] = str(payload[key] or "").strip()
        translator.configure(CONFIG.get("baidu_appid", ""), CONFIG.get("baidu_key", ""))
        if payload.get("autostart") is not None:
            CONFIG["autostart"] = bool(payload["autostart"])
            set_autostart(CONFIG["autostart"])
        save_config()

        STATE.auto_capture = bool(CONFIG.get("auto_capture", True))
        push_prefs()
        if TRAY_ICON is not None:
            try:
                TRAY_ICON.update_menu()
            except Exception:
                pass

        notes = []
        if CONFIG.get("hotkeys", {}) != old_hotkeys:
            reload_hotkeys_async()
            push_hotkeys()
            notes.append("快捷键已换")
        if CONFIG.get("dictionaries", []) != old_dicts:
            threading.Thread(target=reload_engine, daemon=True).start()
            notes.append("词典在后台重新加载（约几秒）")
            if not CONFIG.get("dictionaries"):
                notes.append("现在一本词典都没有，取词会查不到释义")
        if CONFIG.get("hide_tray_icon"):
            notes.append("隐藏托盘图标要重启程序才生效")
        msg = "已保存：" + ("；".join(notes) if notes else "设置已更新")
        log(f"[设置] {msg}")
        fresh = settings_payload()
        return {"ok": True, "message": msg, "detail": f"已写入 {CONFIG_PATH}",
                "hotkeys": fresh["hotkeys"], "dictionaries": fresh["dictionaries"]}

    def close_window(self, which: str) -> None:
        win = WINDOWS.get(which)
        if win is None:
            return
        hwnd = user32.FindWindowW(None, WINDOW_TITLES[which])
        was_active = bool(hwnd) and user32.GetForegroundWindow() == hwnd
        position_window(which, OFFSCREEN[0], OFFSCREEN[1], show=True)
        if which == "float":
            with STATE.lock:
                STATE.float_visible = False
                STATE.float_pinned = False
        elif was_active:
            # 窗口只是挪到屏幕外，焦点不还回去的话，接下来打的字会进这个看不见的窗口
            restore_focus()

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
            if CONFIG.get("auto_speak"):
                threading.Thread(target=speak, args=(res["word"], CONFIG.get("accent", "us")),
                                 daemon=True).start()
            return {"word": res["word"], "loading": False, "status": res.get("note", ""),
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
            entries = ENGINE.lookup(word)[:6]  # 浮窗里每本词典一个标签，可以切换
            note = ""
            if not entries and len(word) >= 5 and re.fullmatch(r"[A-Za-z'’-]+", word):
                # 识别偶尔认错一个字母（l/I、rn/m）：只接受编辑距离为 1 的纠正，
                # 以前用前 4 个字母的前缀去猜，人名、术语会弹出不相干的词
                for cand in ENGINE.fuzzy(word, limit=3, max_dist=1):
                    entries = ENGINE.lookup(cand)[:6]
                    if entries:
                        note = f"识别为「{word}」，按「{cand}」显示"
                        log(f"[取词] {note}")
                        word = cand
                        break
            if not entries:
                return
            with STATE.lock:
                STATE.result = {"word": word, "entries": entries, "x": x, "y": y, "note": note}
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
            if not text:  # 选区接口读不到时，模拟一次复制再读剪贴板（读完恢复原剪贴板）
                text = copy_selection_text()
            if text:
                show_translate(text.strip()[:2000])
            else:
                notify(f"没读到选中的文字：先用鼠标选中一段文字，再按 {hotkey_text('translate')}",
                       key="no-selection")
        elif task == "capture":
            x, y = cursor_pos()
            word = None
            try:
                if CAPTURE is not None:
                    word = CAPTURE.word_at(x, y)
            except Exception as e:
                log(f"[取词] 手动取词失败: {e!r}")
            if not word or not re.search(r"[A-Za-z\u4e00-\u9fff]", word):
                notify("鼠标下没认出文字：把鼠标移到单词正上方再试", key="no-word")
                return
            word = word.strip()
            if ENGINE is None:
                notify("词典还在加载，请稍等几秒", key="engine-loading")
                return
            entries = ENGINE.lookup(word)[:6]
            # 手动取词查不到也弹浮窗告诉用户，不再悄无声息
            show_float_near_cursor(word, entries, x, y,
                                   status="" if entries else "词典里没有")
            if entries and CONFIG.get("auto_speak"):
                speak(word, CONFIG.get("accent", "us"))
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
                word = word.strip()
                if add_vocab(word):
                    notify(f"已收进生词本：{word}", key="vocab")
                else:
                    notify(f"生词本里已经有「{word}」了", key="vocab")
            else:
                notify("鼠标下没有英文单词，没收藏", key="vocab")

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

    def export_anki(self) -> str:
        return export_anki()

    def clear_vocab(self) -> int:
        n = len(load_vocab())
        save_vocab([])
        log(f"[生词本] 已清空（{n} 个）")
        return n

    def open_folder(self, which: str = "export") -> bool:
        path = {"export": EXPORT_DIR, "data": DATA}.get(which, DATA)
        try:
            path.mkdir(parents=True, exist_ok=True)
            os.startfile(str(path))
            return True
        except Exception as e:
            log(f"[打开目录] 失败 {e!r}")
            return False

    def speak_word(self, word: str, accent: str = "") -> bool:
        return speak(word, accent or CONFIG.get("accent", "us"))

    def download_ecdict(self) -> dict:
        if ECDICT_STATE["running"]:
            return {"ok": False, "error": "已经在下载了"}
        threading.Thread(target=download_ecdict, daemon=True).start()
        return {"ok": True}


API = JsApi()


# ---------------- 下载 ECDICT ----------------
ECDICT_STATE = {"running": False, "pct": 0, "msg": ""}


def _ecdict_progress(pct: int, msg: str, done: bool = False, ok: bool = True) -> None:
    ECDICT_STATE.update(pct=pct, msg=msg)
    push("settings", "ecdictProgress", {"pct": pct, "msg": msg, "done": done, "ok": ok})


def download_ecdict() -> None:
    """下载开源词库 ECDICT（约 63MB，MIT 许可），完成后自动加进词典列表。"""
    ECDICT_STATE["running"] = True
    target = DICT_DIR / "ecdict.csv"
    part = target.with_suffix(".csv.part")
    try:
        DICT_DIR.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            _ecdict_progress(0, "正在连接 GitHub…")
            with requests.get(ECDICT_URL, stream=True, timeout=(10, 30)) as r:
                r.raise_for_status()
                total = int(r.headers.get("Content-Length") or 0)
                got, last = 0, 0.0
                with open(part, "wb") as f:
                    for chunk in r.iter_content(256 * 1024):
                        f.write(chunk)
                        got += len(chunk)
                        if time.time() - last > 0.5:
                            last = time.time()
                            pct = min(99, int(got * 100 / total)) if total else 0
                            _ecdict_progress(pct, f"已下载 {got / 1048576:.1f} MB"
                                             + (f" / {total / 1048576:.0f} MB" if total else ""))
            part.replace(target)
        _ecdict_progress(100, "下载完成，正在建立索引（第一次约半分钟）…")
        dicts = CONFIG.setdefault("dictionaries", [])
        if not any(Path(d.get("path", "")) == target for d in dicts):
            dicts.append({"path": str(target), "name": "ECDICT"})
            save_config()
        reload_engine()
        ok = ENGINE is not None and any(Path(b.path) == target for b in ENGINE.books)
        msg = "ECDICT 已就绪" if ok else "ECDICT 加载失败，详见日志"
        _ecdict_progress(100, msg, done=True, ok=ok)
        push("settings", "renderSettings", settings_payload())
        notify(msg)
    except Exception as e:
        log(f"[ECDICT] 下载失败 {e!r}")
        try:
            part.unlink()
        except Exception:
            pass
        _ecdict_progress(0, "下载失败：连不上 GitHub。可以手动下载 ecdict.csv 后用「添加词典文件」选它",
                         done=True, ok=False)
    finally:
        ECDICT_STATE["running"] = False


# ---------------- 热键 ----------------
def hotkey_thread():
    try:
        STATE.hotkey_tid = kernel32.GetCurrentThreadId()
        reg: dict[int, str] = {}

        def register_all() -> None:
            for idx in list(reg.keys()):
                user32.UnregisterHotKey(None, idx)
            reg.clear()
            STATE.hotkey_status = {}
            for idx, (action, spec) in enumerate(CONFIG["hotkeys"].items(), start=1):
                if not spec:
                    continue
                mods, vk = parse_hotkey(spec)
                ok = user32.RegisterHotKey(None, idx, mods, vk)
                STATE.hotkey_status[action] = bool(ok)
                log(f"[热键] {HOTKEY_LABELS.get(action, action)} {spec} -> "
                    f"{'注册成功' if ok else '注册失败（可能被其他软件占用）'}")
                if ok:
                    reg[idx] = action

        register_all()
        threading.Timer(3.0, report_hotkey_failures).start()  # 等托盘图标出来再提示
        MSG = ctypes.wintypes.MSG()
        while not STATE.quit:
            r = user32.GetMessageW(ctypes.byref(MSG), None, 0, 0)
            if r in (0, -1):
                break
            if MSG.message == WM_HOTKEY and MSG.wParam in reg:
                on_hotkey(reg[MSG.wParam])
            elif MSG.message == WM_APP_RELOAD_HOTKEYS:
                log("[热键] 配置有变，重新注册")
                register_all()
                report_hotkey_failures()
    except Exception as e:
        log(f"[热键线程异常] {e!r}")


def report_hotkey_failures() -> None:
    bad = [f"{HOTKEY_LABELS.get(a, a)}（{hotkey_text(a)}）"
           for a, ok in STATE.hotkey_status.items() if not ok]
    if bad:
        notify("这些快捷键被别的软件占用了：" + "、".join(bad) + "。可在托盘右键「设置」里换一个",
               key="hotkey-fail", interval=1)


def reload_hotkeys_async() -> None:
    """通知热键线程按新配置重新注册。"""
    try:
        if STATE.hotkey_tid:
            user32.PostThreadMessageW(STATE.hotkey_tid, WM_APP_RELOAD_HOTKEYS, 0, 0)
    except Exception as e:
        log(f"[热键] 重载通知失败 {e!r}")


def preload_resources(engine: DictEngine) -> None:
    """后台提前读好各词典的 mdd 索引，第一次查词时不用等。"""
    def run():
        for b in engine.books:
            if not hasattr(b, "resources"):
                continue
            t0 = time.time()
            res = b.resources()
            if res.has_mdd:
                log(f"[资源] {b.name}: 已加载 mdd（{time.time() - t0:.1f} 秒）")
            for err in res.errors:
                log(f"[资源] {b.name}: {err}")
    threading.Thread(target=run, daemon=True).start()


def build_engine() -> DictEngine:
    books = [(d["path"], d.get("name")) for d in CONFIG.get("dictionaries", [])]
    engine = DictEngine(books, cache_dir=INDEX_DIR)
    BOOK_CSS_REFS.clear()
    preload_resources(engine)
    names = "、".join(f"{b.name}（{len(b):,} 条）" for b in engine.books)
    log(f"[词典就绪] {names or '没有可用的词典'}")
    if engine.errors:
        for name, why in engine.errors:
            log(f"[词典加载失败] {name}: {why}")
        notify("有词典没加载上：" + "、".join(f"{n}（{w if w == '找不到文件' else '文件读不了'}）"
                                         for n, w in engine.errors), key="dict-fail", interval=1)
    return engine


def reload_engine() -> None:
    """按当前配置重新加载词典（设置里换过词典后调用）。"""
    global ENGINE
    try:
        log("[词典] 重新加载…")
        ENGINE = build_engine()
    except Exception as e:
        log(f"[词典] 重新加载失败 {e!r}")


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
        if CAPTURE is not None:
            CAPTURE.close()
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
        ex = (ex | 0x00000080 | no_activate_flag(name)) & ~0x00040000
        user32.SetWindowLongPtrW(hwnd, -20, ctypes.c_void_p(ex))
        delete_taskbar_tab(hwnd)
        return True
    except Exception as e:
        log(f"[任务栏] {name} 脱钩失败 {e!r}")
        return False


def delete_taskbar_tab(hwnd: int) -> bool:
    """调用系统官方接口 ITaskbarList::DeleteTab，把窗口按钮从任务栏强制删除。"""
    try:
        import uuid
        ole32 = ctypes.windll.ole32

        class GUID(ctypes.Structure):
            _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort),
                        ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]

        def mk_guid(s):
            u = uuid.UUID(s)
            g = GUID()
            g.Data1, g.Data2, g.Data3 = u.time_low, u.time_mid, u.time_hi_version
            for i, b in enumerate(u.bytes[8:]):
                g.Data4[i] = b
            return g

        ole32.CoInitializeEx(None, 0x2)  # 已初始化也无害
        clsid = mk_guid("{56FDF344-FD6D-11d0-958A-006097C9A090}")
        iid = mk_guid("{56FDF342-FD6D-11d0-958A-006097C9A090}")
        ptr = ctypes.c_void_p()
        hr = ole32.CoCreateInstance(ctypes.byref(clsid), None, 1,
                                    ctypes.byref(iid), ctypes.byref(ptr))
        if hr != 0 or not ptr.value:
            log(f"[任务栏] 取任务栏接口失败 hr=0x{hr & 0xFFFFFFFF:X}")
            return False
        vtbl = ctypes.cast(ptr, ctypes.POINTER(ctypes.c_void_p))[0]
        slot = ctypes.sizeof(ctypes.c_void_p)

        def method(i):
            return ctypes.cast(vtbl + i * slot, ctypes.POINTER(ctypes.c_void_p))[0]

        HRESULT = ctypes.c_long
        ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p)(method(3))(ptr)          # HrInit
        ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p,
                           ctypes.c_void_p)(method(5))(ptr, ctypes.c_void_p(hwnd))  # DeleteTab
        ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(method(2))(ptr)   # Release
        return True
    except Exception as e:
        log(f"[任务栏] DeleteTab 异常 {e!r}")
        return False


def no_activate_flag(name: str) -> int:
    """只有取词浮窗不抢焦点；查词/翻译/设置窗要能打字，加了「不激活」就收不到键盘输入。"""
    return 0x08000000 if name == "float" else 0


def make_tool_window(name: str) -> bool:
    """让窗口成为「工具窗口」：不出现在任务栏、不进 Alt+Tab；浮窗另外不抢焦点。"""
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
        want = (ex | WS_EX_TOOLWINDOW | no_activate_flag(name)) & ~0x00040000
        if name != "float":
            want &= ~WS_EX_NOACTIVATE  # 旧版本给所有窗口都加过，这里清掉
        if want != ex:
            user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, ctypes.c_void_p(want))
        # 通知系统刷新窗口框架，任务栏立刻撤掉图标
        user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0,
                            0x0001 | 0x0002 | 0x0004 | 0x0020)  # NOSIZE|NOMOVE|NOZORDER|FRAMECHANGED
        return True
    except Exception as e:
        log(f"[窗口] 设置工具窗口失败 {name}: {e!r}")
        return False


WINDOWS_READY = threading.Event()


def init_windows():
    """窗口初始化：先隐藏状态下挂好「不进任务栏」的样式，再显示到屏幕外待命。"""
    FLOAT_HWND["hwnd"] = 0
    names = ("float", "lookup", "translate", "vocab", "settings")
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
    log("[窗口] 五个窗口就位（屏幕外待命，已从任务栏摘除）")
    push_hotkeys()
    WINDOWS_READY.set()


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


FLOAT_HIDE_SECONDS = 1.2   # 鼠标离开单词和浮窗多久后收起（以前 3.5 秒，挡着视线太久）
MODIFIER_DELAY = 0.15      # 按住取词键时，鼠标停多久就取词


def hover_thread():
    last_pos = None
    idle_since = time.time()
    hide_stamp = 0.0
    while not STATE.quit:
        time.sleep(0.06 if CONFIG.get("capture_modifier") else 0.12)
        pos = ctypes.wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pos))
        cur = (pos.x, pos.y)

        with STATE.lock:
            visible = STATE.float_visible
            pinned = STATE.float_pinned
            word_xy = STATE.float_xy
        if visible and not pinned:
            near_word = bool(word_xy and abs(pos.x - word_xy[0]) <= 110 * SCALE
                             and abs(pos.y - word_xy[1]) <= 80 * SCALE)
            if mouse_in_float() or near_word:
                hide_stamp = 0.0  # 鼠标在浮窗上或还停在取的那个词上：不收起
            else:
                if hide_stamp == 0.0:
                    hide_stamp = time.time()
                elif time.time() - hide_stamp > FLOAT_HIDE_SECONDS:
                    API.close_window("float")
                    hide_stamp = 0.0

        with STATE.lock:
            if not STATE.auto_capture:
                last_pos = cur
                continue

        # 「按住 Ctrl/Shift/Alt 才取词」模式：没按着就不取，阅读时鼠标随便停也不会弹窗
        vk = CAPTURE_MODIFIERS.get(CONFIG.get("capture_modifier") or "")
        if vk is not None:
            # 只认「单独按住」这个键：按快捷键组合（如 Ctrl+Alt+T）时不顺带取词
            others = [k for k in (0x10, 0x11, 0x12, 0x5B, 0x5C) if k != vk]
            if not (user32.GetAsyncKeyState(vk) & 0x8000) or any(
                    user32.GetAsyncKeyState(k) & 0x8000 for k in others):
                last_pos = cur
                idle_since = time.time() - 10  # 一按下就能立刻取词
                continue
            delay = MODIFIER_DELAY
        else:
            delay = CONFIG.get("hover_delay_ms", 600) / 1000.0

        if cur != last_pos:
            last_pos = cur
            idle_since = time.time()
            continue
        if time.time() - idle_since < delay:
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
    lines = ["暖词自检报告", f"时间: {time.strftime('%Y-%m-%d %H:%M:%S')}",
             f"数据目录: {DATA}", ""]
    if ENGINE is None:
        lines.append("词典: 还没加载完")
    else:
        total = sum(len(b) for b in ENGINE.books)
        lines.append(f"词典: {len(ENGINE.books)} 本，共 {total:,} 条")
        for b in ENGINE.books:
            lines.append(f"  · {b.name}: {len(b):,} 条")
        for name, why in ENGINE.errors:
            lines.append(f"  × {name}: {why}")
        t0 = time.time()
        hit = ENGINE.lookup("test")
        lines.append(f"查询测试: test -> {len(hit)} 条，{(time.time()-t0)*1000:.0f} 毫秒")
    lines.append("")
    if STATE.hotkey_status:
        for action, ok in STATE.hotkey_status.items():
            spec = CONFIG.get("hotkeys", {}).get(action, "")
            lines.append(f"热键 {HOTKEY_LABELS.get(action, action)}（{spec}）: "
                         f"{'正常' if ok else '注册失败（被占用）'}")
    else:
        lines.append("热键: 还没注册")
    lines.append("")
    mod = CONFIG.get("capture_modifier") or ""
    lines.append(f"自动取词: {'开' if STATE.auto_capture else '关'}"
                 + (f"（按住 {mod.capitalize()} 取词）" if mod else "（停住即取词）"))
    lines.append(f"开机自启: {'开' if autostart_enabled() else '关'}"
                 + ("" if autostart_value() in (None, autostart_command()) else "（路径过期，重启程序会修正）"))
    t0 = time.time()
    ok_voice = speak("test", "us")
    lines.append(f"发音: {'正常' if ok_voice else '不可用'} ({(time.time()-t0):.1f} 秒)")
    t0 = time.time()
    tr = translator.translate("This is a self check.")
    lines.append(f"联网翻译: {'正常（' + translator.last_source() + '）' if tr else '不可用'} "
                 f"({(time.time()-t0):.1f} 秒)")
    lines.append("")
    lines.append(f"日志文件: {LOG_FILE}")
    report = "\n".join(lines)
    try:
        path = DATA / "自检报告.txt"
        path.write_text(report, encoding="utf-8")
        os.startfile(str(path))
    except Exception:
        pass
    log("[自检]\n" + report)
    return report


# ---------------- 触发文件（调试用，配置里 debug=true 才开） ----------------
def trigger_file_thread():
    path = DATA / "触发.txt"
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
            (DATA / "测试_窗口内容.txt").write_text("\n".join(out), encoding="utf-8")
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
        elif action == "settings":
            show_settings()
        elif action == "js" and rest:
            # 调试用：js <窗口名>\n<JS 代码>
            target, _, code = rest.partition("\n")
            win = WINDOWS.get(target.strip() or "float")
            if win is not None and code.strip():
                try:
                    win.evaluate_js(code)
                except Exception as e:
                    log(f"[调试] 执行 JS 失败 {e!r}")
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
            (DATA / "探测结果.txt").write_text(
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
        log(f"[托盘] 配置为隐藏托盘图标；退出请按 {hotkey_text('quit')}")
        return

    def on_settings(icon, item):
        show_settings()

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
        log(f"[托盘] 已隐藏托盘图标（退出按 {hotkey_text('quit')}，想恢复把 config.json 里 hide_tray_icon 改 false）")
        try:
            icon.stop()
        except Exception:
            pass

    def on_restart(icon, item):
        """真正重启：新进程带 --force 跳过单实例检查，然后自己退出。"""
        import subprocess
        log("[重启] 托盘请求重启程序")
        cmd = [sys.executable] if FROZEN else [sys.executable, str(BASE / "app.py")]
        try:
            subprocess.Popen(cmd + ["--force"], cwd=str(BASE), creationflags=0x00000008)
        except Exception as e:
            log(f"[重启] 拉起新进程失败 {e!r}")
        time.sleep(0.8)
        os._exit(0)

    def on_toggle(icon, item):
        toggle_auto()

    def on_autostart(icon, item):
        change_autostart(not autostart_enabled())
        try:
            icon.update_menu()
        except Exception:
            pass

    def on_selfcheck(icon, item):
        threading.Thread(target=self_check, daemon=True).start()

    def on_quit(icon, item):
        STATE.quit = True
        if CAPTURE is not None:
            CAPTURE.close()
        try:
            icon.stop()
        except Exception:
            pass
        os._exit(0)

    def label(text, action):
        # 菜单文字按当前快捷键动态生成，改键后不用重启
        return lambda item: f"{text} ({hotkey_text(action)})"

    menu = pystray.Menu(
        Item("设置（快捷键 / 词典 / 取词）", on_settings),
        pystray.Menu.SEPARATOR,
        Item(label("快捷查词", "lookup"), on_lookup, default=True),
        Item(label("翻译选中内容", "translate"), on_translate),
        Item(label("鼠标处取词", "capture"), on_capture),
        pystray.Menu.SEPARATOR,
        Item(label("生词本 · 收藏用", "add_vocab"), on_vocab),
        pystray.Menu.SEPARATOR,
        Item(label("鼠标自动取词", "toggle_auto"), on_toggle, checked=lambda item: STATE.auto_capture),
        Item("开机自启", on_autostart, checked=lambda item: autostart_enabled()),
        pystray.Menu.SEPARATOR,
        Item("自检（生成报告）", on_selfcheck),
        Item("重启程序（改配置/更新后用）", on_restart),
        Item(label("隐藏托盘图标 · 退出用", "quit"), on_hide_tray),
        Item("退出", on_quit),
    )
    TRAY_ICON = pystray.Icon("warmword", make_tray_icon(), "暖词 · 取词助手", menu)
    TRAY_ICON.run()


# ---------------- 启动 ----------------
def engine_thread():
    global ENGINE
    try:
        ENGINE = build_engine()
    except Exception as e:
        log(f"[词典] 加载失败 {e!r}")
        ENGINE = DictEngine([])
    if not ENGINE.books:
        # 第一次用（或词典全都找不到）：直接打开设置，告诉用户下一步做什么
        WINDOWS_READY.wait(30)
        time.sleep(1.0)
        notify("还没有可用的词典：在设置里添加 .mdx 词典，或点「下载开源词库 ECDICT」", key="no-dict")
        show_settings()


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

    migrate_old_data(log)
    CONFIG = load_config()
    STATE.auto_capture = bool(CONFIG.get("auto_capture", True))
    translator.configure(CONFIG.get("baidu_appid", ""), CONFIG.get("baidu_key", ""))

    force = "--force" in sys.argv or "--restart" in sys.argv
    if force:
        if ask_old_instance_quit():
            log("[重启] 已通知旧实例退出，等它让路")
            time.sleep(2.5)
    elif not single_instance():
        log("[启动] 已有实例在跑，唤出查词窗后退出")
        user32.AllowSetForegroundWindow(-1)  # ASFW_ANY：让旧实例能把查词窗切到前台
        signal_event(SHOW_EVENT)
        return

    log("=" * 40)
    log(f"[启动] 暖词取词助手（数据目录 {DATA}{'，便携模式' if PORTABLE else ''}）")

    threading.Thread(target=engine_thread, daemon=True).start()
    threading.Thread(target=hotkey_thread, daemon=True).start()
    threading.Thread(target=hover_thread, daemon=True).start()
    threading.Thread(target=tray_thread, daemon=True).start()
    threading.Thread(target=quit_watcher, daemon=True).start()
    threading.Thread(target=show_watcher, daemon=True).start()
    if CONFIG.get("debug") or os.environ.get("WARMWORD_DEBUG"):
        log(f"[调试] 已开启触发文件 {DATA / '触发.txt'}")
        threading.Thread(target=trigger_file_thread, daemon=True).start()

    ensure_autostart()
    CAPTURE = CaptureClient()  # 取词交给独立小进程（文字接口只能在进程主线程用）

    ui = RES / "ui"
    # 释义渲染脚本和样式几个窗口共用，加载时内嵌进每个页面（页面是 html 字符串，没法引外部文件）
    common = ("<style>" + (ui / "entry.css").read_text(encoding="utf-8") + "</style>\n<script>"
              + (ui / "entry.js").read_text(encoding="utf-8") + "</script>")

    def page(name: str) -> str:
        return (ui / f"{name}.html").read_text(encoding="utf-8").replace("<!--WW_COMMON-->", common)

    float_html = page("float")
    lookup_html = page("lookup")
    translate_html = page("translate")
    vocab_html = page("vocab")
    settings_html = page("settings")

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
    WINDOWS["settings"] = webview.create_window(
        WINDOW_TITLES["settings"], html=settings_html, js_api=API,
        width=780, height=820, on_top=True, hidden=True)

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
                API.close_window(name)
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
