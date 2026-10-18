"""深度動画を作る補助スクリプト (scripts/make_depth_video.py、Phase 8.7) のテスト。AI のモデルは使わない。"""
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import convert  # noqa: E402
from make_depth_video import DepthNormalizer, make_depth_video  # noqa: E402
from mvcodec.decode import iter_decoded_frames, load_frame_data  # noqa: E402

HAS_FFMPEG = shutil.which("ffmpeg") is not None


def test_normalizer_maps_near_to_bright_and_smooths():
    depth = np.tile(np.linspace(0.0, 10.0, 50, dtype=np.float32), (10, 1))  # 右ほど手前 (値が大きい)
    out = DepthNormalizer(temporal_smoothing=0)(depth)
    assert out.dtype == np.uint8 and out[:, 0].max() == 0 and out[:, -1].min() == 255
    assert np.all(np.diff(out.astype(int), axis=1) >= 0)
    # 「大きいほど遠い」モデルは反転する
    assert DepthNormalizer(temporal_smoothing=0, invert=True)(depth)[0, 0] == 255
    # 前のフレームの値を混ぜるので、急に変わっても途中の明るさになる
    normalizer = DepthNormalizer(temporal_smoothing=0.5)
    normalizer(depth)
    flipped = normalizer(depth[:, ::-1])
    assert 100 < flipped[0, 0] < 155


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg が必要")
def test_depth_video_drives_relief(tmp_path):
    """推定した深度で作った動画を --depth-video に渡すと、立体の奥行きが深度に従う (明るいほど手前)。"""
    source = tmp_path / "source.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=10", "-t", "1",
                    "-pix_fmt", "yuv420p", str(source)], check=True)

    def estimate(frame):
        # 左ほど手前 (元の映像に関係なく、横方向の傾き)
        return np.tile(np.linspace(1.0, 0.0, frame.shape[1], dtype=np.float32), (frame.shape[0], 1))

    depth = tmp_path / "depth.mp4"
    assert make_depth_video(source, depth, 32, 18, 10, estimate) == 10
    out = tmp_path / "relief.js"
    assert convert.main(["--input-video", str(source), "--depth-video", str(depth), "--output", str(out),
                         "--width", "32", "--height", "18", "--fps", "10", "--mode", "relief", "--depth", "8",
                         "--device", "cpu", "--palette", "full", "--dither-method", "none"]) == 0
    data = load_frame_data(out)
    *_, (board, _rgb) = iter_decoded_frames(data)
    solid = board != data["air_level"]  # (奥行き, 高さ, 幅)。relief は列ごとに 1 個の面
    assert np.all(solid.sum(axis=0) == 1)
    layer = solid.argmax(axis=0).mean(axis=0)  # 列ごとの面の奥行きの段 (高さ方向に平均)
    # 左 (手前と推定) と右 (奥) で、面の段が大きく違い、その間は単調に変わる
    assert abs(layer[0] - layer[-1]) >= 5, layer
    steps = np.sign(np.diff(layer))
    assert np.all(steps[steps != 0] == steps[steps != 0][0]), layer
