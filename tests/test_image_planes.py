"""画像モードの「ガラスケースの立体」(面の板。image_mode/planes.py・glass.py・glass_rp.py) のテスト。

一番大事なのは、板を geometry の UV の決まり (FaceMapping) どおりに置いたとき、ボクセルの外に出ている面がちょうど全部、
正しい位置・色でそろうこと (そうならば、ゲームの GPU が描く絵は本物の立方体と同じ)。docs/research/glass_case.md の 4 章。
"""
import itertools
import json
import math
import zipfile

import numpy as np
import pytest
from PIL import Image
from test_image_main import messages, props, run

import convert_image_mode
from image_mode import glass, glass_rp, planes
from mvcodec.volume import AIR_LEVEL, write_vox

ALL_MAPPINGS = [planes.FaceMapping(*signs) for signs in itertools.product((1, -1), repeat=4)]


def blob(seed=1, shape=(9, 7, 11)):
    """でこぼこの立体 (穴・出っ張り・1 ボクセルの細い所がある)。色番号 1〜4。"""
    rng = np.random.default_rng(seed)
    volume = np.zeros(shape, np.uint8)
    volume[2:7, 2:5, 0:8] = 1
    volume[rng.random(shape) < 0.25] = 0
    volume[4, 3, 8:11] = 2                       # 細い柱
    volume[0:2, 3, 5] = 3                        # 横に出た腕
    volume[3, 1:3, 2] = 4
    return volume


def palette():
    return np.array([(200, 40, 40), (40, 200, 40), (40, 40, 200), (230, 230, 230)], np.uint8)


def expected_faces(volume, rgb, shade=True):
    """外に出ている面: {(軸, 境目の位置, 板の上の 2 つの座標): 色}。"""
    faces = {}
    solid = volume > 0
    for axis in range(3):
        pa, pb = planes.PLANE_AXES[axis]
        for idx in np.argwhere(solid):
            for sign in (1, -1):
                n = idx.copy()
                n[axis] += sign
                outside = not (0 <= n[axis] < volume.shape[axis]) or not solid[tuple(n)]
                if not outside:
                    continue
                k = idx[axis] + (1 if sign > 0 else 0)
                colour = rgb[volume[tuple(idx)] - 1].astype(np.float32)
                if shade:
                    colour = colour * planes.SHADE[(axis, sign)]
                faces[(axis, int(k), int(idx[pa]), int(idx[pb]))] = tuple(int(c) for c in np.rint(colour))
    return faces


def faces_from_geometry(bones, sheet, placing, mapping):
    """geometry の直方体と UV の決まり (FaceMapping) から、テクスチャの画素がボクセルのどの面に来るかを組み立て直す。"""
    image = np.asarray(sheet)
    found = {}
    centres = {0: placing.centre[0], 1: placing.centre[1], 2: 0.0}
    inverse = {}
    for vox_axis in range(3):
        model, sign = mapping.model_axis(vox_axis)
        inverse[model] = (vox_axis, sign)
    for bone in bones:
        for cube in bone["cubes"]:
            [(face, uv)] = cube["uv"].items()
            vox_axis = {"east": 0, "north": 1, "up": 2}[face]
            (u_axis, u_sign), (v_axis, v_sign) = mapping.face_uv(vox_axis)
            origin, size = np.array(cube["origin"], float), np.array(cube["size"], float)
            (u0, v0), (w, h) = uv["uv"], uv["uv_size"]
            assert w > 0 and h > 0
            for i in range(w):
                for j in range(h):
                    pixel = image[v0 + j, u0 + i]
                    if pixel[3] == 0:
                        continue
                    position = origin.copy()
                    position[u_axis] += (i + 0.5) / w * size[u_axis] if u_sign > 0 else size[u_axis] - (i + 0.5) / w * size[u_axis]
                    position[v_axis] += (j + 0.5) / h * size[v_axis] if v_sign > 0 else size[v_axis] - (j + 0.5) / h * size[v_axis]
                    position -= np.array(placing.offset)
                    vox = [0.0, 0.0, 0.0]
                    for model in range(3):
                        axis, sign = inverse[model]
                        vox[axis] = sign * position[model] / placing.scale + centres[axis]
                    pa, pb = planes.PLANE_AXES[vox_axis]
                    key = (vox_axis, round(vox[vox_axis]), math.floor(vox[pa]), math.floor(vox[pb]))
                    assert key not in found, f"同じ面に 2 つの画素: {key}"
                    found[key] = tuple(int(c) for c in pixel[:3])
    return found


@pytest.mark.parametrize("mapping", ALL_MAPPINGS, ids=lambda m: f"{m.north_u},{m.east_u},{m.up_u},{m.up_v}")
def test_planes_rebuild_exactly_the_exposed_faces(mapping):
    """どの UV の向き (16 通り) でも、組み立て直した面が、外に出ているボクセルの面と同じ (位置・色)。"""
    volume, rgb = blob(), palette()
    figure = glass.figure_from_volumes([volume], [rgb], figure_blocks=2, mapping=mapping)
    (x0, y0, z0), (x1, y1, z1) = planes.figure_extent([volume])
    scale = 16 * 2 / (z1 - z0)
    placing = planes.Placing(scale, ((x0 + x1) / 2, (y0 + y1) / 2), (0.0, -z0 * scale, 0.0))
    assert faces_from_geometry(figure["bones"], figure["sheets"][0], placing, mapping) == expected_faces(volume, rgb)


@pytest.mark.parametrize("split", planes.PLANE_SPLITS)
def test_every_split_keeps_the_same_faces(split):
    volume, rgb = blob(seed=3, shape=(20, 6, 18)), palette()
    figure = glass.figure_from_volumes([volume], [rgb], figure_blocks=2, split=split)
    (x0, y0, z0), (x1, y1, z1) = planes.figure_extent([volume])
    scale = 16 * 2 / (z1 - z0)
    placing = planes.Placing(scale, ((x0 + x1) / 2, (y0 + y1) / 2), (0.0, -z0 * scale, 0.0))
    found = faces_from_geometry(figure["bones"], figure["sheets"][0], placing, planes.DEFAULT_MAPPING)
    assert found == expected_faces(volume, rgb)


def test_tiles_trade_more_planes_for_fewer_texels():
    volume = np.zeros((40, 4, 40), np.uint8)
    volume[2:6, 1:3, 2:6] = 1
    volume[30:38, 1:3, 30:38] = 1
    rgb = palette()
    rect = planes.frame_quads(volume, rgb, "rect")
    tile = planes.frame_quads(volume, rgb, "tile8")
    assert len(tile) > len(rect)
    assert sum(q.width * q.height for q in tile) < sum(q.width * q.height for q in rect)


def raycast_cubes(volume, origins, direction):
    """本物の立方体で、各光線が最初に当たるボクセル (平らな番号。当たらなければ -1)。"""
    best_t = np.full(len(origins), np.inf)
    best = np.full(len(origins), -1)
    for axis in range(3):
        if abs(direction[axis]) < 1e-9:
            continue
        for k in range(volume.shape[axis] + 1):
            t = (k - origins[:, axis]) / direction[axis]
            point = origins + t[:, None] * direction
            idx = np.floor(point).astype(int)
            idx[:, axis] = k if direction[axis] > 0 else k - 1
            ok = np.all((idx >= 0) & (idx < volume.shape), axis=1) & (t > 0) & (t < best_t)
            sel = np.nonzero(ok)[0]
            sel = sel[volume[idx[sel, 0], idx[sel, 1], idx[sel, 2]] > 0]
            best_t[sel] = t[sel]
            best[sel] = np.ravel_multi_index(idx[sel].T, volume.shape)
    return best


def raycast_quads(quads, shape, origins, direction):
    """面の板 (アルファテスト・両面) で、各光線が最初に当たる不透明な画素の面のボクセル。"""
    best_t = np.full(len(origins), np.inf)
    best = np.full(len(origins), -1)
    for quad in quads:
        axis = quad.axis
        if abs(direction[axis]) < 1e-9:
            continue
        pa, pb = planes.PLANE_AXES[axis]
        t = (quad.k - origins[:, axis]) / direction[axis]
        point = origins + t[:, None] * direction
        a = np.floor(point[:, pa]).astype(int) - quad.a0
        b = np.floor(point[:, pb]).astype(int) - quad.b0
        ok = (a >= 0) & (a < quad.width) & (b >= 0) & (b < quad.height) & (t > 0) & (t < best_t)
        sel = np.nonzero(ok)[0]
        sel = sel[quad.image[b[sel], a[sel], 3] == 255]
        # 面を持つボクセルは、境目の両側のうち埋まっている方
        best_t[sel] = t[sel]
        idx = np.zeros((len(sel), 3), int)
        idx[:, pa] = a[sel] + quad.a0
        idx[:, pb] = b[sel] + quad.b0
        idx[:, axis] = quad.k
        best[sel] = -2 - np.ravel_multi_index(idx.T, tuple(s + 1 for s in shape))
    return best, best_t


@pytest.mark.parametrize("yaw,pitch", [(0.3, 0.2), (22.5, 0), (45, 0), (45, 35.26), (10, 60), (200, -40), (90, -89)])
def test_raycast_through_planes_matches_real_cubes(yaw, pitch):
    """光線を 1 本ずつ飛ばすと、面の板と本物の立方体で、当たる所と当たらない所が同じ (斜め下からも)。"""
    volume, rgb = blob(seed=5), palette()
    quads = planes.frame_quads(volume, rgb)
    y, p = math.radians(yaw), math.radians(pitch)
    d = np.array([math.sin(y) * math.cos(p), -math.cos(y) * math.cos(p), math.sin(p)])
    up = np.array([0, 0, 1.0]) if abs(d[2]) < 0.99 else np.array([0, 1.0, 0])
    r = np.cross(up, d)
    r /= np.linalg.norm(r)
    u = np.cross(d, r)
    centre = np.array(volume.shape) / 2
    radius = np.linalg.norm(volume.shape) / 2 + 1
    grid = (np.arange(int(radius * 2 * 3)) + 0.5) / 3 - radius
    a, b = np.meshgrid(grid, grid, indexing="ij")
    origins = centre + a.ravel()[:, None] * r + b.ravel()[:, None] * u + d * (radius + 2)
    truth = raycast_cubes(volume, origins, -d)
    got, _t = raycast_quads(quads, volume.shape, origins, -d)
    assert np.array_equal(truth >= 0, got != -1)


def test_pack_frames_uses_a_fixed_number_of_frames_per_sheet_without_overlaps():
    rng = np.random.default_rng(2)
    frames = [[np.full((int(rng.integers(1, 30)), int(rng.integers(1, 40)), 4), n, np.uint8) for _ in range(25)]
              for n in range(12)]
    sheets, placements, per_sheet, (w, h) = planes.pack_frames(frames, max_sheet=256, gutter=1)
    assert all(sheet.shape == (h, w, 4) for sheet in sheets)
    assert len(sheets) == math.ceil(len(frames) / per_sheet)
    for number, (images, places) in enumerate(zip(frames, placements)):
        for img, place in zip(images, places):
            assert place.sheet == number // per_sheet
            assert 1 <= place.x and place.x + img.shape[1] + 1 <= w and 1 <= place.y and place.y + img.shape[0] + 1 <= h
            assert np.array_equal(sheets[place.sheet][place.y:place.y + img.shape[0], place.x:place.x + img.shape[1]], img)


def test_pack_frames_refuses_a_frame_that_does_not_fit():
    with pytest.raises(ValueError):
        planes.pack_frames([[np.zeros((300, 10, 4), np.uint8)]], max_sheet=256)


def test_lift_volume_turns_depth_rows_and_columns_into_xyz():
    """mvcodec.volume の (奥行き 0 が一番奥, 高さ 0 が一番上, 幅) → (x, y = 奥, z = 上)。"""
    volume = np.full((4, 3, 5), AIR_LEVEL, np.int16)
    volume[0, 0, 1] = 7       # 一番奥・一番上・左から 2 つ目
    volume[3, 2, 4] = 2       # 一番手前・一番下・右端
    out = planes.lift_video_volume(volume, AIR_LEVEL)
    assert out.shape == (5, 4, 3)
    assert out[1, 3, 2] == 8 and out[4, 0, 0] == 3
    assert np.count_nonzero(out) == 2


def test_lift_frames_makes_a_relief_from_a_video_frame():
    frame = np.zeros((6, 8, 3), np.uint8)
    frame[:, 4:] = 255                       # 右半分が明るい = 手前
    volumes, _rgbs = glass.lift_frames([frame], "relief", 4)
    [volume] = volumes
    assert volume.shape == (8, 4, 6)
    front_right = volume[6, 0, 3]            # 手前 (y = 0) の右側には面がある
    back_left = volume[1, 3, 3]              # 暗い左側は奥 (y = 3)
    assert front_right > 0 and back_left > 0
    assert volume[1, 0, 3] == 0              # 左側の手前は空


# --- ケース ---

def test_case_walls_show_only_from_their_inner_side():
    video = {"glass": {"box": [4, 3, 5]}}
    conditions = glass_rp.wall_conditions(video)
    assert conditions["back"] == "v.cam_y < 1.5" and conditions["front"] == "v.cam_y > -1.5"
    assert conditions["left"] == "v.cam_x > -2" and conditions["right"] == "v.cam_x < 2"


@pytest.mark.parametrize("mapping", ALL_MAPPINGS[:4])
def test_case_image_is_not_mirrored_from_inside(mapping):
    """内側から見て、絵の右が見ている人の右・絵の上が上 (床は奥) に来る向きで置く。"""
    for part, (face, right, down) in glass_rp.case_faces(mapping).items():
        flip_u, flip_v = glass_rp.case_image_flips(part, mapping)
        (u_axis, u_sign), (v_axis, v_sign) = glass_rp.face_uv_directions(face, mapping)
        r_axis, r_sign = mapping.model_axis(right[0])
        assert u_axis == r_axis and (u_sign * (-1 if flip_u else 1)) == r_sign * right[1]
        d_axis, d_sign = mapping.model_axis(down[0])
        assert v_axis == d_axis and (v_sign * (-1 if flip_v else 1)) == d_sign * down[1]


def test_case_texture_packs_images_and_default_colours(tmp_path):
    picture = tmp_path / "bg.png"
    Image.new("RGB", (64, 32), (10, 120, 200)).save(picture)
    texture, uv, frame = glass.case_texture((4, 2, 3), glass.CaseOptions(images={"back": str(picture)}, frame=True))
    assert set(uv) == set(glass_rp.CASE_PARTS) and frame is not None
    x, y, w, h = uv["back"]
    assert (w, h) == (4 * 32, 3 * 32)
    assert np.asarray(texture)[y + 5, x + 5, :3].tolist() == [10, 120, 200]
    assert uv["floor"][2:] == [1, 1]


# --- 変換ツール ---

def write_figure(path, frames=3):
    models = []
    for n in range(frames):
        volume = blob(seed=n)
        voxels = np.argwhere(volume > 0)
        models.append((volume.shape, np.column_stack([voxels, volume[volume > 0]]).astype(np.uint8)))
    colours = np.zeros((256, 4), np.uint8)
    colours[:4, :3] = palette()
    colours[:, 3] = 255
    path.write_bytes(write_vox(models, colours))


def load(addon):
    with zipfile.ZipFile(addon) as z:
        return {name: z.read(name) for name in z.namelist()}


def test_vox_with_a_case_builds_a_glass_case_addon(tmp_path):
    source = tmp_path / "fig.vox"
    write_figure(source)
    picture = tmp_path / "bg.png"
    Image.new("RGB", (40, 40), (200, 100, 0)).save(picture)
    out = tmp_path / "fig.mcaddon"
    assert convert_image_mode.main([str(source), "-o", str(out), "--case", "--case-image", f"back={picture}",
                                    "--case-frame", "--figure-blocks", "3"]) == 0
    files = load(out)
    entity = json.loads(files["RP/entity/screen_fig_a.entity.json"])["minecraft:client_entity"]["description"]
    assert entity["materials"] == {"default": "entity_alphatest", "opaque": "entity"}
    assert set(entity["geometry"]) == {"default", "case"}
    assert len(entity["render_controllers"]) == 2
    geometry = json.loads(files["RP/models/entity/screen_fig_a.geo.json"])["minecraft:geometry"][0]
    description = geometry["description"]
    sheet = np.asarray(Image.open(__import__("io").BytesIO(files["RP/textures/bvp_image/fig/fig_0.png"])))
    assert sheet.shape[:2] == (description["texture_height"], description["texture_width"])
    assert [bone["name"] for bone in geometry["bones"]] == ["f0", "f1", "f2"]
    for bone in geometry["bones"]:
        for cube in bone["cubes"]:
            [(face, uv)] = cube["uv"].items()
            assert face in ("east", "north", "up")
            assert sum(1 for s in cube["size"] if s == 0) == 1
            (u, v), (w, h) = uv["uv"], uv["uv_size"]
            assert 0 <= u and u + w <= description["texture_width"] and 0 <= v and v + h <= description["texture_height"]
    controllers = json.loads(files["RP/render_controllers/screen_fig_a.render_controllers.json"])["render_controllers"]
    figure = controllers["controller.render.bvpimg.screen_fig_a"]
    assert figure["part_visibility"][1] == {"f0": "v.fig == 0 && q.property('bvpimg:state') != 0"}
    case = controllers["controller.render.bvpimg.screen_fig_a_case"]
    back = case["part_visibility"][1]["case_back"]
    assert back.startswith("(v.cam_y < ") and back.endswith(") && q.property('bvpimg:state') != 0")
    meta = json.loads(files["BP/scripts/image_videos.js"].decode("utf-8").split("IMAGE_VIDEOS = [", 1)[1]
                      .rsplit("];", 1)[0])
    assert meta["loop"] is True and meta["frame_count"] == 3 and meta["glass"]["master"] == "figure"
    assert meta["glass"]["case"]["frame"] is True
    report = json.loads((tmp_path / "fig.report.json").read_text(encoding="utf-8"))
    assert report["face_mapping"] == {"north_u": 1, "east_u": -1, "up_u": 1, "up_v": -1}


def test_glass_options_need_planes_and_a_lift_for_videos(tmp_path):
    source = tmp_path / "fig.vox"
    write_figure(source, frames=1)
    assert convert_image_mode.main([str(source), "-o", str(tmp_path / "a.mcaddon"), "--solid", "views", "--case"]) == 1
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"not a video")
    assert convert_image_mode.main([str(video), "-o", str(tmp_path / "b.mcaddon"), "--case"]) == 1
    assert convert_image_mode.main([str(source), "-o", str(tmp_path / "c.mcaddon"), "--lift", "relief"]) == 1
    with pytest.raises(SystemExit):
        convert_image_mode.parse_args([str(video), "--lift", "extrude", "--depth-video", str(video)])


def test_too_many_figure_frames_are_refused(tmp_path):
    source = tmp_path / "fig.vox"
    write_figure(source, frames=3)
    assert convert_image_mode.main([str(source), "-o", str(tmp_path / "a.mcaddon"), "--solid", "planes",
                                    "--max-figure-frames", "2"]) == 1


def test_wall_video_drives_the_clock_and_the_figure_follows(tmp_path):
    """壁の動画があると、主の時計はその動画 (fps・コマ数・音声)。人物は同じ時刻から自分の fps で繰り返す。"""
    volume, rgb = blob(), palette()
    figure = glass.figure_from_volumes([volume, volume], [rgb, rgb], figure_blocks=2)
    frames = [np.full((18, 32, 3), n * 20, np.uint8) for n in range(12)]
    entry = {"id": "show", "title": "show", "figure": figure, "figure_fps": 10, "figure_loop": True,
             "case": glass.CaseOptions(), "wall_video": {"frames": frames, "width": 32, "height": 18, "fps": 24},
             "audio": lambda seconds: [(0, "ogg", b"x"), (1, "ogg", b"y")]}
    files, report = convert_image_mode.build_glass_files([entry], namespace="bvpimg", pack_name="t", log=lambda *a: None)
    [video] = report["videos"]
    assert video["fps"] == 24 and video["frame_count"] == 12 and "loop" not in video and video["audio_chunks"] == 2
    lines = json.loads(files["RP/entity/screen_show_a.entity.json"])["minecraft:client_entity"]["description"]["scripts"]["pre_animation"]
    assert any(line.startswith("v.secs = ") and "/ 24" in line for line in lines)
    assert "v.fig = math.mod(math.floor(v.secs * 10 + 0.0001), 2);" in lines
    assert "v.vsheet = math.floor(v.frame / 12);" in lines
    wall = json.loads(files["RP/models/entity/screen_show_a_wall.geo.json"])["minecraft:geometry"][0]
    assert len(wall["bones"]) == 12
    controllers = json.loads(files["RP/render_controllers/screen_show_a.render_controllers.json"])["render_controllers"]
    assert controllers["controller.render.bvpimg.screen_show_a_wall"]["textures"] == ["Array.vid[v.vsheet]"]
    assert "RP/sounds/bvp_image/show/chunk_1.ogg" in files


def test_wall_video_without_a_case_is_refused():
    volume, rgb = blob(), palette()
    figure = glass.figure_from_volumes([volume], [rgb], figure_blocks=2)
    entry = {"id": "x", "title": "x", "figure": figure, "figure_fps": 10, "figure_loop": True, "case": None,
             "wall_video": {"frames": [np.zeros((4, 4, 3), np.uint8)], "width": 4, "height": 4, "fps": 10}}
    with pytest.raises(convert_image_mode.ConvertError):
        convert_image_mode.build_glass_files([entry], namespace="bvpimg", pack_name="t", log=lambda *a: None)


# --- スクリプト ---

GLASS_VIDEO = {"id": "case", "title": "case", "fps": 10, "frame_count": 10, "audio_chunks": 0, "audio_chunk_seconds": 4,
               "screen_blocks": [3, 6], "methods": ["a"], "width": 8, "height": 16, "loop": True,
               "glass": {"master": "figure"}}


def test_setup_wall_puts_a_glass_case_on_the_floor_in_front(tmp_path):
    hit = {"block": {"location": {"x": 10, "y": 64, "z": -5}}, "face": "East"}
    out = run(tmp_path, [{"lookAt": hit}, {"cmd": "setup", "arg": "wall"}, {"tick": 2}, {"cmd": "start"},
                         {"tick": 25}, {"snap": "playing"}], videos=[GLASS_VIDEO])
    [placed] = out["log"]["placed"]
    assert placed["location"]["x"] != 11.02            # 壁には貼らない
    assert messages(out, "case_not_on_wall")
    state = props({s["label"]: s for s in out["snaps"]}["playing"])
    assert state["state"] == 1 and state["frame"] < 5   # 繰り返している


def test_the_uv_test_pack_holds_sixteen_variants_and_a_case(tmp_path):
    import importlib.util
    from pathlib import Path as _Path

    script = _Path(__file__).resolve().parent.parent / "scripts" / "make_planes_test_pack.py"
    spec = importlib.util.spec_from_file_location("make_planes_test_pack", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    out = tmp_path / "planes_test.mcaddon"
    assert module.main([str(out)]) == 0
    files = load(out)
    geometry = json.loads(files["RP/models/entity/screen_planes_test_a.geo.json"])["minecraft:geometry"][0]
    [bone] = geometry["bones"]
    faces = {next(iter(cube["uv"])) for cube in bone["cubes"]}
    assert faces == {"east", "north", "up"}
    assert len(module.MAPPINGS) == 16 and len(set(module.MAPPINGS)) == 16
    assert "RP/models/entity/screen_case_test_a_case.geo.json" in files


def test_rig_test_pack_script_adds_bone_animations(tmp_path):
    """骨で動かす人物の試験用のアドオン (A20): 回す形のボーンに pivot があり、アニメーションが client_entity から動く。"""
    import importlib.util
    from pathlib import Path as _Path

    script = _Path(__file__).resolve().parent.parent / "scripts" / "make_rig_test_pack.py"
    spec = importlib.util.spec_from_file_location("make_rig_test_pack", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    out = tmp_path / "rig_test.mcaddon"
    assert module.main([str(out)]) == 0
    files = load(out)
    bones = {b["name"]: b for b in
             json.loads(files["RP/models/entity/screen_r_chain_a.geo.json"])["minecraft:geometry"][0]["bones"]}
    assert bones["test"]["parent"] == "f0" and bones["child"]["parent"] == "test"
    # pivot は白い角の中心と赤の先の中心 (1 ボクセル = 8、見本との間は geometry の x)
    assert bones["test"]["pivot"] == [module.TEST_X, (1 + module.LIFT) * module.VOXEL, 0.0]
    assert bones["child"]["pivot"][0] == module.TEST_X + 7 * module.VOXEL
    description = json.loads(files["RP/entity/screen_r_x_a.entity.json"])["minecraft:client_entity"]["description"]
    assert description["scripts"]["animate"] == ["pose"]
    animation = json.loads(files["RP/animations/screen_r_x_a.animation.json"])["animations"]
    assert animation[description["animations"]["pose"]]["bones"]["test"]["rotation"] == [90, 0, 0]
    clock = json.loads(files["RP/animations/screen_r_clock_a.animation.json"])["animations"]
    assert next(iter(clock.values()))["anim_time_update"] == "math.mod(v.t, 4)"
