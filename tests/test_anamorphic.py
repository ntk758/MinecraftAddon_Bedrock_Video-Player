"""視点に合わせた立体のだまし絵 (--view anamorphic) のテスト。

立体の人物はワールドに置かず、見ている人の目から人物を見たとおりに、後ろの壁と足元の床へ塗る。
  - codec.js の projectToStage: 目から壁と床を見た絵が、目から人物を直接見た絵と一致する (tests/js/stage_view.mjs)
  - main.js: 壁と床だけを置き、置いた色が「最後のフレームの人物を、プレイヤーの目から写した盤面」と一致する。
    プレイヤーが動くと、再生が終わった後でも描き直す
"""
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
from mvcodec.decode import iter_decoded_frames, load_frame_data  # noqa: E402
from test_playback_control import drive  # noqa: E402
from test_volume3d import convert_vox, make_depth_walk  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node が必要")

WALL, FLOOR = 30000, 30001
WALL_BLOCK, FLOOR_BLOCK = "minecraft:light_gray_concrete", "minecraft:black_concrete"
EYE_HEIGHT = 1.62


def run_node(script, args=(), stdin=None):
    work = ROOT / "tests" / "js"
    tmp = Path(subprocess.check_output(["node", "-e", "console.log(require('os').tmpdir())"], text=True).strip())
    # codec.js と同じフォルダで動かす (import "./codec.js")
    run_dir = tmp / f"bvp_stage_{script.replace('.', '_')}"
    run_dir.mkdir(exist_ok=True)
    shutil.copy(ROOT / "codec.js", run_dir / "codec.js")
    shutil.copy(work / script, run_dir / script)
    (run_dir / "package.json").write_text(json.dumps({"type": "module"}))
    result = subprocess.run(["node", script, *args], cwd=run_dir, input=stdin, capture_output=True, text=True,
                            encoding="utf-8", timeout=300)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def figure_eye(dx, y, dz):
    """人物 (stage_view.mjs の 24×40×8) の足元の中心から横に dx・手前に dz、高さ y の目の位置 (舞台の内側の座標)。"""
    g = run_node("stage_view.mjs", ["[]"])["geom"]
    return {"x": g["side"] + 12 + dx, "y": y, "z": 1 + g["gap"] + 4 + dz}


@pytest.mark.parametrize("offset", [
    (6, 20, 61),    # 正面
    (6, 45, 51),    # 上から見下ろす
    (-13, 30, 31),  # 左斜め前の近く
    (22, 8, 41),    # 右斜め前の低い所
    (6, 3, 36),     # 床すれすれ
])
def test_stage_looks_like_the_figure_from_the_eye(offset):
    out = run_node("stage_view.mjs", [json.dumps([figure_eye(*offset)])])
    r = out["results"][0]
    assert r["drawn"]
    total = r["figurePixels"]
    assert total > 500
    # ずれるのはブロックの境目 (1 ブロック = 1 色) だけ
    assert r["matched"] / total > 0.85, r
    assert r["missing"] / total < 0.08, r
    assert r["extra"] / total < 0.10, r
    assert r["offStage"] / total < 0.03, r


def test_stage_layout_and_eye_outside_is_refused():
    out = run_node("stage_view.mjs", [json.dumps([{"x": 24, "y": 20, "z": 0.5}, {"x": 24, "y": 0.5, "z": 40},
                                                  {"x": -3, "y": 20, "z": 40}])])
    g = out["geom"]
    # 盤面の 0 番は後ろの壁の左上 (一番高い所)。左の壁が x = 0 の列なので、後ろの壁と床は x = 1 から
    assert out["wallTopLeft"] == {"x": 1, "y": g["wallHeight"], "z": 0}
    assert out["floorLast"] == {"x": g["stageWidth"], "y": 0, "z": g["floorRows"]}
    assert out["leftFirst"] == {"x": 0, "y": g["wallHeight"], "z": 1}
    assert out["rightLast"] == {"x": g["stageWidth"] + 1, "y": 1, "z": g["floorRows"]}
    # 後ろの壁の後ろ、床の下、左右の壁の外からは写せない
    assert [r["drawn"] for r in out["results"]] == [False, False, False]


@pytest.mark.parametrize("offset", [
    (28, 20, 2),    # 右の真横 (後ろの壁と床だけでは人物の上半分が写らない)
    (-28, 30, 8),   # 左の真横、高い所
])
def test_side_walls_catch_side_views(offset):
    r = run_node("stage_view.mjs", [json.dumps([figure_eye(*offset)])])["results"][0]
    assert r["drawn"]
    assert r["offStage"] / r["figurePixels"] < 0.03, r
    assert r["matched"] / r["figurePixels"] > 0.85, r


def expected_stage(data, volume, eye):
    """最後のフレームの立体を、目 eye (舞台の原点から) から写した盤面のブロック名。"""
    video = {k: data[k] for k in ("width", "height", "depth", "air_level")}
    out = run_node("project_stage.mjs", stdin=json.dumps({"video": video, "figure": volume.reshape(-1).tolist(),
                                                          "eye": eye, "wall": WALL, "floor": FLOOR}))
    assert out["drawn"]
    blocks = [spec["block"] for spec in data["level_blocks"]]
    names = {WALL: WALL_BLOCK, FLOOR: FLOOR_BLOCK, -1: None}
    return out["geom"], [names[v] if v in names else blocks[v] for v in out["board"]]


def stage_offset(geom, p):
    """codec.js の stageCellOffset と同じ規則で、盤面の p 番目のマスの舞台の原点からの位置 (区画の余りは None)。"""
    row, column = divmod(p, geom["rowWidth"])
    wall, floor = geom["wallHeight"], geom["floorRows"]
    if row < wall + floor:
        if column >= geom["stageWidth"]:
            return None
        return (1 + column, wall - row, 0) if row < wall else (1 + column, 0, 1 + row - wall)
    if column >= floor:
        return None
    r = (row - wall - floor) % wall
    x = 0 if row < 2 * wall + floor else geom["stageWidth"] + 1
    return (x, wall - r, 1 + column)


def placed_stage(out, geom, anchor):
    """モックのワールドに置かれたブロックを、舞台の盤面の順に読む (区画の余りは None)。"""
    world = {(x, y, z): block for x, y, z, block in out["allBlocks"]}
    cells = []
    for p in range(geom["cells"]):
        offset = stage_offset(geom, p)
        cells.append(None if offset is None else
                     world.get((anchor["x"] + offset[0], anchor["y"] + offset[1], anchor["z"] + offset[2])))
    return cells, world


def check_playback(tmp_path, path, data):
    final = [board for board, _ in iter_decoded_frames(data)][-1]
    start = {"x": 0.5, "y": 64, "z": 100.5}
    moved = {"x": -6.5, "y": 70, "z": 92.5}
    steps = [{"x": start["x"], "y": start["y"], "z": start["z"]},
             {"event": "badapple:setup"}, {"event": "badapple:start"}, {"until": "bvp.playback_finished"},
             {"snap": "end"},
             # 再生が終わった後でも、プレイヤーが動けば描き直す
             {"x": moved["x"], "y": moved["y"], "z": moved["z"]}, {"tick": 20}]
    out = drive(tmp_path / "addon", {"stage": path}, steps)
    assert out["errors"] == []
    anchor = out["snaps"][0]["anchor"]
    # 舞台の内側の座標は、左の壁の 1 列ぶん東から
    eye = {"x": moved["x"] - anchor["x"] - 1, "y": moved["y"] + EYE_HEIGHT - anchor["y"], "z": moved["z"] - anchor["z"]}
    geom, expected = expected_stage(data, final, eye)
    placed, world = placed_stage(out, geom, anchor)
    assert placed == expected
    # 人物そのもの (舞台の上の空間) には何も置かない: 置いたブロックはすべて舞台 (床と 3 枚の壁) のマス
    stage_cells = {tuple(a + o for a, o in zip((anchor["x"], anchor["y"], anchor["z"]), offset))
                   for offset in (stage_offset(geom, p) for p in range(geom["cells"])) if offset is not None}
    assert set(world) <= stage_cells
    # 人物が舞台に写っている
    figure_cells = sum(1 for b in placed if b not in (WALL_BLOCK, FLOOR_BLOCK, None))
    assert figure_cells > 20
    return out, geom


def test_vox_animation_is_drawn_on_wall_and_floor(tmp_path):
    path = make_depth_walk(tmp_path)
    data = convert_vox(path, tmp_path / "walk.js", "--view", "anamorphic")
    assert data["view"] == "anamorphic"
    check_playback(tmp_path, tmp_path / "walk.js", data)


def test_inflated_silhouette_is_drawn_on_wall_and_floor(tmp_path):
    """2.5D (色と厚みの平面で送る inflate) でも、組み立てた立体を写す。"""
    frames = tmp_path / "frames"
    frames.mkdir()
    for i in range(12):
        arr = np.zeros((40, 32, 3), np.uint8)
        arr[6:36, 10 + i // 3:20 + i // 3] = (240, 200, 60)
        arr[2:8, 12 + i // 3:18 + i // 3] = (230, 60, 60)
        Image.fromarray(arr).save(frames / f"frame_{i:04d}.png")
    out = tmp_path / "inflate.js"
    argv = ["--frames-dir", str(frames), "--output", str(out), "--width", "32", "--height", "40", "--fps", "10",
            "--threads", "2", "--device", "cpu", "--mode", "inflate", "--depth", "8", "--view", "anamorphic",
            "--no-adaptive-fps"]
    assert convert.main(argv) == 0
    data = load_frame_data(out)
    assert data["layout"] == "heightfield" and data["view"] == "anamorphic"
    check_playback(tmp_path, out, data)


def test_view_option_needs_a_3d_mode(tmp_path):
    with pytest.raises(SystemExit):
        convert.main(["--frames-dir", str(tmp_path), "--output", str(tmp_path / "x.js"), "--view", "anamorphic"])
