"""3D 立体 (ホログラム) 再生のテスト: ボクセル生成、.vox の読み書き、変換 → 復元 (Python / codec.js / main.js)。"""
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
sys.path.insert(0, str(Path(__file__).resolve().parent))

import convert  # noqa: E402
from mvcodec.color import PALETTES, create_palette_image, process_single_frame_img  # noqa: E402
from mvcodec.decode import iter_decoded_frames, load_frame_data, parse_index  # noqa: E402
from mvcodec.volume import (  # noqa: E402
    AIR_LEVEL,
    extrude_volume,
    hollow,
    lift_frame,
    load_vox_frames,
    parse_vox,
    relief_volume,
    vox_to_volume,
    write_vox,
)

WIDTH, HEIGHT, DEPTH = 24, 16, 6
PALETTE = PALETTES["full"]
PALETTE_RGB = np.array([item["rgb"] for item in PALETTE], dtype=np.float64)
HAS_NODE = shutil.which("node") is not None


# --- ボクセル生成 ---

def test_relief_builds_surface_and_side_walls():
    indices = np.full((3, 3), 5)
    heights = np.zeros((3, 3))
    heights[1, 1] = 1.0  # 真ん中だけ一番手前に飛び出す
    volume = relief_volume(indices, heights, depth=4)
    assert volume.shape == (4, 3, 3)
    # 真ん中は奥 1 段目 (隣の高さ 0 の 1 つ上) から手前 3 段目まで埋まり、穴が空かない
    assert list(volume[:, 1, 1]) == [AIR_LEVEL, 5, 5, 5]
    # 周りは一番奥の 1 段だけ
    assert list(volume[:, 0, 0]) == [5, AIR_LEVEL, AIR_LEVEL, AIR_LEVEL]


def test_extrude_and_hollow():
    indices = np.full((5, 5), 7)
    mask = np.zeros((5, 5), bool)
    mask[1:4, 1:4] = True
    solid = extrude_volume(indices, mask, depth=5)
    assert (solid != AIR_LEVEL).sum() == 9 * 5
    shell = hollow(solid)
    # 3x3x5 の柱の内部 (1x1x3) だけが空気になる
    assert (shell != AIR_LEVEL).sum() == 9 * 5 - 3
    assert shell[2, 2, 2] == AIR_LEVEL and shell[0, 2, 2] == 7


def test_vox_roundtrip_and_orientation(tmp_path):
    palette = np.zeros((256, 4), np.uint8)
    palette[0] = (230, 40, 40, 255)   # 色番号 1
    palette[1] = (20, 30, 200, 255)   # 色番号 2
    voxels = np.array([[0, 0, 0, 1], [1, 2, 3, 2]], np.uint8)
    data = write_vox([((2, 3, 4), voxels)], palette)
    models, parsed_palette = parse_vox(data)
    assert len(models) == 1 and models[0][0] == (2, 3, 4)
    assert np.array_equal(models[0][1], voxels)
    assert np.array_equal(parsed_palette[:2], palette[:2])

    color_to_level = np.full(256, AIR_LEVEL, np.int16)
    color_to_level[1], color_to_level[2] = 3, 9
    volume = vox_to_volume(models[0], (3, 4, 2), color_to_level)  # (奥行き, 高さ, 幅)
    # vox の y が大きいほど北 (奥行きの配列番号が小さい = ワールドの z が小さい)。z=0 (床) は一番下の行
    assert volume[2, 3, 0] == 3   # x=0, y=0 (一番手前), z=0
    assert volume[0, 0, 1] == 9   # x=1, y=2 (一番奥), z=3 (一番上)
    assert (volume != AIR_LEVEL).sum() == 2

    (tmp_path / "anim").mkdir()
    for i in range(3):
        (tmp_path / "anim" / f"frame_{i:02d}.vox").write_bytes(write_vox([((2, 3, 4), voxels)], palette))
    frames, _ = load_vox_frames(tmp_path / "anim")
    assert len(frames) == 3


# --- 変換 → 復元 ---

def make_blob_frames(frames_dir, count=12):
    """白い円が動き、背景が黒の映像 (extrude / relief 用)。"""
    frames_dir.mkdir()
    frames = []
    yy, xx = np.mgrid[0:HEIGHT, 0:WIDTH]
    for t in range(count):
        frame = np.zeros((HEIGHT, WIDTH, 3), np.uint8)
        inside = (xx - (4 + t)) ** 2 + (yy - 8) ** 2 <= 16
        frame[inside] = (255, 255, 255)
        frame[2:5, 18:22] = (230, 40, 40)
        frames.append(frame)
        Image.fromarray(frame).save(frames_dir / f"frame_{t:04d}.png")
    return frames


def quantize(frame):
    return process_single_frame_img(Image.fromarray(frame), create_palette_image(PALETTE), WIDTH, HEIGHT,
                                    len(PALETTE), "none", True, PALETTE_RGB)


def convert_volume(frames_dir, out, mode, *extra):
    argv = ["--frames-dir", str(frames_dir), "--output", str(out), "--width", str(WIDTH), "--height", str(HEIGHT),
            "--fps", "10", "--device", "cpu", "--palette", "full", "--mode", mode, "--depth", str(DEPTH),
            "--no-adaptive-fps", "--keyframe-interval", "5", "--threads", "2", *extra]
    assert convert.main(argv) == 0
    return load_frame_data(out)


@pytest.fixture(scope="module", params=["extrude", "relief"])
def video_volume(request, tmp_path_factory):
    tmp = tmp_path_factory.mktemp(f"volume_{request.param}")
    frames = make_blob_frames(tmp / "frames")
    out = tmp / "frames_demo.js"
    data = convert_volume(tmp / "frames", out, request.param)
    return request.param, frames, out, data, tmp


def test_video_volume_roundtrip(video_volume):
    mode, frames, _out, data, _tmp = video_volume
    assert data["layout"] == "volume" and data["depth"] == DEPTH and data["air_level"] == AIR_LEVEL
    decoded = list(iter_decoded_frames(data))
    assert len(decoded) == len(frames)
    for frame, (volume, _rgb) in zip(frames, decoded):
        expected = lift_frame(mode, quantize(frame), PALETTE_RGB, DEPTH)
        assert volume.shape == (DEPTH, HEIGHT, WIDTH)
        assert np.array_equal(volume, expected)


def test_keyframes_do_not_encode_air(video_volume):
    """キーフレームは空気を基準に符号化するので、空気のランは含まれない (データが小さい)。"""
    _mode, _frames, _out, data, _tmp = video_volume
    first = parse_index(data)[0]
    assert first[3]
    volume0, _ = next(iter_decoded_frames(data))
    solid = int((volume0 != AIR_LEVEL).sum())
    assert solid < DEPTH * HEIGHT * WIDTH // 2


@pytest.mark.skipif(not HAS_NODE, reason="node が必要")
def test_codec_js_decodes_volume(video_volume):
    _mode, _frames, out, data, _tmp = video_volume
    result = subprocess.run(["node", str(ROOT / "tests" / "js" / "decode_frames.mjs"), str(out)],
                            capture_output=True, text=True, check=True)
    decoded = json.loads(result.stdout)
    assert decoded["frames"] == [v.reshape(-1).tolist() for v, _ in iter_decoded_frames(data)]
    assert decoded["seekMismatches"] == []
    assert decoded["staleGopFrames"] == []


@pytest.mark.skipif(not HAS_NODE, reason="node が必要")
def test_main_js_plays_volume(video_volume):
    from test_main_js import run_addon

    _mode, _frames, out, data, tmp = video_volume
    result = run_addon(tmp / "addon", out)
    assert result["finished"]
    *_, (volume, _rgb) = iter_decoded_frames(data)
    blocks = [spec["block"] for spec in data["level_blocks"]]
    expected = [None if level == AIR_LEVEL else blocks[level] for level in volume.reshape(-1)]
    # 空気のマスは「何も置いていない」か「空気を置いた」のどちらか
    got = [None if block in (None, "minecraft:air") else block for block in result["finalBoard"]]
    assert got == expected


def test_vox_animation_convert(tmp_path):
    palette = np.zeros((256, 4), np.uint8)
    palette[0] = (230, 40, 40, 255)
    palette[1] = (240, 240, 240, 255)
    models = []
    for t in range(6):
        cube = [[x, y, z, 1 if z < 2 else 2] for x in range(t, t + 3) for y in range(3) for z in range(4)]
        models.append(((10, 3, 4), np.array(cube, np.uint8)))
    vox_path = tmp_path / "walk.vox"
    vox_path.write_bytes(write_vox(models, palette))

    out = tmp_path / "frames_vox.js"
    argv = ["--input-vox", str(vox_path), "--output", str(out), "--fps", "10", "--no-adaptive-fps",
            "--keyframe-interval", "3", "--palette", "full"]
    assert convert.main(argv) == 0
    data = load_frame_data(out)
    assert (data["width"], data["height"], data["depth"]) == (10, 4, 3)
    decoded = [v for v, _ in iter_decoded_frames(data)]
    assert len(decoded) == 6
    for t, volume in enumerate(decoded):
        solid = np.argwhere(volume != AIR_LEVEL)
        assert len(solid) == 3 * 3 * 4
        assert solid[:, 2].min() == t and solid[:, 2].max() == t + 2  # x 方向に 1 ずつ動く
        # 赤 (下 2 段) と白 (上 2 段) が、それぞれ赤系・白系のブロックになっている
        bottom = PALETTE_RGB[volume[0, 3, t]]
        top = PALETTE_RGB[volume[0, 0, t]]
        assert bottom[0] > bottom[2] and top.min() > 180


def test_mode_arguments_are_validated():
    with pytest.raises(SystemExit):
        convert.parse_args(["--frames-dir", "x", "--mode", "vox"])
    with pytest.raises(SystemExit):
        convert.parse_args(["--frames-dir", "x", "--mode", "screen", "--depth-video", "d.mp4"])
    assert convert.parse_args(["--input-vox", "a.vox"]).mode == "vox"
    assert convert.parse_args(["--frames-dir", "x"]).mode == "screen"


def test_moving_vox_frames_are_not_skipped(tmp_path):
    """ほとんどが空気の立体でも、小さな物体が動くフレームを「変化が少ない」として省略しない。"""
    palette = np.zeros((256, 4), np.uint8)
    palette[0] = (230, 40, 40, 255)
    models = []
    for t in range(8):
        cube = [[x, y, z, 1] for x in range(t, t + 2) for y in range(2) for z in range(2)]
        models.append(((32, 32, 32), np.array(cube, np.uint8)))  # 32^3 のうち 8 マスだけ
    vox_path = tmp_path / "small.vox"
    vox_path.write_bytes(write_vox(models, palette))
    out = tmp_path / "small.js"
    assert convert.main(["--input-vox", str(vox_path), "--output", str(out), "--fps", "10"]) == 0
    data = load_frame_data(out)
    assert all(length > 0 for _gop, _offset, length, _key in parse_index(data))



def test_vox_mapping_is_a_rotation_not_a_mirror():
    """vox (x, y, z) → Minecraft (東, 上, 南) の対応が鏡像 (行列式 -1) になっていないこと。"""
    shape = (4, 4, 4)
    color_to_level = np.arange(256, dtype=np.int16)
    def world_of(x, y, z):
        volume = vox_to_volume(((4, 4, 4), np.array([[x, y, z, 1]], np.uint8)), shape, color_to_level)
        d, row, col = np.argwhere(volume != AIR_LEVEL)[0]
        # main.js の cellToWorld と同じ: 東 = 列、上 = 高さ-1-行、南 = 奥行きの番号
        return np.array([col, shape[1] - 1 - row, d])
    origin = world_of(0, 0, 0)
    axes = np.array([world_of(1, 0, 0) - origin, world_of(0, 1, 0) - origin, world_of(0, 0, 1) - origin]).T
    assert round(np.linalg.det(axes)) == 1
    assert list(world_of(0, 0, 1) - origin) == [0, 1, 0]  # vox の z (上) はワールドの上


def test_vox_scene_graph_static_objects_make_one_frame():
    palette = np.zeros((256, 4), np.uint8)
    cube = np.array([[x, y, z, 1] for x in range(2) for y in range(2) for z in range(2)], np.uint8)
    scene = [
        {"translations": [(0, (0, 0, 0))], "models": [(0, 0)]},
        {"translations": [(0, (10, 0, 0))], "models": [(0, 0)]},
    ]
    frames, _ = parse_vox(write_vox([((2, 2, 2), cube)], palette, scene))
    assert len(frames) == 1  # 2 つのオブジェクトは 2 フレームではなく同じシーン
    size, voxels = frames[0]
    assert len(voxels) == 16 and size[0] == 12


def test_vox_scene_graph_animation_keyframes():
    palette = np.zeros((256, 4), np.uint8)
    small = np.array([[0, 0, 0, 1]], np.uint8)
    big = np.array([[x, 0, 0, 2] for x in range(3)], np.uint8)
    scene = [{"translations": [(0, (0, 0, 0)), (2, (5, 0, 0))], "models": [(0, 0), (1, 1)]}]
    frames, _ = parse_vox(write_vox([((1, 1, 1), small), ((3, 1, 1), big)], palette, scene))
    assert len(frames) == 3
    counts = [len(v) for _s, v in frames]
    assert counts == [1, 3, 3]  # フレーム 1 からモデル 1 に切り替わる
    # モデルは中心 (pivot) を基準に置かれるので、中心の位置で比べる
    centers = [float(v[:, 0].mean()) for _s, v in frames]
    assert centers[0] == centers[1] < centers[2]  # 位置はフレーム 2 で動く (それまでは直前のキーフレーム)
    assert centers[2] - centers[1] == 5


def test_vox_without_rgba_uses_default_palette():
    frames, palette = parse_vox(write_vox([((1, 1, 1), np.array([[0, 0, 0, 1]], np.uint8))]))
    assert tuple(palette[0][:3]) == (255, 255, 255)  # 仕様の既定パレットで色番号 1 は白
