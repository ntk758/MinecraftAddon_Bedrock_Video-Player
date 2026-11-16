"""ブラウザ版の画面 (web/index.html・web/ui.js) を作り直しても、変換の処理が使う要素が消えないことのテスト。

- app.js・preview.js・camera.js・mmd.js・usage.js・ui.js が $("id") で探す要素が、index.html にちょうど 1 つずつあること
- スクリプトの読み込み順 (ui.js は app.js より前。app.js が UI.* を呼ぶ)
- 翻訳のキーを入れる要素に子の要素を入れない (i18n.js が textContent で中身を置き換えるので、アイコンが消える)
- 外部のサーバーに何かを送る仕組みや外部の読み込み (フォント・CDN) を足していないこと (動画はブラウザの外に出さない)
- ui.js の小さな関数 (大きさ・時間・プリセットの名前の分け方) を Node で確かめる
"""
import json
import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
HTML = (WEB / "index.html").read_text(encoding="utf-8")


class _Collector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []
        self.stack = []  # (tag, has_i18n)
        self.i18n_with_children = []
        self.scripts = []
        self.links = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            self.ids.append(attrs["id"])
        if self.stack and self.stack[-1][1] and tag not in ("br",):
            self.i18n_with_children.append(self.stack[-1][2])
        if tag == "script" and attrs.get("src"):
            self.scripts.append(attrs["src"])
        if tag == "link":
            self.links.append(attrs.get("href", ""))
        if tag not in ("input", "meta", "link", "br", "img", "source", "path", "circle", "rect"):
            self.stack.append((tag, "data-i18n" in attrs, attrs.get("data-i18n")))

    def handle_startendtag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            self.ids.append(attrs["id"])
        if self.stack and self.stack[-1][1]:
            self.i18n_with_children.append(self.stack[-1][2])

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break


@pytest.fixture(scope="module")
def page():
    collector = _Collector()
    collector.feed(HTML)
    return collector


def test_every_element_the_scripts_look_for_is_on_the_page(page):
    wanted = set()
    for name in ("app.js", "preview.js", "camera.js", "mmd.js", "usage.js", "ui.js"):
        wanted |= set(re.findall(r'\$\("([A-Za-z][\w-]*)"\)', (WEB / name).read_text(encoding="utf-8")))
    assert len(wanted) > 60
    missing = sorted(wanted - set(page.ids))
    assert not missing, f"index.html に無い要素: {missing}"
    duplicated = sorted({i for i in page.ids if page.ids.count(i) > 1})
    assert not duplicated, f"同じ id が 2 つ以上: {duplicated}"


def test_scripts_load_in_order(page):
    order = [s for s in page.scripts]
    assert order.index("ui.js") < order.index("app.js")
    assert order.index("i18n.js") < order.index("ui.js")
    assert order.index("preview.js") < order.index("app.js")
    for name in order:
        assert (WEB / name).exists(), name


def test_translated_elements_have_no_child_elements(page):
    assert not page.i18n_with_children, f"data-i18n の中に要素がある (翻訳で消える): {page.i18n_with_children}"


def test_no_external_requests_were_added(page):
    """動画はブラウザの外に出さない。読み込むのは同じサイトのファイルだけ (利用数の GoatCounter は usage.js の決まった道だけ)"""
    for src in page.scripts:
        assert not re.match(r"https?://|//", src), src
    for href in page.links:
        assert not re.match(r"https?://|//", href), href
    ui = (WEB / "ui.js").read_text(encoding="utf-8").replace('"http://www.w3.org/2000/svg"', "")  # SVG の名前空間 (通信ではない)
    assert re.findall(r'fetch\(\s*"([^"]+)"', ui) == ["app/pack_metadata.py", "media/hero_blocks.json", "media/showcase.json"]  # 同じサイトのファイルだけ
    for forbidden in ("XMLHttpRequest", "sendBeacon", "WebSocket", "http://", "https://"):
        assert forbidden not in ui, forbidden
    css = (WEB / "style.css").read_text(encoding="utf-8")
    assert "@import" not in css and "url(http" not in css


def test_settings_keep_their_inputs():
    """設定を種類ごとにまとめ直しても、app.js の settings() が読む入力欄は全部残っている"""
    app = (WEB / "app.js").read_text(encoding="utf-8")
    body = re.search(r"function settings\(\) \{(.*?)\n\}", app, re.S).group(1)
    for key in set(re.findall(r'\$\("(\w+)"\)', body)):
        assert re.search(rf'<(input|select)[^>]*\bid="{key}"', HTML), key


def test_reduced_motion_is_respected():
    css = (WEB / "style.css").read_text(encoding="utf-8")
    block = css[css.index("@media (prefers-reduced-motion: reduce)"):]
    block = block[:block.index("\n}\n")]
    # 位置の動き・続く動きを止め、スクロールで出る中身は最初から見せ、スクロールは瞬時に
    for rule in ("scroll-behavior: auto", "animation-duration: 1ms !important", "transition-duration: 1ms !important",
                 "animation-iteration-count: 1 !important", "transition-delay: 0s !important",
                 ".js .hero-title, .js .hero-side, .js .stage-figure { opacity: 1; }"):
        assert rule in block, rule
    ui = (WEB / "ui.js").read_text(encoding="utf-8")
    assert 'matchMedia("(prefers-reduced-motion: reduce)")' in ui
    # Motion は動きを減らす設定では使わない。JS の動き (なめらかなスクロール・最初の絵の境目・カードを外す) も止める
    assert "const hasMotion = () => Boolean(self.Motion && self.Motion.animate) && !reduced();" in ui
    assert 'if (!reduced()) $("results").scrollIntoView' in ui
    assert "let wanted = !reduced();" in ui  # 舞台の映像は、動きを減らす設定では押すまで再生しない
    # <head> の小さなスクリプトも、動きを減らす設定では最初の画面を隠さない
    assert 'matchMedia("(prefers-reduced-motion: reduce)").matches)) d.classList.add("has-motion")' in HTML
    assert "if (reduced()) { onRemove(); return; }" in ui


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js が無い")
def test_ui_helpers_in_node():
    script = """
      globalThis.I18n = { t: (k) => k, onChange() {} };
      globalThis.window = {};
      const src = require("fs").readFileSync(process.argv[1], "utf8");
      const UI = new Function(src.replace('"use strict";', "") + "; return UI;")();
      console.log(JSON.stringify({
        bytes: [UI.formatBytes(512), UI.formatBytes(2048), UI.formatBytes(5 * 1024 * 1024), UI.formatBytes(-1)],
        time: [UI.formatTime(0), UI.formatTime(65.4), UI.formatTime(NaN)],
        split: [UI.splitLabel("High (128×128 / 10fps)"), UI.splitLabel("高画質（128×128 / 10fps）"), UI.splitLabel("Light")],
      }));
    """
    out = subprocess.run(["node", "-e", script, str(WEB / "ui.js")], capture_output=True, text=True, encoding="utf-8", check=True)
    result = json.loads(out.stdout)
    assert result["bytes"] == ["512 B", "2.0 KB", "5.0 MB", ""]
    assert result["time"] == ["0:00", "1:05", "0:00"]
    assert result["split"] == [["High", "128×128 / 10fps"], ["高画質", "128×128 / 10fps"], ["Light", ""]]


# ---- web/vendor の Motion と Lucide (scripts/vendor_web_libs.mjs が npm の配布物から写す)
VENDOR = WEB / "vendor"


def _vendored_icon_names():
    source = (ROOT / "scripts" / "vendor_web_libs.mjs").read_text(encoding="utf-8")
    block = re.search(r"export const ICONS = \[(.*?)\];", source, re.S).group(1)
    return set(re.findall(r'"([a-z0-9-]+)"', block))


def test_vendored_libraries_match_the_readme():
    import hashlib
    readme = (VENDOR / "README.md").read_text(encoding="utf-8")
    for path in ("motion/motion.min.js", "lucide/icons.js", "fonts/bricolage-grotesque-latin-wght-normal.woff2",
                 "fonts/bricolage-grotesque-latin-ext-wght-normal.woff2"):
        digest = hashlib.sha256((VENDOR / path).read_bytes()).hexdigest()
        assert digest in readme, path
    for license_file in ("motion/LICENSE.md", "lucide/LICENSE", "fonts/LICENSE"):
        assert (VENDOR / license_file).exists(), license_file
    lock = json.loads((ROOT / "package-lock.json").read_text(encoding="utf-8"))
    versions = {name.split("node_modules/")[-1]: info["version"] for name, info in lock["packages"].items() if name}
    assert f"| {versions['motion']}・MIT |" in readme and f"| {versions['lucide']}・ISC |" in readme


def test_icons_used_on_the_page_are_exactly_the_vendored_ones():
    used = set(re.findall(r'data-lucide="([a-z0-9-]+)"', HTML))
    ui = (WEB / "ui.js").read_text(encoding="utf-8")
    for call in re.findall(r"\bicon\(([^()]*)\)", ui):  # icon("x") と icon(kind ? "a" : "b")
        if "className" not in call:  # function icon(name, className = "icon") の定義は除く
            used |= set(re.findall(r'(?<!= )"([a-z0-9-]+)"', call))  # kind === "error" の比べる値は除く
    vendored = _vendored_icon_names()
    icons_js = (VENDOR / "lucide" / "icons.js").read_text(encoding="utf-8")
    shipped = set(json.loads(re.search(r"self\.LUCIDE_ICONS = (\{.*\});", icons_js, re.S).group(1)))
    assert shipped == vendored
    assert not used - vendored, f"web/vendor/lucide に無いアイコン: {sorted(used - vendored)}"
    assert not vendored - used, f"使っていないアイコン (ICONS から外す): {sorted(vendored - used)}"


def test_libraries_load_from_this_site_and_motion_is_optional():
    assert '<script src="vendor/motion/motion.min.js" defer></script>' in HTML
    assert HTML.index('src="vendor/lucide/icons.js"') < HTML.index('src="ui.js"')
    ui = (WEB / "ui.js").read_text(encoding="utf-8")
    assert "const hasMotion = () => Boolean(self.Motion && self.Motion.animate) && !reduced();" in ui
    # React 用の API は使わない
    assert "motion/react" not in ui and "framer-motion" not in ui
    head = (VENDOR / "motion" / "motion.min.js").read_text(encoding="utf-8")[:400]
    assert ".Motion={}" in head  # ブラウザ用 (UMD、グローバルの Motion)


def test_no_emoji_icons_left_in_the_web_strings():
    """アイコンは Lucide で出す。画面の文の頭に絵文字・記号を付けない (language.label は Windows 版と共通)"""
    from gui_i18n import LOCALES
    for language, table in LOCALES.items():
        for key, value in table.items():
            if key.startswith("web."):
                assert not re.match(r"[\U0001F000-\U0001FFFF☀-➿■-◿]", value), (language, key, value)


def test_github_pages_build_ships_the_vendored_libraries(tmp_path):
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    import build_web
    out = build_web.build(tmp_path / "site")
    for path in ("ui.js", "vendor/motion/motion.min.js", "vendor/lucide/icons.js", "vendor/README.md",
                 "vendor/fonts/bricolage-grotesque-latin-wght-normal.woff2", "media/hero.mp4", "media/hero_blocks.json",
                 "media/showcase.json", "media/showcase/128x72_auto_blue_noise.png"):
        assert (out / path).exists(), path


# ---- ページの見本 (scripts/make_web_media.py が実際の convert.py の出力から作る)
MEDIA = WEB / "media"


def test_showcase_pictures_match_the_manifest_and_the_controls():
    from PIL import Image
    items = json.loads((MEDIA / "showcase.json").read_text(encoding="utf-8"))["items"]
    files = {p.name for p in (MEDIA / "showcase").glob("*.png")}
    assert {i["file"] for i in items} == files
    for item in items:
        assert item["file"] == f'{item["width"]}x{item["height"]}_{item["palette"]}_{item["dither"]}.png'
        with Image.open(MEDIA / "showcase" / item["file"]) as im:
            assert im.size == (item["width"], item["height"])  # 1 マス = 1 画素
            assert len(im.convert("RGB").getcolors(100000)) == item["colours"]
        assert item["blocks"] == item["width"] * item["height"]
    # 画面の選択肢の組み合わせが全部ある (選ぶと絵が無い、にならない)
    def values(group_id):
        block = re.search(rf'id="{group_id}"[^>]*>(.*?)</div>', HTML, re.S).group(1)
        return re.findall(r'data-value="([^"]+)"', block)
    for size in values("exSize"):
        for palette in values("exPalette"):
            for dither in values("exDither"):
                assert f"{size}_{palette}_{dither}.png" in files
    # 選択肢の値は、変換ツールの入力欄が受け付ける値
    for select_id, group_id in (("palette", "exPalette"), ("dither", "exDither")):
        options = re.findall(r'<option value="([^"]*)"', re.search(rf'<select id="{select_id}">(.*?)</select>', HTML, re.S).group(1))
        assert set(values(group_id)) <= set(options)


def test_hero_sample_is_real_converter_output_that_decodes():
    """最初の画面のブロックの絵は、変換の出力 (FRAME_DATA) そのもの。参照デコーダで最後まで復元でき、説明の文の設定と合っている"""
    import sys
    sys.path.insert(0, str(ROOT))
    from gui_i18n import LOCALES
    from mvcodec.decode import iter_decoded_frames
    sample = json.loads((MEDIA / "hero_blocks.json").read_text(encoding="utf-8"))
    data, settings, spec = sample["frameData"], sample["settings"], sample["spec"]
    assert (data["width"], data["height"]) == (settings["width"], settings["height"]) == (128, 72)
    frames = [rgb for _i, rgb in iter_decoded_frames(data)]
    assert len(frames) == spec["frame_count"] == data["frame_count"] and spec["fps"] == settings["fps"] == 10
    assert (settings["palette"], settings["dither"]) == ("auto", "blue_noise")
    caption = LOCALES["en"]["web.stage_caption"]
    for text in ("128 × 72", "10 fps", "auto palette", "Blue Noise"):
        assert text in caption
    for name in ("hero.mp4", "hero_poster.jpg", "hero_blocks.png"):
        assert (MEDIA / name).stat().st_size > 1000
    assert sum(p.stat().st_size for p in MEDIA.rglob("*") if p.is_file()) < 400_000  # 最初の表示を重くしない


def test_page_states_only_what_the_code_does():
    """プライバシーの欄に書いたこと (変換の本体を jsDelivr から読む) が、実際の worker.js と合っている"""
    from gui_i18n import LOCALES
    assert "jsDelivr" in LOCALES["en"]["web.privacy_loads_2"]
    assert "cdn.jsdelivr.net/pyodide" in (WEB / "worker.js").read_text(encoding="utf-8")
