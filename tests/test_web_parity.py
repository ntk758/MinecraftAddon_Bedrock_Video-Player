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
    # Windows 版の画面と convert.py の --profile は conversion_profiles.py の表を使う。ブラウザ版の JS の表がそれと同じ
    import conversion_profiles
    presets = conversion_profiles.QUALITY_PRESETS
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


def test_mmd_height_limits_match_the_windows_app():
    source = (ROOT / "video_player_gui.py").read_text(encoding="utf-8")
    gui = {name: int(re.search(rf"^{name} = (\d+)", source, re.M).group(1)) for name in (
        "MMD_MAX_HEIGHT", "MMD_STAGE_MAX_HEIGHT", "MMD_EXTREME_MAX_HEIGHT", "MMD_EXTREME_STAGE_MAX_HEIGHT")}
    mmd = (WEB / "mmd.js").read_text(encoding="utf-8")
    limits = dict(re.findall(r"(\w+): (\d+)", re.search(r"const LIMITS = \{([^}]*)\}", mmd).group(1)))
    assert {k: int(v) for k, v in limits.items()} == {
        "normal": gui["MMD_MAX_HEIGHT"], "stage": gui["MMD_STAGE_MAX_HEIGHT"],
        "extreme": gui["MMD_EXTREME_MAX_HEIGHT"], "extremeStage": gui["MMD_EXTREME_STAGE_MAX_HEIGHT"]}


def test_web_defaults_are_the_recommended_profile():
    """ブラウザ版の画面の既定 (プリセット・パレット・ディザ) が、convert.py --profile recommended と同じ。"""
    import conversion_profiles

    def selected(select_id):
        block = re.search(rf'<select id="{select_id}">(.*?)</select>', HTML, re.S).group(1)
        # selected の付いた選択肢。無ければ最初の選択肢 (ブラウザの既定)
        chosen = re.search(r'<option value="([^"]*)"[^>]*selected', block)
        return chosen.group(1) if chosen else re.search(r'<option value="([^"]*)"', block).group(1)

    assert selected("preset") == conversion_profiles.DEFAULT_PRESET
    assert selected("palette") == conversion_profiles.PROFILES["recommended"]["palette"]
    assert selected("dither") == conversion_profiles.PROFILES["recommended"]["dither_method"]


def test_flat_areas_choice_is_the_same_in_both_apps_and_accepted_by_convert():
    """「平らな面のムラを減らす」で渡す --flat-areas の値が、Windows 版とブラウザ版で同じで、convert.py が受け付ける (TODO.md の P2-1)。"""
    from mvcodec.color import FLAT_AREA_CHOICES

    gui = re.search(r'^FLAT_AREAS_CHOICE = "(\w+)"', (ROOT / "video_player_gui.py").read_text(encoding="utf-8"), re.M).group(1)
    worker = (WEB / "worker.js").read_text(encoding="utf-8")
    assert re.search(r'const FLAT_AREAS_CHOICE = "(\w+)";', worker).group(1) == gui
    assert gui in FLAT_AREA_CHOICES and gui != "off"
    assert 'id="flatAreas"' in HTML and "s.flatAreas" in worker
