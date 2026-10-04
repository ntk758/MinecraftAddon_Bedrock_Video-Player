"""GUI の多言語対応 (locales/*.json と言語切替) のテスト。"""
import os
import re
import string
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gui_i18n  # noqa: E402
from gui_i18n import LOCALES, Translator, available_languages, language_from_locale_name  # noqa: E402

BASE = LOCALES["en"]
EXPECTED_LANGUAGES = {"ja", "en", "zh-CN", "zh-TW", "ko", "es", "pt-BR", "fr", "de", "ru"}


def placeholders(text):
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


def test_all_major_languages_present():
    assert EXPECTED_LANGUAGES <= set(LOCALES)
    assert [code for code, _ in available_languages()][:2] == ["ja", "en"]


@pytest.mark.parametrize("language", sorted(LOCALES))
def test_locale_has_same_keys_and_placeholders_as_english(language):
    data = LOCALES[language]
    assert set(data) == set(BASE), f"{language}: 不足 {set(BASE) - set(data)}, 余分 {set(data) - set(BASE)}"
    for key, text in data.items():
        assert text.strip(), f"{language}:{key} が空です"
        assert placeholders(text) == placeholders(BASE[key]), f"{language}:{key} のプレースホルダーが英語版と違います"


@pytest.mark.parametrize("language", sorted(LOCALES))
def test_option_labels_are_unique_within_a_language(language):
    """表示名から値を逆引きするため、同じ種類の選択肢で表示名が重複してはいけない"""
    data = LOCALES[language]
    for kind in ("palette", "dither", "device", "preset"):
        labels = [v for k, v in data.items() if k.startswith(kind + ".")]
        assert len(labels) == len(set(labels)), f"{language}:{kind} の表示名が重複しています"


def test_translator_formats_and_falls_back():
    assert Translator("ja")("info.version", version="1.2.3") == "パック版 v1.2.3"
    assert Translator("xx").language == "en"
    assert Translator("ja")("no.such.key") == "no.such.key"


@pytest.mark.parametrize("name, expected", [
    ("ja_JP", "ja"), ("Japanese_Japan", "ja"), ("en_US", "en"), ("zh_CN", "zh-CN"), ("zh-Hans-CN", "zh-CN"),
    ("zh_TW", "zh-TW"), ("zh_HK", "zh-TW"), ("ko_KR", "ko"), ("es_MX", "es"), ("pt_PT", "pt-BR"),
    ("fr_CA", "fr"), ("de_AT", "de"), ("ru_RU", "ru"), ("it_IT", "en"), (None, "en"),
])
def test_language_from_locale_name(name, expected):
    assert language_from_locale_name(name) == expected


def test_settings_roundtrip(tmp_path):
    path = tmp_path / "BlockVideoPlayer" / "settings.json"
    gui_i18n.save_settings({"language": "ko"}, path)
    assert gui_i18n.load_settings(path) == {"language": "ko"}
    assert gui_i18n.initial_language({"language": "ko"}) == "ko"
    assert gui_i18n.load_settings(tmp_path / "missing.json") == {}


@pytest.fixture
def app(monkeypatch, tmp_path):
    tk = pytest.importorskip("tkinter")
    monkeypatch.setattr(gui_i18n, "settings_path", lambda: tmp_path / "settings.json")
    import video_player_gui

    monkeypatch.setattr(video_player_gui, "load_settings", lambda: {})
    monkeypatch.setattr(video_player_gui, "save_settings", lambda settings: None)
    try:
        instance = video_player_gui.PackBuilderApp(language="ja")
    except tk.TclError:
        pytest.skip("ディスプレイが無い環境")
    instance.withdraw()
    yield instance
    instance.destroy()


def test_language_switch_keeps_user_input(app):
    app.video_tree.insert("", "end", values=("clip", "C:/videos/clip.mp4", "3"))
    app.palette_var.set(app.tr("palette.full"))
    app.dither_var.set(app.tr("dither.atkinson"))
    app.device_var.set(app.tr("device.directml"))
    app.quality_var.set(app.tr("preset.ultra"))
    app._apply_quality_preset()
    app._append_log("hello log")

    for code, _name in available_languages():
        app.change_language(code)
        assert app.tr.language == code
        assert app.title() == app.tr("app.title")
        assert app._selected_values() == ("full", "atkinson", "directml")
        assert app.quality_var.get() == app.tr("preset.ultra")
        assert (app.width_var.get(), app.height_var.get()) == (256, 256)
        rows = [app.video_tree.item(i)["values"] for i in app.video_tree.get_children()]
        assert [str(v) for v in rows[0]] == ["clip", "C:/videos/clip.mp4", "3"]
        assert "hello log" in app.log.get("1.0", "end")
        assert app.build_button.cget("text") == app.tr("build.button")
