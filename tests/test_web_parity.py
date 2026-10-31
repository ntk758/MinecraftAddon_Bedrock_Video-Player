"""ブラウザ版 (web/) の設定が Windows 版と食い違わないことのテスト。

画質プリセットの値・パレットとディザの選択肢が、Windows 版の GUI と convert.py が受け付ける値と一致すること。
"""
import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import convert  # noqa: E402

WEB = ROOT / "web"
HTML = (WEB / "index.html").read_text(encoding="utf-8")
APP = (WEB / "app.js").read_text(encoding="utf-8")


def select_values(select_id):
    block = re.search(rf'<select id="{select_id}">(.*?)</select>', HTML, re.S).group(1)
    return [v for v in re.findall(r'<option value="([^"]*)"', block) if v]


def gui_constant(name):
    """video_player_gui.py の定数を、tkinter を読み込まずに取り出す。"""
    source = (ROOT / "video_player_gui.py").read_text(encoding="utf-8")
    match = re.search(rf"^{name}(?::[^=]+)? = (\{{.*?^\}})", source, re.S | re.M)
    return ast.literal_eval(match.group(1))


def test_quality_presets_match_the_windows_app():
    presets = gui_constant("QUALITY_PRESETS")
    block = re.search(r"const QUALITY_PRESETS = (\{.*?\});", APP, re.S).group(1)
    web = json.loads(re.sub(r",\s*\}", "}", re.sub(r"(\w+):", r'"\1":', block)))
    assert web == {key: list(value) for key, value in presets.items()}
    assert select_values("preset") == list(presets)


def test_palette_and_dither_choices_are_accepted_by_convert():
    parser_source = (ROOT / "convert.py").read_text(encoding="utf-8")
    dithers = re.search(r'"--dither-method".*?choices=\[([^\]]*)\]', parser_source).group(1)
    assert set(select_values("dither")) == set(re.findall(r'"(\w+)"', dithers))
    assert set(select_values("palette")) <= {*convert.PALETTES, "auto"}
    assert "expanded" in select_values("palette")


def test_web_passes_the_same_quality_options_as_the_windows_app():
    worker = (WEB / "worker.js").read_text(encoding="utf-8")
    assert '"--keyframe-interval", String(s.keyframe)' in worker
    assert '"--no-perceptual"' in worker
