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
import mvcodec.volume as volume_module  # noqa: E402
from mvcodec.volume import (  # noqa: E402
    AIR_LEVEL,
    distance_to_edge,
    enclosed_air,
    fill_enclosed_air,
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
    # 動き補償の効果だけを比べるので、エントロピー符号化 (v6) は使わない
    with_motion = convert_vox(path, tmp / "motion.js", "--no-entropy")
    without_motion = convert_vox(path, tmp / "plain.js", "--no-motion", "--no-entropy")
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
    assert decoded["dictionaryMismatches"] == []


def make_blink_walk(path, size, frames=14, seed=3):
    """奥から手前へ 1 マスずつ歩く物体の横で、飾りが 3 つの模様を順に繰り返し、一番奥の壁が所々塗り替わる .vox。
    歩きは 3D の動きベクトルに、繰り返す模様はタイル辞書の参照に、壁の塗り替えは複数の行にまたがるランになる。"""
    palette = np.zeros((256, 4), np.uint8)
    for i, rgb in enumerate([(230, 40, 40), (240, 240, 240), (30, 60, 200), (40, 200, 60), (230, 200, 30)]):
        palette[i] = (*rgb, 255)
    rng = np.random.default_rng(seed)
    sx, sy, sz = size
    patterns = [[(x, y, z, 1 + int(rng.integers(0, 4)))
                 for x in range(sx - 5, sx - 1) for y in range(3) for z in range(1, sz - 1) if rng.random() < 0.6]
                for _ in range(3)]
    wall = rng.integers(1, 5, size=sx * sz)  # 盤面の行優先の順 (vox の z は盤面の行では逆向き)
    models = []
    for t in range(frames):
        for _ in range(3):
            start = int(rng.integers(0, wall.size))
            wall[start:start + int(rng.integers(10, 130))] = rng.integers(1, 5)
        voxels = {(i % sx, sy - 1, sz - 1 - i // sx): int(color) for i, color in enumerate(wall)}
        for x in range(2, min(sx - 6, 9)):
            for y in range(6):
                for z in range(min(sz - 1, 8)):
                    if y + t < sy:
                        voxels[(x, y + t, z)] = 1 + (x + 2 * z + y) % 3
        for x, y, z, color in patterns[t % 3]:
            voxels[(x, y, z)] = color
        models.append((size, np.array([[*cell, color] for cell, color in voxels.items()], np.uint8)))
    path.write_bytes(write_vox(models, palette))


def decode_with_node(path):
    result = subprocess.run(["node", str(ROOT / "tests" / "js" / "decode_frames.mjs"), str(path)],
                            capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


# 立体は、そのフレームで書いたタイルだけを辞書の更新で比べる (codec.js の TileDictionary の dirty)。
# 印の付け漏れが 1 つでもあると辞書がずれて、以後のフレームがすべて壊れるので、辞書の中身そのものも
# 毎回すべてのタイルを比べた正解 (tests/js/decode_frames.mjs の ReferenceTiles) と照らす。
# 幅 13・行数 242 は、タイルの大きさ (4, 8) でも動きのブリック (8) でも割り切れず、端の部分的なタイル・ブリックに当たる。
@pytest.mark.skipif(not HAS_NODE, reason="node が必要")
@pytest.mark.parametrize("tile", ["4", "8"])
@pytest.mark.parametrize("size", [(13, 22, 11), (40, 30, 20)])
def test_codec_js_tile_dictionary_follows_3d_motion(tmp_path, tile, size):
    path = tmp_path / "blink.vox"
    make_blink_walk(path, size)
    out = tmp_path / "blink.js"
    data = convert_vox(path, out, "--tile-size", tile, "--keyframe-interval", "6")
    assert data["tile_size"] == int(tile) and data["layout"] == "volume"
    decoded = decode_with_node(out)
    assert decoded["frames"] == [v.reshape(-1).tolist() for v, _ in iter_decoded_frames(data)]
    assert decoded["motionFrames"], "3D の動きベクトルを使ったフレームがありません"
    assert decoded["tileFrames"], "タイル辞書の参照を使ったフレームがありません"
    assert len(decoded["keyframes"]) > 1
    assert decoded["seekMismatches"] == []
    assert decoded["dictionaryMismatches"] == []
    assert decoded["seekDictionaryMismatches"] == []


@pytest.mark.skipif(not HAS_NODE, reason="node が必要")
@pytest.mark.parametrize("tile", ["4", "8"])
def test_codec_js_tile_dictionary_follows_depth_walk(tmp_path, tile):
    path = make_depth_walk(tmp_path)
    out = tmp_path / "walk.js"
    data = convert_vox(path, out, "--tile-size", tile)
    assert data["tile_size"] == int(tile)
    decoded = decode_with_node(out)
    assert decoded["frames"] == [v.reshape(-1).tolist() for v, _ in iter_decoded_frames(data)]
    assert decoded["motionFrames"], "3D の動きベクトルを使ったフレームがありません"
    assert decoded["seekMismatches"] == []
    assert decoded["dictionaryMismatches"] == []
    assert decoded["seekDictionaryMismatches"] == []


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


# --- 入力の扱い ---

@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg が必要")
def test_short_depth_video_is_an_error(tmp_path):
    """深度動画が入力より短いとき、途中から黙ってブロック色の明るさに切り替えず、エラーにする。"""
    from PIL import Image

    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()
    for i in range(12):
        Image.fromarray(np.full((8, 8, 3), 40 * (i % 6), np.uint8)).save(frames_dir / f"frame_{i:04d}.png")
    depth = tmp_path / "depth.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=gray:size=8x8:rate=10", "-frames:v", "5",
                    "-pix_fmt", "yuv420p", str(depth)], check=True)
    argv = ["--frames-dir", str(frames_dir), "--output", str(tmp_path / "out.js"), "--width", "8", "--height", "8",
            "--fps", "10", "--device", "cpu", "--mode", "relief", "--depth", "4", "--depth-video", str(depth)]
    with pytest.raises(ValueError, match="depth-video"):
        convert.main(argv)


def test_vox_folder_uses_each_files_palette(tmp_path):
    """フレームごとの .vox のフォルダでは、各ファイルが自分のパレットで色を決める (同じ色番号でも別の色)。"""
    folder = tmp_path / "anim"
    folder.mkdir()
    cube = np.array([[x, y, z, 1] for x in range(2) for y in range(2) for z in range(2)], np.uint8)
    for i, rgb in enumerate([(240, 240, 240), (230, 40, 40)]):
        palette = np.zeros((256, 4), np.uint8)
        palette[0] = (*rgb, 255)
        (folder / f"frame_{i}.vox").write_bytes(write_vox([((2, 2, 2), cube)], palette))
    data = convert_vox(folder, tmp_path / "anim.js")
    blocks = [spec["block"] for spec in data["level_blocks"]]
    first, second = [v for v, _ in iter_decoded_frames(data)]
    from mvcodec.color import BLOCK_RGB

    white = BLOCK_RGB[blocks[first[first != AIR_LEVEL][0]]]
    red = BLOCK_RGB[blocks[second[second != AIR_LEVEL][0]]]
    assert min(white) > 200, white  # 白っぽいブロック
    assert red[0] > red[1] + 60 and red[0] > red[2] + 60, red  # 赤いブロック


def _scene_change_frames(folder):
    """1 場面目は色がたくさん、2 場面目は 2 色。明るい人型が場面をまたいで動き、最後は消える (空の列だけになる)。"""
    from PIL import Image
    folder.mkdir()
    rng = np.random.default_rng(0)
    for i in range(60):
        if i < 30:
            arr = rng.integers(0, 255, (24, 32, 3), dtype=np.uint8)
        else:
            arr = np.zeros((24, 32, 3), np.uint8)
            arr[:] = (20, 20, 120)
        arr[4:20, 8 + i % 10:16 + i % 10] = (250, 250, 250) if i < 45 else (250, 240, 200)
        if i >= 50:
            arr[:] = (20, 20, 120)
        Image.fromarray(arr).save(folder / f"f_{i:04d}.png")
    return folder


def test_inflate_survives_palette_change_between_scenes(tmp_path):
    """自動パレットで場面ごとに色の数が変わっても、2.5D の空の列の色に古いパレットの番号が残らない (以前は落ちた)。"""
    frames = _scene_change_frames(tmp_path / "frames")
    out = tmp_path / "out.js"
    argv = ["--frames-dir", str(frames), "--output", str(out), "--width", "32", "--height", "24", "--fps", "10",
            "--mode", "inflate", "--depth", "8", "--palette", "auto", "--device", "cpu", "--no-adaptive-fps"]
    assert convert.main(argv) == 0
    data = load_frame_data(out)
    assert data["adaptive_palette"] and len(data["level_blocks"]) > 1
    assert sum(1 for _ in iter_decoded_frames(data)) == 60


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg が必要")
def test_mask_video_decides_the_inflated_shape(tmp_path):
    """--mask-video の白い所だけを立体にする (明るさに関係なく、色は元の映像から)。"""
    from PIL import Image
    frames = tmp_path / "frames"
    masks = tmp_path / "masks"
    frames.mkdir()
    masks.mkdir()
    for i in range(10):
        # 元の映像は全面が明るい (明るさで分けると全部が立体になる)
        Image.fromarray(np.full((24, 32, 3), 230, np.uint8)).save(frames / f"f_{i:04d}.png")
        mask = np.zeros((24, 32, 3), np.uint8)
        mask[6:18, 10:20] = 255
        Image.fromarray(mask).save(masks / f"m_{i:04d}.png")
    video = tmp_path / "in.mp4"
    mask_video = tmp_path / "mask.mp4"
    for src, dst in ((frames / "f_%04d.png", video), (masks / "m_%04d.png", mask_video)):
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-framerate", "10", "-i", str(src), "-c:v", "libx264",
                        "-pix_fmt", "yuv444p", "-crf", "0", str(dst)], check=True)
    out = tmp_path / "out.js"
    argv = ["--input-video", str(video), "--mask-video", str(mask_video), "--output", str(out), "--width", "32",
            "--height", "24", "--fps", "10", "--mode", "inflate", "--depth", "8", "--palette", "full",
            "--device", "cpu", "--no-adaptive-fps"]
    assert convert.main(argv) == 0
    data = load_frame_data(out)
    *_, (volume, _rgb) = iter_decoded_frames(data)
    columns = (volume != data["air_level"]).any(axis=0)
    expected = np.zeros((24, 32), bool)
    expected[6:18, 10:20] = True
    assert np.array_equal(columns, expected)


def test_mask_video_needs_extrude_or_inflate(tmp_path):
    with pytest.raises(SystemExit):
        convert.main(["--frames-dir", str(tmp_path), "--output", str(tmp_path / "x.js"), "--mode", "relief",
                      "--mask-video", "m.mp4"])


# --- 閉じた空洞の埋め (だまし絵の変換) ---

def _enclosed_air_reference(is_air):
    """縁から 6 方向に空気をたどる幅優先探索 (遅いが単純な正解)。閉じた空洞 (縁とつながらない空気) の真偽配列。"""
    from collections import deque

    outside = np.zeros_like(is_air)
    queue = deque()
    for d, row, x in np.argwhere(is_air):
        if d in (0, is_air.shape[0] - 1) or row in (0, is_air.shape[1] - 1) or x in (0, is_air.shape[2] - 1):
            outside[d, row, x] = True
            queue.append((d, row, x))
    while queue:
        d, row, x = queue.popleft()
        for step in ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)):
            n = (d + step[0], row + step[1], x + step[2])
            if all(0 <= v < s for v, s in zip(n, is_air.shape)) and is_air[n] and not outside[n]:
                outside[n] = True
                queue.append(n)
    return is_air & ~outside


def _hollow_cube(hole=None, wall=5, left=3):
    """9×9×9 の空気の中の、厚さ 1 の殻の立方体 (1〜7、内側の 2〜6 が空洞)。左の壁 (x = 1) だけ別の番号。hole のマスの殻は抜く。"""
    volume = np.full((9, 9, 9), AIR_LEVEL, np.int16)
    volume[1:8, 1:8, 1:8] = wall
    volume[1:8, 1:8, 1] = left
    volume[2:7, 2:7, 2:7] = AIR_LEVEL
    if hole is not None:
        volume[hole] = AIR_LEVEL
    return volume


@pytest.fixture(params=["numpy", "scipy", "auto"])
def backend(request, monkeypatch):
    """閉じた空洞を求める方法: numpy だけ (scipy が無いときと同じ)、scipy.ndimage だけ (あれば)、ふだんの切り替え (あれば)。"""
    if request.param == "numpy":
        monkeypatch.setattr(volume_module, "_NDIMAGE", None)
    else:
        monkeypatch.setattr(volume_module, "_NDIMAGE", pytest.importorskip("scipy.ndimage"))
        if request.param == "scipy":
            monkeypatch.setattr(volume_module, "NUMPY_MAX_SWEEPS", 0)  # numpy を試さずに scipy に任せる
    return request.param


def test_fill_enclosed_air_fills_a_closed_cavity_from_the_left_neighbour(backend):
    volume = _hollow_cube()
    original = volume.copy()
    filled = fill_enclosed_air(volume, AIR_LEVEL)
    assert np.array_equal(volume, original), "入力は書き換えない"
    assert filled.dtype == volume.dtype and filled.shape == volume.shape
    expected = original.copy()
    expected[2:7, 2:7, 2:7] = 3  # 空洞のランの左隣 (x = 1) の壁の番号
    assert np.array_equal(filled, expected)


@pytest.mark.parametrize("hole", [(4, 4, 1), (4, 4, 7), (1, 4, 4), (7, 4, 4), (4, 1, 4), (4, 7, 4)])
def test_fill_enclosed_air_keeps_a_cavity_that_opens_to_the_outside(backend, hole):
    """殻の 1 マスに穴があれば (どの壁でも)、中は外とつながっているので埋めない。"""
    volume = _hollow_cube(hole=hole)
    assert np.array_equal(fill_enclosed_air(volume, AIR_LEVEL), volume)


def test_fill_enclosed_air_takes_the_value_left_of_each_run(backend):
    """同じ x の列で、空洞のランごとに左隣の実体の値で埋める (柱をはさむ 2 つの空洞は別の値)。"""
    volume = np.full((5, 5, 9), AIR_LEVEL, np.int16)
    volume[1:4, 1:4, 1:8] = 6
    volume[1:4, 1:4, 1] = 7
    volume[1:4, 1:4, 4] = 9
    volume[2, 2, [2, 3, 5, 6]] = AIR_LEVEL
    filled = fill_enclosed_air(volume, AIR_LEVEL)
    assert filled[2, 2].tolist() == [AIR_LEVEL, 7, 7, 7, 9, 9, 9, 6, AIR_LEVEL]
    changed = np.argwhere(filled != volume)
    assert len(changed) == 4 and set(map(tuple, changed)) == {(2, 2, x) for x in (2, 3, 5, 6)}


def _tunnel_volume(closed):
    """11×11×11 の詰まった塊 (1〜9) に、曲がりくねった細い通路と奥の部屋を掘る。入口 (左の面) を埋めると閉じた空洞になる。"""
    volume = np.full((11, 11, 11), AIR_LEVEL, np.int16)
    volume[1:10, 1:10, 1:10] = 5
    corners = [(5, 2, 1), (5, 2, 8), (5, 4, 8), (5, 4, 2), (5, 6, 2), (5, 6, 8), (5, 8, 8), (5, 8, 3), (3, 8, 3), (3, 4, 3),
               (3, 4, 6), (7, 4, 6), (7, 6, 6)]
    for (d0, r0, x0), (d1, r1, x1) in zip(corners, corners[1:]):
        volume[min(d0, d1):max(d0, d1) + 1, min(r0, r1):max(r0, r1) + 1, min(x0, x1):max(x0, x1) + 1] = AIR_LEVEL
    if closed:
        volume[5, 2, 1] = 5
    return volume


def test_fill_enclosed_air_follows_a_winding_tunnel(backend):
    """入口のある長い曲がりくねった通路は外とつながっている (埋めない)。入口をふさぐと、通路ぜんぶが空洞になる。"""
    opened = _tunnel_volume(closed=False)
    assert np.array_equal(fill_enclosed_air(opened, AIR_LEVEL), opened)
    closed = _tunnel_volume(closed=True)
    filled = fill_enclosed_air(closed, AIR_LEVEL)
    tunnel = closed[1:10, 1:10, 1:10] == AIR_LEVEL
    assert tunnel.sum() > 40
    assert (filled[1:10, 1:10, 1:10] != AIR_LEVEL).all() and (filled[0] == AIR_LEVEL).all()


@pytest.mark.parametrize("shape", [(1, 1, 1), (3, 3, 3), (1, 5, 4), (6, 1, 2)])
def test_fill_enclosed_air_leaves_volumes_without_cavities_alone(backend, shape):
    air = np.full(shape, AIR_LEVEL, np.int16)
    assert np.array_equal(fill_enclosed_air(air, AIR_LEVEL), air)
    solid = np.full(shape, 4, np.int16)
    assert np.array_equal(fill_enclosed_air(solid, AIR_LEVEL), solid)
    pocket = np.full((5, 5, 5), 4, np.int16)
    pocket[2, 2, 2] = AIR_LEVEL
    assert fill_enclosed_air(pocket, AIR_LEVEL)[2, 2, 2] == 4


def _random_air_masks(seed, count=120):
    rng = np.random.default_rng(seed)
    for trial in range(count):
        shape = tuple(int(v) for v in rng.integers(1, 11, size=3))
        density = rng.uniform(0.2, 0.8)
        air = rng.random(shape) < density
        if trial % 3 == 0:
            # 実体を中ほどの小さな範囲にだけ置く (まわりは全部空気)
            air = np.ones(shape, bool)
            low = [int(rng.integers(0, s)) for s in shape]
            high = [int(rng.integers(l, s)) + 1 for l, s in zip(low, shape)]
            inner = tuple(slice(l, h) for l, h in zip(low, high))
            air[inner] = rng.random(air[inner].shape) < density
        yield air


def test_enclosed_air_matches_the_flood_fill_reference(backend):
    for air in _random_air_masks(11):
        reference = _enclosed_air_reference(air)
        assert np.array_equal(enclosed_air(air), reference)
        assert np.array_equal(volume_module._enclosed_air_numpy(air), reference)


def test_enclosed_air_leaves_the_long_numpy_pass_to_scipy(monkeypatch):
    """入り組んだ立体 (ばらばらの点) は numpy の処理が長くかかるので、上限の回数で止めて、scipy があればそれに任せる。
    scipy が無ければ numpy で最後まで続ける。どちらでも結果は同じ。"""
    rng = np.random.default_rng(3)
    noise = rng.random((24, 24, 24)) < 0.35
    assert volume_module._enclosed_air_numpy(noise, volume_module.NUMPY_MAX_SWEEPS) is None
    assert volume_module._enclosed_air_numpy(_tunnel_volume(True) == AIR_LEVEL, volume_module.NUMPY_MAX_SWEEPS) is not None
    reference = _enclosed_air_reference(noise)
    monkeypatch.setattr(volume_module, "_NDIMAGE", None)
    assert np.array_equal(enclosed_air(noise), reference)
    monkeypatch.setattr(volume_module, "_NDIMAGE", pytest.importorskip("scipy.ndimage"))
    assert np.array_equal(enclosed_air(noise), reference)


def test_enclosed_air_numpy_matches_scipy():
    """scipy が無い環境 (ブラウザ版・EXE) で使う numpy の処理が、scipy.ndimage と同じ結果を返す。"""
    ndimage = pytest.importorskip("scipy.ndimage")
    rng = np.random.default_rng(5)
    masks = list(_random_air_masks(23)) + [rng.random((24, 30, 20)) < p for p in (0.3, 0.6, 0.9)]
    masks += [_tunnel_volume(closed) == AIR_LEVEL for closed in (False, True)]
    for air in masks:
        assert np.array_equal(volume_module._enclosed_air_numpy(air), volume_module._enclosed_air_scipy(air, ndimage))


def _write_hollow_cube_vox(path):
    palette = np.zeros((256, 4), np.uint8)
    palette[0] = (230, 40, 40, 255)
    shell = [[x, y, z, 1] for x in range(1, 8) for y in range(1, 8) for z in range(1, 8) if {x, y, z} & {1, 7}]
    path.write_bytes(write_vox([((9, 9, 9), np.array(shell, np.uint8))], palette))


def test_vox_conversion_fills_closed_cavities_only_for_anamorphic_view(tmp_path):
    """だまし絵 (--view anamorphic) の .vox の変換だけが、閉じた空洞を埋める。ブロックで置く方は殻のまま。"""
    _write_hollow_cube_vox(tmp_path / "shell.vox")
    solid = {}
    for view in ("blocks", "anamorphic"):
        data = convert_vox(tmp_path / "shell.vox", tmp_path / f"{view}.js", "--view", view)
        *_, (volume, _rgb) = iter_decoded_frames(data)
        solid[view] = int((volume != AIR_LEVEL).sum())
    assert solid["blocks"] == 7 ** 3 - 5 ** 3
    assert solid["anamorphic"] == 7 ** 3
    # 埋めたほうが符号化のランが長くなるので、データは大きくならない
    assert (tmp_path / "anamorphic.js").stat().st_size <= (tmp_path / "blocks.js").stat().st_size
