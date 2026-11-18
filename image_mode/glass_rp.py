"""画像モードの「ガラスケースの立体」のリソースパック JSON (docs/research/glass_case.md の 5 章・9.1)。

1 つのエンティティ <ns>:screen_<id>_a に、render controller を最大 3 つ持たせる。
- 人物 (面の板。image_mode/planes.py): geometry はコマごとのボーン f<n>。テクスチャは K コマずつのシート
- ケース (床と 4 枚の壁。--case): 内側を向いた板。カメラがどの壁の外側にいるかを Molang で求め、手前の壁を隠す
  (奥の壁だけが背景として見える。参考動画の「奥に背景、手前は透ける」)。マテリアルが裏を描くかどうかに左右されない
- 壁の動画 (--case-video): 奥の壁の内側に、今の方式 a (マス・ボーン・part_visibility) のスクリーン

時計: スクリプトが書くのは今までどおり frame (主のコマ) と seq の 1 組。主は、壁の動画があればその動画 (fps・コマ数・音声)、
無ければ人物 (繰り返し)。クライアントは同じ時刻 v.secs から、壁の動画のコマと人物のコマ (v.fig) を別々に出す。
標準ライブラリだけで動く (ブラウザの中の Python でも使える)。
"""
from __future__ import annotations

import json
import math

from image_mode.rp import IMPOSTOR_MATERIAL, _num, cell_origin, entity_name

# 手前の壁を隠す計算: rp.py のインポスターと同じく、v.rel (正面から上から見て反時計回りにカメラまでの角度) を
# q.body_y_rotation - q.rotation_to_camera(1) とする (符号は Minecraft の実機で決めた: DECISIONS.md の A18)。
# カメラの位置 (ケースの中心から、ブロック) はボクセルの向き (x 右・y 奥) で (flat * sin(rel), -flat * cos(rel))。
# flat は水平の距離 (q.distance_from_camera * cos(上下の角度))。cos なので上下の角度の符号は関係ない
CASE_PARTS = ("back", "front", "left", "right", "floor")
CASE_IMAGE_SIDES = CASE_PARTS + ("all",)
# 壁の動画は奥の壁から少し手前に浮かせる (geometry の単位。同じ面に重ねるとちらつく)
WALL_VIDEO_GAP = 0.1
# 床は人物の足の面と重ならないよう、少し下げる
FLOOR_DROP = 0.05
# 縁の太さ (geometry の単位 = 1/16 ブロック)
FRAME_THICKNESS = 0.5
OPAQUE_MATERIAL = "entity"


def _short(video: dict) -> str:
    return f"screen_{video['id']}_a"


def _state(namespace: str) -> str:
    return f"q.property('{namespace}:state')"


def _bounds(video: dict) -> dict:
    """カリングされないよう、ケース (無ければ人物) 全体を覆う大きさ (ブロック)。"""
    width, depth, height = video["glass"]["box"]
    size = math.ceil(math.hypot(width, depth, height)) + 2
    return {"visible_bounds_width": size, "visible_bounds_height": size,
            "visible_bounds_offset": [0, _num(height / 2), 0]}


def _geometry(identifier: str, texture_size, bones: list, video: dict) -> dict:
    return {
        "format_version": "1.12.0",
        "minecraft:geometry": [{
            "description": {"identifier": identifier, "texture_width": texture_size[0],
                            "texture_height": texture_size[1], **_bounds(video)},
            "bones": bones,
        }],
    }


def figure_geometry(video: dict, namespace: str, bones: list) -> dict:
    figure = video["glass"]["figure"]
    return _geometry(f"geometry.{namespace}.{_short(video)}", (figure["sheet_width"], figure["sheet_height"]), bones, video)


# --- ケース ---

def face_uv_directions(face: str, mapping) -> tuple[tuple[int, int], tuple[int, int]]:
    """面の u・v の向き (geometry の軸, 符号)。south・west は、向かいの north・east を外から見た向きから求める
    (north・east・up は実機で確かめた。south・west は向かいの面からの推測。A19)。"""
    north_u, east_u = mapping.north_u, mapping.east_u
    return {
        "north": ((0, north_u), (1, -1)),
        "south": ((0, -north_u), (1, -1)),
        "east": ((2, east_u), (1, -1)),
        "west": ((2, -east_u), (1, -1)),
        "up": ((0, mapping.up_u), (2, mapping.up_v)),
    }[face]


def case_faces(mapping) -> dict:
    """ケースの部品ごとの (面の名前, 内側から見た絵の右の向き, 下の向き)。向きはボクセルの軸 (0 x 右・1 y 奥・2 z 上) と符号。"""
    # 左の壁 (ボクセルの x の小さい側) の内向きは、geometry の x で north_u の向き。east の面は east_normal() の向きを向く
    # (実機では -x: east の面を左の壁に使うと外を向いて、中から見えなかった。2026-11-18)
    inward_left = mapping.north_u
    left_face = "east" if mapping.east_normal() == inward_left else "west"
    right_face = "west" if left_face == "east" else "east"
    return {
        "back": ("north", (0, 1), (2, -1)),     # 奥の壁を正面から: 右は +x、下は -z
        "front": ("south", (0, -1), (2, -1)),   # 手前の壁を中から (正面のほうを向いて): 右は -x
        "left": (left_face, (1, 1), (2, -1)),   # 左の壁を中から: 右は +y (奥)
        "right": (right_face, (1, -1), (2, -1)),
        "floor": ("up", (0, 1), (1, -1)),       # 床を上から: 右は +x、下は -y (手前)。絵の上が奥
    }


def case_image_flips(part: str, mapping) -> tuple[bool, bool]:
    """ケースの部品の絵を (左右, 上下) 反転して置くか (uv_size をいつも正にするため)。"""
    face, right, down = case_faces(mapping)[part]
    (u_axis, u_sign), (v_axis, v_sign) = face_uv_directions(face, mapping)
    r_axis, r_sign = mapping.model_axis(right[0])
    d_axis, d_sign = mapping.model_axis(down[0])
    if (r_axis, d_axis) != (u_axis, v_axis):
        raise ValueError(f"ケースの {part} の絵の向きが面と合いません")
    return u_sign != r_sign * right[1], v_sign != d_sign * down[1]


def _case_cube(part: str, box, uv, mapping) -> dict:
    """ケースの部品 1 枚 (厚み 0)。box は (幅, 奥行き, 高さ) ブロック、uv は (x, y, 幅, 高さ) 画素。"""
    width, depth, height = (v * 16 for v in box)
    face = case_faces(mapping)[part][0]
    x_sign = mapping.north_u
    if part == "back":
        origin, size = [-width / 2, 0, depth / 2], [width, height, 0]
    elif part == "front":
        origin, size = [-width / 2, 0, -depth / 2], [width, height, 0]
    elif part in ("left", "right"):
        side = -1 if part == "left" else 1
        origin, size = [x_sign * side * width / 2, 0, -depth / 2], [0, height, depth]
    else:
        origin, size = [-width / 2, -FLOOR_DROP, -depth / 2], [width, 0, depth]
    return {"origin": [_num(round(v, 4)) for v in origin], "size": [_num(round(v, 4)) for v in size],
            "uv": {face: {"uv": [uv[0], uv[1]], "uv_size": [uv[2], uv[3]]}}}


def _frame_cubes(box, texel) -> list:
    """ケースの 12 本の縁 (細い直方体。全部の面に縁の色の 1 画素を貼る)。"""
    width, depth, height = (v * 16 for v in box)
    t = FRAME_THICKNESS
    uv = {face: {"uv": [texel[0], texel[1]], "uv_size": [1, 1]}
          for face in ("north", "south", "east", "west", "up", "down")}
    cubes = []
    for y in (0, height - t):
        cubes.append({"origin": [-width / 2, y, -depth / 2], "size": [width, t, t]})
        cubes.append({"origin": [-width / 2, y, depth / 2 - t], "size": [width, t, t]})
        cubes.append({"origin": [-width / 2, y, -depth / 2], "size": [t, t, depth]})
        cubes.append({"origin": [width / 2 - t, y, -depth / 2], "size": [t, t, depth]})
    for x in (-width / 2, width / 2 - t):
        for z in (-depth / 2, depth / 2 - t):
            cubes.append({"origin": [x, 0, z], "size": [t, height, t]})
    for cube in cubes:
        cube["origin"] = [_num(round(v, 4)) for v in cube["origin"]]
        cube["size"] = [_num(round(v, 4)) for v in cube["size"]]
        cube["uv"] = uv
    return cubes


def case_geometry(video: dict, namespace: str, mapping) -> dict:
    case = video["glass"]["case"]
    box = video["glass"]["box"]
    bones = [{"name": f"case_{part}", "pivot": [0, 0, 0], "cubes": [_case_cube(part, box, case["uv"][part], mapping)]}
             for part in CASE_PARTS]
    if case.get("frame"):
        bones.append({"name": "case_frame", "pivot": [0, 0, 0], "cubes": _frame_cubes(box, case["frame_texel"])})
    return _geometry(f"geometry.{namespace}.{_short(video)}_case", case["texture_size"], bones, video)


def wall_geometry(video: dict, namespace: str) -> dict:
    """奥の壁の内側の、壁の動画のスクリーン (方式 a: マスごとのボーン c<i>、north の面)。"""
    wall = video["glass"]["wall_video"]
    box = video["glass"]["box"]
    plane_w, plane_h, bottom = (v * 16 for v in wall["plane"])
    z = box[1] * 16 / 2 - WALL_VIDEO_GAP
    bones = []
    for cell in range(wall["cells_per_sheet"]):
        u, v = cell_origin(wall, cell)
        bones.append({"name": f"c{cell}", "pivot": [0, 0, 0], "cubes": [{
            "origin": [_num(round(-plane_w / 2, 4)), _num(round(bottom, 4)), _num(round(z, 4))],
            "size": [_num(round(plane_w, 4)), _num(round(plane_h, 4)), 0],
            "uv": {"north": {"uv": [u, v], "uv_size": [wall["width"], wall["height"]]}},
        }]})
    return _geometry(f"geometry.{namespace}.{_short(video)}_wall", (wall["sheet_width"], wall["sheet_height"]), bones, video)


# --- 時計と見え方 (pre_animation) ---

def pre_animation(video: dict, namespace: str) -> list:
    ns = namespace
    glass = video["glass"]
    figure = glass["figure"]
    fps = _num(video["fps"])
    frames = video["frame_count"]
    if video.get("loop"):
        master = f"math.mod(v.base_frame + math.floor((v.t - v.base_time) * {fps}), {frames})"
    else:
        master = f"math.min(v.base_frame + math.floor((v.t - v.base_time) * {fps}), {frames - 1})"
    lines = [
        f"v.seq_now = q.property('{ns}:seq');",
        "v.changed = (v.seq_seen ?? -1) != v.seq_now;",
        "v.t = (math.floor(q.life_time * 20) + q.frame_alpha) / 20;",
        "v.base_time = v.changed ? v.t : v.base_time;",
        f"v.base_frame = v.changed ? q.property('{ns}:frame') : v.base_frame;",
        "v.seq_seen = v.seq_now;",
        f"v.frame = q.property('{ns}:state') == 1 ? {master} : v.base_frame;",
    ]
    if glass["master"] == "figure":
        lines.append("v.fig = v.frame;")
    else:
        # 人物は主 (壁の動画・音声) の時刻から、自分の fps で進める。主の終わり (最後のコマ) より先へは進めない
        ffps = _num(figure["fps"])
        count = figure["frames"]
        lines.append(f"v.secs = q.property('{ns}:state') == 1 ? "
                     f"math.min(v.base_frame / {fps} + (v.t - v.base_time), {_num(round(frames / fps, 6))}) : v.base_frame / {fps};")
        index = f"math.floor(v.secs * {ffps} + 0.0001)"
        lines.append(f"v.fig = math.mod({index}, {count});" if figure["loop"] else f"v.fig = math.min({index}, {count - 1});")
    lines.append(f"v.fsheet = math.floor(v.fig / {figure['per_sheet']});")
    if glass.get("wall_video"):
        per_sheet = glass["wall_video"]["cells_per_sheet"]
        lines += [f"v.vsheet = math.floor(v.frame / {per_sheet});", f"v.vcell = v.frame - v.vsheet * {per_sheet};"]
    if glass.get("case"):
        lines += [
            "v.rel = q.body_y_rotation - q.rotation_to_camera(1);",
            "v.flat = q.distance_from_camera * math.cos(q.rotation_to_camera(0));",
            "v.cam_x = v.flat * math.sin(v.rel);",
            "v.cam_y = -v.flat * math.cos(v.rel);",
        ]
    return lines


def wall_conditions(video: dict) -> dict:
    """ケースの部品ごとに、見せる条件 (カメラがその壁の内側にいる = 壁が奥になる)。床はいつも見せる。"""
    width, depth, _height = video["glass"]["box"]
    half_w, half_d = _num(round(width / 2, 4)), _num(round(depth / 2, 4))
    return {
        "back": f"v.cam_y < {half_d}",
        "front": f"v.cam_y > -{half_d}",
        "left": f"v.cam_x > -{half_w}",
        "right": f"v.cam_x < {half_w}",
        "floor": "1",
    }


def render_controllers(video: dict, namespace: str) -> dict:
    glass = video["glass"]
    state = _state(namespace)
    bright = video.get("lighting", "world") == "bright"
    short = _short(video)
    controllers = {}

    figure = glass["figure"]
    visibility = [{"*": False}] + [{f"f{n}": f"v.fig == {n} && {state} != 0"} for n in range(figure["frames"])]
    controllers[f"controller.render.{namespace}.{short}"] = {
        "arrays": {"textures": {"Array.fig": [f"Texture.fig_{n}" for n in range(figure["sheets"])]}},
        "geometry": "Geometry.default",
        "materials": [{"*": "Material.default"}],
        "textures": ["Array.fig[v.fsheet]"],
        "part_visibility": visibility,
    }
    if glass.get("case"):
        conditions = wall_conditions(video)
        visibility = [{"*": False}] + [{f"case_{part}": f"({conditions[part]}) && {state} != 0"} for part in CASE_PARTS]
        if glass["case"].get("frame"):
            visibility.append({"case_frame": f"{state} != 0"})
        controllers[f"controller.render.{namespace}.{short}_case"] = {
            "geometry": "Geometry.case",
            "materials": [{"*": "Material.opaque"}],
            "textures": ["Texture.case"],
            "part_visibility": visibility,
        }
    if glass.get("wall_video"):
        wall = glass["wall_video"]
        back = wall_conditions(video)["back"]
        visibility = [{"*": False}] + [{f"c{i}": f"v.vcell == {i} && {state} != 0 && ({back})"}
                                       for i in range(wall["cells_per_sheet"])]
        controllers[f"controller.render.{namespace}.{short}_wall"] = {
            "arrays": {"textures": {"Array.vid": [f"Texture.vid_{n}" for n in range(wall["sheets"])]}},
            "geometry": "Geometry.wall",
            "materials": [{"*": "Material.opaque"}],
            "textures": ["Array.vid[v.vsheet]"],
            "part_visibility": visibility,
        }
    if bright:
        for controller in controllers.values():
            controller["ignore_lighting"] = True
    return {"format_version": "1.10.0", "render_controllers": controllers}


def client_entity(video: dict, namespace: str) -> dict:
    glass = video["glass"]
    short = _short(video)
    textures = {f"fig_{n}": path for n, path in enumerate(glass["figure"]["texture_paths"])}
    geometry = {"default": f"geometry.{namespace}.{short}"}
    controllers = [f"controller.render.{namespace}.{short}"]
    if glass.get("case"):
        textures["case"] = glass["case"]["texture_path"]
        geometry["case"] = f"geometry.{namespace}.{short}_case"
        controllers.append(f"controller.render.{namespace}.{short}_case")
    if glass.get("wall_video"):
        textures.update({f"vid_{n}": path for n, path in enumerate(glass["wall_video"]["texture_paths"])})
        geometry["wall"] = f"geometry.{namespace}.{short}_wall"
        controllers.append(f"controller.render.{namespace}.{short}_wall")
    return {"format_version": "1.10.0", "minecraft:client_entity": {"description": {
        "identifier": entity_name(video, "a", namespace),
        # 人物は背景を透かす (アルファテスト。裏も描くが、面の裏はボクセルの内側に隠れる)。ケースと壁の動画は不透明
        "materials": {"default": IMPOSTOR_MATERIAL, "opaque": OPAQUE_MATERIAL},
        "textures": textures,
        "geometry": geometry,
        "render_controllers": controllers,
        "scripts": {"pre_animation": pre_animation(video, namespace)},
    }}}


def _dump(data: dict) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def _dump_compact(data: dict) -> str:
    """人物の geometry は板がとても多い (コマ × 板) ので、空白を入れずに書く (字下げ付きの 3 分の 1 ほど)。"""
    return json.dumps(data, separators=(",", ":"), ensure_ascii=False) + "\n"


def glass_rp_files(video: dict, namespace: str, figure_bones: list, mapping) -> dict:
    """リソースパック内の相対パス (RP/ の下) → JSON の文字列。"""
    short = _short(video)
    files = {
        f"entity/{short}.entity.json": _dump(client_entity(video, namespace)),
        f"models/entity/{short}.geo.json": _dump_compact(figure_geometry(video, namespace, figure_bones)),
        f"render_controllers/{short}.render_controllers.json": _dump(render_controllers(video, namespace)),
    }
    if video["glass"].get("case"):
        files[f"models/entity/{short}_case.geo.json"] = _dump(case_geometry(video, namespace, mapping))
    if video["glass"].get("wall_video"):
        files[f"models/entity/{short}_wall.geo.json"] = _dump(wall_geometry(video, namespace))
    return files
