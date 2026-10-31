"""視点に合わせた立体のだまし絵 (--view anamorphic) のテスト。

立体の人物はワールドに置かず、見ている人の目から人物を見たとおりに、後ろの壁と足元の床へ塗る。
  - codec.js の projectToStage: 目から壁と床を見た絵が、目から人物を直接見た絵と一致する (tests/js/stage_view.mjs)
  - main.js: 壁と床だけを置き、置いた色が「最後のフレームの人物を、プレイヤーの目から写した盤面」と一致する。
    プレイヤーが動くと、再生が終わった後でも描き直す。一時停止中も、描きかけのフレームをそのときの目で最後まで描く
"""
import json
import math
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import convert  # noqa: E402
from mvcodec.decode import iter_decoded_frames, load_frame_data  # noqa: E402
from mvcodec.volume import write_vox  # noqa: E402
from test_playback_control import drive  # noqa: E402
from test_volume3d import convert_vox, make_depth_walk  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node が必要")

WALL, FLOOR, SIDE = 30000, 30001, 30002
WALL_BLOCK, FLOOR_BLOCK = "minecraft:light_gray_concrete", "minecraft:black_concrete"
EYE_HEIGHT = 1.62


def run_node(script, args=(), stdin=None):
    work = ROOT / "tests" / "js"
    # 並列に走らせても codec.js を上書きし合わないよう、呼ぶたびに別のフォルダで動かす
    with tempfile.TemporaryDirectory(prefix=f"bvp_stage_{script.replace('.', '_')}_") as folder:
        return _run_node_in(Path(folder), work, script, args, stdin)


def _run_node_in(run_dir, work, script, args, stdin):
    # codec.js と同じフォルダで動かす (import "./codec.js")
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


def expected_stages(data, volumes, eye):
    """立体 (複数) を、同じ目 eye (舞台の原点から) から写した盤面のブロック名を、立体ごとに並べて返す。"""
    video = {k: data[k] for k in ("width", "height", "depth", "air_level")}
    out = run_node("project_stage.mjs", stdin=json.dumps({"video": video, "figures": [v.reshape(-1).tolist() for v in volumes],
                                                          "eye": eye, "wall": WALL, "floor": FLOOR, "side": SIDE}))
    assert out["drawn"]
    blocks = [spec["block"] for spec in data["level_blocks"]]
    # 左右の壁の人物の写らないマスは空気
    names = {WALL: WALL_BLOCK, FLOOR: FLOOR_BLOCK, SIDE: "minecraft:air", -1: None}
    return out["geom"], [[names[v] if v in names else blocks[v] for v in board] for board in out["boards"]]


def expected_stage(data, volume, eye):
    """最後のフレームの立体を、目 eye (舞台の原点から) から写した盤面のブロック名。"""
    geom, boards = expected_stages(data, [volume], eye)
    return geom, boards[0]


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
    figure_cells = sum(1 for b in placed if b not in (WALL_BLOCK, FLOOR_BLOCK, "minecraft:air", None))
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


@pytest.mark.parametrize("dims", [(24, 40, 8), (32, 56, 20), (128, 72, 16), (9, 13, 31)])
def test_python_stage_geometry_matches_codec_js(dims):
    """変換ツール (背景の映像の大きさを決める) と codec.js の舞台の大きさが同じ。"""
    from mvcodec.stage import stage_geometry
    width, height, depth = dims
    video = {"width": width, "height": height, "depth": depth, "air_level": 127}
    out = run_node("project_stage.mjs", stdin=json.dumps({"video": video, "figure": [127] * (width * height * depth),
                                                          "eye": {"x": 1, "y": 2, "z": 3}, "wall": WALL, "floor": FLOOR}))
    js = out["geom"]
    assert stage_geometry(width, height, depth) == {key: js[key] for key in stage_geometry(width, height, depth)}


class _Var:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg が必要")
@pytest.mark.parametrize("palette", ["full", "auto"])
def test_background_video_plays_on_the_back_wall(tmp_path, palette):
    """GUI の組み立て (画面なし) で、.vox の人物に背景の映像を付ける。背景の映像は後ろの壁の大きさの、一覧に出さない
    動画になり、再生は背景の映像の長さ (人物は繰り返す)、音声は背景の映像のもの、後ろの壁は背景の映像の最後の
    フレームの上に人物を描いたものになる。"""
    import queue
    import zipfile

    import video_player_gui as gui
    from gui_i18n import Translator

    vox = make_depth_walk(tmp_path)  # 10 フレーム (10 fps で 1 秒)
    background = tmp_path / "bg.mp4"
    # 2.5 秒ごとに場面が変わる 5 秒の映像 (自動パレットが場面ごとに切り替わる。場面は 20 フレーム以上) と音声
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=10:duration=2.5",
                    "-f", "lavfi", "-i", "mandelbrot=size=320x180:rate=10", "-f", "lavfi", "-i", "sine=frequency=440:duration=5",
                    "-filter_complex", "[1:v]trim=duration=2.5,setpts=PTS-STARTPTS[m];[0:v][m]concat=n=2:v=1[v]",
                    "-map", "[v]", "-map", "2:a", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    str(background)], check=True)

    class Headless:
        def __init__(self):
            self.tr = Translator("ja")
            self.messages = queue.Queue()
            self.keyframe_interval_var = _Var(30)
            self.perceptual_var = _Var(True)

        def _run(self, command):
            gui.PackBuilderApp._run(self, command)

        def _extract_audio(self, *args):
            return gui.PackBuilderApp._extract_audio(self, *args)

        def _convert_stage_background(self, *args):
            return gui.PackBuilderApp._convert_stage_background(self, *args)

    app = Headless()
    output = tmp_path / "stage.mcaddon"
    gui.PackBuilderApp._build_pack(app, [{"file_path": str(vox), "video_id": "walk", "thumb_sec": 0}], output,
                                   "Stage", "badapple", 64, 48, 10, None, palette, "none", "cpu", "screen", 16,
                                   None, True, str(background))
    log = []
    while not app.messages.empty():
        log.append(str(app.messages.get()))
    assert output.is_file(), "\n".join(log)

    scripts = tmp_path / "addon"
    with zipfile.ZipFile(output) as z:
        z.extractall(scripts / "pack")
        names = z.namelist()
    assert any("sounds/music/walk_bg/chunk_0" in n for n in names)
    script_dir = next(p.parent for p in (scripts / "pack").rglob("main.js"))
    videos_js = (script_dir / "videos.js").read_text(encoding="utf-8")
    assert '"id":"walk_bg"' in videos_js and '"hidden":true' in videos_js
    figure_data = load_frame_data(script_dir / "frames_walk.js")
    background_data = load_frame_data(script_dir / "frames_walk_bg.js")
    assert figure_data["stage_background"] == "walk_bg"
    if palette == "auto":
        assert background_data["adaptive_palette"] and len(background_data["level_blocks"]) > 1

    # main.js をモックで動かす (videos.js はアドオンのもの)
    work = tmp_path / "play"
    shutil.copytree(script_dir, work)
    shutil.copy(ROOT / "tests" / "js" / "drive_main.mjs", work / "drive_main.mjs")
    for module, source in (("server", "server.js"), ("server-ui", "server-ui.js")):
        target = work / "node_modules" / "@minecraft" / module
        target.mkdir(parents=True)
        (target / "package.json").write_text(json.dumps({"name": f"@minecraft/{module}", "type": "module", "main": "index.js"}))
        shutil.copy(ROOT / "tests" / "js" / "minecraft_mock" / source, target / "index.js")
    (work / "package.json").write_text(json.dumps({"type": "module"}))
    steps = [{"event": "badapple:setup"}, {"event": "badapple:start"}, {"until": "bvp.playback_finished"},
             {"snap": "end", "video": "walk"}, {"event": "badapple:list"}]
    result = subprocess.run(["node", "drive_main.mjs", json.dumps(steps)], cwd=work, capture_output=True, text=True,
                            encoding="utf-8", timeout=600)
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert out["errors"] == []
    # 一覧に背景の映像は出さない
    listing = [m for m in out["messages"] if "bvp.video_list_item" in m]
    assert len(listing) == 1 and "walk_bg" not in listing[0]
    # 再生したのは背景の映像の長さ (50 フレーム)。人物 (10 フレーム) は繰り返した
    assert background_data["frame_count"] == 50 and figure_data["frame_count"] == 10
    assert out["stats"]["blocksPlaced"] > 0

    # 後ろの壁 = 背景の映像の最後のフレームの上に、人物の最後のフレーム (49 % 10 = 9) を描いたもの
    figures = [board for board, _ in iter_decoded_frames(figure_data)]
    backgrounds = [board for board, _ in iter_decoded_frames(background_data)]
    anchor = out["snaps"][0]["anchor"]
    eye = {"x": 0 - anchor["x"] - 1, "y": 64 + EYE_HEIGHT - anchor["y"], "z": 100 - anchor["z"]}
    video = {k: figure_data[k] for k in ("width", "height", "depth", "air_level")}
    projected = run_node("project_stage.mjs", stdin=json.dumps({
        "video": video, "figure": figures[49 % 10].reshape(-1).tolist(), "eye": eye, "wall": WALL, "floor": FLOOR, "side": SIDE,
        "wallImage": backgrounds[49].reshape(-1).tolist()}))
    geom = projected["geom"]
    assert (background_data["width"], background_data["height"]) == (geom["stageWidth"], geom["wallHeight"])
    # 自動パレットは GOP ごとにパレットが違う。最後のフレームは最後の GOP
    last_palette = lambda data: data["level_blocks"][-1] if data.get("adaptive_palette") else data["level_blocks"]  # noqa: E731
    figure_blocks = [spec["block"] for spec in last_palette(figure_data)]
    background_blocks = [spec["block"] for spec in last_palette(background_data)]
    names = {WALL: WALL_BLOCK, FLOOR: FLOOR_BLOCK, SIDE: "minecraft:air", -1: None}
    expected = [names[v] if v in names else background_blocks[v - 20000] if v >= 20000 else figure_blocks[v]
                for v in projected["board"]]
    placed, _world = placed_stage(out, geom, anchor)
    assert placed == expected
    wall_cells = geom["stageWidth"] * geom["wallHeight"]
    assert len(set(expected[:wall_cells])) > 5  # 後ろの壁に背景の映像 (いろいろな色) が写っている


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg が必要")
@pytest.mark.parametrize("mode, model_class, other_class", [("person", "PersonSegmenter", "AnimeSegmenter"),
                                                            ("person_anime", "AnimeSegmenter", "PersonSegmenter")])
def test_person_mode_builds_figure_and_background(tmp_path, monkeypatch, mode, model_class, other_class):
    """GUI の「人物を切り抜いて立体に (AI)」(実写向き・アニメ向き) + だまし絵: 人物の範囲をふくらませた立体と、
    人物を消した背景の映像 (元の音声付き) ができる。AI のモデルの代わりに、明るい四角を人物とする切り抜きを使う。"""
    import queue
    import zipfile

    import numpy as np

    import mvcodec.person as person
    import video_player_gui as gui
    from gui_i18n import Translator

    class FakeSegmenter:
        def __init__(self, **_options):
            pass

        def __call__(self, rgb):
            return (rgb.mean(axis=2) > 200).astype(np.float32)

    class WrongSegmenter:
        def __init__(self, **_options):
            raise AssertionError(f"{mode} で {other_class} を使った")

    monkeypatch.setattr(person, model_class, FakeSegmenter)
    monkeypatch.setattr(person, other_class, WrongSegmenter)
    video = tmp_path / "dance.mp4"
    # 暗い背景の前で、白い四角 (人物) が左右に動く 3 秒の映像と音声
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=0x203060:size=160x90:rate=10:duration=3",
                    "-f", "lavfi", "-i", "sine=frequency=330:duration=3",
                    "-vf", "drawbox=x='40+20*sin(t*3)':y=20:w=30:h=60:color=white:t=fill",
                    "-c:v", "libx264", "-pix_fmt", "yuv444p", "-crf", "0", "-c:a", "aac", "-shortest", str(video)],
                   check=True)

    class Headless:
        def __init__(self):
            self.tr = Translator("ja")
            self.messages = queue.Queue()
            self.keyframe_interval_var = _Var(30)
            self.perceptual_var = _Var(True)

        def _run(self, command):
            gui.PackBuilderApp._run(self, command)

        def _extract_audio(self, *args):
            return gui.PackBuilderApp._extract_audio(self, *args)

        def _convert_stage_background(self, *args):
            return gui.PackBuilderApp._convert_stage_background(self, *args)

        def _split_person(self, *args):
            return gui.PackBuilderApp._split_person(self, *args)

        def _person_process(self, argv):
            # GUI は別のプロセスで切り抜くが、テストでは偽の切り抜きを使うため同じプロセスで動かす
            return person.main(argv), ""

    app = Headless()
    output = tmp_path / "person.mcaddon"
    gui.PackBuilderApp._build_pack(app, [{"file_path": str(video), "video_id": "dance", "thumb_sec": 1}], output,
                                   "Person", "badapple", 128, 72, 10, None, "full", "none", "cpu", mode, 8,
                                   None, True, None)
    log = []
    while not app.messages.empty():
        log.append(str(app.messages.get()))
    assert output.is_file(), "\n".join(log)
    with zipfile.ZipFile(output) as z:
        names = z.namelist()
        figure = z.read("BP/scripts/frames_dance.js").decode("utf-8")
        background = z.read("BP/scripts/frames_dance_bg.js").decode("utf-8")
    figure_data = json.loads(figure[figure.index("{"):figure.rindex("}") + 1])
    background_data = json.loads(background[background.index("{"):background.rindex("}") + 1])
    assert figure_data["view"] == "anamorphic" and figure_data["stage_background"] == "dance_bg"
    assert figure_data["layout"] == "heightfield" and figure_data["solid_rule"] == "span"
    # 設定の 128 × 72 は、だまし絵の上限 (長い辺 80) に縮める (実機で 128 × 128 と背景 320 × 192 は重すぎた)
    assert (figure_data["width"], figure_data["height"]) == (80, 45)
    assert any("80 × 45" in line for line in log)
    # 音声は背景の映像のものだけ
    assert any("sounds/music/dance_bg/chunk_0" in n for n in names)
    assert not any("sounds/music/dance/" in n for n in names)
    # 立体になったのは人物 (白い四角) の所だけ
    *_, (volume, _rgb) = iter_decoded_frames(figure_data)
    columns = (volume != figure_data["air_level"]).any(axis=0)
    assert 0.05 < columns.mean() < 0.4
    # 背景の映像からは人物 (白) が消えている
    *_, (_board, rgb) = iter_decoded_frames(background_data)
    assert (rgb.reshape(-1, 3).mean(axis=1) > 200).mean() < 0.01


# --- 一時停止: 描きかけのフレームも、止めている間に動いた目で最後まで描く ---
# 以前は、フレームを置いている途中で止めると、描きかけのまま止まり、止めている間に目が動いても描き直さなかった
# (描き直しは、動いている iterator が無いときだけ始めていた)。目が動いた描き直しの途中で止めたときも、残りを描かなかった。
# また、シークの復元の途中で中断すると、人物は新しいフレームに変わっているのに、外から見える面の一覧は古いままだった。

# 舞台が 184×120 で、1 フレームに 7000 マスほど変わる立体。置くときは 500 マスごとに yield し (14 回ほど)、1 tick に
# 進める yield は 8 回までなので、どのフレームも 2 tick かかる (test_busy_frames_take_more_than_one_tick_to_place)。
# .vox の x・y・z は幅・奥行き・高さ。色の模様を毎フレーム変えながら、右へ 1 ブロックずつずらす
BUSY_SIZE = (64, 8, 80)
BUSY_SLAB = (56, 8, 74)
BUSY_FRAMES = 36
# プレイヤーの足元。START で setup し、舞台の左右中央・手前の WATCH から見る。MOVED と FAR は WATCH から離れた位置、
# OUTSIDE は舞台の床より下 (箱の外) で、見ている人がいない扱いになる
START = {"x": 0.5, "y": 64, "z": 100.5}
WATCH = {"x": 0.5, "y": 102, "z": 80.5}
MOVED = {"x": -14.5, "y": 97, "z": 74.5}
FAR = {"x": 40.5, "y": 112, "z": 70.5}
OUTSIDE = {"x": 0.5, "y": 50, "z": 80.5}
PAUSE = {"ui": [{"selection": 0}]}  # リモコンの 1 番目のボタン (一時停止・再開)
MAX_BLOCKS_PER_TICK = 4000  # main.js の MAX_BLOCKS_PER_TICK (500 マスごとの yield を 1 tick に 8 回まで)
# 再生を始めてから最初のフレームを置き始めるまでの tick 数 (開始待ち + 余り) と、1 フレームにかかる tick 数
FIRST_FRAME_TICKS, FRAME_TICKS = 8, 2


# コンクリートの色 (壁の薄灰色と床の黒は除く)。色が多いほど、変わったマスは色ごとの座標リストが小さくなり、
# 1 フレームの設置が多くの yield に分かれる (1 tick に進める yield は 8 回まで)
BUSY_COLORS = ((207, 213, 214), (224, 97, 0), (169, 48, 159), (36, 137, 199), (241, 175, 21), (94, 168, 24), (213, 101, 142),
               (55, 58, 62), (21, 119, 136), (100, 32, 156), (45, 47, 143), (96, 60, 32), (73, 91, 36), (142, 33, 33))


def make_busy_vox(path, frames=BUSY_FRAMES):
    palette = np.zeros((256, 4), np.uint8)
    palette[1:1 + len(BUSY_COLORS), :3] = BUSY_COLORS
    palette[1:1 + len(BUSY_COLORS), 3] = 255
    x, y, z = np.meshgrid(*(np.arange(n) for n in BUSY_SLAB), indexing="ij")
    models = []
    for t in range(frames):
        colors = 1 + (x + 2 * y + 3 * z + 3 * t) % len(BUSY_COLORS)
        voxels = np.stack([x + t % (BUSY_SIZE[0] - BUSY_SLAB[0] + 1), y, z, colors], axis=-1)
        models.append((BUSY_SIZE, voxels.reshape(-1, 4).astype(np.uint8)))
    path.write_bytes(write_vox(models, palette))


@pytest.fixture(scope="module")
def busy(tmp_path_factory):
    """fps 20 と fps 1 の動画。どちらも (frames_*.js のパス, 変換結果, 展開した立体のフレーム)。"""
    tmp = tmp_path_factory.mktemp("busy")
    make_busy_vox(tmp / "busy.vox")
    videos = {}
    for fps in (20, 1):
        out = tmp / f"busy_{fps}.js"
        data = convert_vox(tmp / "busy.vox", out, "--view", "anamorphic", "--fps", str(fps))
        videos[fps] = (out, data, [volume for volume, _ in iter_decoded_frames(data)])
    return videos


def setup_anchor(data):
    """START で setup したときの舞台の置き場所 (main.js の anchorForVideo と同じ規則)。"""
    from mvcodec.stage import stage_geometry
    geom = stage_geometry(data["width"], data["height"], data["depth"])
    size_x, size_z = geom["stageWidth"] + 2, 1 + geom["floorRows"]
    return {"x": math.floor(START["x"]) - size_x // 2, "y": math.floor(START["y"]),
            "z": math.floor(START["z"]) - size_z - math.ceil(data["height"] / 2)}


def stage_eye(position, anchor):
    """プレイヤーの足元 position (ワールドの座標) から見た、舞台の内側の座標の目の位置 (viewerEye と同じ規則)。"""
    return {"x": position["x"] - anchor["x"] - 1, "y": position["y"] + EYE_HEIGHT - anchor["y"], "z": position["z"] - anchor["z"]}


def stage_in(world, geom, anchor):
    """drive_main の {"world": ...} で記録したワールドの、舞台の盤面のブロック名 (置かれていないマスは None)。"""
    return placed_stage({"allBlocks": world["blocks"]}, geom, anchor)[0]


def changed_cells(a, b):
    return sum(1 for x, y in zip(a, b) if x != y)


def assert_is_one_of(cells, candidates, what):
    """舞台が候補のどれか 1 つと完全に一致する (2 つのフレーム・2 つの目の絵が混ざっていない)。その番号を返す。"""
    if cells in candidates:
        return candidates.index(cells)
    differing, index = min((changed_cells(cells, c), i) for i, c in enumerate(candidates))
    pytest.fail(f"{what}: 一番近い候補 (フレーム {index}) とも {differing} マス食い違う (全 {len(cells)} マス)")


def test_busy_frames_take_more_than_one_tick_to_place(busy, tmp_path):
    """この素材の前提: 1 フレームの設置は 1 tick で終わらない (fps 20 は 1 フレーム 1 tick の予定なので、遅れて続く)。"""
    path, data, _volumes = busy[20]
    steps = [dict(START), {"event": "badapple:setup"}, dict(WATCH), {"event": "badapple:start"},
             {"until": "bvp.playback_finished"}]
    out = drive(tmp_path / "addon", {"stage": path}, steps)
    assert out["errors"] == []
    # 開始待ちの 5 tick を除いて、1 フレームあたり 2 tick 以上かかる
    assert out["ticks"] - 5 >= 2 * data["frame_count"], out["ticks"]


def no_tick_budget(work):
    """drive の prepare に渡す。main.js の 1 tick の時間の上限 (TICK_TIME_BUDGET_MS) を事実上なくす。上限に当たると、
    遅い機械 (CI の共有ランナーなど) では 1 フレームにかかる tick が増え、止めたフレームの番号の見積りがずれるため。"""
    main = work / "main.js"
    text = main.read_text(encoding="utf-8")
    assert text.count("const TICK_TIME_BUDGET_MS = 30;") == 1
    main.write_text(text.replace("const TICK_TIME_BUDGET_MS = 30;", "const TICK_TIME_BUDGET_MS = 1e9;"), encoding="utf-8")


def chain(*prepares):
    """drive の prepare を、順に全部実行する 1 つにまとめる。"""
    def prepare(work):
        for each in prepares:
            each(work)
    return prepare


# ticks: 再生を始めてから止めるまでの tick 数。偶数は置いている途中のフレームで止まり、奇数 (21) は置き終えた直後で止まる
# (奇数は以前も正しく描き直した)
@pytest.mark.parametrize("ticks, leave", [(10, False), (20, False), (21, False), (30, False), (40, False), (30, True)])
def test_pause_in_the_middle_of_a_frame_redraws_for_the_moved_eye(busy, tmp_path, ticks, leave):
    """フレームを置いている途中で一時停止し、プレイヤーが動いたら、止めたときのフレームを、動いた先の目で最後まで描く
    (前のフレームの絵や前の目の絵と混ざらない)。続きを再生すれば、最後のフレームが期待どおりに描かれる。
    leave: 止めた後に箱の外へ出たら、最後に描いた目 (WATCH) のままで最後まで描く。"""
    path, data, volumes = busy[20]
    anchor = setup_anchor(data)
    after = OUTSIDE if leave else MOVED
    steps = [dict(START), {"event": "badapple:setup"}, dict(WATCH), {"event": "badapple:start"}, {"tick": ticks}, PAUSE,
             dict(after), {"tick": 30}, {"world": "paused"}, PAUSE, {"until": "bvp.playback_finished"}, {"world": "end"},
             {"snap": "anchor"}]
    out = drive(tmp_path / "addon", {"stage": path}, steps, prepare=no_tick_budget)
    assert out["errors"] == []
    assert out["snaps"][0]["anchor"] == anchor
    assert any("bvp.paused" in m for m in out["messages"])

    geom, boards = expected_stages(data, volumes, stage_eye(WATCH if leave else after, anchor))
    paused, end = (stage_in(world, geom, anchor) for world in out["worlds"])
    # 止めたときに置いていたフレーム (f) か、その 1 つ前 (f - 1) が、新しい目で完全に描かれている (描きかけではない)
    frame = assert_is_one_of(paused, boards, f"{ticks} tick で止めて動いた後の舞台")
    assert abs(frame - (ticks - FIRST_FRAME_TICKS) // FRAME_TICKS) <= 1, frame
    # 再開すると最後のフレームまで再生する
    assert end == boards[-1]


def test_pause_in_the_middle_of_a_redraw_finishes_the_redraw(busy, tmp_path):
    """目が動いたための描き直しの途中で一時停止しても、止めている間に描き直しを終える (以前は残りを描かなかった)。
    fps 1 の動画で、最初のフレームを置き終えた後の待ち時間にプレイヤーを大きく動かし、描き直しが始まった次の tick で止める。"""
    path, data, volumes = busy[1]
    anchor = setup_anchor(data)
    geom, (at_watch,) = expected_stages(data, volumes[:1], stage_eye(WATCH, anchor))
    _geom, (at_far,) = expected_stages(data, volumes[:1], stage_eye(FAR, anchor))
    # 前提: 描き直しで変わるマスは 1 tick (4000 マス) では置き終わらない
    assert changed_cells(at_watch, at_far) > 2 * MAX_BLOCKS_PER_TICK

    # 開始待ちの 5 tick が過ぎてから、最初のフレームを置き終えるのを待つ (次のフレームは 20 tick 後)
    steps = [dict(START), {"event": "badapple:setup"}, dict(WATCH), {"event": "badapple:start"}, {"tick": 6},
             {"settle": 3}, {"world": "drawn"}, dict(FAR), {"tick": 1}, {"world": "redrawing"}, PAUSE, {"tick": 30},
             {"world": "paused"}, {"snap": "anchor"}]
    out = drive(tmp_path / "addon", {"stage": path}, steps, prepare=no_tick_budget)
    assert out["errors"] == []
    assert out["snaps"][0]["anchor"] == anchor
    assert any("bvp.paused" in m for m in out["messages"])
    drawn, redrawing, paused = (stage_in(world, geom, anchor) for world in out["worlds"])
    # 前提: 最初のフレームを置き終えていて、描き直しは途中
    assert drawn == at_watch
    assert redrawing not in (at_watch, at_far)
    assert paused == at_far


def test_seek_interrupted_by_resume_leaves_a_stage_that_redraws_correctly(busy, tmp_path):
    """シークの復元の途中で再生を押して、すぐ一時停止しても、止めている間に動いた目の描き直しは、そのとき復元して
    あるフレームの人物を写す (以前は、外から見える面の一覧が古いままだった)。"""
    path, data, volumes = busy[20]
    anchor = setup_anchor(data)
    steps = [dict(START), {"event": "badapple:setup"}, dict(WATCH), {"event": "badapple:start"}, {"tick": 20}, PAUSE,
             {"ui": [{"selection": 5}, {"formValues": [1]}]},  # 1 秒 (20 フレーム目) へシーク
             {"tick": 2},
             PAUSE, PAUSE,                                      # 再生 (シークを中断する) → すぐ一時停止
             dict(MOVED), {"tick": 30}, {"world": "paused"}, {"snap": "anchor"}]
    out = drive(tmp_path / "addon", {"stage": path}, steps)
    assert out["errors"] == []
    assert out["snaps"][0]["anchor"] == anchor
    # 前提: シークは終わらないうちに中断した
    assert not any("bvp.seek_done" in m for m in out["messages"])
    geom, boards = expected_stages(data, volumes, stage_eye(MOVED, anchor))
    assert_is_one_of(stage_in(out["worlds"][0], geom, anchor), boards, "中断したシークの後に動いた目で描き直した舞台")


def test_stop_after_pausing_mid_frame_leaves_nothing_on_the_stage(busy, tmp_path):
    """フレームの途中で止めてから停止 (クリア) すると、その後にプレイヤーが動いても、舞台に何も描き直さない。"""
    path, _data, _volumes = busy[20]
    steps = [dict(START), {"event": "badapple:setup"}, dict(WATCH), {"event": "badapple:start"}, {"tick": 21}, PAUSE,
             {"event": "badapple:stop"}, {"until": "bvp.clear_done"}, dict(MOVED), {"tick": 30}, {"world": "cleared"}]
    out = drive(tmp_path / "addon", {"stage": path}, steps)
    assert out["errors"] == []
    assert {block for *_position, block in out["worlds"][0]["blocks"]} == {"minecraft:air"}


# --- /scriptevent <接頭辞>:stats の「舞台」の行: 人物を壁と床へ写した時間・回数と、目の動きでの描き直しの回数 ---

def wrap_projections(min_ms):
    """drive の prepare に渡す。work の codec.js の projectToStage を、呼んだ回数を globalThis.__bvpProjections に数え、
    1 回に min_ms 以上かける関数で包む (drive_main が回数を projections に出す)。stats の時計は Date.now で 1 ms 単位なので、
    速すぎる計算では平均が 0 になってしまう。min_ms が 0 なら数えるだけ。"""
    def prepare(work):
        codec = work / "codec.js"
        text = codec.read_text(encoding="utf-8")
        assert text.count("export function projectToStage(") == 1
        text = text.replace("export function projectToStage(", "function projectToStageOriginal(")
        text += (
            "\nexport function projectToStage(...args) {\n"
            "  globalThis.__bvpProjections = (globalThis.__bvpProjections ?? 0) + 1;\n"
            "  const start = Date.now();\n"
            "  const result = projectToStageOriginal(...args);\n"
            f"  while (Date.now() - start < {min_ms}) {{ /* 計算が速すぎて 0 ms にならないよう待つ */ }}\n"
            "  return result;\n"
            "}\n")
        codec.write_text(text, encoding="utf-8")
    return prepare


def stats_messages(out, key):
    """drive_main の messages から、翻訳キー key の知らせの引数 (数値) を、出た順に取り出す。"""
    found = []
    for message in out["messages"]:
        for part in json.loads(message)["rawtext"]:
            if isinstance(part, dict) and part.get("translate") == key:
                found.append([float(arg["text"]) for arg in part["with"]["rawtext"]])
    return found


def test_stats_report_how_long_the_stage_projections_took(tmp_path):
    """だまし絵を再生した後の stats に、舞台の行 (投影の平均・最大・回数、目の動きでの描き直しの回数) が出る。回数は
    再生中に投影した回数と一致し、平均は 0 より大きい。再生が終わった後で目が動けば、描き直しの回数だけが増える。"""
    path = make_depth_walk(tmp_path)
    data = convert_vox(path, tmp_path / "walk.js", "--view", "anamorphic")
    start = {"x": 0.5, "y": 64, "z": 100.5}
    moved = {"x": -6.5, "y": 70, "z": 92.5}
    stats = {"event": "badapple:stats"}
    steps = [dict(start), {"event": "badapple:setup"}, {"event": "badapple:start"}, {"until": "bvp.playback_finished"}, stats,
             dict(moved), {"tick": 20}, stats]
    out = drive(tmp_path / "addon", {"stage": tmp_path / "walk.js"}, steps, prepare=wrap_projections(2))
    assert out["errors"] == []
    (first, second) = stats_messages(out, "bvp.stats_stage")
    average, longest, count, redraws = first
    # 再生中は、フレームが変わるたびに 1 回ずつ写す。目は動かないので、描き直しは 0 回
    assert count == data["frame_count"] and redraws == 0
    assert 2 <= average <= longest and longest >= 2
    # 目が動いた後は、描き直しの 1 回が増える。再生が終わって統計の時計が止まっているので、その投影は回数・時間に入れない
    assert second[3] == 1 and second[2] == count and out["projections"] == count + 1
    assert second[0] == first[0]
    # 並び: 再生の行 → 負荷の行 → 舞台の行
    keys = [part["translate"] for message in out["messages"] for part in json.loads(message)["rawtext"]
            if isinstance(part, dict) and part.get("translate", "").startswith("bvp.stats_")]
    assert keys[:3] == ["bvp.stats_play", "bvp.stats_load", "bvp.stats_stage"]


def test_stage_stats_count_only_projections_made_since_the_play_started(tmp_path):
    """再生を始め直す (止めてから再生) と、舞台の回数も数え直す。動かない目で、再生を 2 回すれば、回数は 1 回目と同じ。"""
    path = make_depth_walk(tmp_path)
    data = convert_vox(path, tmp_path / "walk.js", "--view", "anamorphic")
    stats = {"event": "badapple:stats"}
    steps = [{"x": 0.5, "y": 64, "z": 100.5}, {"event": "badapple:setup"}, {"event": "badapple:start"},
             {"until": "bvp.playback_finished"}, stats,
             {"event": "badapple:start"}, {"tick": 80}, stats]   # 終わりの知らせは 1 回目のが残っているので、tick 数で待つ
    out = drive(tmp_path / "addon", {"stage": tmp_path / "walk.js"}, steps, prepare=wrap_projections(0))
    assert out["errors"] == []
    (first, second) = stats_messages(out, "bvp.stats_stage")
    assert first[2] == second[2] == data["frame_count"]
    assert out["projections"] == 2 * data["frame_count"]   # 投影そのものは 2 回分あるが、2 回目の stats は 2 回目の分だけ


def test_flat_video_stats_have_no_stage_line(tmp_path):
    """だまし絵でない動画 (平面) の stats は今までどおり。舞台の行は出ない。"""
    from test_codec_v4 import make_scene_frames, run_convert

    make_scene_frames(tmp_path / "frames")
    run_convert(tmp_path / "frames", tmp_path / "scene.js")
    out = drive(tmp_path / "addon", {"demo": tmp_path / "scene.js"},
                [{"event": "badapple:setup"}, {"event": "badapple:start"}, {"until": "bvp.playback_finished"},
                 {"event": "badapple:stats"}])
    text = json.dumps(out["messages"])
    assert "bvp.stats_play" in text and "bvp.stats_load" in text and "bvp.stats_stage" not in text


def test_stage_stats_line_is_translated_in_every_language():
    """舞台の行の翻訳が、10 言語とも、4 つの数 (%1〜%4) を入れる形である。"""
    import gui_i18n

    files = gui_i18n.addon_lang_files()
    languages = [path for path in files if path.endswith(".lang")]
    assert len(languages) >= 10
    for path in languages:
        line = next(line for line in files[path].splitlines() if line.startswith("bvp.stats_stage="))
        assert [f"%{n}" in line for n in (1, 2, 3, 4)] == [True] * 4, path


def test_paused_without_moving_the_eye_is_not_counted_as_a_view_redraw(busy, tmp_path):
    """目を動かさずに一時停止すると、捨てたフレームの仕上げの描き直しが 1 回走るが、目の動きでの描き直しには数えない。
    その投影も、再生の統計 (フレーム数・時間) には入れない。止めている間にそれ以上の描き直しはない。"""
    path, _data, _volumes = busy[20]
    # tick の時間の上限をなくすと、1 フレームは MAX_BLOCKS_PER_TICK ごとに 2 tick で置き終わる。21 tick では置いている途中で止まる
    # (20 や 22 では置き終えた直後で、仕上げの描き直しが無い)
    stats = {"event": "badapple:stats"}
    steps = [dict(START), {"event": "badapple:setup"}, dict(WATCH), {"event": "badapple:start"}, {"tick": 21}, {"mark": "playing"},
             PAUSE, {"tick": 30}, {"mark": "paused"}, stats, {"tick": 200}, stats]
    out = drive(tmp_path / "addon", {"stage": path}, steps, prepare=chain(no_tick_budget, wrap_projections(0)))
    assert out["errors"] == []
    # 前提: 止めたフレームの仕上げのために、投影はもう 1 回走る
    assert out["marks"]["paused"] == out["marks"]["playing"] + 1
    first, second = stats_messages(out, "bvp.stats_stage")
    assert first[3] == 0 and second == first
    assert first[2] == out["marks"]["playing"]


def test_benchmark_does_not_redraw_the_stage_while_it_runs(busy, tmp_path):
    """描きかけのフレームで bench を送ると、再生を止めて捨てたフレームの仕上げの描き直しが、bench の間に始まって
    ブロックを置いてしまわない (bench の間は、舞台の描き直しを始めない)。"""
    path, _data, _volumes = busy[20]
    steps = [dict(START), {"event": "badapple:setup"}, dict(WATCH), {"event": "badapple:start"}, {"tick": 20},
             {"event": "badapple:bench"}, {"mark": "bench"}, {"tick": 1}, {"mark": "bench_tick"}]
    out = drive(tmp_path / "addon", {"stage": path}, steps, prepare=wrap_projections(0))
    assert out["errors"] == []
    assert out["marks"]["bench_tick"] == out["marks"]["bench"]
    # 前提: bench はこの 1 tick では終わらない
    assert not any("bvp.bench_done" in m for m in out["messages"])


@pytest.fixture(scope="module")
def background_addon(tmp_path_factory):
    """背景の映像 (自動パレット) 付きの舞台のアドオン。人物は 10 フレームで、背景の 5 秒の間に繰り返す。"""
    import queue
    import zipfile

    import video_player_gui as gui
    from gui_i18n import Translator

    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg が必要")
    tmp = tmp_path_factory.mktemp("background")
    vox = make_depth_walk(tmp)
    background = tmp / "bg.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=10:duration=2.5",
                    "-f", "lavfi", "-i", "mandelbrot=size=320x180:rate=10", "-f", "lavfi", "-i", "sine=frequency=440:duration=5",
                    "-filter_complex", "[1:v]trim=duration=2.5,setpts=PTS-STARTPTS[m];[0:v][m]concat=n=2:v=1[v]",
                    "-map", "[v]", "-map", "2:a", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    str(background)], check=True)

    class Headless:
        def __init__(self):
            self.tr = Translator("ja")
            self.messages = queue.Queue()
            self.keyframe_interval_var = _Var(30)
            self.perceptual_var = _Var(True)

        def _run(self, command):
            gui.PackBuilderApp._run(self, command)

        def _extract_audio(self, *args):
            return gui.PackBuilderApp._extract_audio(self, *args)

        def _convert_stage_background(self, *args):
            return gui.PackBuilderApp._convert_stage_background(self, *args)

    output = tmp / "stage.mcaddon"
    gui.PackBuilderApp._build_pack(Headless(), [{"file_path": str(vox), "video_id": "walk", "thumb_sec": 0}], output,
                                   "Stage", "badapple", 64, 48, 10, None, "auto", "none", "cpu", "screen", 16,
                                   None, True, str(background))
    with zipfile.ZipFile(output) as z:
        z.extractall(tmp / "pack")
    return next(p.parent for p in (tmp / "pack").rglob("main.js"))


def drive_pack(pack, work, steps):
    """drive と同じことを、組み立てたアドオン (展開したフォルダ) に対してする。main.js もそのフォルダのものを使う。"""
    shutil.copytree(pack, work)
    shutil.copy(ROOT / "tests" / "js" / "drive_main.mjs", work / "drive_main.mjs")
    for module, source in (("server", "server.js"), ("server-ui", "server-ui.js")):
        target = work / "node_modules" / "@minecraft" / module
        target.mkdir(parents=True)
        (target / "package.json").write_text(json.dumps({"name": f"@minecraft/{module}", "type": "module", "main": "index.js"}))
        shutil.copy(ROOT / "tests" / "js" / "minecraft_mock" / source, target / "index.js")
    (work / "package.json").write_text(json.dumps({"type": "module"}))
    result = subprocess.run(["node", "drive_main.mjs", json.dumps(steps)], cwd=work, capture_output=True, text=True,
                            encoding="utf-8", timeout=600)
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert out["errors"] == []
    return out


def final_world(out):
    return {tuple(b[:3]): b[3] for b in out["allBlocks"]}


def count_differences(a, b):
    return sum(1 for key in set(a) | set(b) if a.get(key) != b.get(key))


BACKGROUND_WATCH = [{"x": 0.5, "y": 64, "z": 100.5}, {"event": "badapple:setup"}]
BACKGROUND_FINISH = [{"until": "bvp.playback_finished"}, {"tick": 40}]


@pytest.mark.parametrize("pauses", [[7], [7, 13, 30, 31, 55]])
def test_pause_and_resume_with_a_background_video_ends_like_an_uninterrupted_play(background_addon, tmp_path, pauses):
    """背景の映像付きの舞台を、一時停止と再開を挟みながら再生しても、最後のワールドは止めない再生と同じ
    (人物のフレームを捨てて作り直す道筋が、背景のデコードと食い違わない)。"""
    base = drive_pack(background_addon, tmp_path / "base", BACKGROUND_WATCH + [{"event": "badapple:start"}] + BACKGROUND_FINISH)
    steps = BACKGROUND_WATCH + [{"event": "badapple:start"}]
    now = 0
    for k in pauses:
        steps += [{"tick": k - now}, PAUSE, {"tick": 4}, PAUSE]
        now = k
    out = drive_pack(background_addon, tmp_path / "paused", steps + BACKGROUND_FINISH)
    assert len(final_world(base)) > 1000
    assert count_differences(final_world(base), final_world(out)) == 0


def test_seek_back_while_paused_with_a_background_video_ends_like_an_uninterrupted_play(background_addon, tmp_path):
    """背景の映像付きの舞台で、一時停止中に後ろへシークしてから再開しても、最後のワールドは止めない再生と同じ。"""
    base = drive_pack(background_addon, tmp_path / "base", BACKGROUND_WATCH + [{"event": "badapple:start"}] + BACKGROUND_FINISH)
    steps = BACKGROUND_WATCH + [{"event": "badapple:start"}, {"tick": 33}, PAUSE,
                                {"ui": [{"selection": 5}, {"formValues": [1]}]}, {"until": "bvp.seek_done"}, {"tick": 3}, PAUSE]
    out = drive_pack(background_addon, tmp_path / "seek", steps + BACKGROUND_FINISH)
    assert any("bvp.seek_done" in m for m in out["messages"])
    assert count_differences(final_world(base), final_world(out)) == 0
