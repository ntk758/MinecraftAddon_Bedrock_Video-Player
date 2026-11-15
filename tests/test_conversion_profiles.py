"""convert.py の --profile (TODO.md の P2-6): 既定値の組を conversion_profiles.py の 1 か所で定義する。

- --profile を付けないときは legacy で、今までの出力を変えない。
- --profile recommended は、Windows 版の画面の既定 (build_converter_args) と同じ設定になり、同じ出力になる。
- 個別の引数はプロファイルより優先する。
"""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import conversion_profiles  # noqa: E402
import convert  # noqa: E402

FFMPEG = shutil.which("ffmpeg")
SOURCE = ["--input-video", "clip.mp4", "--output", "out.js"]


def namespace(argv):
    return vars(convert.parse_args(argv))


def without(values, *keys):
    return {k: v for k, v in values.items() if k not in keys}


def test_no_profile_is_legacy_and_keeps_the_old_defaults():
    plain = namespace(SOURCE)
    assert plain["profile"] == "legacy"
    assert without(plain, "profile") == without(namespace([*SOURCE, "--profile", "legacy"]), "profile")
    assert (plain["width"], plain["height"], plain["fps"], plain["palette"], plain["dither_method"]) == (64, 64, 20.0, "full", "none")


def test_explicit_arguments_win_over_the_profile():
    args = namespace([*SOURCE, "--profile", "recommended", "--width", "32", "--palette", "concrete"])
    assert (args["width"], args["height"], args["palette"], args["dither_method"]) == (32, 128, "concrete", "blue_noise")


def gui_args(video, output):
    """Windows 版の画面の既定で convert.py に渡す引数 (起動コマンドを除く)。"""
    pytest.importorskip("tkinter")
    import video_player_gui as gui

    width, height, fps = gui.QUALITY_PRESETS[gui.DEFAULT_PRESET]
    return gui.build_converter_args(Path(video), Path(output), "ffmpeg", width, height, fps, gui.DEFAULT_PALETTE,
                                    gui.DEFAULT_DITHER, 30, "auto", True, None, "en")


def test_recommended_parses_to_the_same_settings_as_the_windows_app():
    from_gui = namespace(gui_args("clip.mp4", "out.js"))
    from_profile = namespace([*SOURCE, "--ffmpeg", "ffmpeg", "--lang", "en", "--mode", "screen", "--profile", "recommended"])
    assert without(from_gui, "profile") == without(from_profile, "profile")


def run_convert(tmp_path, name, argv):
    out = tmp_path / f"{name}.js"
    assert convert.main([*argv, "--output", str(out), "--threads", "1", "--device", "cpu"]) == 0
    return out.read_bytes()


@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg が必要")
def test_outputs_match_byte_for_byte(tmp_path):
    """同じ短い映像で: 画面の既定の引数と --profile recommended、引数なしと --profile legacy の出力が同じ。"""
    video = tmp_path / "clip.mp4"
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=160x90:r=10:d=1", "-pix_fmt", "yuv420p",
                    str(video)], check=True)
    source = ["--input-video", str(video), "--ffmpeg", FFMPEG, "--lang", "en"]
    gui = [a for a in gui_args(video, tmp_path / "ignored.js")]
    gui = gui[:gui.index("--output")] + gui[gui.index("--output") + 2:]   # 出力先は run_convert が決める
    gui[gui.index("--ffmpeg") + 1] = FFMPEG
    gui = [a for i, a in enumerate(gui) if not (a == "--device" or (i > 0 and gui[i - 1] == "--device"))]
    assert run_convert(tmp_path, "gui", gui) == run_convert(tmp_path, "recommended",
                                                           [*source, "--mode", "screen", "--profile", "recommended"])
    assert run_convert(tmp_path, "plain", source) == run_convert(tmp_path, "legacy", [*source, "--profile", "legacy"])


def test_the_tables_are_the_single_source():
    import importlib
    gui = pytest.importorskip("tkinter") and importlib.import_module("video_player_gui")
    assert gui.QUALITY_PRESETS == conversion_profiles.QUALITY_PRESETS
    assert gui.DEFAULT_PALETTE == conversion_profiles.PROFILES["recommended"]["palette"]
    assert gui.DEFAULT_DITHER == conversion_profiles.PROFILES["recommended"]["dither_method"]
    assert (convert.WIDTH, convert.HEIGHT) == (64, 64)
