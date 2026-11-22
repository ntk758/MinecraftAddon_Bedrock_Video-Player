"""MMD の髪・スカートの揺れ (剛体とジョイント) を、変換のときに計算する (docs/research/mmd_image_mode_design_2026-11.md の 5 章)。

ゲームの中では計算しない。踊りの姿勢を 1 コマずつ求めるときに、物理で動くボーンの向きをここで決めて、
ボーンのアニメーションに焼き込む (mvcodec.mmd.Poser.pose の physics)。

作り (位置で解く、鎖の物理):
- 物理で動く剛体 (種類 1・2) のボーンを「節」にする。節は、ボーンの根元 (head) から先 (tail) へ伸びた棒で、
  先の点を粒として動かす。根元は親の節の棒の向き (親が物理なら) か、踊りの姿勢 (親がボーンに付いて動くなら) で決まる。
- 粒は重力と速さで動き (Verlet)、次の制約で直す (根元に近い節から順に。同じ深さの節はまとめて numpy で計算する):
  1. 形を保つばね: 親に付いて固く動いたときの先の位置へ、少し引き戻す (ジョイントのばねが強いほど強く)
  2. 曲がる角度: ジョイントの回転の範囲を越えて曲がらない (範囲が 0 の節は固く付いて動く)
  3. 長さ: 根元からの長さを変えない
  4. 体との当たり: ボーンに付いて動く剛体 (球・カプセル・箱) に、粒と棒の真ん中が入らない (当たりのグループに従う)
  5. 横のつながり: 親子でない物理の剛体どうしのジョイント (スカートの横) は、粒どうしの距離を保つ
  6. 本格のときは、物理の粒どうしも当たる
- 1 コマ (1/30 秒) を substeps に分け、踊りのボーンの動きをその間で直線に補間する。最初に 2 秒、踊りの最初の
  姿勢のまま揺らして落ち着かせる。
- 節のボーンの向きは、「親に付いて固く動いたときの向き」を、先の向きが粒へ向くように最小の回転でひねったもの
  (棒の軸まわりのねじれは親に従う)。

MMD (Bullet) の剛体の計算とそっくりにはならない。毎回同じ結果になり、numpy だけで動く (EXE とブラウザ版でも使える)。
座標は MMD のまま (Y が上、単位は MMD の 1 = 約 8 cm)。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from mvcodec import mmd

# MMD の重力 (MMD の単位 / 秒²。Bullet の 9.8 m/s² を MMD の 1 単位 = 0.1 m として 98)
GRAVITY = np.array([0.0, -98.0, 0.0])
FRAME_RATE = 30
SETTLE_SECONDS = 2.0
# ばねの強さ (ジョイントの回転のばねの値) を、1 回の直しで引き戻す割合にする係数と上限
SPRING_SCALE = 1.0 / 400.0
SPRING_MAX = 0.35
# 範囲がこれより小さいジョイントは、曲がらない (固く付いて動く) とみなす (ラジアン)
LOCK_ANGLE = 1e-3
# 長さがこれより短い棒は向きを決められないので、物理で動かさない (MMD の単位)
MIN_LENGTH = 1e-3


@dataclass(frozen=True)
class Preset:
    """物理の細かさ。substeps は 1 コマを分ける数、iterations は制約を直す回数、self_collision は物理の粒どうしの当たり。
    tolerance はアニメーションのキーを落としてよいずれ (ボクセル。image_mode.rig_anim が使う。揺れるボーンはずっと
    動くので、キーの数はこれで決まる)。"""
    name: str
    substeps: int
    iterations: int
    self_collision: bool
    tolerance: float


PRESETS = {
    "light": Preset("light", substeps=2, iterations=2, self_collision=False, tolerance=0.5),
    "standard": Preset("standard", substeps=4, iterations=4, self_collision=False, tolerance=0.35),
    "full": Preset("full", substeps=8, iterations=6, self_collision=True, tolerance=0.25),
}
DEFAULT_PRESET = "standard"


# --- 回転 (四元数は (x, y, z, w)。mvcodec.mmd と同じ) ---

def euler_matrix(rotation) -> np.ndarray:
    """PMX の剛体・ジョイントの回転 (ラジアン、x・y・z) → 行列。MMD と同じ Y・X・Z の順 (R = Ry · Rx · Rz)。"""
    x, y, z = (float(v) for v in rotation)
    cx, sx, cy, sy, cz, sz = math.cos(x), math.sin(x), math.cos(y), math.sin(y), math.cos(z), math.sin(z)
    rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    rz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    return ry @ rx @ rz


def quat_matrices(q: np.ndarray) -> np.ndarray:
    """四元数 (..., 4) → 行列 (..., 3, 3)。"""
    x, y, z, w = q[..., 0], q[..., 1], q[..., 2], q[..., 3]
    out = np.empty(q.shape[:-1] + (3, 3))
    out[..., 0, 0] = 1 - 2 * (y * y + z * z)
    out[..., 0, 1] = 2 * (x * y - z * w)
    out[..., 0, 2] = 2 * (x * z + y * w)
    out[..., 1, 0] = 2 * (x * y + z * w)
    out[..., 1, 1] = 1 - 2 * (x * x + z * z)
    out[..., 1, 2] = 2 * (y * z - x * w)
    out[..., 2, 0] = 2 * (x * z - y * w)
    out[..., 2, 1] = 2 * (y * z + x * w)
    out[..., 2, 2] = 1 - 2 * (x * x + y * y)
    return out


def quat_mul(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """四元数の積 (..., 4)。mvcodec.mmd._quat_mul と同じ並び。"""
    ax, ay, az, aw = a[..., 0], a[..., 1], a[..., 2], a[..., 3]
    bx, by, bz, bw = b[..., 0], b[..., 1], b[..., 2], b[..., 3]
    return np.stack([aw * bx + ax * bw + ay * bz - az * by,
                     aw * by - ax * bz + ay * bw + az * bx,
                     aw * bz + ax * by - ay * bx + az * bw,
                     aw * bw - ax * bx - ay * by - az * bz], axis=-1)


def rotation_between(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """単位ベクトル a (..., 3) を b へ向ける最小の回転の四元数 (..., 4)。正反対のときは a に垂直な軸で 180 度。"""
    dot = np.clip(np.sum(a * b, axis=-1), -1.0, 1.0)
    axis = np.cross(a, b)
    q = np.concatenate([axis, (1.0 + dot)[..., None]], axis=-1)
    opposite = dot < -1 + 1e-9
    if np.any(opposite):
        other = np.where(np.abs(a[..., :1]) < 0.9, np.array([1.0, 0.0, 0.0]), np.array([0.0, 1.0, 0.0]))
        perpendicular = np.cross(a, other)
        q = np.where(opposite[..., None], np.concatenate([perpendicular, np.zeros(a.shape[:-1] + (1,))], axis=-1), q)
    return q / np.linalg.norm(q, axis=-1, keepdims=True)


def _normalize(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v, axis=-1, keepdims=True)
    return v / np.maximum(n, 1e-12)


# --- 体の当たり ---

def _collider_shape(body):
    """剛体 → (半径, 軸方向の半分の長さ, 剛体の座標での軸)。球は長さ 0。箱は一番長い辺を軸にしたカプセルで近似する。"""
    size = np.abs(body["size"])
    if body["shape"] == mmd.SHAPE_SPHERE:
        return float(size[0]), 0.0, np.array([0.0, 1.0, 0.0])
    if body["shape"] == mmd.SHAPE_CAPSULE:
        return float(size[0]), float(size[1]) / 2, np.array([0.0, 1.0, 0.0])
    axis = int(np.argmax(size))
    others = [size[k] for k in range(3) if k != axis]
    direction = np.zeros(3)
    direction[axis] = 1.0
    radius = float(np.mean(others))
    return radius, max(0.0, float(size[axis]) - radius), direction


def _can_collide(a, b) -> bool:
    """当たりのグループ: 互いの mask に相手のグループの印があれば当たる (saba・Bullet と同じ読み方)。"""
    return bool(a["mask"] >> b["group"] & 1) and bool(b["mask"] >> a["group"] & 1)


class Physics:
    """モデルの物理。step_frame を毎コマ呼ぶ (Poser.pose の physics に callback を渡す)。物理の剛体が無いモデルでは
    enabled が偽で、何もしない。"""

    def __init__(self, model, poser, preset: Preset | str = DEFAULT_PRESET, frame_rate: float = FRAME_RATE):
        self.model = model
        self.poser = poser
        self.frame_rate = float(frame_rate)
        self.preset = PRESETS[preset] if isinstance(preset, str) else preset
        bodies = model.rigid_bodies
        rest = poser.rest
        # 物理で動く剛体のボーン → 節 (同じボーンに剛体が 2 つあれば最初のもの)
        node_of_bone = {}
        node_body = []
        for k, body in enumerate(bodies):
            if body["mode"] in (mmd.RIGID_PHYSICS, mmd.RIGID_PHYSICS_BONE_POSITION) and body["bone"] >= 0:
                if body["bone"] not in node_of_bone:
                    node_of_bone[body["bone"]] = len(node_body)
                    node_body.append(k)
        nodes = []
        for n, k in enumerate(node_body):
            bone = bodies[k]["bone"]
            head = rest[bone]
            tail = self._tail(bone, bodies[k])
            nodes.append((bone, k, head, tail))
        # 長さの無い節 (向きを決められない) は外す
        nodes = [(bone, k, head, tail) for bone, k, head, tail in nodes if np.linalg.norm(tail - head) > MIN_LENGTH]
        self.bones = np.array([bone for bone, *_ in nodes], np.int64)
        self.body_index = [k for _, k, _, _ in nodes]
        self.enabled = len(nodes) > 0
        if not self.enabled:
            return
        node_of_bone = {bone: n for n, bone in enumerate(self.bones)}
        count = len(nodes)
        self.rest_head = np.array([head for _, _, head, _ in nodes])
        self.rest_tail = np.array([tail for _, _, _, tail in nodes])
        self.length = np.linalg.norm(self.rest_tail - self.rest_head, axis=1)
        # 親の節 (一番近い祖先のボーンで、物理で動くもの)。無ければ -1 (根元は踊りの姿勢で決まる)
        self.parent_node = np.full(count, -1, np.int64)
        for n, bone in enumerate(self.bones):
            p = poser.parent[bone]
            while p >= 0 and p not in node_of_bone:
                p = poser.parent[p]
            self.parent_node[n] = node_of_bone.get(p, -1)
        depth = np.zeros(count, np.int64)
        for n in range(count):
            d, p = 0, self.parent_node[n]
            while p >= 0:
                d += 1
                p = self.parent_node[p]
            depth[n] = d
        self.levels = [np.flatnonzero(depth == d) for d in range(int(depth.max()) + 1)]
        # 粒の半径・減衰
        self.radius = np.array([_collider_shape(bodies[k])[0] for k in self.body_index])
        damping = np.array([bodies[k]["linear_damping"] for k in self.body_index])
        self.keep = np.clip(1.0 - damping, 0.0, 1.0)   # 1 秒あたりの速さの残り
        # ジョイント: 節 (子の剛体) ごとの曲がる角度の上限とばね、横のつながり
        self.max_angle = np.full(count, math.pi)
        self.spring = np.zeros(count)
        node_of_body = {k: n for n, k in enumerate(self.body_index)}
        links = []
        for joint in model.joints:
            a, b = joint["a"], joint["b"]
            na, nb = node_of_body.get(a), node_of_body.get(b)
            child = None
            if nb is not None and (na is None or self.parent_node[nb] == na):
                child = nb
            elif na is not None and nb is not None and self.parent_node[na] == nb:
                child = na
            if child is not None:
                limits = np.abs(np.concatenate([joint["rotate_lower"], joint["rotate_upper"]]))
                swing = float(max(limits[[0, 2, 3, 5]]))
                self.max_angle[child] = min(self.max_angle[child], swing)
                self.spring[child] = max(self.spring[child], float(np.mean(np.abs(joint["spring_rotate"]))))
            elif na is not None and nb is not None:
                links.append((na, nb))
        self.locked = self.max_angle < LOCK_ANGLE
        self.links = np.array(links, np.int64).reshape(-1, 2)
        self.link_length = (np.linalg.norm(self.rest_tail[self.links[:, 0]] - self.rest_tail[self.links[:, 1]], axis=1)
                            if len(self.links) else np.zeros(0))
        # 体の当たり: ボーンに付いて動く剛体 (形は剛体の座標、ボーンからのずれ)
        colliders = []
        for k, body in enumerate(bodies):
            if body["mode"] != mmd.RIGID_FOLLOW_BONE or body["bone"] < 0:
                continue
            radius, half, axis = _collider_shape(body)
            rotation = euler_matrix(body["rotation"])
            colliders.append((k, body["bone"], body["position"] - rest[body["bone"]], rotation @ axis, radius, half))
        self.collider_bone = np.array([c[1] for c in colliders], np.int64)
        self.collider_offset = np.array([c[2] for c in colliders]).reshape(-1, 3)
        self.collider_axis = np.array([c[3] for c in colliders]).reshape(-1, 3)
        self.collider_radius = np.array([c[4] for c in colliders])
        self.collider_half = np.array([c[5] for c in colliders])
        self.collides = np.array([[_can_collide(bodies[k], bodies[c[0]]) for c in colliders] for k in self.body_index],
                                 bool).reshape(count, len(colliders))
        # 物理どうしの当たり (本格のとき)。親子と横のつながりの相手は除く
        pairs = []
        if self.preset.self_collision:
            linked = {tuple(sorted(pair)) for pair in self.links.tolist()}
            for i in range(count):
                for j in range(i + 1, count):
                    if self.parent_node[i] == j or self.parent_node[j] == i or (i, j) in linked:
                        continue
                    if _can_collide(bodies[self.body_index[i]], bodies[self.body_index[j]]):
                        pairs.append((i, j))
        self.pairs = np.array(pairs, np.int64).reshape(-1, 2)
        self.x = None        # 粒 (先の点) の位置
        self.previous = None
        self.kinematic = None  # 前のコマの (根元の位置, 固く動いたときの向き, 当たりの位置・軸)

    def _tail(self, bone, body):
        """節の先の点 (初期姿勢)。ボーンの先 → 最初の子のボーン → 剛体の中心、の順に、根元から離れているものを使う。"""
        rest = self.poser.rest
        head = rest[bone]
        tail_info = self.model.bones[bone].get("tail")
        candidates = []
        if tail_info is not None:
            kind, value = tail_info
            if kind == "bone" and 0 <= value < len(rest):
                candidates.append(rest[value])
            elif kind == "offset":
                candidates.append(head + value)
        candidates += [rest[c] for c in self.poser.children[bone]]
        candidates.append(np.asarray(body["position"], np.float64))
        for point in candidates:
            if np.linalg.norm(point - head) > MIN_LENGTH:
                return np.asarray(point, np.float64)
        return head.copy()

    # --- 1 コマ ---

    def _kinematic(self, g_rot, g_pos):
        """踊りの姿勢から、根元が踊りで決まる節の根元・固く動いたときの向き (四元数)・体の当たりの位置と軸。"""
        follow = g_rot[self.bones]   # 物理の前の姿勢では、節のボーンは親に付いて固く動いている
        heads = g_pos[self.bones]
        if len(self.collider_bone):
            r = quat_matrices(g_rot[self.collider_bone])
            centres = g_pos[self.collider_bone] + np.einsum("kij,kj->ki", r, self.collider_offset)
            axes = np.einsum("kij,kj->ki", r, self.collider_axis)
        else:
            centres = axes = np.zeros((0, 3))
        return heads.copy(), follow.copy(), centres, axes

    def callback(self, settle: bool = False):
        """Poser.pose の physics に渡す関数。settle なら、落ち着かせる時間だけ同じ姿勢で揺らしてから答える。"""
        def run(g_rot, g_pos, _local_rot):
            if not self.enabled:
                return {}
            now = self._kinematic(g_rot, g_pos)
            if self.x is None or settle:
                if self.x is None:
                    self._reset(now)
                for _ in range(int(SETTLE_SECONDS * self.frame_rate)):
                    self._frame(now, now)
            else:
                self._frame(self.kinematic, now)
            self.kinematic = now
            return self._result(now)
        return run

    def _reset(self, now):
        heads, follow, _centres, _axes = now
        tails = self._rigid_tails(heads, follow, np.arange(len(self.bones)))
        self.x = tails.copy()
        self.previous = tails.copy()
        self.kinematic = now

    def _rigid_tails(self, heads, rotations, nodes):
        """節が向き rotations (四元数) で、根元 heads にあるときの先の点。"""
        r = quat_matrices(rotations)
        return heads + np.einsum("kij,kj->ki", r, (self.rest_tail - self.rest_head)[nodes])

    def _frame(self, before, after):
        steps = self.preset.substeps
        dt = 1.0 / (self.frame_rate * steps)
        keep = self.keep ** dt
        for s in range(1, steps + 1):
            t = s / steps
            heads = before[0] + (after[0] - before[0]) * t
            follow = after[1]
            centres = before[2] + (after[2] - before[2]) * t
            axes = after[3]
            velocity = (self.x - self.previous) * keep[:, None]
            self.previous = self.x.copy()
            self.x = self.x + velocity + GRAVITY * dt * dt
            for _ in range(self.preset.iterations):
                self._solve(heads, follow, centres, axes)

    def _solve(self, kin_heads, kin_follow, centres, axes):
        """制約を根元に近い節から順に直す。節の根元と向きを決めながら進む (親の結果を子が使う)。"""
        heads = np.empty_like(self.x)
        rotations = np.empty((len(self.x), 4))
        for level in self.levels:
            parents = self.parent_node[level]
            root = parents < 0
            head = kin_heads[level].copy()
            follow = kin_follow[level].copy()
            if np.any(~root):
                p = parents[~root]
                rp = quat_matrices(rotations[p])
                head[~root] = heads[p] + np.einsum("kij,kj->ki", rp, self.rest_head[level[~root]] - self.rest_head[p])
                # 親の節が曲がった分だけ、子の「固く動いたとき」の向きも回す (親の今の向き × 初期姿勢では同じ向き)
                follow[~root] = rotations[p]
            heads[level] = head
            target = self._rigid_tails(head, follow, level)
            x = self.x[level]
            # 1. 形を保つばね
            pull = np.clip(self.spring[level] * SPRING_SCALE, 0.0, SPRING_MAX)
            x = x + (target - x) * pull[:, None]
            # 2. 曲がる角度
            want = _normalize(target - head)
            have = _normalize(x - head)
            cos_angle = np.clip(np.sum(want * have, axis=1), -1.0, 1.0)
            limit = np.where(self.locked[level], 0.0, self.max_angle[level])
            over = np.arccos(cos_angle) > limit + 1e-9
            if np.any(over):
                axis = np.cross(want[over], have[over])
                bad = np.linalg.norm(axis, axis=1) < 1e-9
                axis[bad] = np.cross(want[over][bad], np.array([1.0, 0.0, 0.0]))
                axis = _normalize(axis)
                a = limit[over]
                w = want[over]
                rotated = (w * np.cos(a)[:, None] + np.cross(axis, w) * np.sin(a)[:, None]
                           + axis * np.sum(axis * w, axis=1)[:, None] * (1 - np.cos(a))[:, None])
                have[over] = rotated
            # 3. 長さ
            x = head + have * self.length[level][:, None]
            # 4. 体との当たり (粒と、棒の真ん中)
            x = self._push_out(level, head, x, centres, axes)
            x = head + _normalize(x - head) * self.length[level][:, None]
            self.x[level] = x
            rotations[level] = quat_mul(rotation_between(_normalize(target - head), _normalize(x - head)), follow)
        # 5. 横のつながり (スカートの横)
        if len(self.links):
            a, b = self.links[:, 0], self.links[:, 1]
            delta = self.x[b] - self.x[a]
            distance = np.linalg.norm(delta, axis=1)
            fix = ((distance - self.link_length) / np.maximum(distance, 1e-9))[:, None] * delta * 0.25
            np.add.at(self.x, a, fix)
            np.add.at(self.x, b, -fix)
        # 6. 物理の粒どうし
        if len(self.pairs):
            a, b = self.pairs[:, 0], self.pairs[:, 1]
            delta = self.x[b] - self.x[a]
            distance = np.linalg.norm(delta, axis=1)
            reach = self.radius[a] + self.radius[b]
            hit = distance < reach
            if np.any(hit):
                push = ((reach - distance) / np.maximum(distance, 1e-9))[:, None] * delta * 0.5 * hit[:, None]
                np.add.at(self.x, a, -push)
                np.add.at(self.x, b, push)
        self._heads, self._rotations = heads, rotations

    def _push_out(self, level, head, x, centres, axes):
        """粒 x (と棒の真ん中) が体の当たりに入っていたら外へ出す。"""
        if not len(centres):
            return x
        for point_weight in (1.0, 0.5):   # 1.0 は先の粒、0.5 は棒の真ん中 (動かすのは先の粒を 2 倍)
            point = head + (x - head) * point_weight
            # 当たりの軸の線分で一番近い点
            rel = point[:, None, :] - centres[None, :, :]
            along = np.clip(np.einsum("nkj,kj->nk", rel, axes), -self.collider_half[None, :], self.collider_half[None, :])
            nearest = centres[None, :, :] + axes[None, :, :] * along[..., None]
            delta = point[:, None, :] - nearest
            distance = np.linalg.norm(delta, axis=2)
            reach = self.collider_radius[None, :] + self.radius[level][:, None] * point_weight
            inside = (distance < reach) & self.collides[level]
            if not np.any(inside):
                continue
            depth = np.where(inside, reach - distance, 0.0)
            normal = delta / np.maximum(distance, 1e-9)[..., None]
            # 真上・真下などで向きが決まらないときは、根元から外へ
            flat = distance < 1e-9
            if np.any(flat):
                normal[flat] = np.broadcast_to(_normalize(x - head)[:, None, :], normal.shape)[flat]
            move = np.sum(normal * depth[..., None], axis=1) / point_weight
            x = x + move
        return x

    def _result(self, now):
        """物理で決めた節のボーンの {ボーン: (回転 (四元数), 位置)}。位置は根元 (親から決まる)。"""
        heads, rotations = self._heads, self._rotations
        return {int(bone): (rotations[n], heads[n]) for n, bone in enumerate(self.bones)}


def simulate(model, poser, tracks, frames, preset: Preset | str | None = DEFAULT_PRESET, morph_bones=None,
             progress=None, frame_rate: float = FRAME_RATE):
    """踊りの各フレーム (MMD のフレーム番号の並び。1 秒 frame_rate コマで並んでいること) の姿勢 (F, B, 4, 4) を、物理で
    揺らして求める。preset が None なら物理なし。morph_bones はフレーム → ボーンモーフ (morph_bone_offsets) の関数。
    物理の剛体が無いモデルでは、物理なしと同じ姿勢。"""
    physics = Physics(model, poser, preset, frame_rate) if preset is not None else None
    out = np.empty((len(frames), poser.count, 4, 4), np.float32)
    for number, frame in enumerate(frames):
        offsets = morph_bones(frame) if morph_bones else None
        callback = physics.callback(settle=number == 0) if physics is not None and physics.enabled else None
        out[number] = poser.pose(tracks, float(frame), bone_offsets=offsets, physics=callback)
        if progress:
            progress(number + 1, len(frames))
    return out
