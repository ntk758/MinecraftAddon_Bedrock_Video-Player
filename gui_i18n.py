"""GUI の多言語対応。翻訳は locales/<言語コード>.json に置く (キーは en.json が基準)。"""
from __future__ import annotations

import json
import locale
import os
import sys
from pathlib import Path

LOCALE_DIR = Path(__file__).resolve().parent / "locales"
FALLBACK_LANGUAGE = "en"
# 言語選択欄での並び順
LANGUAGE_ORDER = ("ja", "en", "zh-CN", "zh-TW", "ko", "es", "pt-BR", "fr", "de", "ru")


def load_locales(locale_dir: Path = LOCALE_DIR) -> dict[str, dict[str, str]]:
    locales = {}
    for path in sorted(locale_dir.glob("*.json")):
        locales[path.stem] = json.loads(path.read_text(encoding="utf-8"))
    return locales


LOCALES = load_locales()


def available_languages() -> list[tuple[str, str]]:
    """[(言語コード, その言語での表示名), ...]"""
    codes = [c for c in LANGUAGE_ORDER if c in LOCALES] + sorted(c for c in LOCALES if c not in LANGUAGE_ORDER)
    return [(code, LOCALES[code].get("_language_name", code)) for code in codes]


class Translator:
    def __init__(self, language: str) -> None:
        self.language = language if language in LOCALES else FALLBACK_LANGUAGE

    def __call__(self, key: str, **kwargs) -> str:
        text = LOCALES.get(self.language, {}).get(key)
        if text is None:
            text = LOCALES.get(FALLBACK_LANGUAGE, {}).get(key, key)
        return text.format(**kwargs) if kwargs else text


def language_from_locale_name(name: str | None) -> str:
    """'ja_JP' / 'zh-Hant-TW' / 'Japanese_Japan' などから対応言語コードを返す。"""
    if not name:
        return FALLBACK_LANGUAGE
    normalized = name.replace("-", "_").lower()
    if normalized.startswith(("zh_tw", "zh_hk", "zh_mo", "zh_hant", "chinese (traditional)")):
        return "zh-TW"
    if normalized.startswith(("zh", "chinese")):
        return "zh-CN"
    if normalized.startswith(("pt", "portuguese")):
        return "pt-BR"
    aliases = {
        "japanese": "ja", "english": "en", "korean": "ko", "spanish": "es",
        "french": "fr", "german": "de", "russian": "ru",
    }
    for word, code in aliases.items():
        if normalized.startswith(word):
            return code
    base = normalized.split("_")[0]
    return base if base in LOCALES else FALLBACK_LANGUAGE


def detect_system_language() -> str:
    """OS の表示言語から対応言語を選ぶ (対応外なら英語)。"""
    if sys.platform == "win32":
        try:
            import ctypes
            lang_id = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            name = locale.windows_locale.get(lang_id)
            if name:
                return language_from_locale_name(name)
        except Exception:
            pass
    for env in ("LC_ALL", "LC_MESSAGES", "LANG"):
        if os.environ.get(env):
            return language_from_locale_name(os.environ[env])
    try:
        return language_from_locale_name(locale.getlocale()[0])
    except Exception:
        return FALLBACK_LANGUAGE


def settings_path() -> Path:
    if sys.platform == "win32" and os.environ.get("APPDATA"):
        base = Path(os.environ["APPDATA"])
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "BlockVideoPlayer" / "settings.json"


def load_settings(path: Path | None = None) -> dict:
    try:
        return json.loads((path or settings_path()).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_settings(settings: dict, path: Path | None = None) -> None:
    path = path or settings_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass  # 保存できなくても GUI は動かす


def initial_language(settings: dict | None = None) -> str:
    """保存済みの言語 → OS の言語 → 英語 の順に決める。"""
    saved = (settings if settings is not None else load_settings()).get("language")
    if saved in LOCALES:
        return saved
    return detect_system_language()
