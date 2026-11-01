# -*- coding: utf-8 -*-
"""配置迁移、设置校验、生词本与历史。"""
import json

from glance.config import Config, apply_settings, migrate
from glance.store import History, Vocab


def _valid(spec):
    parts = spec.split("+")
    return len(parts) >= 2 and any(p in ("ctrl", "alt", "win") for p in parts)


def test_migrate_old_capture_settings():
    assert migrate({"auto_capture": True, "capture_modifier": ""})["capture_mode"] == "hover"
    assert migrate({"auto_capture": True, "capture_modifier": "ctrl"}) == \
        {"capture_mode": "modifier", "capture_modifier": "ctrl"}
    assert migrate({"auto_capture": False})["capture_mode"] == "off"
    assert "render_mode" not in migrate({"render_mode": "rich"})


def test_config_roundtrip_and_broken_file(tmp_path):
    path = tmp_path / "config.json"
    c = Config(path, log=lambda m: None)
    assert path.exists() and c["capture_mode"] == "modifier"
    c["accent"] = "uk"
    c.save()
    assert Config(path, log=lambda m: None)["accent"] == "uk"
    path.write_text("{坏掉的", encoding="utf-8")
    c = Config(path, log=lambda m: None)
    assert c["accent"] == "us" and (tmp_path / "config.broken.json").exists()


def test_config_keeps_new_default_hotkeys(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"hotkeys": {"lookup": "ctrl+alt+l"}}), encoding="utf-8")
    c = Config(path, log=lambda m: None)
    assert c["hotkeys"]["lookup"] == "ctrl+alt+l" and c["hotkeys"]["translate"] == "ctrl+alt+t"


def test_apply_settings_validates_hotkeys(tmp_path):
    c = Config(tmp_path / "c.json", log=lambda m: None)
    _, err = apply_settings(c, {"hotkeys": {"lookup": "shift+d"}}, _valid)
    assert err and "不合法" in err
    _, err = apply_settings(c, {"hotkeys": {"lookup": "ctrl+alt+t"}}, _valid)  # 和「翻译」重复
    assert err and "同一个快捷键" in err
    assert c["hotkeys"]["lookup"] == "ctrl+alt+d"
    changes, err = apply_settings(c, {"hotkeys": {"lookup": "ctrl+alt+l"}, "font_scale": 9,
                                      "capture_mode": "hover", "accent": "xx"}, _valid)
    assert not err and changes.get("hotkeys")
    assert c["font_scale"] == 1.6 and c["capture_mode"] == "hover" and c["accent"] == "us"


def test_apply_settings_dictionaries(tmp_path):
    c = Config(tmp_path / "c.json", log=lambda m: None)
    changes, _ = apply_settings(c, {"dictionaries": [{"path": "a.mdx"}, {"path": "a.mdx"}, {"path": ""}]}, _valid)
    assert changes == {"dictionaries": True} and c["dictionaries"] == [{"path": "a.mdx", "name": "a"}]


def test_vocab_and_history(tmp_path):
    v = Vocab(tmp_path / "v.json", log=lambda m: None)
    assert v.add("Abandon", "v. 放弃")
    assert not v.add("abandon")
    assert v.has("ABANDON")
    assert v.toggle("abandon") is False and not v.has("abandon")
    v.add("hello")
    assert v.export_csv(tmp_path / "out").endswith(".csv")
    anki = v.export_anki(tmp_path / "out", lambda w: "<b>你好</b>")
    assert "hello\t<b>你好</b>\t一瞥" in open(anki, encoding="utf-8").read()
    h = History(tmp_path / "h.json", log=lambda m: None)
    for w in ["a1", "b2", "A1"]:
        h.add(w)
    assert h.recent() == ["A1", "b2"]


def test_migrate_renamed_data(tmp_path):
    from glance.paths import migrate_renamed_data
    old, new = tmp_path / "WarmWord", tmp_path / "Glance"
    (old / "词库").mkdir(parents=True)
    (old / "生词本.json").write_text('[{"word": "abandon"}]', encoding="utf-8")
    (old / "config.json").write_text(json.dumps({"dictionaries": [
        {"path": str(old / "词库" / "ecdict.csv")}, {"path": "D:\\词典\\oald.mdx"}]}), encoding="utf-8")
    assert migrate_renamed_data(old, new, log=lambda m: None)
    assert (new / "生词本.json").exists() and (old / "生词本.json").exists()
    paths = [d["path"] for d in json.loads((new / "config.json").read_text(encoding="utf-8"))["dictionaries"]]
    assert paths == [str(new / "词库" / "ecdict.csv"), "D:\\词典\\oald.mdx"]
    assert not migrate_renamed_data(old, new, log=lambda m: None)  # 新目录已存在：不再覆盖
