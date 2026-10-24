"""MMD (MikuMikuDance) のモデル (.pmx) と踊り (.vmd) から、立体のアニメ (MagicaVoxel .vox) を作る (Phase 12.3)。

    python scripts/mmd_to_vox.py model.pmx motion.vmd out.vox --height 56 --fps 10

作った .vox は、ほかの .vox と同じように変換できる (だまし絵にするなら --view anamorphic)。

やっていること:
  1. PMX (2.0 / 2.1) を読む: 頂点 (位置・UV・ボーンの重み)、面、テクスチャ、材質、ボーン (親・付与・IK)
  2. VMD を読む: ボーンのキーフレーム (位置・回転・補間曲線)
  3. フレームごとに姿勢を求める: キーフレームの補間 → 変形階層の順に、付与 (回転・移動) → 親からの行列 → IK (CCD)
  4. 頂点を変形する (BDEF1 / BDEF2 / BDEF4。SDEF・QDEF は BDEF2 / BDEF4 として扱う)
  5. 三角形の表面を、ブロックの半分より細かい間隔の点で埋めて、点の色 (テクスチャか材質の色) をブロックに集める
物理 (揺れる髪・スカートの剛体) と表情 (モーフ) は使わない (髪やスカートは体に付いたまま動く)。

座標: MMD は Y が上、モデルは -Z を向く (正面から見る人は -Z の側)。.vox は z が上で、y の小さい方を正面
(見る人の側) にする (scripts/make_demo_dancer.py と同じ向き)。
"""
import math
import struct
from pathlib import Path

import numpy as np
from PIL import Image

# ---------------------------------------------------------------------------
# PMX
# ---------------------------------------------------------------------------

BONE_TAIL_IS_BONE = 0x0001
BONE_IK = 0x0020
BONE_APPEND_ROTATE = 0x0100
BONE_APPEND_TRANSLATE = 0x0200
BONE_FIXED_AXIS = 0x0400
BONE_LOCAL_AXIS = 0x0800
BONE_AFTER_PHYSICS = 0x1000
BONE_EXTERNAL_PARENT = 0x2000


class _Reader:
    def __init__(self, data):
        self.data = data
        self.pos = 0

    def take(self, n):
        chunk = self.data[self.pos:self.pos + n]
        if len(chunk) < n:
            raise ValueError("PMX / VMD ファイルが途中で終わっています")
        self.pos += n
        return chunk

    def unpack(self, fmt):
        size = struct.calcsize(fmt)
        return struct.unpack("<" + fmt, self.take(size))

    def int(self):
        return self.unpack("i")[0]

    def float(self):
        return self.unpack("f")[0]

    def byte(self):
        return self.unpack("B")[0]

    def floats(self, n):
        return np.array(self.unpack(f"{n}f"), np.float32)


class PmxModel:
    """PMX の中身 (使う所だけ)。配列は numpy。"""

    def __init__(self):
        self.positions = None   # (V, 3)
        self.uvs = None         # (V, 2)
        self.bone_indices = None  # (V, 4) int
        self.bone_weights = None  # (V, 4) float
        self.faces = None       # (F, 3) int
        self.textures = []      # 相対パス
        self.materials = []     # dict(name, diffuse (4,), texture, face_count)
        self.bones = []         # dict(name, position, parent, layer, flags, append_parent, append_ratio, ik)


def read_pmx(path):
    """PMX ファイルを読む。"""
    data = Path(path).read_bytes()
    r = _Reader(data)
    if r.take(4) != b"PMX ":
        raise ValueError(f"PMX ファイルではありません: {path}")
    version = r.float()
    if version < 2.0:
        raise ValueError(f"未対応の PMX の版です: {version}")
    globals_count = r.byte()
    header = list(r.take(globals_count))
    encoding = "utf-16-le" if header[0] == 0 else "utf-8"
    extra_uv = header[1]
    vertex_size, texture_size, material_size, bone_size = header[2], header[3], header[4], header[5]

    def text():
        n = r.int()
        return r.take(n).decode(encoding, errors="replace")

    def index(size, unsigned=False):
        if size == 1:
            return r.unpack("B" if unsigned else "b")[0]
        if size == 2:
            return r.unpack("H" if unsigned else "h")[0]
        return r.int()

    model = PmxModel()
    for _ in range(4):  # 名前 (日・英)、コメント (日・英)
        text()

    count = r.int()
    positions = np.zeros((count, 3), np.float32)
    uvs = np.zeros((count, 2), np.float32)
    indices = np.zeros((count, 4), np.int32)
    weights = np.zeros((count, 4), np.float32)
    for v in range(count):
        values = r.unpack("8f")
        positions[v] = values[0:3]
        uvs[v] = values[6:8]
        if extra_uv:
            r.take(16 * extra_uv)
        kind = r.byte()
        if kind == 0:  # BDEF1
            indices[v, 0] = index(bone_size)
            weights[v, 0] = 1.0
        elif kind in (1, 3):  # BDEF2 / SDEF (SDEF は BDEF2 として扱う)
            indices[v, 0] = index(bone_size)
            indices[v, 1] = index(bone_size)
            w = r.float()
            weights[v, 0:2] = (w, 1.0 - w)
            if kind == 3:
                r.take(36)
        elif kind in (2, 4):  # BDEF4 / QDEF
            for k in range(4):
                indices[v, k] = index(bone_size)
            weights[v] = r.floats(4)
        else:
            raise ValueError(f"未対応の頂点の重みの種類: {kind}")
        r.take(4)  # エッジ倍率
    weights_sum = weights.sum(axis=1, keepdims=True)
    weights = np.where(weights_sum > 0, weights / np.maximum(weights_sum, 1e-9), weights)
    indices[indices < 0] = 0
    model.positions, model.uvs, model.bone_indices, model.bone_weights = positions, uvs, indices, weights

    count = r.int()
    faces = np.array([index(vertex_size, unsigned=vertex_size < 4) for _ in range(count)], np.int64)
    model.faces = faces.reshape(-1, 3)

    count = r.int()
    model.textures = [text() for _ in range(count)]

    count = r.int()
    for _ in range(count):
        name = text()
        text()
        diffuse = r.floats(4)
        r.take(12 + 4 + 12 + 1 + 16 + 4)  # 鏡面色・鏡面係数・環境色・描画の印・エッジの色・エッジの太さ
        texture = index(texture_size)
        index(texture_size)  # スフィア
        r.take(1)  # スフィアの種類
        shared_toon = r.byte()
        if shared_toon == 0:
            index(texture_size)
        else:
            r.take(1)
        text()  # メモ
        face_count = r.int()
        model.materials.append({"name": name, "diffuse": diffuse, "texture": texture, "face_count": face_count})

    count = r.int()
    for _ in range(count):
        name = text()
        text()
        position = r.floats(3)
        parent = index(bone_size)
        layer = r.int()
        flags = r.unpack("H")[0]
        if flags & BONE_TAIL_IS_BONE:
            index(bone_size)
        else:
            r.take(12)
        append_parent, append_ratio = -1, 0.0
        if flags & (BONE_APPEND_ROTATE | BONE_APPEND_TRANSLATE):
            append_parent = index(bone_size)
            append_ratio = r.float()
        if flags & BONE_FIXED_AXIS:
            r.take(12)
        if flags & BONE_LOCAL_AXIS:
            r.take(24)
        if flags & BONE_EXTERNAL_PARENT:
            r.take(4)
        ik = None
        if flags & BONE_IK:
            target = index(bone_size)
            loops = r.int()
            limit = r.float()
            links = []
            for _ in range(r.int()):
                link = index(bone_size)
                lower = upper = None
                if r.byte():
                    lower, upper = r.floats(3), r.floats(3)
                links.append({"bone": link, "lower": lower, "upper": upper})
            ik = {"target": target, "loops": loops, "limit": limit, "links": links}
        model.bones.append({"name": name, "position": position, "parent": parent, "layer": layer, "flags": flags,
                            "append_parent": append_parent, "append_ratio": append_ratio, "ik": ik})
    return model


# ---------------------------------------------------------------------------
# VMD
# ---------------------------------------------------------------------------

def read_vmd(path):
    """VMD のボーンのキーフレームを読む。{ボーン名: [(フレーム, 位置 (3,), 回転 (x, y, z, w), 補間 (64 バイト))…]}"""
    data = Path(path).read_bytes()
    r = _Reader(data)
    signature = r.take(30)
    if not signature.startswith(b"Vocaloid Motion Data"):
        raise ValueError(f"VMD ファイルではありません: {path}")
    r.take(20 if b"0002" in signature else 10)  # モデル名
    tracks = {}
    for _ in range(r.unpack("I")[0]):
        raw_name = r.take(15)
        name = raw_name.split(b"\0", 1)[0].decode("shift_jis", errors="replace")
        frame = r.unpack("I")[0]
        values = r.unpack("7f")
        interpolation = r.take(64)
        tracks.setdefault(name, []).append(
            (frame, np.array(values[0:3], np.float64), np.array(values[3:7], np.float64), interpolation))
    for keys in tracks.values():
        keys.sort(key=lambda key: key[0])
    return tracks


def _bezier(x1, y1, x2, y2, x):
    """VMD の補間曲線 (制御点は 0〜127) で、進み x (0〜1) のときの値 (0〜1)。"""
    if x1 == y1 and x2 == y2:
        return x
    p1x, p1y, p2x, p2y = x1 / 127, y1 / 127, x2 / 127, y2 / 127
    lo, hi = 0.0, 1.0
    t = x
    for _ in range(24):
        t = (lo + hi) / 2
        bx = 3 * (1 - t) ** 2 * t * p1x + 3 * (1 - t) * t ** 2 * p2x + t ** 3
        if bx < x:
            lo = t
        else:
            hi = t
    return 3 * (1 - t) ** 2 * t * p1y + 3 * (1 - t) * t ** 2 * p2y + t ** 3


def _slerp(a, b, t):
    dot = float(np.dot(a, b))
    if dot < 0:
        b, dot = -b, -dot
    if dot > 0.9995:
        q = a + t * (b - a)
        return q / np.linalg.norm(q)
    theta = math.acos(dot)
    return (math.sin((1 - t) * theta) * a + math.sin(t * theta) * b) / math.sin(theta)


def sample_track(keys, frame):
    """キーフレームの列から、frame (小数可) の (位置, 回転) を求める。"""
    if frame <= keys[0][0]:
        return keys[0][1], keys[0][2]
    if frame >= keys[-1][0]:
        return keys[-1][1], keys[-1][2]
    lo, hi = 0, len(keys) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if keys[mid][0] <= frame:
            lo = mid
        else:
            hi = mid
    f0, p0, q0, _ = keys[lo]
    f1, p1, q1, interp = keys[hi]
    x = (frame - f0) / max(1, f1 - f0)
    # 補間は終わりのキーフレームが持つ。並びは X, Y, Z, 回転 の各 (x1, y1, x2, y2) が 4 バイトおき
    c = interp
    position = np.array([p0[i] + (p1[i] - p0[i]) * _bezier(c[i], c[4 + i], c[8 + i], c[12 + i], x) for i in range(3)])
    rotation = _slerp(q0, q1, _bezier(c[3], c[7], c[11], c[15], x))
    return position, rotation


# ---------------------------------------------------------------------------
# 姿勢 (クォータニオンは (x, y, z, w))
# ---------------------------------------------------------------------------

def _quat_mul(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return np.array([aw * bx + ax * bw + ay * bz - az * by,
                     aw * by - ax * bz + ay * bw + az * bx,
                     aw * bz + ax * by - ay * bx + az * bw,
                     aw * bw - ax * bx - ay * by - az * bz])


def _quat_matrix(q):
    x, y, z, w = q
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


def _axis_angle(axis, angle):
    s = math.sin(angle / 2)
    return np.array([axis[0] * s, axis[1] * s, axis[2] * s, math.cos(angle / 2)])


IDENTITY = np.array([0.0, 0.0, 0.0, 1.0])


class Poser:
    """モデルの姿勢を求める。pose(tracks, frame) は各ボーンの、初期姿勢からの変形の行列 (B, 4, 4) を返す。"""

    def __init__(self, model):
        self.model = model
        bones = model.bones
        self.count = len(bones)
        self.rest = np.array([b["position"] for b in bones], np.float64)
        self.parent = [b["parent"] if 0 <= b["parent"] < len(bones) else -1 for b in bones]
        # 変形の順: 物理後か → 変形階層 → 番号
        self.order = sorted(range(self.count), key=lambda i: (bool(bones[i]["flags"] & BONE_AFTER_PHYSICS),
                                                              bones[i]["layer"], i))
        self.children = [[] for _ in range(self.count)]
        for i, p in enumerate(self.parent):
            if p >= 0:
                self.children[p].append(i)
        self.names = [b["name"] for b in bones]

    def _update_global(self, i, local_rot, local_pos, g_rot, g_pos):
        p = self.parent[i]
        offset = self.rest[i] - (self.rest[p] if p >= 0 else 0) + local_pos[i]
        if p >= 0:
            g_rot[i] = _quat_mul(g_rot[p], local_rot[i])
            g_pos[i] = g_pos[p] + _quat_matrix(g_rot[p]) @ offset
        else:
            g_rot[i] = local_rot[i]
            g_pos[i] = offset

    def _update_subtree(self, i, local_rot, local_pos, g_rot, g_pos):
        stack = [i]
        while stack:
            j = stack.pop()
            self._update_global(j, local_rot, local_pos, g_rot, g_pos)
            stack.extend(self.children[j])

    def pose(self, tracks, frame):
        bones = self.model.bones
        n = self.count
        motion_rot = np.tile(IDENTITY, (n, 1))
        motion_pos = np.zeros((n, 3))
        for i, name in enumerate(self.names):
            keys = tracks.get(name)
            if keys:
                motion_pos[i], motion_rot[i] = sample_track(keys, frame)
        local_rot = motion_rot.copy()
        local_pos = motion_pos.copy()
        g_rot = np.tile(IDENTITY, (n, 1))
        g_pos = np.zeros((n, 3))
        for i in self.order:
            bone = bones[i]
            # 付与: 元のボーンの回転 (付与・IK を含む) を割合だけ足す。変形階層の順に処理するので、IK で曲げた
            # ボーンを元にする「足D」なども IK の後に計算される
            source = bone["append_parent"]
            if 0 <= source < n and bone["flags"] & BONE_APPEND_ROTATE:
                local_rot[i] = _quat_mul(_slerp(IDENTITY, local_rot[source], bone["append_ratio"]), motion_rot[i])
            if 0 <= source < n and bone["flags"] & BONE_APPEND_TRANSLATE:
                local_pos[i] = motion_pos[i] + local_pos[source] * bone["append_ratio"]
            self._update_global(i, local_rot, local_pos, g_rot, g_pos)
            if bone["ik"] is not None:
                self._solve_ik(i, bone["ik"], local_rot, local_pos, g_rot, g_pos)
        # 初期姿勢からの変形: 点 x (初期姿勢の座標) → R (x - rest) + g_pos
        skin = np.zeros((n, 4, 4))
        for i in range(n):
            rot = _quat_matrix(g_rot[i])
            skin[i, :3, :3] = rot
            skin[i, :3, 3] = g_pos[i] - rot @ self.rest[i]
            skin[i, 3, 3] = 1
        return skin

    def _solve_ik(self, ik_bone, ik, local_rot, local_pos, g_rot, g_pos):
        """CCD で、target のボーン (効果を受ける先端) を ik_bone の位置へ近づける。"""
        target = ik["target"]
        if not (0 <= target < self.count):
            return
        goal = g_pos[ik_bone].copy()
        links = [link for link in ik["links"] if 0 <= link["bone"] < self.count]
        limit = ik["limit"] if ik["limit"] > 0 else math.pi
        for _ in range(max(1, min(ik["loops"], 40))):
            for link in links:
                j = link["bone"]
                effector = g_pos[target]
                to_effector = effector - g_pos[j]
                to_goal = goal - g_pos[j]
                if np.linalg.norm(to_effector) < 1e-6 or np.linalg.norm(to_goal) < 1e-6:
                    continue
                # 回転はリンクのボーンの座標系で求める
                inverse = _quat_matrix(g_rot[j]).T
                a = inverse @ to_effector
                b = inverse @ to_goal
                a /= np.linalg.norm(a)
                b /= np.linalg.norm(b)
                dot = float(np.clip(np.dot(a, b), -1, 1))
                angle = math.acos(dot)
                if angle < 1e-5:
                    continue
                angle = min(angle, limit)
                axis = np.cross(a, b)
                if np.linalg.norm(axis) < 1e-8:
                    continue
                axis /= np.linalg.norm(axis)
                if link["lower"] is not None:
                    # 角度の制限 (膝など、X 軸だけ曲がるボーン): X 軸まわりに直し、範囲に収める
                    lower, upper = link["lower"], link["upper"]
                    if np.allclose(lower[1:], 0) and np.allclose(upper[1:], 0):
                        sign = 1.0 if axis[0] >= 0 else -1.0
                        current = 2 * math.atan2(local_rot[j][0], local_rot[j][3])
                        wanted = float(np.clip(current + sign * angle, min(lower[0], upper[0]), max(lower[0], upper[0])))
                        local_rot[j] = np.array([math.sin(wanted / 2), 0.0, 0.0, math.cos(wanted / 2)])
                        self._update_subtree(j, local_rot, local_pos, g_rot, g_pos)
                        continue
                delta = _axis_angle(axis, angle)
                local_rot[j] = _quat_mul(local_rot[j], delta)
                local_rot[j] /= np.linalg.norm(local_rot[j])
                self._update_subtree(j, local_rot, local_pos, g_rot, g_pos)
            if np.linalg.norm(g_pos[target] - goal) < 1e-4:
                break


def skin_vertices(model, skin):
    """頂点を変形する (B, 4, 4) → (V, 3)。"""
    p = np.concatenate([model.positions.astype(np.float64), np.ones((len(model.positions), 1))], axis=1)
    out = np.zeros((len(p), 3))
    for k in range(4):
        w = model.bone_weights[:, k]
        used = w > 0
        if not used.any():
            continue
        m = skin[model.bone_indices[used, k]]
        out[used] += w[used, None] * np.einsum("vij,vj->vi", m[:, :3, :], p[used])
    return out


# ---------------------------------------------------------------------------
# 色と立体
# ---------------------------------------------------------------------------

class _Materials:
    """材質ごとの面の範囲と、色 (テクスチャか材質の色) の引き方。"""

    def __init__(self, model, base_dir):
        self.ranges = []
        start = 0
        cache = {}
        for material in model.materials:
            count = material["face_count"] // 3
            texture = None
            index = material["texture"]
            if 0 <= index < len(model.textures):
                path = Path(base_dir) / model.textures[index].replace("\\", "/")
                if path not in cache:
                    try:
                        with Image.open(path) as image:
                            cache[path] = np.asarray(image.convert("RGBA"))
                    except (OSError, ValueError):
                        cache[path] = None
                texture = cache[path]
            diffuse = material["diffuse"]
            self.ranges.append((start, start + count, texture, diffuse))
            start += count


def _triangle_points(tri, uv, step):
    """三角形 (T, 3, 3) の表面を、辺の長さ / step 等分した格子の点で埋める。戻り値: 点 (N, 3)、UV (N, 2)、三角形の番号 (N,)。"""
    edges = np.stack([np.linalg.norm(tri[:, 1] - tri[:, 0], axis=1), np.linalg.norm(tri[:, 2] - tri[:, 1], axis=1),
                      np.linalg.norm(tri[:, 0] - tri[:, 2], axis=1)], axis=1).max(axis=1)
    divisions = np.clip(np.ceil(edges / step), 1, 64).astype(int)
    points, uvs, owners = [], [], []
    for k in np.unique(divisions):
        chosen = np.nonzero(divisions == k)[0]
        a, b = np.meshgrid(np.arange(k + 1), np.arange(k + 1), indexing="ij")
        keep = a + b <= k
        u = (a[keep] / k)[None, :, None]
        v = (b[keep] / k)[None, :, None]
        t = tri[chosen]
        points.append((t[:, 0, None] * (1 - u - v) + t[:, 1, None] * u + t[:, 2, None] * v).reshape(-1, 3))
        w = uv[chosen]
        uvs.append((w[:, 0, None] * (1 - u - v) + w[:, 1, None] * u + w[:, 2, None] * v).reshape(-1, 2))
        owners.append(np.repeat(chosen, keep.sum()))
    return np.concatenate(points), np.concatenate(uvs), np.concatenate(owners)


def voxel_colours(model, materials, vertices, origin, scale, size):
    """変形した頂点から、表面のブロックの色 {(x, y, z): RGB} を求める。座標は .vox の向き (z が上、y の小さい方が正面)。"""
    width, depth, height = size
    sums = {}
    for start, end, texture, diffuse in materials.ranges:
        if end <= start or diffuse[3] <= 0.01:
            continue
        faces = model.faces[start:end]
        tri = (vertices[faces] - origin) * scale
        # MMD (x 右, y 上, z 奥 = 正面の反対) → .vox (x 右, y 奥, z 上)
        tri = np.stack([tri[..., 0], tri[..., 2], tri[..., 1]], axis=-1)
        points, uvs, _ = _triangle_points(tri, model.uvs[faces], 0.5)
        if texture is not None:
            th, tw = texture.shape[:2]
            tx = np.clip((np.mod(uvs[:, 0], 1) * tw).astype(int), 0, tw - 1)
            ty = np.clip((np.mod(uvs[:, 1], 1) * th).astype(int), 0, th - 1)
            rgba = texture[ty, tx].astype(np.float64)
            visible = rgba[:, 3] >= 128
            colours = rgba[visible, :3] * np.clip(diffuse[:3], 0, 1)[None]
            points = points[visible]
        else:
            colours = np.tile(np.clip(diffuse[:3], 0, 1) * 255, (len(points), 1))
        cells = np.floor(points).astype(int)
        inside = ((cells >= 0) & (cells < np.array([width, depth, height]))).all(axis=1)
        cells, colours = cells[inside], colours[inside]
        keys = (cells[:, 0] * depth + cells[:, 1]) * height + cells[:, 2]
        order = np.argsort(keys)
        keys, colours = keys[order], colours[order]
        unique, first, counts = np.unique(keys, return_index=True, return_counts=True)
        totals = np.add.reduceat(colours, first, axis=0)
        for key, total, count in zip(unique.tolist(), totals, counts):
            entry = sums.get(key)
            if entry is None:
                sums[key] = [total, count]
            else:
                entry[0] = entry[0] + total
                entry[1] += count
    out = {}
    for key, (total, count) in sums.items():
        x, rest = divmod(key, depth * height)
        y, z = divmod(rest, height)
        out[(x, y, z)] = total / count
    return out


def mmd_to_vox(pmx_path, vmd_path, output, height=56, fps=10, duration=None, max_side=128, progress=None):
    """PMX と VMD から .vox の立体アニメを作る。height は人物の高さ (ブロック)。戻り値は (フレームの数, 大きさ (x, y, z))。"""
    from .volume import write_vox

    model = read_pmx(pmx_path)
    tracks = read_vmd(vmd_path)
    poser = Poser(model)
    materials = _Materials(model, Path(pmx_path).parent)
    last_key = max((keys[-1][0] for keys in tracks.values()), default=0)
    seconds = last_key / 30 if duration is None else min(duration, last_key / 30)
    frames = max(1, int(seconds * fps) + 1)

    # 大きさ: 初期姿勢の高さを height ブロックにし、全フレームの動きが収まる範囲にする
    rest_height = float(model.positions[:, 1].max() - min(0.0, model.positions[:, 1].min()))
    scale = height / max(rest_height, 1e-6)
    posed = []
    lo = np.full(3, np.inf)
    hi = np.full(3, -np.inf)
    for f in range(frames):
        vertices = skin_vertices(model, poser.pose(tracks, f * 30 / fps))
        posed.append(vertices)
        lo = np.minimum(lo, vertices.min(axis=0))
        hi = np.maximum(hi, vertices.max(axis=0))
        if progress:
            progress(f + 1, frames)
    # 床 (y = 0) から上。左右と前後は動いた範囲に余白 1 ブロック
    origin = np.array([lo[0] - 1 / scale, min(0.0, lo[1]), lo[2] - 1 / scale])
    extent = (hi - origin) * scale + 1
    size = (int(min(max_side, math.ceil(extent[0]) + 1)), int(min(max_side, math.ceil(extent[2]) + 1)),
            int(min(256, math.ceil(extent[1]) + 1)))

    voxel_frames = [voxel_colours(model, materials, v, origin, scale, size) for v in posed]
    colours = np.array([c for frame in voxel_frames for c in frame.values()] or [[255, 255, 255]], np.float64)
    palette = _palette(colours)
    models = []
    for frame in voxel_frames:
        if not frame:
            models.append((size, np.zeros((0, 4), np.uint8)))
            continue
        cells = np.array(list(frame.keys()), np.int64)
        rgb = np.array(list(frame.values()))
        nearest = np.argmin(((rgb[:, None, :] - palette[None, :, :3]) ** 2).sum(axis=2), axis=1)
        voxels = np.concatenate([cells, (nearest + 1)[:, None]], axis=1).astype(np.uint8)
        models.append((size, voxels))
    vox_palette = np.zeros((256, 4), np.uint8)
    vox_palette[:len(palette), :3] = palette[:, :3]
    vox_palette[:len(palette), 3] = 255
    Path(output).write_bytes(write_vox(models, vox_palette))
    return frames, size


def _palette(colours, count=255):
    """色の集まりから、count 色までのパレット (count, 3) を作る (Pillow の減色)。"""
    sample = colours
    if len(sample) > 200000:
        sample = sample[np.random.default_rng(0).choice(len(sample), 200000, replace=False)]
    image = Image.fromarray(np.clip(sample, 0, 255).astype(np.uint8).reshape(1, -1, 3))
    quantized = image.quantize(colors=min(count, max(1, len(sample))), method=Image.Quantize.MEDIANCUT)
    raw = np.array(quantized.getpalette()[:3 * count], np.float64).reshape(-1, 3)
    used = np.unique(np.asarray(quantized).reshape(-1))
    return raw[used]
