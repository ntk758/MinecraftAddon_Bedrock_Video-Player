"""立体の符号化と描画の改良のテスト: 3D 動きベクトル、inflate、relief のヒステリシス、3D の箱でまとめた設置、建築限界。"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import convert  # noqa: E402
from mvcodec.decode import iter_decoded_frames, load_frame_data  # noqa: E402
from mvcodec.motion import (  # noqa: E402
    apply_motion_3d,
    estimate_motion_3d,
    pack_vector_3d,
    unpack_vector_3d,
)
from mvcodec.volume import (  # noqa: E402
    AIR_LEVEL,
    distance_to_edge,
    inflate_volume,
    load_vox_frames,
    relief_levels,
    vox_to_volume,
    vox_volume_shape,
    write_vox,
)

HAS_NODE = shutil.which("node") is not None


# --- 3D 動きベクトル ---

def test_vector_3d_packing_roundtrip():
    for r in (1, 2, 3):
        span = range(-r, r + 1)
        packed = {pack_vector_3d(dx, dy, dz, r) for dz in span for dy in span for dx in span}
        assert packed == set(range((2 * r + 1) ** 3))
        for dz in span:
            for dy in span:
                for dx in span:
                    assert unpack_vector_3d(pack_vector_3d(dx, dy, dz, r), r) == (dx, dy, dz)
    assert pack_vector_3d(2, 2, 2, 2) == 124  # ±2 なら varint 1 バイトに収まる


def test_estimate_motion_3d_finds_shift_in_every_axis():
    rng = np.random.default_rng(1)
    depth, height, width = 16, 16, 24
    prev = rng.integers(0, 20, size=(depth, height, width))
    # cur[z][y][x] = prev[z + 2][y - 1][x + 1] (端は端の値)
    zs = np.clip(np.arange(depth) + 2, 0, depth - 1)
    ys = np.clip(np.arange(height) - 1, 0, height - 1)
    xs = np.clip(np.arange(width) + 1, 0, width - 1)
    cur = prev[np.ix_(zs, ys, xs)]
    vectors = estimate_motion_3d(prev.reshape(-1), cur.reshape(-1), width, height, depth)
    assert vectors and set(vectors.values()) == {(1, -1, 2)}
    predicted = apply_motion_3d(prev.reshape(-1), vectors, width, height, depth)
    assert np.array_equal(predicted, cur.reshape(-1))


def make_depth_walk(tmp_path, frames=10):
    """2 色の模様の付いた物体が、奥から手前へ (vox の y 方向) 1 マスずつ動く .vox。"""
    palette = np.zeros((256, 4), np.uint8)
    palette[0] = (230, 40, 40, 255)
    palette[1] = (240, 240, 240, 255)
    palette[2] = (30, 60, 200, 255)
    models = []
    for t in range(frames):
        voxels = [[x, y + t, z, 1 + (x + 2 * z + y) % 3]
                  for x in range(2, 10) for y in range(6) for z in range(8)]
        models.append(((12, 20, 10), np.array(voxels, np.uint8)))
    path = tmp_path / "walk_depth.vox"
    path.write_bytes(write_vox(models, palette))
    return path


def convert_vox(path, out, *extra):
    argv = ["--input-vox", str(path), "--output", str(out), "--fps", "10", "--no-adaptive-fps",
            "--keyframe-interval", "0", "--palette", "full", *extra]
    assert convert.main(argv) == 0
    return load_frame_data(out)


@pytest.fixture(scope="module")
def depth_walk(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("depth_walk")
    path = make_depth_walk(tmp)
    with_motion = convert_vox(path, tmp / "motion.js")
    without_motion = convert_vox(path, tmp / "plain.js", "--no-motion")
    return path, tmp / "motion.js", with_motion, without_motion


def test_depth_motion_is_exact_and_much_smaller(depth_walk):
    path, _out, data, plain = depth_walk
    assert data["mv_range"] == 2
    frames, vox_palette = load_vox_frames(path)
    shape = vox_volume_shape(frames)
    decoded = [v for v, _ in iter_decoded_frames(data)]
    plain_decoded = [v for v, _ in iter_decoded_frames(plain)]
    for volume, reference in zip(decoded, plain_decoded):
        assert np.array_equal(volume, reference)
    # 物体の形 (空気かどうか) が .vox のとおり
    for frame, volume in zip(frames, decoded):
        expected = vox_to_volume(frame, shape, np.where(np.arange(256) == 0, AIR_LEVEL, 0).astype(np.int16))
        assert np.array_equal(volume != AIR_LEVEL, expected != AIR_LEVEL)
    size = sum(len(c) for c in data["chunks"])
    plain_size = sum(len(c) for c in plain["chunks"])
    assert size < plain_size * 0.5, (size, plain_size)


@pytest.mark.skipif(not HAS_NODE, reason="node が必要")
def test_codec_js_decodes_3d_motion(depth_walk):
    _path, out, data, _plain = depth_walk
    result = subprocess.run(["node", str(ROOT / "tests" / "js" / "decode_frames.mjs"), str(out)],
                            capture_output=True, text=True, check=True)
    decoded = json.loads(result.stdout)
    assert decoded["frames"] == [v.reshape(-1).tolist() for v, _ in iter_decoded_frames(data)]
    assert decoded["motionFrames"], "3D の動きベクトルを使ったフレームがありません"
    assert decoded["seekMismatches"] == []


# --- relief のヒステリシス / inflate ---

def test_relief_hysteresis_ignores_small_wobble():
    depth = 9  # 段は 0〜8、連続値は heights01 * 8
    first = relief_levels(np.array([[0.50, 0.50, 0.50]]), depth)
    assert first.tolist() == [[4, 4, 4]]
    # 4.6 段 (丸めると 5) / 4.9 段 (丸めると 5) / 5.1 段: 1 段未満の揺れなら前の段のまま、1 段以上なら変える
    wobble = np.array([[4.6, 4.9, 5.1]]) / 8
    assert relief_levels(wobble, depth, first).tolist() == [[4, 4, 5]]
    assert relief_levels(wobble, depth, first, hysteresis=0).tolist() == [[5, 5, 5]]


def test_distance_to_edge():
    mask = np.zeros((7, 7), bool)
    mask[1:6, 1:6] = True
    dist = distance_to_edge(mask, 10)
    assert dist[0, 0] == 0 and dist[1, 1] == 1 and dist[2, 3] == 2 and dist[3, 3] == 3
    # 画像の外は mask の続きとみなす (端で切れた形は端で細くならない)
    assert distance_to_edge(np.ones((3, 3), bool), 10)[0, 0] == 10  # 背景が無ければ打ち切りの値


def test_inflate_is_round_and_symmetric():
    depth = 12
    mask = np.zeros((30, 30), bool)
    yy, xx = np.mgrid[0:30, 0:30]
    mask[(yy - 15) ** 2 + (xx - 15) ** 2 <= 12 ** 2] = True
    volume = inflate_volume(np.full(mask.shape, 3), mask, depth)
    solid = volume != AIR_LEVEL
    thickness = solid.sum(axis=0)
    assert (thickness[~mask] == 0).all()
    assert (thickness[mask] >= 1).all()            # 縁の画素も必ず 1 段以上
    assert thickness[15, 15] == depth              # 中央は奥行きいっぱい
    assert thickness[15, 3] < thickness[15, 4] < thickness[15, 5] < depth  # 縁へ行くほど薄い (丸い)
    assert np.array_equal(solid, solid[::-1])       # 前後対称


# --- main.js: 3D の箱でまとめた設置 / 建築限界 ---

def write_cube_vox(path, size=4):
    palette = np.zeros((256, 4), np.uint8)
    palette[0] = (230, 40, 40, 255)
    cube = [[x, y, z, 1] for x in range(size) for y in range(size) for z in range(size)]
    path.write_bytes(write_vox([((size, size, size), np.array(cube, np.uint8))], palette))


@pytest.mark.skipif(not HAS_NODE, reason="node が必要")
def test_main_js_fills_a_solid_cube_with_one_call(tmp_path):
    from test_main_js import run_addon

    write_cube_vox(tmp_path / "cube.vox")
    out = tmp_path / "cube.js"
    convert_vox(tmp_path / "cube.vox", out)
    result = run_addon(tmp_path / "addon", out)
    assert result["finished"]
    # 奥行き 4 段を段ごとに置けば 4 回。奥へも伸ばした 1 個の箱なら 1 回
    assert result["stats"]["fillCalls"] == 1
    assert result["stats"]["blocksPlaced"] == 64
    assert sum(1 for block in result["finalBoard"] if block not in (None, "minecraft:air")) == 64


@pytest.mark.skipif(not HAS_NODE, reason="node が必要")
def test_main_js_keeps_volume_within_build_height(tmp_path):
    from test_main_js import run_addon

    write_cube_vox(tmp_path / "cube.vox", size=6)
    out = tmp_path / "cube.js"
    data = convert_vox(tmp_path / "cube.vox", out)
    result = run_addon(tmp_path / "addon", out, "y=318")
    # オーバーワールドの最上段は Y=319 (heightRange.max = 320 は含まない)
    assert result["anchor"]["y"] == 320 - data["height"]
    assert any("bvp.setup_shifted" in json.dumps(m) for m in result["messages"])
    assert sum(1 for block in result["finalBoard"] if block not in (None, "minecraft:air")) == 6 ** 3


# --- プレビュー ---

def test_iso_view_draws_three_shaded_faces():
    from mvcodec.volume import iso_view

    volume = np.full((3, 3, 3), AIR_LEVEL, np.int16)
    volume[1, 1, 1] = 0
    background = (24, 26, 32)
    image = iso_view(volume, np.array([[200, 100, 50]]), unit=4, background=background)
    colors = {tuple(c) for c in image.reshape(-1, 3)} - {background}
    assert len(colors) == 3  # 上面・正面・側面で明るさが違う
    assert (200, 100, 50) in colors  # 上面は元の色
    # 手前のボクセルが奥のボクセルを隠す: 南 (手前) に 1 個足すと、元の正面の色は見えなくなる
    volume[2, 1, 1] = 1
    image2 = iso_view(volume, np.array([[200, 100, 50], [10, 200, 10]]), unit=4, background=background)
    front_of_first = tuple(int(v) for v in np.array([200, 100, 50]) * 0.82)
    assert front_of_first not in {tuple(c) for c in image2.reshape(-1, 3)}


def test_preview_script_writes_gif(depth_walk, tmp_path):
    from PIL import Image

    sys.path.insert(0, str(ROOT / "scripts"))
    import preview_volume

    _path, out, data, _plain = depth_walk
    gif = tmp_path / "preview.gif"
    assert preview_volume.main([str(out), "--output", str(gif), "--unit", "2"]) == 0
    with Image.open(gif) as image:
        assert image.n_frames == data["frame_count"]
