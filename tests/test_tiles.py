"""タイル辞書のテスト: 以前の模様が再び現れる映像で、辞書参照が使われ、復元が完全に一致し、データが小さくなること。"""
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
from mvcodec.decode import iter_decoded_frames, load_frame_data  # noqa: E402
from mvcodec.tiles import TileDictionary  # noqa: E402

WIDTH, HEIGHT = 64, 48
FRAMES = 18
PALETTE = PALETTES["full"]
PALETTE_RGB = np.array([item["rgb"] for item in PALETTE], dtype=np.float64)


def make_alternating_frames(frames_dir):
    """2 つの模様 A / B が 2 フレームごとに入れ替わり、その上を小さな四角が動く映像。"""
    rng = np.random.default_rng(5)
    colors = PALETTE_RGB.astype(np.uint8)
    scenes = []
    for _ in range(2):
        cells = rng.integers(0, len(colors), size=(HEIGHT // 4, WIDTH // 4))
        scenes.append(colors[np.repeat(np.repeat(cells, 4, axis=0), 4, axis=1)])
    frames_dir.mkdir()
    frames = []
    for t in range(FRAMES):
        frame = scenes[(t // 2) % 2].copy()
        frame[20:28, t * 2:t * 2 + 8] = colors[0]
        frames.append(frame)
        Image.fromarray(frame).save(frames_dir / f"frame_{t:04d}.png")
    return frames


def convert_frames(frames_dir, out, *extra):
    argv = ["--frames-dir", str(frames_dir), "--output", str(out), "--width", str(WIDTH), "--height", str(HEIGHT),
            "--fps", "10", "--device", "cpu", "--palette", "full", "--no-adaptive-fps", "--keyframe-interval", "0",
            "--threads", "2", *extra]
    assert convert.main(argv) == 0
    return load_frame_data(out)


def expected_indices(frame):
    return process_single_frame_img(Image.fromarray(frame), create_palette_image(PALETTE), WIDTH, HEIGHT,
                                    len(PALETTE), "none", True, PALETTE_RGB)


@pytest.fixture(scope="module")
def outputs(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("tiles")
    frames = make_alternating_frames(tmp / "frames")
    with_tiles = convert_frames(tmp / "frames", tmp / "tiles.js")
    without_tiles = convert_frames(tmp / "frames", tmp / "plain.js", "--tile-size", "0")
    return frames, tmp / "tiles.js", with_tiles, without_tiles


def test_dictionary_rules():
    tiles = TileDictionary(16, 8, 8)
    a = np.zeros(16 * 8, np.int32)
    b = a.copy().reshape(8, 16)
    b[:, 8:] = 3
    b = b.reshape(-1)
    tiles.update(None, a, True)       # キーフレーム: 2 タイルとも登録
    assert len(tiles) == 2
    tiles.update(a, b, False)         # 右のタイルだけ変化 → 1 個追加
    assert len(tiles) == 3
    assert tiles.recency_of(np.full((8, 8), 3)) == 0
    assert tiles.recency_of(np.zeros((8, 8))) == 1  # 同じ内容は新しい方の番号
    tiles.update(b, a, True)          # キーフレームでリセット
    assert len(tiles) == 2 and tiles.recency_of(np.full((8, 8), 3)) is None


def test_tile_output_is_exact(outputs):
    frames, _path, data, _plain = outputs
    assert data["tile_size"] == 8
    for frame, (index_board, _rgb) in zip(frames, iter_decoded_frames(data)):
        assert np.array_equal(index_board, expected_indices(frame))


def test_tiles_make_repeats_smaller(outputs):
    _frames, _path, with_tiles, without_tiles = outputs
    size = lambda d: sum(len(c) for c in d["chunks"])  # noqa: E731
    assert size(with_tiles) < size(without_tiles) * 0.6, (size(with_tiles), size(without_tiles))
    for (a, _), (b, _) in zip(iter_decoded_frames(with_tiles), iter_decoded_frames(without_tiles)):
        assert np.array_equal(a, b)


@pytest.mark.skipif(shutil.which("node") is None, reason="node が必要")
def test_codec_js_decodes_tile_frames(outputs):
    _frames, path, data, _plain = outputs
    result = subprocess.run(["node", str(ROOT / "tests" / "js" / "decode_frames.mjs"), str(path)],
                            capture_output=True, text=True, check=True)
    decoded = json.loads(result.stdout)
    assert decoded["tileFrames"], "タイル辞書を使ったフレームがありません"
    assert decoded["frames"] == [b.reshape(-1).tolist() for b, _ in iter_decoded_frames(data)]
    assert decoded["seekMismatches"] == []


@pytest.mark.skipif(shutil.which("node") is None, reason="node が必要")
def test_main_js_plays_tile_frames(outputs, tmp_path):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from test_main_js import run_addon

    _frames, path, data, _plain = outputs
    result = run_addon(tmp_path / "addon", path)
    assert result["finished"]
    *_, (index_board, _rgb) = iter_decoded_frames(data)
    blocks = [spec["block"] for spec in data["level_blocks"]]
    assert result["finalBoard"] == [blocks[i] for i in index_board.reshape(-1)]
