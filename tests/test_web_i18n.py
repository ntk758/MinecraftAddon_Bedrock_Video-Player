"""ブラウザ版 (web/) の多言語対応と、簡易的な利用数の計測のテスト。

- 画面 (index.html) と app.js・worker.js が使う翻訳のキーが、locales/en.json にあること (全言語にそろっていることと
  プレースホルダーは tests/test_i18n.py が確かめる)
- 言語の決め方が Windows 版の GUI (gui_i18n) と同じこと
- 利用数は、計測先が設定され、公開したサイト (https) で、Do Not Track・送信の停止・自動テストでないときだけ送ること
- scripts/build_web.py が config.js に GoatCounter のコードを書き、おかしなコードを拒むこと
"""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import build_web  # noqa: E402
from gui_i18n import LANGUAGE_ORDER, LOCALES, language_from_locale_name  # noqa: E402

WEB = ROOT / "web"
EN = LOCALES["en"]
KEY_PREFIXES = ("web", "load", "settings", "mode", "palette", "pack", "build", "language")


def used_keys():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    keys = set(re.findall(r'data-i18n(?:-title|-placeholder)?="([^"]+)"', html))
    prefixes = "|".join(KEY_PREFIXES)
    for name in ("app.js", "worker.js"):
        source = (WEB / name).read_text(encoding="utf-8")
        keys |= set(re.findall(rf'"((?:{prefixes})\.[a-z0-9_.]+)"', source))
    return keys


def test_every_key_the_page_uses_is_translated():
    keys = used_keys()
    assert len(keys) > 30  # キーを拾えていること
    missing = sorted(k for k in keys if k not in EN)
    assert not missing, f"locales/en.json に無いキー: {missing}"


def test_every_web_key_is_used():
    """使わなくなった web.* のキーを残さない"""
    unused = sorted(k for k in EN if k.startswith("web.") and k not in used_keys())
    assert not unused, f"どこからも使われていない web.* のキー: {unused}"


def test_page_text_is_not_left_untranslated():
    """画面に出る文は、日本語と英語を並べて書かず、翻訳のキーから入れる"""
    for name in ("index.html", "app.js", "worker.js"):
        source = (WEB / name).read_text(encoding="utf-8")
        code = re.sub(r"/\*.*?\*/|<!--.*?-->", "", source, flags=re.S)
        code = re.sub(r"(^|[^:\"'`])//.*", r"\1", code)  # 行末のコメント (URL の // は残す)
        code = re.sub(r"(?m)^\s*#.*$", "", code)  # worker.js の中の Python のコメント
        japanese = [line.strip() for line in code.splitlines() if re.search(r"[ぁ-んァ-ヶ]", line)]
        assert not japanese, f"{name}: 翻訳を通さない日本語の文: {japanese[:3]}"


@pytest.mark.parametrize("language", sorted(LOCALES))
def test_howto_keeps_both_commands(language):
    text = LOCALES[language]["web.howto_setup"]
    assert "{setup}" in text and "{start}" in text


def test_build_writes_goatcounter_code(tmp_path):
    out = build_web.build(tmp_path / "site", "my-site")
    assert 'self.BVP_CONFIG = {"goatcounter": "my-site"};' in (out / "config.js").read_text(encoding="utf-8")
    off = build_web.build(tmp_path / "off")
    assert 'self.BVP_CONFIG = {"goatcounter": ""};' in (off / "config.js").read_text(encoding="utf-8")
    for name in ("i18n.js", "usage.js", "config.js"):
        assert (out / name).exists()
    assert (out / "app" / "locales" / "ko.json").exists()


@pytest.mark.parametrize("code", ['a"; alert(1)//', "Upper", "-start", "a/b", "a" * 64])
def test_build_rejects_bad_goatcounter_code(tmp_path, code):
    with pytest.raises(ValueError):
        build_web.build(tmp_path / "site", code)
    assert not (tmp_path / "site").exists()


def test_repository_config_does_not_count():
    """リポジトリの web/ をそのまま開いたとき (手元の確認) は数えない"""
    assert 'goatcounter: ""' in (WEB / "config.js").read_text(encoding="utf-8")


PUBLIC = {"code": "my-site", "protocol": "https:", "hostname": "ntk758.github.io", "search": "",
          "webdriver": False, "doNotTrack": None, "globalPrivacyControl": False, "optedOut": False}
ALLOWED_CASES = [
    ({}, True),
    ({"code": ""}, False),
    ({"protocol": "http:"}, False),
    ({"protocol": "file:", "hostname": ""}, False),
    ({"hostname": "localhost"}, False),
    ({"hostname": "127.0.0.1"}, False),
    ({"hostname": "::1"}, False),
    ({"search": "?src=test.mp4"}, False),
    ({"search": "?lang=ko"}, True),
    ({"webdriver": True}, False),
    ({"doNotTrack": "1"}, False),
    ({"doNotTrack": "0"}, True),
    ({"globalPrivacyControl": True}, False),
    ({"optedOut": True}, False),
]
LOCALE_NAMES = ["ja-JP", "en-US", "zh-CN", "zh-Hans-CN", "zh-TW", "zh-HK", "zh-Hant", "ko-KR", "es-MX", "pt-PT",
                "pt-BR", "fr-CA", "de-AT", "ru-RU"]


@pytest.fixture(scope="module")
def node_results():
    if shutil.which("node") is None:
        pytest.skip("node が必要")
    question = {
        "normalize": [*LOCALE_NAMES, "it-IT", "", None],
        "detect": [
            {"search": "?lang=ko", "stored": "de", "languages": ["ja-JP"]},
            {"search": "?lang=xx", "stored": "de", "languages": ["ja-JP"]},
            {"search": "", "stored": None, "languages": ["it-IT", "pt-PT", "ja"]},
            {"search": "", "stored": None, "languages": ["it-IT"]},
            {"search": "", "stored": "zh_TW", "languages": []},
        ],
        "format": [["{a} / {b} / {c}", {"a": 1, "b": "x"}], ["no params", None], ["{n} KB", {"n": 0}]],
        "allowed": [{**PUBLIC, **change} for change, _ in ALLOWED_CASES],
        "countUrl": [["my-site", {"path": "build-done", "event": True}],
                     ["my-site", {"path": "/", "title": "Web Converter (ja)"}]],
        "kindOf": [
            [{"mode": "screen", "anamorphic": False}, False, True],
            [{"mode": "relief", "anamorphic": False}, False, True],
            [{"mode": "screen", "anamorphic": True}, False, True],
            [{"mode": "inflate", "anamorphic": True}, False, True],
            [{"mode": "screen", "anamorphic": False}, True, False],
            [{"mode": "screen", "anamorphic": True}, True, False],
        ],
    }
    result = subprocess.run(["node", str(ROOT / "tests" / "js" / "web_i18n_usage.mjs"), str(WEB)],
                            input=json.dumps(question), capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_language_order_matches_gui(node_results):
    assert node_results["languages"] == list(LANGUAGE_ORDER)


def test_language_names_map_like_gui(node_results):
    for name, code in zip(LOCALE_NAMES, node_results["normalize"]):
        assert code == language_from_locale_name(name), name
    assert node_results["normalize"][-3:] == [None, None, None]  # 対応しない言語は null (英語にするのは detect)


def test_language_detection_order(node_results):
    assert node_results["detect"] == ["ko", "de", "pt-BR", "en", "zh-TW"]


def test_format_like_python(node_results):
    assert node_results["format"] == ["1 / x / {c}", "no params", "0 KB"]


def test_usage_is_sent_only_when_allowed(node_results):
    assert node_results["allowed"] == [expected for _, expected in ALLOWED_CASES]


def test_usage_url_carries_no_file_details(node_results):
    event, page = node_results["countUrl"]
    assert re.fullmatch(r"https://my-site\.goatcounter\.com/count\?p=build-done&e=true&rnd=[a-z0-9]+", event)
    assert re.fullmatch(r"https://my-site\.goatcounter\.com/count\?p=%2F&t=Web\+Converter\+%28ja%29&rnd=[a-z0-9]+", page)


def test_usage_kind(node_results):
    assert node_results["kindOf"] == ["screen", "3d", "screen", "trompe", "vox", "trompe"]


def test_length_defaults_to_the_whole_video():
    """変換する長さは既定で最後まで (上限なし)。以前は 60 秒で何も言わずに切れていた"""
    html = (WEB / "index.html").read_text(encoding="utf-8")
    field = re.search(r'<input[^>]*id="duration"[^>]*>', html).group(0)
    assert "value=" not in field and "max=" not in field
    app = (WEB / "app.js").read_text(encoding="utf-8")
    assert "Math.min(video.duration, durationLimit(s))" in app
