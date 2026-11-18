"""画像モードのガラスケースの、骨で動かす人物 (docs/research/glass_case_practical.md の 4 章)。

MMD の人物を初期姿勢で 1 回だけボクセルにし、各ボクセルを一番強く効いているボーンの部品に分ける。部品ごとに
面の板 (planes.py) にして geometry のボーンに入れ、踊りはボーンのアニメーションで動かす (アニメーションの書き出しは別)。

- rest_grid: 初期姿勢の高さを voxels ボクセルにする格子 (MMD の床 y = 0 がボクセルの境目に来る)
- surface_voxels: 表面のボクセルの色 (mvcodec.mmd.voxel_colours と同じ) と、多数決で決めた部品のボーン
- fill_interior: 閉じた中を埋める。中のボクセルは一番近い表面のボクセルの色・ボーンを受け継ぐ
- MotionCheck・choose_bones: 踊りで (親から見て) 動かないボーンを、親にまとめる
- part_members: 部品ごとのボクセル。関節 (ウェイトが 2 本のボーンに分かれている所) は隣の部品にも重ねる (重ね代)
- build_rig・rig_figure: 全部をまとめて、シートと geometry のボーンにする
- posed_cells: 部品をボーンの変形どおりに動かした形 (ゲームの外のテストで、正しい形と比べる)

座標: ボクセルは planes.py と同じ (x が右、y が奥、z が上)。MMD (x 右、y 上、z 奥) の y と z を入れ替えた向き。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
from PIL import Image

from image_mode import planes
from mvcodec import mmd

# 三角形の 2 番目のボーンのウェイトが、三角形の全体のこの割合以上なら「関節」(2 本のボーンの間で曲がる所) とみなす
JOINT_SHARE = 0.1
# ボーンを親にまとめてよい、ずれの上限 (ボクセル)。踊りの全部の姿勢で、部品のどこもこれより動かなければまとめる
DEFAULT_TOLERANCE = 0.25
DEFAULT_OVERLAP = 1
DEFAULT_VOXELS = 128
# 動かないボーンを調べるときの姿勢の間隔 (1 秒あたり)
DEFAULT_CHECK_RATE = 10
_SIDES = [(axis, step) for axis in range(3) for step in (1, -1)]


@dataclass(frozen=True)
class RestGrid:
    """初期姿勢をボクセルにする格子。origin は MMD の座標でのボクセル (0, 0, 0) の角、scale は MMD の 1 単位の
    ボクセル数、size は (x, y, z) のボクセル数、floor は MMD の床 (y = 0) のボクセルの z、height は初期姿勢の高さ
    (ボクセル。rest_grid の voxels)。"""
    origin: tuple[float, float, float]
    scale: float
    size: tuple[int, int, int]
    floor: int
    height: float

    def to_voxel(self, points) -> np.ndarray:
        """MMD の点 (..., 3) → ボクセルの座標 (..., 3。実数で、ボクセル (i, j, k) は [i, i + 1) の範囲)。"""
        q = (np.asarray(points, np.float64) - np.asarray(self.origin)) * self.scale
        return q[..., [0, 2, 1]]

    def to_mmd(self, voxels) -> np.ndarray:
        """ボクセルの座標 (..., 3) → MMD の点。"""
        v = np.asarray(voxels, np.float64)
        return v[..., [0, 2, 1]] / self.scale + np.asarray(self.origin)


def rest_height(model) -> float:
    """初期姿勢の高さ (MMD の単位)。床 (y = 0) より下に出た所も含める (mvcodec.mmd.mmd_to_vox と同じ)。"""
    return float(model.positions[:, 1].max() - min(0.0, model.positions[:, 1].min()))


def rest_grid(model, voxels: int = DEFAULT_VOXELS, margin: int = 2) -> RestGrid:
    """初期姿勢の高さを voxels ボクセルにする格子。周りに margin ボクセルの余白を付ける。"""
    height = rest_height(model)
    if height <= 0:
        raise ValueError("モデルの高さが 0 です")
    scale = voxels / height
    lo = model.positions.min(axis=0).astype(np.float64)
    hi = model.positions.max(axis=0).astype(np.float64)
    floor = margin + math.ceil(max(0.0, -lo[1]) * scale)
    origin = (float(lo[0] - margin / scale), -floor / scale, float(lo[2] - margin / scale))
    size = tuple(int(math.ceil((hi[m] - origin[m]) * scale)) + margin for m in (0, 2, 1))
    return RestGrid(origin, scale, size, floor, float(voxels))


def triangle_bones(model, joint_share: float = JOINT_SHARE) -> tuple[np.ndarray, np.ndarray]:
    """三角形ごとの (一番強いボーン, 関節の相手のボーン)。3 つの頂点のウェイトを合計して比べる。

    相手のボーンは、2 番目に強いボーンのウェイトが合計の joint_share 以上のときだけ (無ければ -1)。"""
    faces = np.asarray(model.faces)
    count = len(faces)
    if count == 0:
        return np.zeros(0, np.int64), np.zeros(0, np.int64)
    bones = np.asarray(model.bone_indices)[faces].reshape(count, -1).astype(np.int64)
    weights = np.asarray(model.bone_weights)[faces].reshape(count, -1).astype(np.float64)
    weights = np.where((weights > 0) & (bones >= 0), weights, 0.0)
    bones = np.maximum(bones, 0)
    span = int(bones.max()) + 1
    rows = np.repeat(np.arange(count), bones.shape[1])
    unique, inverse = np.unique(rows * span + bones.ravel(), return_inverse=True)
    totals = np.bincount(inverse, weights=weights.ravel())
    row, bone = unique // span, unique % span
    order = np.lexsort((bone, -totals, row))   # 三角形ごとに強い順 (同じなら番号の小さい順)
    row, bone, totals = row[order], bone[order], totals[order]
    first = np.r_[True, row[1:] != row[:-1]]
    rank = np.arange(len(row)) - np.maximum.accumulate(np.where(first, np.arange(len(row)), 0))
    dominant = np.zeros(count, np.int64)
    dominant[row[rank == 0]] = bone[rank == 0]
    sums = np.bincount(row, weights=totals, minlength=count)
    second = np.full(count, -1, np.int64)
    pick = (rank == 1) & (totals > 0)
    joint = totals[pick] >= joint_share * np.maximum(sums[row[pick]], 1e-12)
    second[row[pick][joint]] = bone[pick][joint]
    return dominant, second


@dataclass
class RestVoxels:
    """初期姿勢のボクセル。cells (N, 3) の格子の位置、rgb (N, 3) の色 (0〜255)、bone (N,) 部品のボーン (PMX の番号)、
    second (N,) 関節の相手のボーン (-1 は関節でない)、surface (N,) 表面か (偽は中を埋めたボクセル)。"""
    grid: RestGrid
    cells: np.ndarray
    rgb: np.ndarray
    bone: np.ndarray
    second: np.ndarray
    surface: np.ndarray


def _majority(keys: np.ndarray, labels: np.ndarray, unique_keys: np.ndarray) -> np.ndarray:
    """keys ごとに一番多い labels (同じ数なら小さい方)。unique_keys の順に返す。labels が無い key は -1。"""
    out = np.full(len(unique_keys), -1, np.int64)
    if len(keys) == 0:
        return out
    span = int(labels.max()) + 1
    codes, counts = np.unique(keys * span + labels, return_counts=True)
    key, label = codes // span, codes % span
    order = np.lexsort((label, -counts, key))
    key, label = key[order], label[order]
    first = np.r_[True, key[1:] != key[:-1]]
    out[np.searchsorted(unique_keys, key[first])] = label[first]
    return out


def surface_voxels(model, materials, grid: RestGrid, vertices=None, tri_bones=None) -> RestVoxels:
    """表面のボクセル。色は mvcodec.mmd.voxel_colours と同じ (三角形の上の点の色の平均。透明な所は数えない)。
    部品のボーンは、そのボクセルに入った点の三角形の、一番強いボーンの多数決。関節の相手も同じく多数決。"""
    vertices = model.positions if vertices is None else vertices
    dominant, second = triangle_bones(model) if tri_bones is None else tri_bones
    width, depth, height = grid.size
    keys_all, rgb_all, bone_all, second_all = [], [], [], []
    for start, end, texture, diffuse in materials.ranges:
        if end <= start or diffuse[3] <= 0.01:
            continue
        faces = model.faces[start:end]
        tri = grid.to_voxel(np.asarray(vertices, np.float64)[faces])
        points, uvs, owners = mmd._triangle_points(tri, model.uvs[faces], 0.5)
        if texture is not None:
            th, tw = texture.shape[:2]
            tx = np.clip((np.mod(uvs[:, 0], 1) * tw).astype(int), 0, tw - 1)
            ty = np.clip((np.mod(uvs[:, 1], 1) * th).astype(int), 0, th - 1)
            rgba = texture[ty, tx].astype(np.float64)
            visible = rgba[:, 3] >= 128
            colours = rgba[visible, :3] * np.clip(diffuse[:3], 0, 1)[None]
            points, owners = points[visible], owners[visible]
        else:
            colours = np.tile(np.clip(diffuse[:3], 0, 1) * 255, (len(points), 1))
        cells = np.floor(points).astype(np.int64)
        inside = ((cells >= 0) & (cells < np.array([width, depth, height]))).all(axis=1)
        cells, colours, owners = cells[inside], colours[inside], owners[inside] + start
        keys_all.append((cells[:, 0] * depth + cells[:, 1]) * height + cells[:, 2])
        rgb_all.append(colours)
        bone_all.append(dominant[owners])
        second_all.append(second[owners])
    if not keys_all or not sum(len(k) for k in keys_all):
        raise ValueError("モデルの表面が格子に入りません (見える材質がありません)")
    keys = np.concatenate(keys_all)
    rgb = np.concatenate(rgb_all)
    bones = np.concatenate(bone_all)
    seconds = np.concatenate(second_all)
    unique, inverse, counts = np.unique(keys, return_inverse=True, return_counts=True)
    colour = np.stack([np.bincount(inverse, weights=rgb[:, c], minlength=len(unique)) for c in range(3)], axis=1)
    colour /= counts[:, None]
    bone = _majority(keys, bones, unique)
    joint = seconds >= 0
    partner = _majority(keys[joint], seconds[joint], unique)
    partner = np.where(partner == bone, -1, partner)
    x, rest = np.divmod(unique, depth * height)
    y, z = np.divmod(rest, height)
    cells = np.stack([x, y, z], axis=1)
    return RestVoxels(grid, cells, colour, bone, partner, np.ones(len(cells), bool))


def _neighbour(values: np.ndarray, axis: int, step: int) -> np.ndarray:
    """out[i] = values[i + step] (axis の向き)。範囲の外は 0。"""
    out = np.zeros_like(values)
    n = values.shape[axis]
    src = [slice(None)] * 3
    dst = [slice(None)] * 3
    if step > 0:
        src[axis], dst[axis] = slice(step, n), slice(0, n - step)
    else:
        src[axis], dst[axis] = slice(0, n + step), slice(-step, n)
    out[tuple(dst)] = values[tuple(src)]
    return out


def fill_interior(rest: RestVoxels) -> RestVoxels:
    """閉じた中 (6 方向の空気で外とつながらない所) を埋める。中のボクセルは一番近い表面のボクセル (6 方向に 1 つずつ
    たどる近さ) の色・ボーン・関節の相手を受け継ぐ。関節を曲げたとき、部品の切り口が空洞でなく中身の色に見える。"""
    from mvcodec.volume import enclosed_air

    lo = rest.cells.min(axis=0) - 1
    shape = tuple(rest.cells.max(axis=0) - lo + 2)
    source = np.zeros(shape, np.int64)
    local = rest.cells - lo
    source[local[:, 0], local[:, 1], local[:, 2]] = np.arange(len(rest.cells)) + 1
    cavity = enclosed_air(source == 0)
    if not cavity.any():
        return rest
    todo = cavity.copy()
    while todo.any():
        grown = False
        for axis, step in _SIDES:
            near = _neighbour(source, axis, step)
            take = todo & (near > 0)
            if take.any():
                source[take] = near[take]
                todo &= ~take
                grown = True
        if not grown:  # 閉じた中は必ず表面に接しているので起きない
            break
    inner = np.argwhere(cavity & ~todo)
    origin = source[inner[:, 0], inner[:, 1], inner[:, 2]] - 1
    return RestVoxels(rest.grid, np.concatenate([rest.cells, inner + lo]),
                      np.concatenate([rest.rgb, rest.rgb[origin]]), np.concatenate([rest.bone, rest.bone[origin]]),
                      np.concatenate([rest.second, rest.second[origin]]),
                      np.concatenate([rest.surface, np.zeros(len(inner), bool)]))


def ancestors(parents, bone: int) -> list[int]:
    """bone の親、その親、… (根まで)。"""
    out = []
    p = parents[bone]
    while p >= 0 and len(out) <= len(parents):
        out.append(p)
        p = parents[p]
    return out


def needed_bones(parents, rest: RestVoxels) -> list[int]:
    """部品のあるボーンと、その祖先。親が先に来る順 (根からの深さの順)。"""
    need = set()
    for bone in np.unique(rest.bone).tolist():
        need.add(bone)
        need.update(ancestors(parents, bone))
    return sorted(need, key=lambda b: (len(ancestors(parents, b)), b))


def subtree_boxes(parents, rest: RestVoxels, bones: list[int]) -> dict[int, tuple[np.ndarray, np.ndarray]]:
    """ボーンごとに、そのボーンと子孫の部品のボクセルを囲む箱 (MMD の座標の (最小, 最大))。"""
    corners = rest.grid.to_mmd(np.concatenate([rest.cells, rest.cells + 1]))
    owner = np.concatenate([rest.bone, rest.bone])
    boxes = {}
    for bone in np.unique(rest.bone).tolist():
        points = corners[owner == bone]
        boxes[bone] = (points.min(axis=0), points.max(axis=0))
    for bone in reversed(bones):  # 深い方から親へ広げる
        if bone not in boxes:
            continue
        p = parents[bone]
        if p >= 0:
            lo, hi = boxes[bone]
            if p in boxes:
                boxes[p] = (np.minimum(boxes[p][0], lo), np.maximum(boxes[p][1], hi))
            else:
                boxes[p] = (lo.copy(), hi.copy())
    return boxes


class MotionCheck:
    """ボーンを祖先にまとめたときに部品がずれる大きさを、踊りの姿勢から測る。

    ボーン b を祖先 a にまとめると、b と子孫の部品は a の変形で動く。ずれは |S_b p − S_a p| (S は初期姿勢からの変形、
    p は部品の点)。剛体の変形の差なので p について凸で、部品を囲む箱の 8 つの角の最大で上から抑えられる。"""

    def __init__(self, parents, boxes: dict):
        pairs = [(b, a) for b in boxes for a in ancestors(parents, b)]
        self.pairs = pairs
        self.child = np.array([b for b, _a in pairs], np.int64)
        self.ancestor = np.array([a for _b, a in pairs], np.int64)
        corners = []
        for b, _a in pairs:
            lo, hi = boxes[b]
            corners.append([[(lo, hi)[i][0], (lo, hi)[j][1], (lo, hi)[k][2]]
                            for i in (0, 1) for j in (0, 1) for k in (0, 1)])
        self.corners = np.array(corners, np.float64).reshape(-1, 8, 3)
        self.error = np.zeros(len(pairs))
        self.poses = 0

    def add(self, skin: np.ndarray) -> None:
        if not self.pairs:
            return
        diff = skin[self.child] - skin[self.ancestor]
        moved = np.einsum("pij,pkj->pki", diff[:, :3, :3], self.corners) + diff[:, None, :3, 3]
        self.error = np.maximum(self.error, np.linalg.norm(moved, axis=2).max(axis=1))
        self.poses += 1

    def errors(self) -> dict[tuple[int, int], float]:
        return {pair: float(e) for pair, e in zip(self.pairs, self.error)}


def choose_bones(parents, bones: list[int], errors: dict | None, tolerance: float) -> dict[int, int]:
    """ボーン → 部品を受け持つボーン (自分か、まとめた先の祖先)。bones は親が先の順。errors が None なら、まとめない。

    根から順に、ボーン b を親の受け持ち t と比べ、踊りの全部の姿勢で b の部品のずれが tolerance (MMD の単位) 以下なら
    t にまとめる。まとめたボーンの子は t の子になる。"""
    target = {}
    for bone in bones:
        p = parents[bone]
        if p < 0 or errors is None:
            target[bone] = bone
            continue
        t = target[p]
        target[bone] = t if errors.get((bone, t), math.inf) <= tolerance else bone
    return target


def resolve_targets(parents, target: dict[int, int]) -> dict[int, int]:
    """全部のボーン → 部品を受け持つボーン。target に無いボーン (部品も子孫の部品も無い。関節の相手にだけ出てくる
    捩りボーンなど) は、target にある一番近い祖先の受け持ち。そんな祖先が無ければ -1。"""
    out = {}
    for bone in range(len(parents)):
        b = bone
        steps = 0
        while b >= 0 and b not in target and steps <= len(parents):
            b = parents[b]
            steps += 1
        out[bone] = target.get(b, -1) if b >= 0 else -1
    return out


def kept_parents(parents, target: dict[int, int]) -> dict[int, int]:
    """残したボーン → 残したボーンの親 (根は -1)。"""
    out = {}
    for bone, t in target.items():
        if bone == t:
            p = parents[bone]
            out[bone] = target[p] if p >= 0 else -1
    return out


def part_members(rest: RestVoxels, target: dict[int, int], overlap: int = DEFAULT_OVERLAP) -> tuple[np.ndarray, np.ndarray]:
    """部品ごとのボクセル: (ボクセルの番号 (M,), 部品のボーン (M,))。重ね代で、1 つのボクセルが 2 つの部品に入ることがある。

    重ね代は関節だけ: 部品 B のボクセルのうち、関節の相手が A のもの (ウェイトが B と A に分かれていた所) を、A の部品の
    隣から overlap ボクセルの深さまで A にも入れる。関節でない所で触れているだけの部品 (スカートと太もも、髪と背中) は
    重ねない (重ねると、離れたときに相手の色の筋が付いて動く)。"""
    span = max([int(rest.bone.max()), int(rest.second.max())] + list(target)) + 1
    table = np.full(span, -1, np.int64)
    for bone, t in target.items():
        table[bone] = t
    part = table[rest.bone]
    if (part < 0).any():
        raise ValueError("部品のボーンに、受け持つボーンがありません")
    partner = np.where(rest.second >= 0, table[np.maximum(rest.second, 0)], -1)
    partner = np.where(partner == part, -1, partner)
    members_v = np.arange(len(part))
    members_p = part.copy()
    if overlap <= 0 or not (partner >= 0).any():
        return members_v, members_p
    lo = rest.cells.min(axis=0) - 1
    dims = rest.cells.max(axis=0) - lo + 2
    local = rest.cells - lo
    keys = (local[:, 0] * dims[1] + local[:, 1]) * dims[2] + local[:, 2]
    order = np.argsort(keys)
    sorted_keys = keys[order]
    span = max(int(part.max()), int(partner.max())) + 1
    seen = set((members_v * span + members_p).tolist())
    frontier_v, frontier_p = members_v, members_p
    added_v, added_p = [], []
    for _depth in range(overlap):
        found_v, found_p = [], []
        for axis, step in _SIDES:
            near = local[frontier_v].copy()
            near[:, axis] += step
            near_keys = (near[:, 0] * dims[1] + near[:, 1]) * dims[2] + near[:, 2]
            at = np.clip(np.searchsorted(sorted_keys, near_keys), 0, len(sorted_keys) - 1)
            hit = sorted_keys[at] == near_keys
            j = order[at[hit]]
            p = frontier_p[hit]
            ok = partner[j] == p
            found_v.append(j[ok])
            found_p.append(p[ok])
        new_v = np.concatenate(found_v)
        new_p = np.concatenate(found_p)
        codes = np.unique(new_v * span + new_p)
        codes = np.array([c for c in codes.tolist() if c not in seen], np.int64)
        if len(codes) == 0:
            break
        seen.update(codes.tolist())
        frontier_v, frontier_p = codes // span, codes % span
        added_v.append(frontier_v)
        added_p.append(frontier_p)
    if added_v:
        members_v = np.concatenate([members_v] + added_v)
        members_p = np.concatenate([members_p] + added_p)
    return members_v, members_p


@dataclass
class Rig:
    """骨で動かす人物の、部品とボーン。bones は geometry に入れるボーン (PMX の番号、親が先)、parents はその親
    (PMX の番号、根は -1)、target は部品のあるボーン (と祖先) → 受け持つボーン。members_* は part_members。"""
    rest: RestVoxels
    bones: list[int]
    parents: dict[int, int]
    target: dict[int, int]
    members_v: np.ndarray
    members_p: np.ndarray
    rest_positions: np.ndarray
    names: list[str]
    poses_checked: int = 0

    @property
    def grid(self) -> RestGrid:
        return self.rest.grid


def motion_frames(tracks: dict, rate: float) -> np.ndarray:
    """踊りの最初から最後のキーまでを、1 秒 rate 回で並べた MMD のフレーム番号 (30 fps)。"""
    last = max((keys[-1][0] for keys in tracks.values() if keys), default=0)
    count = int(last * rate / 30) + 1
    return np.arange(count) * (30 / rate)


def build_rig(model, materials, motions=(), *, voxels: int = DEFAULT_VOXELS, overlap: int = DEFAULT_OVERLAP,
              tolerance: float = DEFAULT_TOLERANCE, check_rate: float = DEFAULT_CHECK_RATE, fill: bool = True,
              merge: bool = True, margin: int = 2, progress=None) -> Rig:
    """MMD のモデル (と踊り) から、部品とボーンを決める。motions は read_vmd の結果のリスト (同じモデルで入れる全部の
    踊り)。どれかの踊りで動くボーンは残し、どの踊りでも動かないボーンは親にまとめる (merge が偽ならまとめない)。
    tolerance はまとめてよいずれ (ボクセル)。margin は格子の余白 (ボクセル。テストで、踊った形も格子に入れるとき)。"""
    grid = rest_grid(model, voxels, margin)
    rest = surface_voxels(model, materials, grid)
    if fill:
        rest = fill_interior(rest)
    poser = mmd.Poser(model)
    bones = needed_bones(poser.parent, rest)
    errors = None
    checked = 0
    motions = list(motions)
    if merge and motions:
        check = MotionCheck(poser.parent, subtree_boxes(poser.parent, rest, bones))
        frames = [(tracks, f) for tracks in motions for f in motion_frames(tracks, check_rate)]
        for number, (tracks, frame) in enumerate(frames):
            check.add(poser.pose(tracks, frame))
            if progress:
                progress(number + 1, len(frames))
        errors = check.errors()
        checked = check.poses
    elif merge:  # 踊りが無ければ、どのボーンも動かないので全部まとめる (動かない人形)
        errors = {(bone, a): 0.0 for bone in bones for a in ancestors(poser.parent, bone)}
    target = resolve_targets(poser.parent, choose_bones(poser.parent, bones, errors, tolerance / grid.scale))
    parents = kept_parents(poser.parent, target)
    kept = [b for b in bones if b in parents]
    members_v, members_p = part_members(rest, target, overlap)
    return Rig(rest, kept, parents, target, members_v, members_p, poser.rest.copy(), list(poser.names), checked)


def bone_name(bone: int) -> str:
    """geometry のボーンの名前 (PMX の名前は日本語が多いので、番号で付ける)。"""
    return f"b{bone}"


def rig_placing(rig: Rig, figure_blocks: float) -> planes.Placing:
    """人物の初期姿勢の高さ (格子の voxels) を figure_blocks ブロックにし、水平の中心を原点、MMD の床を y = 0 に置く。"""
    rest = rig.rest
    scale = 16 * figure_blocks / rest.grid.height
    lo = rest.cells.min(axis=0)
    hi = rest.cells.max(axis=0) + 1
    return planes.Placing(scale=scale, centre=((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2),
                          offset=(0.0, -rest.grid.floor * scale, 0.0))


def bone_pivot(rig: Rig, bone: int, placing: planes.Placing, mapping: planes.FaceMapping = planes.DEFAULT_MAPPING):
    """geometry のボーンの pivot (全体の座標。A20)。初期姿勢のボーンの位置。"""
    voxel = rig.grid.to_voxel(rig.rest_positions[bone])
    return [planes._num(v) for v in planes.model_point(voxel, placing, mapping)]


def part_quads(rig: Rig, bone: int, split: str = planes.DEFAULT_PLANE_SPLIT, shade: bool = True) -> list[planes.Quad]:
    """部品 1 つの面の板 (格子の位置)。色は 1 ボクセルずつの色 (パレットはボクセルの色そのもの)。"""
    voxels = rig.members_v[rig.members_p == bone]
    if len(voxels) == 0:
        return []
    cells = rig.rest.cells[voxels]
    lo = cells.min(axis=0)
    volume = np.zeros(tuple(cells.max(axis=0) - lo + 1), np.int64)
    local = cells - lo
    volume[local[:, 0], local[:, 1], local[:, 2]] = voxels + 1
    quads = planes.frame_quads(volume, rig.rest.rgb, split, shade)
    out = []
    for quad in quads:
        pa, pb = planes.PLANE_AXES[quad.axis]
        out.append(replace(quad, k=quad.k + int(lo[quad.axis]), a0=quad.a0 + int(lo[pa]), b0=quad.b0 + int(lo[pb])))
    return out


def rig_figure(rig: Rig, *, figure_blocks: float, split: str = planes.DEFAULT_PLANE_SPLIT, shade: bool = True,
               mapping: planes.FaceMapping = planes.DEFAULT_MAPPING, max_sheet: int = 2048, gutter: int = 1) -> dict:
    """部品の板のシートと geometry のボーン (convert_image_mode の人物と同じ形。コマは 1 つ)。

    ボーンは b<PMX の番号>。親は残したボーンの親、pivot は初期姿勢のボーンの位置 (全体の座標)。"""
    placing = rig_placing(rig, figure_blocks)
    quads_of = {bone: part_quads(rig, bone, split, shade) for bone in rig.bones}
    images = [planes.oriented_image(q, mapping) for bone in rig.bones for q in quads_of[bone]]
    if not images:
        raise ValueError("人物の板がありません")
    sheets, [places], _per_sheet, size = planes.pack_frames([images], max_sheet, gutter)
    geometry_bones = []
    cursor = 0
    for bone in rig.bones:
        cubes = []
        for quad in quads_of[bone]:
            cubes.append(planes.quad_cube(quad, places[cursor], placing, mapping))
            cursor += 1
        entry = {"name": bone_name(bone)}
        parent = rig.parents[bone]
        if parent >= 0:
            entry["parent"] = bone_name(parent)
        entry["pivot"] = bone_pivot(rig, bone, placing, mapping)
        entry["cubes"] = cubes
        geometry_bones.append(entry)
    rest = rig.rest
    lo = rest.cells.min(axis=0)
    hi = rest.cells.max(axis=0) + 1
    s = placing.scale / 16
    return {
        "frames": 1, "per_sheet": 1, "sheet_width": size[0], "sheet_height": size[1],
        "sheets": [Image.fromarray(sheets[0], "RGBA")], "bones": geometry_bones, "quads": cursor,
        "size_blocks": ((hi[0] - lo[0]) * s, (hi[1] - lo[1]) * s, figure_blocks),
        "rig": {"placing": placing, "bones": list(rig.bones), "parents": dict(rig.parents)},
    }


_SUB = None


def _sub_points(sub: int) -> np.ndarray:
    global _SUB
    if _SUB is None or len(_SUB) != sub ** 3:
        grid = np.stack(np.meshgrid(*[np.arange(sub)] * 3, indexing="ij"), axis=-1).reshape(-1, 3)
        _SUB = (grid + 0.5) / sub
    return _SUB


def posed_cells(rig: Rig, skin: np.ndarray, shift=None, sub: int = 3, surface_only: bool = False) -> np.ndarray:
    """部品をボーンの変形 (Poser.pose の skin) どおりに動かした形のボクセル (K, 3)。1 ボクセルを sub³ 個の点にして
    動かし、格子に戻す。shift は全体から引く MMD の移動 (その場で踊らせるとき)。"""
    voxels, parts = rig.members_v, rig.members_p
    if surface_only:
        keep = rig.rest.surface[voxels]
        voxels, parts = voxels[keep], parts[keep]
    offsets = _sub_points(sub)
    points = (rig.rest.cells[voxels][:, None, :] + offsets[None]).reshape(-1, 3)
    owner = np.repeat(parts, len(offsets))
    mmd_points = rig.grid.to_mmd(points)
    moved = np.empty_like(mmd_points)
    for bone in np.unique(owner).tolist():
        chosen = owner == bone
        moved[chosen] = mmd_points[chosen] @ skin[bone, :3, :3].T + skin[bone, :3, 3]
    if shift is not None:
        moved -= np.asarray(shift, np.float64)
    cells = np.floor(rig.grid.to_voxel(moved)).astype(np.int64)
    return np.unique(cells, axis=0)


def load_model(pmx_path):
    """PMX と、その材質 (色の引き方)。"""
    model = mmd.read_pmx(pmx_path)
    return model, mmd._Materials(model, Path(pmx_path).parent)
