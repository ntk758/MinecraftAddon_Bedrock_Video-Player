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
        "自動 (動画解析・最適化)": "auto",
        "全39色(concrete + terracotta + 自発光)": "full",
        "拡張 33色（concrete + terracotta）": "expanded",
        "基本 16色（concrete）": "concrete",
    }

    assert set(PALETTE_OPTIONS.keys()) == set(expected.keys()), "PALETTE_OPTIONS のキーが一致すること"

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


def test_manifest_version_increases_with_build_time():
    v1 = build_manifest_version(now=1_800_000_000)
    v2 = build_manifest_version(now=1_800_000_000 + 120)
    assert v1[:2] == list(PACK_VERSION[:2])
    assert v2[2] > v1[2]


def test_converter_args_are_accepted_by_convert():
    """GUI が組み立てる引数を convert.py がそのまま受け付けること (引数名の食い違い防止)"""
    for device in DEVICE_OPTIONS.values():
        for perceptual, duration in [(True, None), (False, 12.5)]:
            argv = build_converter_args(
                Path("in.mp4"), Path("out.js"), "ffmpeg", 128, 128, 2, "auto", "blue_noise",
                30, device, perceptual, duration, "ko",
            )
            args = convert.parse_args(argv)
            assert args.device == device
            assert args.lang == "ko"
            assert args.fps == 10.0
            assert args.perceptual is perceptual
            assert args.duration == duration


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
