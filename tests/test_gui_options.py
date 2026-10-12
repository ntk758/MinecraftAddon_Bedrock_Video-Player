import sys
import os

# プロジェクトルートをパスに追加
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from pathlib import Path

import convert
from video_player_gui import (
    DEVICE_OPTIONS,
    DITHER_OPTIONS,
    PALETTE_OPTIONS,
    build_converter_args,
    build_manifest_version,
    resolve_device,
    dither_after_mode_change,
    resolve_dither,
    resolve_palette,
    stable_pack_uuid,
)
from pack_metadata import PACK_VERSION


def test_dither_options_all_7():
    """全ディザ選択肢(7種)の判定テスト"""
    expected = {
        "なし (最速 / GPU対応)": "none",
        "Blue Noise (超高画質 / GPU対応)": "blue_noise",
        "Ordered (Bayer / GPU対応)": "ordered",
        "Floyd-Steinberg (高画質 / CPU専用)": "floyd",
        "Atkinson (高画質 / CPU専用)": "atkinson",
        "Burkes (高画質 / CPU専用)": "burkes",
        "Sierra Lite (高画質 / CPU専用)": "sierra",
    }

    assert len(expected) == 7, "7種類のディザ選択肢が定義されていること"
    assert set(DITHER_OPTIONS.keys()) == set(expected.keys()), "DITHER_OPTIONS のキーが一致すること"

    for text, expected_value in expected.items():
        actual = resolve_dither(text)
        assert actual == expected_value, (
            f"resolve_dither('{text}') returned '{actual}', expected '{expected_value}'"
        )
        print(f"PASS: dither '{text}' -> '{actual}'")


def test_dither_prefix_matching():
    """ディザの startswith 判定動作テスト"""
    assert resolve_dither("Floyd-Steinberg") == "floyd"
    assert resolve_dither("Atkinson") == "atkinson"
    assert resolve_dither("Burkes") == "burkes"
    assert resolve_dither("Sierra Lite") == "sierra"
    assert resolve_dither("Ordered (Bayer)") == "ordered"
    assert resolve_dither("Blue Noise") == "blue_noise"
    assert resolve_dither("Unknown") == "none"
    print("PASS: dither prefix matching")


def test_palette_options():
    """パレット選択肢の判定テスト"""
    expected = {
        "自動 (おすすめ: 101 色の候補からシーンごとに最大 64 色)": "auto",
        "全ブロック 108 色 (コンクリート・テラコッタ・羊毛・木材・石・光るブロックなど)": "all",
        "全39色(concrete + terracotta + 自発光)": "full",
        "拡張 33色（concrete + terracotta）": "expanded",
        "基本 16色（concrete）": "concrete",
    }

    assert set(PALETTE_OPTIONS.keys()) == set(expected.keys()), "PALETTE_OPTIONS のキーが一致すること"
    # 以前の版で保存された表示名も読める
    assert resolve_palette("自動 (動画解析・最適化)") == "auto"

    for text, expected_value in expected.items():
        actual = resolve_palette(text)
        assert actual == expected_value, (
            f"resolve_palette('{text}') returned '{actual}', expected '{expected_value}'"
        )
        print(f"PASS: palette '{text}' -> '{actual}'")


def test_readme_no_invalid_colors():
    """README.md に 55色 / 110色 / 112色 の記述が残っていないことを検証"""
    readme_path = os.path.join(os.path.dirname(__file__), "..", "README.md")
    with open(readme_path, "r", encoding="utf-8") as f:
        content = f.read()

    for pattern in ["55色", "110色", "112色"]:
        assert pattern not in content, f"README.md に '{pattern}' が残っています"
    print("PASS: README color count check (no 55色/110色/112色)")


def test_pack_uuid_is_stable_per_pack():
    """同じパック名・接頭辞なら UUID が変わらず、BP/RP では異なること"""
    bp1 = stable_pack_uuid("badapple", "Block Video Player", "bp")
    assert bp1 == stable_pack_uuid("badapple", "Block Video Player", "bp")
    assert bp1 != stable_pack_uuid("badapple", "Block Video Player", "rp")
    assert bp1 != stable_pack_uuid("movie", "Block Video Player", "bp")


def test_manifest_version_fits_bedrock_limits():
    """Bedrock はバージョンの各要素を 0〜65535 で扱う。超えるとインポートに失敗する"""
    for now in (1_704_067_200, 1_800_000_000, 2_000_000_000, 4_000_000_000):
        version = build_manifest_version(now=now)
        assert len(version) == 3
        assert all(isinstance(v, int) and 0 <= v <= 65535 for v in version), version


def test_manifest_version_increases_with_build_time():
    base = 1_800_000_000
    versions = [build_manifest_version(now=base + offset) for offset in (0, 60, 3600, 86_400 - 1, 86_400, 86_400 * 400)]
    assert versions == sorted(versions)
    assert len({tuple(v) for v in versions}) == len(versions)
    v1 = build_manifest_version(now=1_800_000_000)
    v2 = build_manifest_version(now=1_800_000_000 + 120)
    assert v1[0] == PACK_VERSION[0]
    assert v2 > v1


def test_converter_args_are_accepted_by_convert():
    """GUI が組み立てる引数を convert.py がそのまま受け付けること (引数名の食い違い防止)"""
    for device in DEVICE_OPTIONS.values():
        for perceptual, duration in [(True, None), (False, 12.5)]:
            argv = build_converter_args(
                Path("in.mp4"), Path("out.js"), "ffmpeg", 128, 128, 10, "auto", "blue_noise",
                30, device, perceptual, duration, "ko",
            )
            args = convert.parse_args(argv)
            assert args.device == device
            assert args.lang == "ko"
            assert args.fps == 10.0
            assert args.perceptual is perceptual
            assert args.duration == duration


def test_volume_modes_switch_dither_off_and_back():
    # 既定の Blue Noise のまま立体にすると「なし」、平面に戻すと Blue Noise に戻る
    dither, auto = dither_after_mode_change("screen", "inflate", "blue_noise", False)
    assert (dither, auto) == ("none", True)
    assert dither_after_mode_change("inflate", "relief", dither, auto) == ("none", True)
    assert dither_after_mode_change("relief", "screen", "none", True) == ("blue_noise", False)
    # 利用者が選んだディザは変えない
    assert dither_after_mode_change("screen", "relief", "floyd", False) == ("floyd", False)
    assert dither_after_mode_change("relief", "screen", "none", False) == ("none", False)
    # 立体のまま利用者がディザを選び直したら、平面に戻しても変えない
    assert dither_after_mode_change("relief", "screen", "ordered", True) == ("ordered", False)


def test_converter_args_for_3d_modes():
    """立体モードと .vox の引数も convert.py がそのまま受け付けること"""
    relief = convert.parse_args(build_converter_args(
        Path("in.mp4"), Path("out.js"), "ffmpeg", 64, 64, 10, "full", "none", 30, "cpu", True, None, "ja",
        "relief", 24,
    ))
    assert (relief.mode, relief.depth, relief.input_video) == ("relief", 24, "in.mp4")

    vox = convert.parse_args(build_converter_args(
        Path("walk.VOX"), Path("out.js"), "ffmpeg", 64, 64, 10, "full", "none", 30, "cpu", True, None, "ja",
        "relief", 24,
    ))
    assert (vox.mode, vox.input_vox, vox.input_video) == ("vox", "walk.VOX", None)

    screen = convert.parse_args(build_converter_args(
        Path("in.mp4"), Path("out.js"), "ffmpeg", 64, 64, 10, "full", "none", 30, "cpu", True, None, "ja",
    ))
    assert screen.mode == "screen"


def test_device_options():
    assert resolve_device("自動 (GPU があれば使用)") == "auto"
    assert resolve_device("DirectML (Windows の AMD / Intel / NVIDIA)") == "directml"
    assert resolve_device("不明") == "auto"
    assert set(DEVICE_OPTIONS.values()) <= set(convert.DEVICE_CHOICES)


if __name__ == "__main__":
    test_dither_options_all_7()
    test_dither_prefix_matching()
    test_palette_options()
    test_readme_no_invalid_colors()
    print("All GUI option tests passed successfully!")


# --- 縦横比の提案・設定の検査・負荷の目安 (Issue #2) ---

def test_suggest_size_keeps_the_aspect_ratio():
    from video_player_gui import suggest_size

    assert suggest_size(1920, 1080, 128) == (128, 72)
    assert suggest_size(1080, 1920, 128) == (72, 128)
    assert suggest_size(640, 480, 96) == (96, 72)
    assert suggest_size(1000, 1000, 64) == (64, 64)
    assert suggest_size(4000, 10, 512) == (512, 1)  # 極端に横長でも 1 ブロック以上
    assert suggest_size(1920, 1080, 9999) == (512, 288)  # 最大 512


def test_probe_video_size_reads_ffmpeg_output(tmp_path):
    import shutil
    import subprocess

    import pytest
    from video_player_gui import probe_video_size

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("ffmpeg が必要")
    video = tmp_path / "wide.mp4"
    subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=5:duration=1",
                    "-pix_fmt", "yuv420p", str(video)], check=True)
    assert probe_video_size(video, ffmpeg) == (320, 180)
    assert probe_video_size(tmp_path / "missing.mp4", ffmpeg) is None


def test_validate_settings_reports_each_problem():
    from video_player_gui import validate_settings

    assert validate_settings(128, 72, 10) == []
    assert validate_settings(0, 72, 10) == ["error.size_range"]
    assert validate_settings(600, 72, 10) == ["error.size_range"]
    assert validate_settings(128, 72, 25) == ["error.fps_range"]
    assert validate_settings(128, 72, 10, duration=0) == ["error.duration"]
    assert validate_settings(128, 72, 10, mode="relief", depth=1) == ["error.depth_range"]
    assert validate_settings(128, 400, 10, mode="extrude", depth=8) == ["error.volume_height"]
    assert validate_settings(128, 400, 10) == []  # 平面スクリーンは高さの制限なし (地面に寝かせる)


def test_estimate_load_levels():
    from video_player_gui import QUALITY_PRESETS, estimate_load

    assert estimate_load(64, 64, 20)["level"] == "light"
    assert estimate_load(*QUALITY_PRESETS["high"])["level"] == "medium"
    assert estimate_load(*QUALITY_PRESETS["extreme"])["level"] == "heavy"
    flat = estimate_load(64, 48, 10)
    volume = estimate_load(64, 48, 10, mode="relief", depth=16)
    assert volume["cells"] == 3 * flat["cells"]


def test_new_gui_texts_exist_in_every_language():
    import json

    keys = ["settings.fit_aspect", "settings.fit_aspect_done", "warn.fit_aspect_no_video", "error.probe",
            "settings.load", "load.light", "load.medium", "load.heavy", "error.size_range", "error.fps_range",
            "error.duration", "error.depth_range", "error.volume_height", "warn.heavy_title", "warn.heavy"]
    for path in Path(__file__).resolve().parent.parent.joinpath("locales").glob("*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        assert all(data.get(key) for key in keys), path.name


def test_probe_video_size_applies_rotation(tmp_path):
    """縦に撮ったスマートフォンの動画 (回転の情報付き) は、回転した後の向きの大きさを返す。"""
    import shutil
    import subprocess

    import pytest
    from video_player_gui import probe_video_size, suggest_size

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("ffmpeg が必要")
    plain = tmp_path / "plain.mp4"
    rotated = tmp_path / "rotated.mp4"
    subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=5:duration=1",
                    "-pix_fmt", "yuv420p", str(plain)], check=True)
    result = subprocess.run([ffmpeg, "-v", "error", "-y", "-display_rotation", "90", "-i", str(plain), "-c", "copy",
                             str(rotated)])
    if result.returncode != 0:
        pytest.skip("この ffmpeg は -display_rotation に対応していない")
    assert probe_video_size(rotated, ffmpeg) == (180, 320)
    assert suggest_size(180, 320, 128) == (72, 128)
