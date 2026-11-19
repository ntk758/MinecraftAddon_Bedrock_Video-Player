"""骨で動かす人物の踊りを、ゲームのボーンのアニメーションにする (docs/research/glass_case_practical.md の 4.4)。

1. 踊りを 30 Hz で並べ、各時刻で mvcodec.mmd.Poser の姿勢を解く (IK・付与・センターの移動は解いた結果を焼き込む)。
2. geometry に入れたボーン (image_mode/rig.py の Rig.bones) ごとに、親から見た変形を求める。全部ボクセルの座標
   (x 右・y 奥・z 上) で考える。ゲームのボーンは「pivot の周りの回転 R」と「親の座標での移動 d」なので、
   親から見た変形 L (v → R v + t) を R と d = t − pivot + R pivot に分ける。関節でつながったボーンは d = 0 になる。
3. R をオイラー角にする。A20 (Minecraft の実機で決めた) のとおり、rotation の値 (a, b, c) は R = Ry(c) · Rz(−b) · Rx(a)。
   角度が ±180 度で跳ばないよう、前の時刻に一番近い値を選んでつなぐ。
4. ゲームはキーの間をオイラー角の各成分の直線で補間するので、同じ補間で誤差が小さいキーを落とす。誤差は、そのボーンと
   子孫の部品の点が動く距離 (ボクセル) で測る。

移動 (d) があるボーン (センター・全ての親など、根の近く) には、geometry に移動だけを受け持つボーン m<番号> を親として
足す (rig.rig_figure の movers)。回転と移動を同じボーンに入れたときの順番を実機で確かめなくて済む。
移動の値の向き (POSITION_SIGNS) は実機で確かめた (DECISIONS.md の A21。geometry の軸の移動がそのまま position)。
"""
from __future__ import annotations

import json
import math

import numpy as np

from image_mode import planes, rig as rigmod
from mvcodec.mmd import MAX_MOTION_SECONDS

ANIMATION_RATE = 30          # 踊りを並べる間隔 (1 秒あたり)。MMD のフレームと同じ
ROTATION_TOLERANCE = 0.25    # キーを落としてよい、部品の点のずれ (ボクセル)
POSITION_TOLERANCE = 0.1     # 移動のキーを落としてよいずれ (ボクセル)
MOVER_THRESHOLD = 0.05       # 踊りの間にこれより動く (ボクセル) ボーンだけ、移動のボーンを足す
# アニメーションの position の値 = geometry の軸での移動 × この符号 (x, y, z)。実機で、x も含めて geometry と同じ向き
# (DECISIONS.md の A21。Bedrock Wiki の「アニメーションでは x が geometry と逆」は、移動には当たらなかった)
POSITION_SIGNS = (1, 1, 1)
# ボクセルの軸 ↔ MMD の軸 (x, y 上, z 奥) → (x, y 奥, z 上)
_SWAP = np.array([[1, 0, 0], [0, 0, 1], [0, 1, 0]], np.float64)


# --- 回転とオイラー角 (A20) ---

def rx(angle):
    c, s = np.cos(angle), np.sin(angle)
    return _stack(np.ones_like(c), 0 * c, 0 * c, 0 * c, c, -s, 0 * c, s, c)


def ry(angle):
    c, s = np.cos(angle), np.sin(angle)
    return _stack(c, 0 * c, s, 0 * c, np.ones_like(c), 0 * c, -s, 0 * c, c)


def rz(angle):
    c, s = np.cos(angle), np.sin(angle)
    return _stack(c, -s, 0 * c, s, c, 0 * c, 0 * c, 0 * c, np.ones_like(c))


def _stack(*values):
    arrays = np.broadcast_arrays(*[np.asarray(v, np.float64) for v in values])
    return np.stack(arrays, axis=-1).reshape(arrays[0].shape + (3, 3))


def game_rotation(values_deg) -> np.ndarray:
    """アニメーションの rotation の値 (a, b, c) 度 (..., 3) → ボクセルの軸の回転行列 (..., 3, 3)。A20 の式。"""
    v = np.radians(np.asarray(values_deg, np.float64))
    return ry(v[..., 2]) @ rz(-v[..., 1]) @ rx(v[..., 0])


def euler_from_matrix(matrices: np.ndarray) -> np.ndarray:
    """回転行列 (..., 3, 3) → (a, β, c) ラジアン (R = Ry(c) · Rz(β) · Rx(a)。rotation の値は (a, −β, c))。
    β が ±90 度 (ジンバルロック) のときは a = 0 にする。"""
    m = np.asarray(matrices, np.float64)
    beta = np.arcsin(np.clip(m[..., 1, 0], -1.0, 1.0))
    a = np.arctan2(-m[..., 1, 2], m[..., 1, 1])
    c = np.arctan2(-m[..., 2, 0], m[..., 0, 0])
    lock = np.cos(beta) < 1e-6
    if np.any(lock):
        a = np.where(lock, 0.0, a)
        c = np.where(lock, np.arctan2(m[..., 0, 2], m[..., 2, 2]), c)
    return np.stack([a, beta, c], axis=-1)


def _nearest_turn(value: np.ndarray, previous: np.ndarray) -> np.ndarray:
    """value に 2π の倍数を足して、previous に一番近くする。"""
    return value + 2 * np.pi * np.round((previous - value) / (2 * np.pi))


def continuous_euler(matrices: np.ndarray) -> np.ndarray:
    """時刻の順の回転行列 (F, K, 3, 3) → つながったオイラー角 (F, K, 3) ラジアン。

    同じ回転は (a, β, c) と (a + π, π − β, c + π) の 2 通りに書け、どの成分も 2π ずらせる。前の時刻に一番近いものを選ぶ
    (ゲームは成分ごとの直線で補間するので、跳ぶと間のコマで大回りする)。"""
    raw = euler_from_matrix(matrices)
    out = np.empty_like(raw)
    out[0] = raw[0]
    for f in range(1, len(raw)):
        prev = out[f - 1]
        first = _nearest_turn(raw[f], prev)
        other = raw[f] + np.array([np.pi, 0.0, np.pi])
        other[..., 1] = np.pi - raw[f][..., 1]
        second = _nearest_turn(other, prev)
        pick = (np.abs(second - prev).max(axis=-1) < np.abs(first - prev).max(axis=-1))[..., None]
        out[f] = np.where(pick, second, first)
    return out


def to_game_values(euler: np.ndarray) -> np.ndarray:
    """(a, β, c) ラジアン → rotation の値 (a, b, c) 度 (b = −β)。"""
    deg = np.degrees(euler)
    return np.stack([deg[..., 0], -deg[..., 1], deg[..., 2]], axis=-1)


# --- 姿勢 → 親から見た変形 ---

def voxel_transforms(grid: rigmod.RestGrid, skin: np.ndarray) -> np.ndarray:
    """MMD の変形 (B, 4, 4) → ボクセルの座標での変形 (B, 4, 4)。"""
    rot = _SWAP @ skin[:, :3, :3] @ _SWAP.T
    origin = np.asarray(grid.origin, np.float64)
    shift = (skin[:, :3, :3] @ origin) + skin[:, :3, 3] - origin
    out = np.zeros_like(skin, dtype=np.float64)
    out[:, :3, :3] = rot
    out[:, :3, 3] = grid.scale * (shift @ _SWAP.T)
    out[:, 3, 3] = 1.0
    return out


def centre_shift(poser, skin: np.ndarray) -> np.ndarray:
    """その場で踊らせるときに全体から引く、センターの前後左右の移動 (MMD の単位。mmd_to_vox の in_place と同じ)。"""
    if mmd_centre(poser) < 0:
        return np.zeros(3)
    i = mmd_centre(poser)
    moved = skin[i, :3, :3] @ poser.rest[i] + skin[i, :3, 3] - poser.rest[i]
    return np.array([moved[0], 0.0, moved[2]])


def mmd_centre(poser) -> int:
    from mvcodec.mmd import CENTRE_BONE

    return poser.names.index(CENTRE_BONE) if CENTRE_BONE in poser.names else -1


def song_frames(tracks: dict, rate: float = ANIMATION_RATE, seconds: float | None = None) -> np.ndarray:
    """踊りを 1 秒 rate 回で並べた MMD のフレーム番号。seconds があれば、その長さまで (踊りが短ければ最後の姿勢のまま)。
    長さは mvcodec.mmd.MAX_MOTION_SECONDS まで (超えたら ValueError)。"""
    last = max((keys[-1][0] for keys in tracks.values() if keys), default=0)
    length = last / 30 if seconds is None else seconds
    if length > MAX_MOTION_SECONDS:
        limit = MAX_MOTION_SECONDS // 60
        if seconds is None:
            raise ValueError(f"踊りが長すぎます: 最後のキーが {last / 30 / 60:.0f} 分の所にあります (上限 {limit} 分)。"
                             "曲を付けると、曲の長さで作ります")
        raise ValueError(f"曲が長すぎます: {seconds / 60:.0f} 分 (上限 {limit} 分)")
    count = int(math.floor(length * rate + 1e-9)) + 1
    return np.minimum(np.arange(count) * (30 / rate), last if seconds is None else np.inf)


def pose_song(poser, tracks: dict, frames: np.ndarray, progress=None) -> np.ndarray:
    """各フレームの姿勢 (F, B, 4, 4)。メモリを抑えるため float32 (188 秒・224 ボーンで約 80 MB)。"""
    out = np.empty((len(frames), poser.count, 4, 4), np.float32)
    for number, frame in enumerate(frames):
        out[number] = poser.pose(tracks, float(frame))
        if progress:
            progress(number + 1, len(frames))
    return out


class Motion:
    """geometry のボーンごとの、時刻ごとの回転 (F, K, 3, 3) と移動 d (F, K, 3。ボクセル)、と踊りが通る範囲。"""

    def __init__(self, built: rigmod.Rig, skins: np.ndarray, poser, in_place: bool = True):
        grid = built.grid
        bones = built.bones
        index = {b: k for k, b in enumerate(bones)}
        parent_index = [index[built.parents[b]] if built.parents[b] >= 0 else -1 for b in bones]
        pivots = grid.to_voxel(built.rest_positions[bones])
        count = len(skins)
        self.rotation = np.empty((count, len(bones), 3, 3))
        self.move = np.empty((count, len(bones), 3))
        hull_bones, hull_points = _part_hulls(built)
        lo = np.full(3, np.inf)
        hi = np.full(3, -np.inf)
        for f in range(count):
            skin = skins[f].astype(np.float64)
            if in_place:
                skin = skin.copy()
                skin[:, :3, 3] -= centre_shift(poser, skin)
            world = voxel_transforms(grid, skin[bones])
            parent = np.where(np.array(parent_index)[:, None, None] >= 0,
                              world[np.maximum(parent_index, 0)], np.eye(4)[None])
            local = np.linalg.inv(parent) @ world
            r = local[:, :3, :3]
            t = local[:, :3, 3]
            self.rotation[f] = r
            self.move[f] = t - pivots + np.einsum("kij,kj->ki", r, pivots)
            if len(hull_bones):
                chosen = world[hull_bones]
                moved = np.einsum("kij,kcj->kci", chosen[:, :3, :3], hull_points) + chosen[:, None, :3, 3]
                lo = np.minimum(lo, moved.reshape(-1, 3).min(axis=0))
                hi = np.maximum(hi, moved.reshape(-1, 3).max(axis=0))
        self.bones = bones
        self.parent_index = parent_index
        self.pivots = pivots
        self.extent = (lo, hi)
        self.radius = _reach(built, bones, parent_index, pivots)

    def movers(self, threshold: float = MOVER_THRESHOLD) -> list[int]:
        """移動のボーンを足すボーン (PMX の番号)。"""
        far = np.abs(self.move).max(axis=(0, 2)) if len(self.move) else np.zeros(len(self.bones))
        return [b for b, d in zip(self.bones, far) if d > threshold]


def _corners(lo, hi) -> np.ndarray:
    return np.array([[(lo, hi)[i][0], (lo, hi)[j][1], (lo, hi)[k][2]] for i in (0, 1) for j in (0, 1) for k in (0, 1)],
                    np.float64)


# 部品の外側の点を選ぶ向き (26 方向)。踊りが通る範囲は、この向きで一番外の点 (ボクセルの角) を動かして求める
_DIRECTIONS = np.array([(x, y, z) for x in (-1, 0, 1) for y in (-1, 0, 1) for z in (-1, 0, 1) if (x, y, z) != (0, 0, 0)],
                       np.float64)
_DIRECTIONS /= np.linalg.norm(_DIRECTIONS, axis=1, keepdims=True)


def _part_hulls(built: rigmod.Rig) -> tuple[np.ndarray, np.ndarray]:
    """geometry のボーンの番号 (Rig.bones の順) と、その部品の外側の点 (K, 26, 3。ボクセルの角)。

    部品を囲む箱の角を動かすと、細長い部品が斜めに回ったときに箱の角がはみ出して、範囲が大きく出る。26 の向き
    それぞれで一番外の角を選ぶ (立体の外形の近似。本物より少し小さく出ることがあるので、使う側で余白を足す。
    実物のモデルの 202 コマでは、メッシュの範囲より各向き 0.6〜1.2 ボクセル外側だった)。"""
    corner = np.array([(x, y, z) for x in (0, 1) for y in (0, 1) for z in (0, 1)], np.float64)
    bones, points = [], []
    for k, bone in enumerate(built.bones):
        cells = built.rest.cells[built.members_v[built.members_p == bone]]
        if not len(cells):
            continue
        surface = built.rest.surface[built.members_v[built.members_p == bone]]
        if surface.any():
            cells = cells[surface]
        candidates = (cells[:, None, :] + corner[None]).reshape(-1, 3)
        picks = np.argmax(candidates @ _DIRECTIONS.T, axis=0)
        bones.append(k)
        points.append(candidates[picks])
    if not bones:
        return np.zeros(0, np.int64), np.zeros((0, 26, 3))
    return np.array(bones, np.int64), np.array(points)


def _part_boxes(built: rigmod.Rig) -> dict[int, tuple[np.ndarray, np.ndarray]]:
    """geometry のボーンの番号 (Rig.bones の順) → その部品のボクセルを囲む箱 (ボクセルの座標)。"""
    out = {}
    for k, bone in enumerate(built.bones):
        cells = built.rest.cells[built.members_v[built.members_p == bone]]
        if len(cells):
            out[k] = (cells.min(axis=0).astype(np.float64), cells.max(axis=0) + 1.0)
    return out


def _reach(built: rigmod.Rig, bones, parent_index, pivots) -> np.ndarray:
    """ボーンごとに、そのボーンが回るとき部品と子孫の部品の点が pivot から離れうる距離の上限 (ボクセル)。
    子孫は自分の関節で回るので、関節までの距離 + 子の上限、で抑える。"""
    radius = np.zeros(len(bones))
    boxes = _part_boxes(built)
    for k, (lo, hi) in boxes.items():
        radius[k] = np.linalg.norm(_corners(lo, hi) - pivots[k], axis=1).max()
    for k in reversed(range(len(bones))):  # 親が先の順なので、後ろから子を親へ足す
        p = parent_index[k]
        if p >= 0:
            radius[p] = max(radius[p], np.linalg.norm(pivots[k] - pivots[p]) + radius[k])
    return radius


# --- キーを落とす ---

def _rotation_angle(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """回転行列どうしの角度 (ラジアン)。"""
    trace = np.einsum("...ji,...ji->...", a, b)
    return np.arccos(np.clip((trace - 1) / 2, -1.0, 1.0))


def _largest_span(count: int, start: int, ok) -> int:
    """start から先で ok(start, j) が真の、一番遠い j (倍々に伸ばして、二分で詰める)。"""
    good = start + 1
    step = 1
    bad = None
    while good < count - 1:
        candidate = min(count - 1, good + step)
        if ok(start, candidate):
            good = candidate
            step *= 2
        else:
            bad = candidate
            break
    if bad is not None:
        while bad - good > 1:
            middle = (good + bad) // 2
            if ok(start, middle):
                good = middle
            else:
                bad = middle
    return good


def reduce_rotation(euler: np.ndarray, truth: np.ndarray, radius: float, tolerance: float) -> list[int]:
    """1 本のボーンの回転のキー (時刻の番号)。euler (F, 3) を成分ごとに直線で補間したとき、正しい回転 truth (F, 3, 3)
    との角度 θ で、部品の点のずれ 2 sin(θ / 2) × radius が tolerance 以下になるキーだけ残す。"""
    count = len(euler)
    if count <= 2:
        return list(range(count))
    limit = 2 * math.asin(min(1.0, tolerance / max(2 * radius, 1e-9))) if radius > 0 else math.pi

    def ok(i: int, j: int) -> bool:
        if j - i < 2:
            return True
        t = (np.arange(i + 1, j) - i)[:, None] / (j - i)
        values = euler[i] * (1 - t) + euler[j] * t
        mats = ry(values[:, 2]) @ rz(values[:, 1]) @ rx(values[:, 0])
        return bool(_rotation_angle(mats, truth[i + 1:j]).max() <= limit)

    if ok(0, count - 1) and _rotation_angle(truth[0], truth).max() <= limit:
        return [0]  # 踊りの間ずっと同じ向き
    keys = [0]
    while keys[-1] < count - 1:
        keys.append(_largest_span(count, keys[-1], ok))
    return keys


def reduce_position(move: np.ndarray, tolerance: float) -> list[int]:
    """移動 (F, 3) のキー。直線の補間のずれが tolerance (ボクセル) 以下。"""
    count = len(move)
    if count <= 2:
        return list(range(count))
    if np.abs(move - move[0]).max() <= tolerance:
        return [0]

    def ok(i: int, j: int) -> bool:
        if j - i < 2:
            return True
        t = (np.arange(i + 1, j) - i)[:, None] / (j - i)
        values = move[i] * (1 - t) + move[j] * t
        return bool(np.linalg.norm(values - move[i + 1:j], axis=1).max() <= tolerance)

    keys = [0]
    while keys[-1] < count - 1:
        keys.append(_largest_span(count, keys[-1], ok))
    return keys


# --- 書き出し ---

def _time(index: int, rate: float) -> str:
    text = f"{index / rate:.4f}".rstrip("0").rstrip(".")
    return text if "." in text else text + ".0"


def _value(v: float) -> float | int:
    v = round(float(v), 3)
    return int(v) if v == int(v) else v


def position_values(move_voxels: np.ndarray, placing: planes.Placing,
                    mapping: planes.FaceMapping = planes.DEFAULT_MAPPING) -> np.ndarray:
    """ボクセルの移動 (..., 3) → アニメーションの position の値 (geometry の単位 × POSITION_SIGNS)。"""
    out = np.zeros(np.shape(move_voxels))
    for vox_axis in range(3):
        model, sign = mapping.model_axis(vox_axis)
        out[..., model] = sign * np.asarray(move_voxels)[..., vox_axis] * placing.scale * POSITION_SIGNS[model]
    return out


def build_animation(motion: Motion, placing: planes.Placing, identifier: str, *, rate: float = ANIMATION_RATE,
                    loop: bool = False, time_expression: str = "v.secs", movers: list[int] | None = None,
                    rotation_tolerance: float = ROTATION_TOLERANCE, position_tolerance: float = POSITION_TOLERANCE,
                    mapping: planes.FaceMapping = planes.DEFAULT_MAPPING) -> tuple[dict, dict]:
    """アニメーションの JSON (dict) と、数 (キーの数など) の dict。時刻は time_expression (秒) で決める
    (anim_time_update。止める・飛ばすは時計の側)。繰り返さないときは最後の姿勢で止まる。"""
    count = len(motion.rotation)
    length = (count - 1) / rate
    euler = continuous_euler(motion.rotation)
    values = to_game_values(euler)
    movers = motion.movers() if movers is None else movers
    channels = {}
    rotation_keys = position_keys = 0
    for k, bone in enumerate(motion.bones):
        keys = reduce_rotation(euler[:, k], motion.rotation[:, k], motion.radius[k], rotation_tolerance)
        rotation_keys += len(keys)
        if len(keys) == 1 and np.allclose(values[keys[0], k], 0, atol=1e-3):
            pass  # 初期姿勢のまま (書かない)
        else:
            channels[rigmod.bone_name(bone)] = {"rotation": {
                _time(i, rate): [_value(v) for v in values[i, k]] for i in keys} if len(keys) > 1
                else [_value(v) for v in values[keys[0], k]]}
        if bone in movers:
            keys = reduce_position(motion.move[:, k], position_tolerance)
            position_keys += len(keys)
            game = position_values(motion.move[:, k], placing, mapping)
            channels[mover_name(bone)] = {"position": {
                _time(i, rate): [_value(v) for v in game[i]] for i in keys} if len(keys) > 1
                else [_value(v) for v in game[keys[0]]]}
    if loop:
        update = f"math.mod({time_expression}, {_value(length) or 1})"
    else:
        update = f"math.clamp({time_expression}, 0, {_value(max(0.0, length - 0.001))})"
    body = {"loop": True, "animation_length": _value(length) or 0.05, "anim_time_update": update, "bones": channels}
    stats = {"seconds": round(length, 3), "rate": rate, "bones": len(motion.bones), "animated": len(channels),
             "rotation_keys": rotation_keys, "position_keys": position_keys, "movers": len(movers),
             "raw_keys": count * len(motion.bones)}
    return {"format_version": "1.8.0", "animations": {identifier: body}}, stats


def mover_name(bone: int) -> str:
    """移動だけを受け持つ geometry のボーンの名前 (rig.rig_figure と同じ)。"""
    return f"m{bone}"


def dump(animation: dict) -> str:
    return json.dumps(animation, separators=(",", ":"), ensure_ascii=False) + "\n"


# --- ゲームと同じ計算で姿勢を戻す (テストと測定用) ---

def _channel_value(channel, time: float) -> np.ndarray:
    if not isinstance(channel, dict):
        return np.asarray(channel, np.float64)
    times = np.array([float(t) for t in channel])
    values = np.array(list(channel.values()), np.float64)
    order = np.argsort(times)
    times, values = times[order], values[order]
    return np.array([np.interp(time, times, values[:, i]) for i in range(3)])


def evaluate(animation: dict, identifier: str, motion: Motion, placing: planes.Placing, time: float,
             mapping: planes.FaceMapping = planes.DEFAULT_MAPPING) -> np.ndarray:
    """アニメーションの時刻 time の、geometry のボーンごとのボクセルの座標での変形 (K, 4, 4)。A20 のとおり
    (回転は pivot の周り・ローカル、移動は移動のボーン (親の座標))。ゲームの中の計算の代わり。"""
    bones = animation["animations"][identifier]["bones"]
    out = np.zeros((len(motion.bones), 4, 4))
    for k, bone in enumerate(motion.bones):
        rotation = np.eye(3)
        channel = bones.get(rigmod.bone_name(bone), {}).get("rotation")
        if channel is not None:
            rotation = game_rotation(_channel_value(channel, time))
        move = np.zeros(3)
        channel = bones.get(mover_name(bone), {}).get("position")
        if channel is not None:
            game = _channel_value(channel, time)
            for vox_axis in range(3):
                model, sign = mapping.model_axis(vox_axis)
                move[vox_axis] = game[model] * POSITION_SIGNS[model] * sign / placing.scale
        pivot = motion.pivots[k]
        local = np.eye(4)
        local[:3, :3] = rotation
        local[:3, 3] = pivot - rotation @ pivot + move
        p = motion.parent_index[k]
        out[k] = (out[p] if p >= 0 else np.eye(4)) @ local
    return out


# --- MMD から人物を作る (convert_image_mode.py の .pmx の入力) ---

class PosedDance:
    """MMD のモデルと、踊りの全部の姿勢 (1 回だけ計算して、重ね代などを変えた人物で使い回す)。"""

    def __init__(self, pmx_path, vmd_path=None, *, seconds: float | None = None, rate: float = ANIMATION_RATE,
                 log=print):
        from mvcodec import mmd

        self.model, self.materials = rigmod.load_model(pmx_path)
        self.tracks = mmd.read_vmd(vmd_path) if vmd_path else {}
        self.poser = mmd.Poser(self.model)
        self.rate = rate
        frames = song_frames(self.tracks, rate, seconds)
        log(f"踊りの姿勢を計算します: {len(frames)} コマ ({rate:g} Hz、{(len(frames) - 1) / rate:.1f} 秒)")
        step = max(1, len(frames) // 10)
        self.skins = pose_song(self.poser, self.tracks, frames,
                               progress=lambda n, total: log(f"  姿勢 {n}/{total}")
                               if n % step == 0 and n != total else None)


def figure_from_dance(dance: PosedDance, *, identifier: str, figure_blocks: float,
                      voxels: int = rigmod.DEFAULT_VOXELS, overlap: int = rigmod.DEFAULT_OVERLAP, loop: bool = False,
                      in_place: bool = True, split: str = planes.DEFAULT_PLANE_SPLIT, shade: bool = True,
                      mapping: planes.FaceMapping = planes.DEFAULT_MAPPING, max_sheet: int = 2048, gutter: int = 1,
                      log=print) -> dict:
    """計算済みの踊りから、骨で動かす人物 (convert_image_mode.build_glass_files の figure)。
    figure["rig"] に、アニメーションの JSON の文字列 ("animation")・長さ ("seconds")・数を入れる。"""
    moving = bool(dance.tracks)
    built = rigmod.build_rig(dance.model, dance.materials, [dance.tracks] if moving else [], voxels=voxels,
                             overlap=overlap, posed=[dance.skins] if moving else None)
    log(f"部品: ボクセル {len(built.rest.cells)} (表面 {int(built.rest.surface.sum())})、"
        f"geometry のボーン {len(built.bones)} 本 (動かないボーンを親にまとめた)")
    motion = Motion(built, dance.skins, dance.poser, in_place)
    movers = motion.movers()
    figure = rigmod.rig_figure(built, figure_blocks=figure_blocks, split=split, shade=shade, mapping=mapping,
                               max_sheet=max_sheet, gutter=gutter, movers=movers, extent=motion.extent)
    animation, stats = build_animation(motion, figure["rig"]["placing"], identifier, rate=dance.rate, loop=loop,
                                       movers=movers, mapping=mapping)
    figure["rig"].update({"animation": dump(animation), "seconds": stats["seconds"], "bones": len(built.bones),
                          "keys": stats["rotation_keys"] + stats["position_keys"], "stats": stats})
    return figure


def mmd_figure(pmx_path, vmd_path=None, *, identifier: str, figure_blocks: float, seconds: float | None = None,
               rate: float = ANIMATION_RATE, log=print, **options) -> dict:
    """PMX と VMD から、骨で動かす人物。VMD が無ければ動かない人物。seconds は再生の長さ (曲があれば曲の長さ)。
    踊りが短ければ最後の姿勢で止まり、長ければそこで切る。options は figure_from_dance のもの。"""
    dance = PosedDance(pmx_path, vmd_path, seconds=seconds, rate=rate, log=log)
    return figure_from_dance(dance, identifier=identifier, figure_blocks=figure_blocks, log=log, **options)
