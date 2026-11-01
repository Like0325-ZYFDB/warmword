# -*- coding: utf-8 -*-
"""配置：默认值、读写、设置窗口提交内容的校验。

配置是一个普通 dict（Config 只是加了锁和保存），各模块读 cfg["键"] 即可。
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from .paths import CONFIG_PATH

HOTKEY_LABELS = {
    "lookup": "快捷查词",
    "translate": "翻译选中内容",
    "capture": "立即取词",
    "toggle_auto": "取词开关",
    "add_vocab": "收藏当前词",
    "quit": "退出程序",
}

# 取词方式：按住修饰键取词（默认，读文章时不会乱弹）/ 鼠标停住就取词 / 只用快捷键
CAPTURE_MODES = ("modifier", "hover", "off")
CAPTURE_MODIFIERS = ("shift", "ctrl", "alt")

DEFAULT_CONFIG: dict = {
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
    "capture_mode": "modifier",
    "capture_modifier": "shift",
    "hover_delay_ms": 450,
    "capture_only_english": True,
    "highlight_word": True,          # 取词时在屏幕上标出取到的那个词
    "selection_button": True,        # 选中文字后在旁边出一个「译」按钮
    "online_translate": True,
    "baidu_appid": "",
    "baidu_key": "",
    "accent": "us",
    "auto_speak": False,
    "font_scale": 1.0,
    "theme": "system",               # system / light / dark
    "autostart": True,
    "hide_tray_icon": False,
    "debug": False,
}


def _merge(base: dict, user: dict) -> dict:
    for k, v in user.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            base[k].update(v)
        else:
            base[k] = v
    return base


def migrate(user: dict) -> dict:
    """旧版配置字段换成新字段（auto_capture + capture_modifier → capture_mode）。"""
    user = dict(user)
    if "capture_mode" not in user and ("auto_capture" in user or "capture_modifier" in user):
        mod = user.get("capture_modifier") or ""
        if user.get("auto_capture") is False:
            user["capture_mode"] = "off"
        elif mod:
            user["capture_mode"] = "modifier"
        else:
            user["capture_mode"] = "hover"
        if not mod:
            user.pop("capture_modifier", None)
    for gone in ("auto_capture", "render_mode"):
        user.pop(gone, None)
    return user


class Config(dict):
    def __init__(self, path: Path = CONFIG_PATH, log=print):
        super().__init__(json.loads(json.dumps(DEFAULT_CONFIG)))
        self.path = Path(path)
        self._log = log
        self._lock = threading.Lock()
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            self.save()
            return
        try:
            user = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(user, dict):
                raise ValueError("不是 JSON 对象")
            _merge(self, migrate(user))
        except Exception as e:
            # 不要直接覆盖：先把坏掉的配置留个备份，方便找回
            backup = self.path.with_name("config.broken.json")
            self._log(f"[配置] 读取失败，改用默认，原文件备份为 {backup.name}: {e!r}")
            try:
                self.path.replace(backup)
            except Exception:
                pass
            self.save()

    def save(self) -> None:
        with self._lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.path.with_suffix(".tmp")
                tmp.write_text(json.dumps(dict(self), ensure_ascii=False, indent=2), encoding="utf-8")
                tmp.replace(self.path)
            except Exception as e:
                self._log(f"[配置] 保存失败: {e!r}")


def hotkey_text(spec: str) -> str:
    """ctrl+alt+d → Ctrl+Alt+D"""
    if not spec:
        return "未设置"
    names = {"ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "win": "Win"}
    return "+".join(names.get(p, p.upper()) for p in spec.split("+"))


def _clamp(v, lo, hi, cast=float, default=None):
    try:
        return max(lo, min(hi, cast(v)))
    except Exception:
        return default


def apply_settings(cfg: dict, payload: dict, valid_hotkey) -> tuple[dict, str | None]:
    """把设置窗口提交的内容校验后写进 cfg。

    valid_hotkey(spec) -> bool。返回 (变化了的大类, 错误信息)；有错误时 cfg 不改。
    """
    payload = payload or {}
    changes = {}
    incoming = {k: (v or "").lower() for k, v in (payload.get("hotkeys") or {}).items()
                if k in HOTKEY_LABELS}
    for action, spec in incoming.items():
        if spec and not valid_hotkey(spec):
            return {}, f"「{HOTKEY_LABELS[action]}」的快捷键不合法：至少要 Ctrl / Alt / Win 之一加一个按键"
    merged = dict(cfg.get("hotkeys", {}), **incoming)  # 只改了一个键时，也要和其余的比有没有重复
    seen: dict[str, str] = {}
    for action, spec in merged.items():
        if not spec:
            continue
        key = "+".join(sorted(spec.split("+")))
        if key in seen:
            first, second = (seen[key], action) if action in incoming else (action, seen[key])
            return {}, f"「{HOTKEY_LABELS.get(second, second)}」和「{HOTKEY_LABELS.get(first, first)}」用了同一个快捷键"
        seen[key] = action

    if incoming and incoming != {k: cfg["hotkeys"].get(k, "") for k in incoming}:
        cfg["hotkeys"].update(incoming)
        changes["hotkeys"] = True

    if "dictionaries" in payload:
        cleaned, paths = [], set()
        for d in payload.get("dictionaries") or []:
            path = str((d or {}).get("path", "")).strip()
            if not path or path in paths:
                continue
            paths.add(path)
            cleaned.append({"path": path, "name": (d or {}).get("name") or Path(path).stem})
        if cleaned != cfg.get("dictionaries"):
            cfg["dictionaries"] = cleaned
            changes["dictionaries"] = True

    for key in ("capture_only_english", "highlight_word", "selection_button", "online_translate",
                "auto_speak", "hide_tray_icon", "autostart"):
        if key in payload and payload[key] is not None and bool(payload[key]) != cfg.get(key):
            cfg[key] = bool(payload[key])
            changes[key] = True
    if payload.get("capture_mode") in CAPTURE_MODES:
        cfg["capture_mode"] = payload["capture_mode"]
    if payload.get("capture_modifier") in CAPTURE_MODIFIERS:
        cfg["capture_modifier"] = payload["capture_modifier"]
    if "hover_delay_ms" in payload:
        v = _clamp(payload["hover_delay_ms"], 200, 2000, int)
        if v is not None:
            cfg["hover_delay_ms"] = v
    if "font_scale" in payload:
        v = _clamp(payload["font_scale"], 0.8, 1.6)
        if v is not None:
            cfg["font_scale"] = round(v, 2)
    if payload.get("accent") in ("us", "uk"):
        cfg["accent"] = payload["accent"]
    if payload.get("theme") in ("system", "light", "dark"):
        cfg["theme"] = payload["theme"]
    for key in ("baidu_appid", "baidu_key"):
        if key in payload:
            v = str(payload[key] or "").strip()
            if v != cfg.get(key):
                cfg[key] = v
                changes["translate"] = True
    return changes, None
