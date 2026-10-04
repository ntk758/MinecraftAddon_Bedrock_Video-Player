"""convert.py → FRAME_DATA (varint_rle_v4) → デコーダ (Python / codec.js) の往復テスト。"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import convert  # noqa: E402
import mvcodec.auto_palette as auto_palette  # noqa: E402
from mvcodec.color import ALL_BLOCKS, PALETTES, create_palette_image, process_single_frame_img  # noqa: E402
from mvcodec.decode import iter_decoded_frames, load_frame_data, parse_index, utf16_str_to_bytes  # noqa: E402
from mvcodec.encode import bytearray_to_utf16_str  # noqa: E402

WIDTH, HEIGHT = 24, 16
SCENE_LENGTH = 25
SCENE_COLORS = [(230, 40, 40), (20, 30, 200), (240, 240, 240)]


def reference_utf16(byte_arr):
    """高速化前の実装 (互換性確認用)"""
    if not byte_arr:
        return ""
    bits = "".join(f"{b:08b}" for b in byte_arr)
    pad_len = (15 - len(bits) % 15) % 15
    bits += "0" * pad_len
    chars = [chr(0x1000 + pad_len)]
    for i in range(0, len(bits), 15):
        chars.append(chr(0x1000 + int(bits[i:i + 15], 2)))
    return "".join(chars)


def make_scene_frames(frames_dir):
    """背景色が大きく変わる 3 シーン。各シーン内では小さな四角が動く。"""
    frames_dir.mkdir()
    frames = []
    for scene, color in enumerate(SCENE_COLORS):
        for t in range(SCENE_LENGTH):
            arr = np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)
            arr[:] = color
            x = t % (WIDTH - 4)
            arr[4:8, x:x + 4] = (255 - color[0], 200, 30 * scene)
            frames.append(arr)
            Image.fromarray(arr).save(frames_dir / f"frame_{len(frames):04d}.png")
    return frames


def expected_indices(frame, palette):
    pal_rgb = np.array([item["rgb"] for item in palette], dtype=np.float64)
    return process_single_frame_img(
        Image.fromarray(frame), create_palette_image(palette), WIDTH, HEIGHT, len(palette), "none", True, pal_rgb
    )


def run_convert(frames_dir, out, *extra, device="cpu"):
    """既定は CPU (Pillow) 減色。期待値と完全一致を比べるため GPU の自動選択を避ける。"""
    argv = ["--frames-dir", str(frames_dir), "--output", str(out), "--width", str(WIDTH), "--height", str(HEIGHT),
            "--fps", "10", "--threads", "2", "--device", device, *extra]
    assert convert.main(argv) == 0
    return load_frame_data(out)


def test_utf16_matches_reference_and_roundtrips():
    rng = np.random.default_rng(1)
    for size in [0, 1, 2, 14, 15, 16, 100, 1001]:
        data = bytearray(rng.integers(0, 256, size=size, dtype=np.uint8).tobytes())
        encoded = bytearray_to_utf16_str(data)
        assert encoded == reference_utf16(data)
        assert utf16_str_to_bytes(encoded) == bytes(data)


def test_video_filter_centers_horizontally():
    vf = convert.build_video_filter(64, 64, 10)
    assert "pad=64:64:(ow-iw)/2:(oh-ih)/2:black" in vf
    assert "(ow-ih)" not in vf


def test_letterbox_image_centers_content():
    wide = Image.new("RGB", (32, 16), (255, 255, 255))
    boxed = np.asarray(convert.letterbox_image(wide, 16, 16))
    assert boxed[0].max() == 0 and boxed[-1].max() == 0  # 上下は黒帯
    assert boxed[8].min() > 200  # 中央は元画像


def test_parse_args_requires_input_and_toggles():
    with pytest.raises(SystemExit):
        convert.parse_args([])
    args = convert.parse_args(["--frames-dir", "x", "--no-adaptive-fps", "--no-perceptual"])
    assert args.adaptive_fps is False and args.perceptual is False
    assert convert.parse_args(["--frames-dir", "x"]).adaptive_fps is True
    with pytest.raises(SystemExit):
        convert.parse_args(["--frames-dir", "x", "--fps", "30"])


def test_fixed_palette_roundtrip(tmp_path):
    frames = make_scene_frames(tmp_path / "frames")
    data = run_convert(tmp_path / "frames", tmp_path / "out.js", "--no-adaptive-fps", "--keyframe-interval", "10")

    assert data["fps"] == 10
    assert data["frame_count"] == len(frames)
    assert data["gop_boundaries"] == [0, 25, 50, 75]

    keyframes = [i for i, entry in enumerate(parse_index(data)) if entry[3]]
    assert keyframes == [0, 10, 20, 25, 35, 45, 50, 60, 70]

    palette = PALETTES["full"]
    for frame, (index_board, _rgb) in zip(frames, iter_decoded_frames(data)):
        assert np.array_equal(index_board, expected_indices(frame, palette))


def test_adaptive_palette_switch_keeps_colors_correct(tmp_path, monkeypatch):
    """GOP ごとにパレット番号の意味が変わっても、表示色が各 GOP のパレットで正しく再現されること。"""
    calls = []

    def rotating_palette(frames_iter, all_blocks, max_colors=110, device=None, sample_stride=10, seed=0):
        shift = 7 * len(calls)
        calls.append(shift)
        return all_blocks[shift:] + all_blocks[:shift]

    monkeypatch.setattr(auto_palette, "generate_auto_palette", rotating_palette)
    frames = make_scene_frames(tmp_path / "frames")
    data = run_convert(tmp_path / "frames", tmp_path / "out.js", "--palette", "auto", "--keyframe-interval", "0")

    assert data["adaptive_palette"] is True
    assert len(calls) == 3
    entries = parse_index(data)
    for start in data["gop_boundaries"][:-1]:
        assert entries[start][3], f"GOP 先頭 {start} がキーフレームではありません"

    for f, (frame, (_idx, rgb)) in enumerate(zip(frames, iter_decoded_frames(data))):
        gop = np.searchsorted(data["gop_boundaries"], f, side="right") - 1
        shift = calls[gop]
        palette = ALL_BLOCKS[shift:] + ALL_BLOCKS[:shift]
        expected_rgb = np.array([item["rgb"] for item in palette], dtype=np.uint8)[expected_indices(frame, palette)]
        if entries[f][2] == 0:
            continue  # adaptive-fps で省略されたフレームは前の表示を維持する
        assert np.array_equal(rgb, expected_rgb), f"frame {f} の表示色が一致しません"


@pytest.mark.skipif(shutil.which("node") is None, reason="node が必要")
def test_codec_js_matches_python_decoder(tmp_path):
    make_scene_frames(tmp_path / "frames")
    out = tmp_path / "out.js"
    data = run_convert(tmp_path / "frames", out, "--palette", "full", "--keyframe-interval", "8")

    result = subprocess.run(
        ["node", str(ROOT / "tests" / "js" / "decode_frames.mjs"), str(out)],
        capture_output=True, text=True, check=True,
    )
    decoded = json.loads(result.stdout)
    py_frames = [idx.reshape(-1).tolist() for idx, _ in iter_decoded_frames(data)]

    assert decoded["frames"] == py_frames
    assert decoded["keyframes"] == [i for i, e in enumerate(parse_index(data)) if e[3]]
    assert decoded["staleGopFrames"] == []
    assert decoded["seekMismatches"] == []


@pytest.mark.parametrize("palette", ["full", "auto"])
def test_auto_device_output_is_consistent(tmp_path, palette):
    """GPU が自動選択される環境でも、出力がシーク可能で全画素が描画されること。"""
    make_scene_frames(tmp_path / "frames")
    data = run_convert(tmp_path / "frames", tmp_path / "out.js", "--palette", palette, device="auto")
    entries = parse_index(data)
    assert all(entries[start][3] for start in data["gop_boundaries"][:-1])
    for index_board, _rgb in iter_decoded_frames(data):
        assert index_board.min() >= 0
