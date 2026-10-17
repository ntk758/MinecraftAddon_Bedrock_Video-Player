"""動き補償 (varint_rle_v5) のテスト: パンする映像で、復元が完全に一致し、シークでき、データが小さくなること。"""
import json
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
from mvcodec.color import PALETTES, create_palette_image, process_single_frame_img  # noqa: E402
from mvcodec.decode import iter_decoded_frames, load_frame_data, parse_index  # noqa: E402
from mvcodec.motion import apply_motion, estimate_motion, pack_vector, unpack_vector  # noqa: E402

WIDTH, HEIGHT = 64, 48
FRAMES = 24
PALETTE = PALETTES["full"]


def make_pan_frames(frames_dir):
    """パレットの色だけでできた大きな模様を、右下へ少しずつずらしながら切り出す (カメラのパン)。"""
    rng = np.random.default_rng(3)
    colors = np.array([item["rgb"] for item in PALETTE], dtype=np.uint8)
    big_h, big_w = HEIGHT + 2 * FRAMES, WIDTH + 3 * FRAMES
    # 3×3 画素単位の色の塊 (減色しても色が変わらない)
    cells = rng.integers(0, len(colors), size=(big_h // 3 + 1, big_w // 3 + 1))
    world = colors[np.repeat(np.repeat(cells, 3, axis=0), 3, axis=1)][:big_h, :big_w]
    frames_dir.mkdir()
    frames = []
    for t in range(FRAMES):
        frame = np.ascontiguousarray(world[t:t + HEIGHT, 2 * t:2 * t + WIDTH])
        frames.append(frame)
        Image.fromarray(frame).save(frames_dir / f"frame_{t:04d}.png")
    return frames


def convert_frames(frames_dir, out, *extra):
    argv = ["--frames-dir", str(frames_dir), "--output", str(out), "--width", str(WIDTH), "--height", str(HEIGHT),
            "--fps", "10", "--device", "cpu", "--palette", "full", "--no-adaptive-fps", "--keyframe-interval", "0",
            "--threads", "2", "--temporal-hysteresis", "0", "--block-rdo", "0", *extra]
    assert convert.main(argv) == 0
    return load_frame_data(out)


def expected_indices(frame):
    pal_rgb = np.array([item["rgb"] for item in PALETTE], dtype=np.float64)
    return process_single_frame_img(Image.fromarray(frame), create_palette_image(PALETTE), WIDTH, HEIGHT,
                                    len(PALETTE), "none", True, pal_rgb)


def payload_size(data):
    return sum(len(chunk) for chunk in data["chunks"])


def test_vector_packing_roundtrip():
    for r in (1, 4, 15):
        for dx in range(-r, r + 1):
            for dy in range(-r, r + 1):
                assert unpack_vector(pack_vector(dx, dy, r), r) == (dx, dy)


def test_estimate_motion_finds_global_shift():
    rng = np.random.default_rng(0)
    prev = rng.integers(0, 20, size=(32, 48))
    cur = np.empty_like(prev)
    # cur[y][x] = prev[y + 1][x + 2] (端は端の画素)
    ys = np.clip(np.arange(32) + 1, 0, 31)
    xs = np.clip(np.arange(48) + 2, 0, 47)
    cur[:] = prev[ys][:, xs]
    vectors = estimate_motion(prev.reshape(-1), cur.reshape(-1), 48, 32)
    assert vectors and set(vectors.values()) == {(2, 1)}
    predicted = apply_motion(prev.reshape(-1), vectors, 48, 32)
    assert np.array_equal(predicted, cur.reshape(-1))


@pytest.fixture(scope="module")
def pan_outputs(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("pan")
    frames = make_pan_frames(tmp / "frames")
    with_motion = convert_frames(tmp / "frames", tmp / "motion.js")
    without_motion = convert_frames(tmp / "frames", tmp / "plain.js", "--no-motion")
    return frames, tmp / "motion.js", with_motion, without_motion


def test_motion_output_is_exact(pan_outputs):
    frames, _path, data, _plain = pan_outputs
    assert data["format"] in ("varint_rle_v5", "varint_rle_v6")
    for frame, (index_board, _rgb) in zip(frames, iter_decoded_frames(data)):
        assert np.array_equal(index_board, expected_indices(frame))


def test_motion_makes_panning_smaller(pan_outputs):
    _frames, _path, with_motion, without_motion = pan_outputs
    assert payload_size(with_motion) < payload_size(without_motion) * 0.6, (
        payload_size(with_motion), payload_size(without_motion)
    )
    # 動き補償を使わなくても同じ映像になる
    for (a, _), (b, _) in zip(iter_decoded_frames(with_motion), iter_decoded_frames(without_motion)):
        assert np.array_equal(a, b)


def test_keyframes_never_use_motion(pan_outputs):
    _frames, _path, data, _plain = pan_outputs
    keyframes = [i for i, entry in enumerate(parse_index(data)) if entry[3]]
    assert keyframes[0] == 0


@pytest.mark.skipif(shutil.which("node") is None, reason="node が必要")
def test_codec_js_decodes_motion_frames(pan_outputs):
    _frames, path, data, _plain = pan_outputs
    result = subprocess.run(["node", str(ROOT / "tests" / "js" / "decode_frames.mjs"), str(path)],
                            capture_output=True, text=True, check=True)
    decoded = json.loads(result.stdout)
    py_frames = [idx.reshape(-1).tolist() for idx, _ in iter_decoded_frames(data)]
    assert decoded["frames"] == py_frames
    assert decoded["motionFrames"], "動きベクトルを使ったフレームがありません"
    assert decoded["seekMismatches"] == []
    assert decoded["staleGopFrames"] == []
