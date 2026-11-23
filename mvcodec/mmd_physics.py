"""MMD の髪・スカートの揺れ (剛体とジョイント) を、変換のときに計算する (docs/research/mmd_image_mode_design_2026-11.md の 5 章)。

ゲームの中では計算しない。踊りの姿勢を 1 コマずつ求めるときに、物理で動くボーンの向きをここで決めて、
ボーンのアニメーションに焼き込む (mvcodec.mmd.Poser.pose の physics)。

解き方はプリセットで 2 つ:
- standard・full: 剛体の物理 (RigidPhysics。このファイルの後半)。MMD (Bullet) と同じく、剛体ごとに位置・向き・速さ・
  回転の速さ・重さ・慣性を持ち、ばね付き 6 自由度のジョイント (移動と回転の範囲・回転のばね) と、剛体どうしの当たり
  (カプセルで近似) で動かす。位置で解く剛体の物理 (XPBD)。full は細かく刻み、物理で動く剛体どうしも当てる。
- light: 下の「鎖の物理」(ChainPhysics)。速いが、剛体の向きと慣性は見ない。

鎖の物理 (light) の作り:
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
    solver: str = "chain"   # chain (先の点の鎖。速い) か rigid (剛体とジョイント。MMD と同じ考え方)


PRESETS = {
    "light": Preset("light", substeps=2, iterations=2, self_collision=False, tolerance=0.5, solver="chain"),
    "standard": Preset("standard", substeps=6, iterations=2, self_collision=False, tolerance=0.35, solver="rigid"),
    "full": Preset("full", substeps=12, iterations=2, self_collision=True, tolerance=0.25, solver="rigid"),
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


class ChainPhysics:
    """鎖の物理 (light)。callback を Poser.pose の physics に渡す。物理の剛体が無いモデルでは enabled が偽で、何もしない。"""

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


# =====================================================================================================================
# 剛体の物理 (standard・full)。MMD (Bullet) と同じ考え方: 剛体ごとに位置・向き・速さ・回転の速さ・重さ・慣性を持ち、
# ジョイント (ばね付き 6 自由度) の移動と回転の範囲・ばね、体との当たり (カプセル) で動かす。
# 解き方は位置で解く剛体の物理 (XPBD。Müller ほか 2020 "Detailed Rigid Body Simulation with Extended Position Based
# Dynamics")。1 コマを substeps に分け、各ステップで「進める → 制約を直す → 速さを求め直す」を行う。
# ジョイントは、同じ剛体を使わないものどうしの組に分けて (色分け)、組ごとに numpy でまとめて解く。
# =====================================================================================================================

def quat_conj(q: np.ndarray) -> np.ndarray:
    return q * np.array([-1.0, -1.0, -1.0, 1.0])


def quat_rotate(q: np.ndarray, v: np.ndarray) -> np.ndarray:
    """四元数 q (..., 4) で v (..., 3) を回す。"""
    u = q[..., :3]
    w = q[..., 3:4]
    t = 2.0 * np.cross(u, v)
    return v + w * t + np.cross(u, t)


def quat_from_matrix(m: np.ndarray) -> np.ndarray:
    """回転行列 (3, 3) → 四元数 (x, y, z, w)。"""
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


def _normalize_quat(q: np.ndarray) -> np.ndarray:
    return q / np.maximum(np.linalg.norm(q, axis=-1, keepdims=True), 1e-12)


def rotation_vector(q: np.ndarray) -> np.ndarray:
    """四元数 (..., 4) → 回転ベクトル (軸 × 角度。..., 3)。短い方の回り方。"""
    q = np.where(q[..., 3:4] < 0, -q, q)
    s = np.linalg.norm(q[..., :3], axis=-1)
    angle = 2.0 * np.arctan2(s, q[..., 3])
    scale = np.where(s > 1e-12, angle / np.maximum(s, 1e-12), 2.0)
    return q[..., :3] * scale[..., None]


def euler_xyz(q: np.ndarray) -> np.ndarray:
    """四元数 → オイラー角 (x, y, z) で R = Rx · Ry · Rz (Bullet のばね付き 6 自由度のジョイントと同じ順)。"""
    m = quat_matrices(q)
    y = np.arcsin(np.clip(m[..., 0, 2], -1.0, 1.0))
    x = np.arctan2(-m[..., 1, 2], m[..., 2, 2])
    z = np.arctan2(-m[..., 0, 1], m[..., 0, 0])
    return np.stack([x, y, z], axis=-1)


def quat_from_euler_xyz(angles: np.ndarray) -> np.ndarray:
    """オイラー角 (x, y, z) → 四元数 (R = Rx · Ry · Rz)。"""
    half = angles / 2
    c, s = np.cos(half), np.sin(half)
    qx = np.stack([s[..., 0], 0 * c[..., 0], 0 * c[..., 0], c[..., 0]], axis=-1)
    qy = np.stack([0 * c[..., 1], s[..., 1], 0 * c[..., 1], c[..., 1]], axis=-1)
    qz = np.stack([0 * c[..., 2], 0 * c[..., 2], s[..., 2], c[..., 2]], axis=-1)
    return quat_mul(quat_mul(qx, qy), qz)


def _segment_closest(p1, q1, p2, q2):
    """線分 p1-q1 と p2-q2 (どれも (N, 3)) の一番近い点の組 ((N, 3), (N, 3))。"""
    d1 = q1 - p1
    d2 = q2 - p2
    r = p1 - p2
    a = np.sum(d1 * d1, axis=1)
    e = np.sum(d2 * d2, axis=1)
    f = np.sum(d2 * r, axis=1)
    c = np.sum(d1 * r, axis=1)
    b = np.sum(d1 * d2, axis=1)
    denom = a * e - b * b
    s = np.where(denom > 1e-12, np.clip((b * f - c * e) / np.maximum(denom, 1e-12), 0, 1), 0.0)
    s = np.where(a > 1e-12, s, 0.0)
    t = np.where(e > 1e-12, (b * s + f) / np.maximum(e, 1e-12), 0.0)
    over = t > 1
    under = t < 0
    t = np.clip(t, 0, 1)
    s = np.where(over, np.where(a > 1e-12, np.clip((b - c) / np.maximum(a, 1e-12), 0, 1), 0.0), s)
    s = np.where(under, np.where(a > 1e-12, np.clip(-c / np.maximum(a, 1e-12), 0, 1), 0.0), s)
    return p1 + d1 * s[:, None], p2 + d2 * t[:, None]


def _colour_groups(pairs, dynamic):
    """制約の組 (N, 2) を、同じ動く剛体を使わない組どうしの色に分ける。[番号の配列…]"""
    colours = []
    used = []
    for k, (a, b) in enumerate(pairs):
        keys = {int(x) for x in (a, b) if dynamic[x]}
        for c, members in enumerate(used):
            if not keys & members:
                colours[c].append(k)
                members |= keys
                break
        else:
            colours.append([k])
            used.append(set(keys))
    return [np.array(c, np.int64) for c in colours]


def _inertia(body, mass):
    """剛体の形からの慣性の対角 (剛体の座標)。"""
    size = np.abs(body["size"])
    if body["shape"] == mmd.SHAPE_SPHERE:
        r = max(size[0], 1e-3)
        return np.full(3, 0.4 * mass * r * r)
    if body["shape"] == mmd.SHAPE_CAPSULE:
        r, h = max(size[0], 1e-3), max(size[1], 0.0)
        side = mass * (3 * r * r + h * h) / 12
        return np.array([side, 0.5 * mass * r * r, side])
    x, y, z = (max(v, 1e-3) * 2 for v in size)
    return mass / 12 * np.array([y * y + z * z, x * x + z * z, x * x + y * y])


class RigidPhysics:
    """剛体とジョイントの物理 (standard・full)。Physics と同じ使い方 (callback を Poser.pose の physics に渡す)。"""

    def __init__(self, model, poser, preset: Preset, frame_rate: float = FRAME_RATE):
        self.model = model
        self.poser = poser
        self.preset = preset
        self.frame_rate = float(frame_rate)
        bodies = model.rigid_bodies
        rest = poser.rest
        count = len(bodies)
        self.count = count
        self.enabled = any(b["mode"] != mmd.RIGID_FOLLOW_BONE for b in bodies)
        if not self.enabled:
            return
        self.mode = np.array([b["mode"] for b in bodies])
        self.bone = np.array([b["bone"] for b in bodies], np.int64)
        self.dynamic = self.mode != mmd.RIGID_FOLLOW_BONE
        mass = np.array([max(b["mass"], 1e-3) for b in bodies])
        self.inv_mass = np.where(self.dynamic, 1.0 / mass, 0.0)
        inertia = np.array([_inertia(b, m) for b, m in zip(bodies, mass)])
        self.inv_inertia = np.where(self.dynamic[:, None], 1.0 / np.maximum(inertia, 1e-9), 0.0)
        # 剛体の初期姿勢 (モデルの座標) と、ボーンからのずれ (ボーンの初期の向きは回転なし)
        rest_q = np.array([quat_from_matrix(euler_matrix(b["rotation"])) for b in bodies])
        rest_x = np.array([b["position"] for b in bodies], np.float64)
        bone_rest = np.where(self.bone[:, None] >= 0, rest[np.maximum(self.bone, 0)], 0.0)
        self.offset_q = rest_q
        self.offset_x = rest_x - bone_rest
        self.rest_q, self.rest_x = rest_q, rest_x
        # 形 (カプセルで近似: 半径・軸方向の半分の長さ・剛体の座標の軸)
        shapes = [_collider_shape(b) for b in bodies]
        self.radius = np.array([s[0] for s in shapes])
        self.half = np.array([s[1] for s in shapes])
        self.axis = np.array([s[2] for s in shapes])
        self.keep_linear = np.clip(1.0 - np.array([b["linear_damping"] for b in bodies]), 0.0, 1.0)
        self.keep_angular = np.clip(1.0 - np.array([b["angular_damping"] for b in bodies]), 0.0, 1.0)
        # ジョイント (どちらかが動く剛体のもの)。ジョイントの初期の位置と向きを、両方の剛体の座標で持つ
        joints = [j for j in model.joints if self.dynamic[j["a"]] or self.dynamic[j["b"]]]
        self.joint_a = np.array([j["a"] for j in joints], np.int64)
        self.joint_b = np.array([j["b"] for j in joints], np.int64)
        joint_q = np.array([quat_from_matrix(euler_matrix(j["rotation"])) for j in joints]).reshape(-1, 4)
        joint_x = np.array([j["position"] for j in joints], np.float64).reshape(-1, 3)
        self.frame_a_q = quat_mul(quat_conj(rest_q[self.joint_a]), joint_q) if joints else np.zeros((0, 4))
        self.frame_b_q = quat_mul(quat_conj(rest_q[self.joint_b]), joint_q) if joints else np.zeros((0, 4))
        self.frame_a_x = quat_rotate(quat_conj(rest_q[self.joint_a]), joint_x - rest_x[self.joint_a]) if joints else joint_x
        self.frame_b_x = quat_rotate(quat_conj(rest_q[self.joint_b]), joint_x - rest_x[self.joint_b]) if joints else joint_x
        self.move_lower = np.array([j["move_lower"] for j in joints]).reshape(-1, 3)
        self.move_upper = np.array([j["move_upper"] for j in joints]).reshape(-1, 3)
        self.turn_lower = np.array([j["rotate_lower"] for j in joints]).reshape(-1, 3)
        self.turn_upper = np.array([j["rotate_upper"] for j in joints]).reshape(-1, 3)
        spring = np.array([np.abs(j["spring_rotate"]) for j in joints]).reshape(-1, 3)
        self.spring = spring.mean(axis=1) if len(spring) else np.zeros(0)
        self.joint_colours = _colour_groups(np.stack([self.joint_a, self.joint_b], axis=1), self.dynamic)
        # 当たり: 動く剛体 × (ボーンに付いて動く剛体、本格なら動く剛体どうしも)。ジョイントでつながった組は当てない
        joined = {tuple(sorted(p)) for p in zip(self.joint_a.tolist(), self.joint_b.tolist())}
        pairs = []
        for i in range(count):
            if not self.dynamic[i]:
                continue
            for j in range(count):
                if i == j or (self.dynamic[j] and (not preset.self_collision or j < i)):
                    continue
                if tuple(sorted((i, j))) in joined or not _can_collide(bodies[i], bodies[j]):
                    continue
                if self.bone[i] >= 0 and self.bone[i] == self.bone[j]:
                    continue
                pairs.append((i, j))
        self.pairs = np.array(pairs, np.int64).reshape(-1, 2)
        self.x = None
        self.q = None
        self.v = np.zeros((count, 3))
        self.w = np.zeros((count, 3))
        self.targets = None
        self.bone_positions = None

    # --- ボーンに付いて動く剛体の位置 ---

    def _targets(self, g_rot, g_pos):
        """ボーンの姿勢から、全部の剛体の「ボーンに付いて固く動いたとき」の (位置, 向き)。ボーンの無い剛体は初期の所。"""
        has = self.bone >= 0
        b = np.maximum(self.bone, 0)
        q = np.where(has[:, None], quat_mul(g_rot[b], self.offset_q), self.rest_q)
        x = np.where(has[:, None], g_pos[b] + quat_rotate(g_rot[b], self.offset_x), self.rest_x)
        return x, _normalize_quat(q), np.where(has[:, None], g_pos[b], 0.0)

    def callback(self, settle: bool = False):
        def run(g_rot, g_pos, _local_rot):
            if not self.enabled:
                return {}
            now = self._targets(g_rot, g_pos)
            if self.x is None:
                self.x, self.q = now[0].copy(), now[1].copy()
                self.targets = now
            if settle:
                for _ in range(int(SETTLE_SECONDS * self.frame_rate)):
                    self._frame(now, now)
            else:
                self._frame(self.targets, now)
            self.targets = now
            return self._result(now, g_rot, g_pos)
        return run

    # --- 1 コマ ---

    def _frame(self, before, after):
        steps = self.preset.substeps
        h = 1.0 / (self.frame_rate * steps)
        keep_v = self.keep_linear ** h
        keep_w = self.keep_angular ** h
        dyn = self.dynamic
        for s in range(1, steps + 1):
            t = s / steps
            kx = before[0] + (after[0] - before[0]) * t
            kq = _normalize_quat(before[1] + (after[1] - before[1]) * t)
            # ボーンに付いて動く剛体は、ボーンの所へ置く
            self.x[~dyn] = kx[~dyn]
            self.q[~dyn] = kq[~dyn]
            # 進める
            previous_x, previous_q = self.x.copy(), self.q.copy()
            self.v[dyn] += GRAVITY * h
            self.x[dyn] += self.v[dyn] * h
            spin = np.concatenate([self.w[dyn], np.zeros((int(dyn.sum()), 1))], axis=1)
            self.q[dyn] = _normalize_quat(self.q[dyn] + 0.5 * h * quat_mul(spin, self.q[dyn]))
            # 制約
            # 当たりを先に、ジョイントを後に直す (つながりが崩れて、髪やスカートの節の間にすき間が見えないように)
            for _ in range(self.preset.iterations):
                self._solve_contacts()
                for colour in self.joint_colours:
                    self._solve_joints(colour, h)
            # 速さを求め直す
            self.v[dyn] = (self.x[dyn] - previous_x[dyn]) / h * keep_v[dyn, None]
            dq = quat_mul(self.q[dyn], quat_conj(previous_q[dyn]))
            dq = np.where(dq[:, 3:4] < 0, -dq, dq)
            self.w[dyn] = 2.0 * dq[:, :3] / h * keep_w[dyn, None]
            # 物理 + ボーンの位置 (種類 2): 位置はボーンに合わせる (向きは物理のまま)
            follow = self.mode == mmd.RIGID_PHYSICS_BONE_POSITION
            if np.any(follow):
                bone_q = quat_mul(self.q[follow], quat_conj(self.offset_q[follow]))
                anchor = before[2][follow] + (after[2][follow] - before[2][follow]) * t
                self.x[follow] = anchor + quat_rotate(bone_q, self.offset_x[follow])
        # コマの終わりに、ジョイントだけをもう一度直す (速さには入れない)。節の間のすき間を消す
        for _ in range(4):
            for colour in self.joint_colours:
                self._solve_joints(colour, h)

    def _apply_inverse_inertia(self, i, q, vector):
        """世界の座標のベクトルに、剛体 i の慣性の逆数を掛ける。"""
        local = quat_rotate(quat_conj(q), vector)
        return quat_rotate(q, local * self.inv_inertia[i])

    def _positional(self, i1, i2, p1, p2, compliance, h):
        """点 p1 (剛体 i1 の上) を p2 (剛体 i2 の上) へ寄せる (位置の制約。両方の剛体を重さと慣性で分けて動かす)。"""
        delta = p2 - p1
        c = np.linalg.norm(delta, axis=1)
        active = c > 1e-9
        if not np.any(active):
            return
        i1, i2, p1, p2, delta, c = i1[active], i2[active], p1[active], p2[active], delta[active], c[active]
        compliance = compliance[active] if np.ndim(compliance) else compliance
        n = delta / c[:, None]
        q1, q2 = self.q[i1], self.q[i2]
        r1 = p1 - self.x[i1]
        r2 = p2 - self.x[i2]
        rn1 = np.cross(r1, n)
        rn2 = np.cross(r2, n)
        w1 = self.inv_mass[i1] + np.sum(rn1 * self._apply_inverse_inertia(i1, q1, rn1), axis=1)
        w2 = self.inv_mass[i2] + np.sum(rn2 * self._apply_inverse_inertia(i2, q2, rn2), axis=1)
        lam = c / np.maximum(w1 + w2 + compliance / (h * h), 1e-12)
        p = n * lam[:, None]
        self.x[i1] += p * self.inv_mass[i1, None]
        self.x[i2] -= p * self.inv_mass[i2, None]
        a1 = self._apply_inverse_inertia(i1, q1, np.cross(r1, p))
        a2 = self._apply_inverse_inertia(i2, q2, np.cross(r2, p))
        zero = np.zeros((len(i1), 1))
        self.q[i1] = _normalize_quat(q1 + 0.5 * quat_mul(np.concatenate([a1, zero], axis=1), q1))
        self.q[i2] = _normalize_quat(q2 - 0.5 * quat_mul(np.concatenate([a2, zero], axis=1), q2))

    def _angular(self, i1, i2, theta, compliance, h):
        """剛体 i2 を i1 に対して、世界の座標の回転ベクトル theta だけ回す (回転の制約。両方で分ける)。"""
        c = np.linalg.norm(theta, axis=1)
        active = c > 1e-9
        if not np.any(active):
            return
        i1, i2, theta, c = i1[active], i2[active], theta[active], c[active]
        compliance = compliance[active] if np.ndim(compliance) else compliance
        n = theta / c[:, None]
        q1, q2 = self.q[i1], self.q[i2]
        w1 = np.sum(n * self._apply_inverse_inertia(i1, q1, n), axis=1)
        w2 = np.sum(n * self._apply_inverse_inertia(i2, q2, n), axis=1)
        lam = c / np.maximum(w1 + w2 + compliance / (h * h), 1e-12)
        p = n * lam[:, None]
        a1 = self._apply_inverse_inertia(i1, q1, p)
        a2 = self._apply_inverse_inertia(i2, q2, p)
        zero = np.zeros((len(i1), 1))
        self.q[i1] = _normalize_quat(q1 - 0.5 * quat_mul(np.concatenate([a1, zero], axis=1), q1))
        self.q[i2] = _normalize_quat(q2 + 0.5 * quat_mul(np.concatenate([a2, zero], axis=1), q2))

    def _solve_joints(self, k, h):
        """ジョイント (色 1 つ分): 回転の範囲 → 回転のばね → 移動の範囲。"""
        a, b = self.joint_a[k], self.joint_b[k]
        frame_a = _normalize_quat(quat_mul(self.q[a], self.frame_a_q[k]))
        frame_b = _normalize_quat(quat_mul(self.q[b], self.frame_b_q[k]))
        # 回転の範囲 (下限 > 上限の軸は自由)
        relative = quat_mul(quat_conj(frame_a), frame_b)
        angles = euler_xyz(relative)
        lower, upper = self.turn_lower[k], self.turn_upper[k]
        limited = lower <= upper
        clamped = np.where(limited, np.clip(angles, lower, upper), angles)
        target_b = quat_mul(frame_a, quat_from_euler_xyz(clamped))
        theta = rotation_vector(quat_mul(target_b, quat_conj(frame_b)))
        self._angular(a, b, theta, 0.0, h)
        # 回転のばね (初期の向きへ戻す。ばねの値の逆数をやわらかさにする)
        springy = self.spring[k] > 0
        if np.any(springy):
            frame_a = _normalize_quat(quat_mul(self.q[a], self.frame_a_q[k]))
            frame_b = _normalize_quat(quat_mul(self.q[b], self.frame_b_q[k]))
            theta = rotation_vector(quat_mul(frame_a, quat_conj(frame_b)))
            compliance = 1.0 / np.maximum(self.spring[k], 1e-6)
            self._angular(a[springy], b[springy], theta[springy], compliance[springy], h)
        # 移動の範囲 (ジョイントの位置どうしのずれを、A のジョイントの座標で範囲に収める)
        frame_a = _normalize_quat(quat_mul(self.q[a], self.frame_a_q[k]))
        pa = self.x[a] + quat_rotate(self.q[a], self.frame_a_x[k])
        pb = self.x[b] + quat_rotate(self.q[b], self.frame_b_x[k])
        local = quat_rotate(quat_conj(frame_a), pb - pa)
        lo, hi = self.move_lower[k], self.move_upper[k]
        local = np.where(lo <= hi, np.clip(local, lo, hi), local)
        self._positional(a, b, pa + quat_rotate(frame_a, local), pb, 0.0, h)

    def _solve_contacts(self):
        """当たり: 動く剛体のカプセルが、ほかの剛体のカプセルに入っていたら押し出す (同じ剛体に当たる組は平均)。"""
        if not len(self.pairs):
            return
        i, j = self.pairs[:, 0], self.pairs[:, 1]
        ai = quat_rotate(self.q[i], self.axis[i]) * self.half[i, None]
        aj = quat_rotate(self.q[j], self.axis[j]) * self.half[j, None]
        ci, cj = _segment_closest(self.x[i] - ai, self.x[i] + ai, self.x[j] - aj, self.x[j] + aj)
        delta = ci - cj
        distance = np.linalg.norm(delta, axis=1)
        reach = self.radius[i] + self.radius[j]
        hit = distance < reach
        if not np.any(hit):
            return
        i, j, ci, cj, delta, distance, reach = i[hit], j[hit], ci[hit], cj[hit], delta[hit], distance[hit], reach[hit]
        normal = np.where(distance[:, None] > 1e-9, delta / np.maximum(distance, 1e-9)[:, None],
                          np.array([0.0, 1.0, 0.0]))
        depth = reach - distance
        share_i = self.inv_mass[i] / np.maximum(self.inv_mass[i] + self.inv_mass[j], 1e-12)
        share_j = 1.0 - share_i
        move = np.zeros_like(self.x)
        hits = np.zeros(len(self.x))
        np.add.at(move, i, normal * (depth * share_i)[:, None])
        np.add.at(move, j, -normal * (depth * share_j)[:, None])
        np.add.at(hits, i, 1)
        np.add.at(hits, j, self.dynamic[j].astype(float))
        moved = hits > 0
        self.x[moved] += move[moved] / hits[moved, None]

    def _result(self, now, g_rot=None, g_pos=None):
        """物理で動く剛体のボーンの {ボーン: (回転, 位置)}。種類 2 は位置をボーンのまま (親から決まる所) にする。

        向きは物理のまま、位置は親のボーン (物理の結果か、物理の前の姿勢) から決める。ボーンは親とつながったまま動くので、
        剛体のジョイントを直しきれなかった分で節の間が離れて見えないようにする (standard で揺れの途中に 0.19 離れていた。2026-11-23)。
        """
        found = {}
        for k in np.flatnonzero(self.dynamic & (self.bone >= 0)):
            bone = int(self.bone[k])
            if bone in found:
                continue
            rotation = _normalize_quat(quat_mul(self.q[k], quat_conj(self.offset_q[k])))
            if self.mode[k] == mmd.RIGID_PHYSICS_BONE_POSITION:
                position = now[2][k]
            else:
                position = self.x[k] - quat_rotate(rotation, self.offset_x[k])
            found[bone] = (rotation, position)
        if g_rot is None or g_pos is None:
            return found
        rest, parent = self.poser.rest, self.poser.parent
        out = {}
        for bone in sorted(found, key=self._depth):
            rotation, position = found[bone]
            p = parent[bone]
            if p >= 0:
                p_rot, p_pos = out[p] if p in out else (g_rot[p], g_pos[p])
                position = p_pos + quat_rotate(p_rot[None], (rest[bone] - rest[p])[None])[0]
            out[bone] = (rotation, position)
        return out

    def _depth(self, bone):
        depth, p = 0, self.poser.parent[bone]
        while p >= 0:
            depth, p = depth + 1, self.poser.parent[p]
        return depth


def Physics(model, poser, preset: Preset | str = DEFAULT_PRESET, frame_rate: float = FRAME_RATE):
    """プリセットに合った物理 (light は鎖、standard・full は剛体)。"""
    preset = PRESETS[preset] if isinstance(preset, str) else preset
    if preset.solver == "rigid":
        return RigidPhysics(model, poser, preset, frame_rate)
    return ChainPhysics(model, poser, preset, frame_rate)


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
