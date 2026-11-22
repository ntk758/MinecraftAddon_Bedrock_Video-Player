"""モーションキャプチャ (BVH。CMU のデータベースなど) を、MMD のモデルの踊り (VMD) にする (骨の向きを合わせる移し替え)。

    python scripts/bvh_to_vmd.py dance.bvh model.pmx dance.vmd

やっていること:
  1. BVH を読む: 骨の木 (OFFSET・CHANNELS・End Site) と、コマごとの値。各コマの骨の位置と向きを求める。
  2. BVH の骨の名前を、役割 (腰・胸・首・頭・肩・腕・ひじ・手首・もも・ひざ・足首・つま先) に当てる (CMU・Mixamo などの名前)。
  3. MMD のボーンごとに、「根元から先への向き」を BVH の同じ役割の骨の向きに合わせる。胴 (下半身・上半身) は、左右の向き
     (左右の足・腕のつけ根を結ぶ線) も合わせてねじれを決める。腕と脚のねじれは親に従う。
     MMD のモデルの初期姿勢 (A ポーズが多い) と BVH の初期姿勢 (T ポーズが多い) が違っても、向きを合わせるので構わない。
  4. センターの移動: BVH の腰の動きを、脚の長さの比で MMD の大きさにする。
  5. 足の IK: MMD の脚は「足ＩＫ」で動くので、IK を使わずに求めた足首・つま先の位置に、IK のボーンを置くキーを作る。
  6. 1 秒 30 コマ (MMD と同じ) に間引いて、VMD に書く (キーの間は直線)。

座標: BVH は Y が上。キャラクターの左がどちら向きかは、左右の脚のつけ根の位置から決め、MMD (左 = +X、正面 = -Z) に回す。
"""
from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from mvcodec import mmd

VMD_FPS = 30
# BVH の骨の名前 (小文字、":" より後ろ) → 役割。CMU (cgspeed の BVH)・Mixamo・よくある名前
ROLE_NAMES = {
    "hips": ["hips", "hip", "pelvis", "root"],
    "spine": ["lowerback", "spine", "abdomen"],
    "chest": ["spine1", "spine2", "chest", "upperchest", "thorax"],
    "neck": ["neck", "neck1"],
    "head": ["head"],
    "l_shoulder": ["leftshoulder", "lcollar", "leftcollar", "l_clavicle"],
    "l_arm": ["leftarm", "lshldr", "leftuparm", "l_upperarm"],
    "l_forearm": ["leftforearm", "lforearm", "leftlowarm", "l_forearm"],
    "l_hand": ["lefthand", "lhand", "l_hand"],
    "l_upleg": ["leftupleg", "lthigh", "lefthip", "l_thigh"],
    "l_leg": ["leftleg", "lshin", "leftknee", "l_calf"],
    "l_foot": ["leftfoot", "lfoot", "leftankle", "l_foot"],
    "l_toe": ["lefttoebase", "lefttoe", "ltoe", "l_toe"],
}
for _role in [r for r in ROLE_NAMES if r.startswith("l_")]:
    ROLE_NAMES["r_" + _role[2:]] = [n.replace("left", "right").replace("l_", "r_") if n.startswith(("left", "l_"))
                                    else "r" + n[1:] for n in ROLE_NAMES[_role]]
# 同じ名前が 2 回あるとき (Neck と Neck1 など) は、根に近い方を使う役割と、先に近い方を使う役割
PREFER_LAST = {"chest"}

# MMD のボーン → (向きの元の役割, 向きの先の役割, 左右の向きを合わせるか)。左右は l_/r_ の両方に作る
BONE_ROLES = [
    ("下半身", "hips", "legs_mid", True),
    ("上半身", "spine", "neck", True),
    ("首", "neck", "head", False),
    ("頭", "head", "head_end", False),
    ("{side}肩", "{s}_shoulder", "{s}_arm", False),
    ("{side}腕", "{s}_arm", "{s}_forearm", False),
    ("{side}ひじ", "{s}_forearm", "{s}_hand", False),
    ("{side}手首", "{s}_hand", "{s}_hand_end", False),
    ("{side}足", "{s}_upleg", "{s}_leg", False),
    ("{side}ひざ", "{s}_leg", "{s}_foot", False),
    ("{side}足首", "{s}_foot", "{s}_toe", False),
]
SIDES = (("左", "l"), ("右", "r"))
LINEAR_INTERPOLATION = bytes([20, 20, 20, 20, 20, 20, 20, 20, 107, 107, 107, 107, 107, 107, 107, 107] * 4)


@dataclass
class Skeleton:
    """BVH の骨。channels は骨ごとの [(チャンネル名, 値の列の番号)]。end は骨 → End Site のずれ。"""
    names: list[str] = field(default_factory=list)
    parents: list[int] = field(default_factory=list)
    offsets: list[np.ndarray] = field(default_factory=list)
    channels: list[list[tuple[str, int]]] = field(default_factory=list)
    end: dict = field(default_factory=dict)
    frame_time: float = 1 / 30
    motion: np.ndarray = None


def read_bvh(path) -> Skeleton:
    """BVH ファイルを読む。"""
    words = Path(path).read_text(encoding="utf-8", errors="replace").split()
    if not words or words[0].upper() != "HIERARCHY":
        raise ValueError(f"BVH ファイルではありません: {path}")
    skeleton = Skeleton()
    stack = []
    column = 0
    i = 1
    in_end = False
    while i < len(words):
        word = words[i].upper()
        if word in ("ROOT", "JOINT"):
            skeleton.names.append(words[i + 1])
            skeleton.parents.append(stack[-1] if stack else -1)
            skeleton.offsets.append(np.zeros(3))
            skeleton.channels.append([])
            stack.append(len(skeleton.names) - 1)
            i += 2
        elif word == "END":
            in_end = True
            i += 2
        elif word == "OFFSET":
            values = np.array([float(v) for v in words[i + 1:i + 4]])
            if in_end:
                skeleton.end[stack[-1]] = values
            else:
                skeleton.offsets[stack[-1]] = values
            i += 4
        elif word == "CHANNELS":
            count = int(words[i + 1])
            names = words[i + 2:i + 2 + count]
            skeleton.channels[stack[-1]] = [(n.lower(), column + k) for k, n in enumerate(names)]
            column += count
            i += 2 + count
        elif word == "{":
            i += 1
        elif word == "}":
            if in_end:
                in_end = False
            else:
                stack.pop()
            i += 1
        elif word == "MOTION":
            i += 1
            break
        else:
            raise ValueError(f"BVH を読めません (知らない言葉 {words[i]!r})")
    if not skeleton.names:
        raise ValueError("BVH に骨がありません")
    # MOTION: Frames: N / Frame Time: t / 値
    if words[i].lower() != "frames:":
        raise ValueError("BVH の MOTION の書き方が違います (Frames: が無い)")
    count = int(words[i + 1])
    if words[i + 2].lower() != "frame" or words[i + 3].lower() != "time:":
        raise ValueError("BVH の MOTION の書き方が違います (Frame Time: が無い)")
    skeleton.frame_time = float(words[i + 4])
    if not (skeleton.frame_time > 0 and math.isfinite(skeleton.frame_time)):
        raise ValueError(f"BVH の Frame Time が正しくありません: {skeleton.frame_time}")
    values = np.array([float(v) for v in words[i + 5:i + 5 + count * column]], np.float64)
    if len(values) < count * column:
        count = len(values) // max(column, 1)
    if count == 0:
        raise ValueError("BVH にコマがありません")
    skeleton.motion = np.nan_to_num(values[:count * column].reshape(count, column))
    return skeleton


def _axis_rotation(axis: str, degrees: float) -> np.ndarray:
    a = math.radians(degrees)
    c, s = math.cos(a), math.sin(a)
    if axis == "x":
        return np.array([[1, 0, 0], [0, c, -s], [0, s, c]])
    if axis == "y":
        return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])


def forward(skeleton: Skeleton, values: np.ndarray):
    """1 コマの値 → 骨ごとの (位置 (J, 3), 向き (J, 3, 3)) と End Site の位置 {骨: (3,)}。回転は CHANNELS の順に掛ける。"""
    count = len(skeleton.names)
    positions = np.zeros((count, 3))
    rotations = np.zeros((count, 3, 3))
    for j in range(count):
        local = np.eye(3)
        move = skeleton.offsets[j].copy()
        for name, col in skeleton.channels[j]:
            if name.endswith("rotation"):
                local = local @ _axis_rotation(name[0], values[col])
            elif name.endswith("position"):
                move["xyz".index(name[0])] = values[col] if skeleton.parents[j] < 0 else move["xyz".index(name[0])] + values[col]
        p = skeleton.parents[j]
        if p < 0:
            positions[j] = move
            rotations[j] = local
        else:
            positions[j] = positions[p] + rotations[p] @ move
            rotations[j] = rotations[p] @ local
    ends = {j: positions[j] + rotations[j] @ offset for j, offset in skeleton.end.items()}
    return positions, rotations, ends


def find_roles(skeleton: Skeleton) -> dict[str, int]:
    """役割 → BVH の骨の番号。見つからない役割は入れない。"""
    simple = [n.split(":")[-1].lower().replace(" ", "") for n in skeleton.names]
    roles = {}
    for role, candidates in ROLE_NAMES.items():
        hits = [j for j, n in enumerate(simple) if n in candidates]
        if hits:
            roles[role] = hits[-1] if role in PREFER_LAST else hits[0]
    if "hips" not in roles:
        roots = [j for j, p in enumerate(skeleton.parents) if p < 0]
        roles["hips"] = roots[0]
    return roles


def _role_points(skeleton: Skeleton, roles: dict, positions: np.ndarray, ends: dict) -> dict[str, np.ndarray]:
    """役割の点 (骨の根元の位置)。合成の点 legs_mid・head_end・*_hand_end も作る。"""
    points = {role: positions[j] for role, j in roles.items()}
    if "l_upleg" in roles and "r_upleg" in roles:
        points["legs_mid"] = (points["l_upleg"] + points["r_upleg"]) / 2
    for role, end_role in (("head", "head_end"), ("l_hand", "l_hand_end"), ("r_hand", "r_hand_end")):
        if role in roles:
            j = roles[role]
            children = [c for c, p in enumerate(skeleton.parents) if p == j]
            if j in ends:
                points[end_role] = ends[j]
            elif children:
                points[end_role] = np.mean([positions[c] for c in children], axis=0)
    return points


def _frame(primary: np.ndarray, side: np.ndarray) -> np.ndarray:
    """向き primary と、左右の向き side から作る正規直交の向き (列が primary・side・その外積)。"""
    p = primary / max(np.linalg.norm(primary), 1e-12)
    s = side - p * np.dot(side, p)
    s /= max(np.linalg.norm(s), 1e-12)
    return np.stack([p, s, np.cross(p, s)], axis=1)


def _rotation_between(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    a = a / max(np.linalg.norm(a), 1e-12)
    b = b / max(np.linalg.norm(b), 1e-12)
    v = np.cross(a, b)
    c = float(np.dot(a, b))
    if c < -1 + 1e-9:
        other = np.array([1.0, 0, 0]) if abs(a[0]) < 0.9 else np.array([0, 1.0, 0])
        axis = np.cross(a, other)
        axis /= np.linalg.norm(axis)
        return 2 * np.outer(axis, axis) - np.eye(3)
    k = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    return np.eye(3) + k + k @ k / (1 + c)


def _matrix_quat(m: np.ndarray) -> np.ndarray:
    """回転行列 → 四元数 (x, y, z, w)。"""
    t = np.trace(m)
    if t > 0:
        s = math.sqrt(t + 1.0) * 2
        q = [(m[2, 1] - m[1, 2]) / s, (m[0, 2] - m[2, 0]) / s, (m[1, 0] - m[0, 1]) / s, 0.25 * s]
    elif m[0, 0] > m[1, 1] and m[0, 0] > m[2, 2]:
        s = math.sqrt(1.0 + m[0, 0] - m[1, 1] - m[2, 2]) * 2
        q = [0.25 * s, (m[0, 1] + m[1, 0]) / s, (m[0, 2] + m[2, 0]) / s, (m[2, 1] - m[1, 2]) / s]
    elif m[1, 1] > m[2, 2]:
        s = math.sqrt(1.0 + m[1, 1] - m[0, 0] - m[2, 2]) * 2
        q = [(m[0, 1] + m[1, 0]) / s, 0.25 * s, (m[1, 2] + m[2, 1]) / s, (m[0, 2] - m[2, 0]) / s]
    else:
        s = math.sqrt(1.0 + m[2, 2] - m[0, 0] - m[1, 1]) * 2
        q = [(m[0, 2] + m[2, 0]) / s, (m[1, 2] + m[2, 1]) / s, 0.25 * s, (m[1, 0] - m[0, 1]) / s]
    q = np.array(q)
    return q / np.linalg.norm(q)


def _bvh_to_mmd_axes(skeleton: Skeleton, roles: dict) -> np.ndarray:
    """BVH の座標 → MMD の座標 (左 = +X、上 = +Y、正面 = -Z) の回転。初期姿勢の左右の脚のつけ根から左を決める。"""
    positions, _rotations, _ends = forward(skeleton, np.zeros(skeleton.motion.shape[1]))
    up = np.array([0.0, 1.0, 0.0])
    if "l_upleg" in roles and "r_upleg" in roles:
        left = positions[roles["l_upleg"]] - positions[roles["r_upleg"]]
    elif "l_arm" in roles and "r_arm" in roles:
        left = positions[roles["l_arm"]] - positions[roles["r_arm"]]
    else:
        left = np.array([1.0, 0.0, 0.0])
    left = left - up * np.dot(left, up)
    left /= max(np.linalg.norm(left), 1e-12)
    # BVH は右手系 (左 × 上 = 正面)、MMD は左手系 (左 = +X・上 = +Y で正面が -Z)。BVH の正面を MMD の -Z にするので、
    # 前後を反転する (鏡に映す)。位置だけを移し、回転は MMD の座標で求め直すので、反転しても回転は正しい
    back = np.cross(up, left)
    source = np.stack([left, up, back], axis=1)
    return np.eye(3) @ source.T  # source の列 (左・上・背中) → MMD の (X, Y, Z)


def _mmd_tail(model, poser, bone: int):
    info = model.bones[bone].get("tail")
    rest = poser.rest
    if info is not None:
        kind, value = info
        if kind == "bone" and 0 <= value < len(rest) and np.linalg.norm(rest[value] - rest[bone]) > 1e-6:
            return rest[value]
        if kind == "offset" and np.linalg.norm(value) > 1e-6:
            return rest[bone] + value
    children = poser.children[bone]
    if children:
        return rest[children[0]]
    return None


def retarget(skeleton: Skeleton, model, *, fps: float = VMD_FPS, max_seconds: float | None = None,
             log=print) -> dict:
    """BVH を、MMD のモデルの踊りのキー ({ボーン名: [(フレーム, 位置, 回転, 補間)…]}。mmd.read_vmd と同じ形) にする。"""
    poser = mmd.Poser(model)
    index = {name: i for i, name in enumerate(poser.names)}
    rest = poser.rest
    roles = find_roles(skeleton)
    axes = _bvh_to_mmd_axes(skeleton, roles)
    # 使う MMD のボーンと、合わせる向き (MMD の初期姿勢の向き、左右の向き)
    plan = []
    for name, start, end, sided in BONE_ROLES:
        for jp, s in SIDES if "{side}" in name else [("", "")]:
            mmd_name = name.format(side=jp)
            start_role, end_role = start.format(s=s), end.format(s=s)
            if mmd_name not in index:
                continue
            bone = index[mmd_name]
            tail = _mmd_tail(model, poser, bone)
            if mmd_name == "下半身" and "左足" in index and "右足" in index:
                tail = (rest[index["左足"]] + rest[index["右足"]]) / 2
            if mmd_name == "上半身" and "首" in index:
                tail = rest[index["首"]]
            if tail is None:
                continue
            if np.linalg.norm(tail - rest[bone]) < 1e-6:
                # 根元と先が同じ所 (下半身と脚のつけ根が同じ高さのモデル): 胴は真下・真上とみなし、ほかは使わない
                fallback = {"下半身": (0.0, -1.0, 0.0), "上半身": (0.0, 1.0, 0.0)}.get(mmd_name)
                if fallback is None:
                    continue
                tail = rest[bone] + np.array(fallback)
            side_rest = None
            if sided:
                pair = ("左足", "右足") if mmd_name == "下半身" else ("左腕", "右腕")
                if pair[0] in index and pair[1] in index:
                    side_rest = rest[index[pair[0]]] - rest[index[pair[1]]]
            plan.append((bone, start_role, end_role, tail - rest[bone], side_rest,
                         ("l_upleg", "r_upleg") if mmd_name == "下半身" else ("l_arm", "r_arm")))
    plan.sort(key=lambda item: poser.topological_order().index(item[0]))
    mapped = {item[0] for item in plan}
    # 大きさ: 脚の長さ (もも → 足首) の比
    sample_positions, _r, _e = forward(skeleton, skeleton.motion[0])
    scale = 1.0
    if {"l_upleg", "l_foot"} <= set(roles) and "左足" in index and "左足首" in index:
        bvh_leg = np.linalg.norm(sample_positions[roles["l_upleg"]] - sample_positions[roles["l_leg"]]) + \
            np.linalg.norm(sample_positions[roles["l_leg"]] - sample_positions[roles["l_foot"]]) \
            if "l_leg" in roles else np.linalg.norm(sample_positions[roles["l_upleg"]] - sample_positions[roles["l_foot"]])
        mmd_leg = np.linalg.norm(rest[index["左足"]] - rest[index["左ひざ"]]) + \
            np.linalg.norm(rest[index["左ひざ"]] - rest[index["左足首"]]) if "左ひざ" in index else \
            np.linalg.norm(rest[index["左足"]] - rest[index["左足首"]])
        if bvh_leg > 1e-6:
            scale = mmd_leg / bvh_leg
    duration = (len(skeleton.motion) - 1) * skeleton.frame_time
    if max_seconds is not None:
        duration = min(duration, max_seconds)
    count = int(math.floor(duration * fps + 1e-9)) + 1
    log(f"BVH: {len(skeleton.names)} 本の骨、{len(skeleton.motion)} コマ ({1 / skeleton.frame_time:.0f} fps)、"
        f"{duration:.1f} 秒 → {count} コマ ({fps:g} fps)。使う役割 {len(roles)}、移し替える MMD のボーン {len(plan)} 本")
    tracks = {}
    hips0 = None
    centre = index.get(mmd.CENTRE_BONE)
    for n in range(count):
        source = skeleton.motion[min(len(skeleton.motion) - 1, int(round(n / fps / skeleton.frame_time)))]
        positions, _rotations, ends = forward(skeleton, source)
        positions = positions @ axes.T
        ends = {j: axes @ p for j, p in ends.items()}
        points = _role_points(skeleton, roles, positions, ends)
        globals_ = {}
        for bone, start, end, rest_dir, side_rest, side_roles in plan:
            if start not in points or end not in points:
                continue
            target = points[end] - points[start]
            if np.linalg.norm(target) < 1e-9:
                continue
            p = poser.parent[bone]
            while p >= 0 and p not in mapped:
                p = poser.parent[p]
            parent = globals_.get(p, np.eye(3))
            if side_rest is not None and all(r in points for r in side_roles):
                side = points[side_roles[0]] - points[side_roles[1]]
                rotation = _frame(target, side) @ _frame(rest_dir, side_rest).T
            else:
                rotation = _rotation_between(parent @ rest_dir, target) @ parent
            globals_[bone] = rotation
        frame = n * VMD_FPS / fps
        for bone, rotation in globals_.items():
            p = poser.parent[bone]
            while p >= 0 and p not in globals_:
                p = poser.parent[p]
            local = (globals_[p].T if p >= 0 else np.eye(3)) @ rotation
            tracks.setdefault(poser.names[bone], []).append(
                (int(round(frame)), np.zeros(3), _matrix_quat(local), LINEAR_INTERPOLATION))
        if centre is not None and "hips" in roles:
            hips = positions[roles["hips"]]
            if hips0 is None:
                hips0 = hips.copy()
            tracks.setdefault(mmd.CENTRE_BONE, []).append(
                (int(round(frame)), (hips - hips0) * scale, np.array([0.0, 0.0, 0.0, 1.0]), LINEAR_INTERPOLATION))
    _add_ik_keys(model, poser, tracks)
    return tracks


def _add_ik_keys(model, poser, tracks: dict) -> None:
    """IK のボーン (足ＩＫ・つま先ＩＫ) に、IK を使わずに求めた先の骨の位置へ置くキーを足す (足の IK が脚を元へ戻さないように)。"""
    ik_bones = [i for i, b in enumerate(model.bones) if b["ik"] is not None and 0 <= b["ik"]["target"] < poser.count]
    if not ik_bones:
        return
    frames = sorted({key[0] for keys in tracks.values() for key in keys})
    order = poser.topological_order()
    ik_bones.sort(key=order.index)
    rest = poser.rest
    identity = np.array([0.0, 0.0, 0.0, 1.0])
    for frame in frames:
        # IK を使わない姿勢 (IK のボーンは、脚の骨の祖先ではないので、IK のボーンのキーはこの姿勢を変えない)
        skin = poser.pose(tracks, float(frame), use_ik=False)
        placed = {}
        for i in ik_bones:
            target = model.bones[i]["ik"]["target"]
            goal = (skin[target] @ np.append(rest[target], 1.0))[:3]
            p = poser.parent[i]
            if p in placed:
                parent_rotation, parent_position = placed[p]
            elif p >= 0:
                parent_rotation, parent_position = skin[p][:3, :3], (skin[p] @ np.append(rest[p], 1.0))[:3]
            else:
                parent_rotation, parent_position = np.eye(3), np.zeros(3)
            # 置いた位置 = 親の位置 + 親の向き · (初期姿勢での親からのずれ + キーの移動)
            offset = rest[i] - (rest[p] if p >= 0 else 0)
            move = parent_rotation.T @ (goal - parent_position) - offset
            placed[i] = (parent_rotation, goal)
            tracks.setdefault(poser.names[i], []).append((frame, move, identity, LINEAR_INTERPOLATION))


def write_vmd(path, tracks: dict, model_name: str = "") -> None:
    """ボーンのキー ({名前: [(フレーム, 位置, 回転, 補間)…]}) を VMD に書く (表情・カメラは無し)。"""
    out = bytearray(b"Vocaloid Motion Data 0002".ljust(30, b"\0"))
    out += model_name.encode("shift_jis", errors="replace")[:20].ljust(20, b"\0")
    keys = [(name, key) for name, entries in tracks.items() for key in entries]
    out += struct.pack("<I", len(keys))
    for name, (frame, position, rotation, interpolation) in keys:
        out += name.encode("shift_jis", errors="replace")[:15].ljust(15, b"\0")
        out += struct.pack("<I", int(frame)) + struct.pack("<3f", *position) + struct.pack("<4f", *rotation)
        out += bytes(interpolation)[:64].ljust(64, b"\0")
    out += struct.pack("<I", 0) * 4   # 表情・カメラ・照明・セルフ影
    Path(path).write_bytes(bytes(out))


def bvh_to_vmd(bvh_path, pmx_path, vmd_path, *, max_seconds: float | None = None, log=print) -> Path:
    """BVH を、PMX のモデル用の VMD にして書く。書いた VMD のパスを返す。"""
    skeleton = read_bvh(bvh_path)
    model = mmd.read_pmx(pmx_path)
    tracks = retarget(skeleton, model, max_seconds=max_seconds, log=log)
    if not tracks:
        raise ValueError("BVH の骨を、モデルのボーンに当てられませんでした (骨の名前が分かりません)")
    write_vmd(vmd_path, tracks)
    return Path(vmd_path)


def ensure_vmd(motion_path, pmx_path, work_dir, *, max_seconds: float | None = None, log=print):
    """踊りのファイルが BVH なら、モデル用の VMD にして work_dir に書き、そのパスを返す。VMD (か None) はそのまま返す。"""
    if not motion_path or Path(motion_path).suffix.lower() != ".bvh":
        return motion_path
    out = Path(work_dir) / (Path(motion_path).stem + "_retarget.vmd")
    log(f"モーションキャプチャ (BVH) をモデルの踊りにします: {Path(motion_path).name}")
    return bvh_to_vmd(motion_path, pmx_path, out, max_seconds=max_seconds, log=log)
