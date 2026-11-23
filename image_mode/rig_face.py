"""骨で動かす人物の表情 (モーフ) を、顔の一部を差し替える板にする (docs/research/mmd_image_mode_design_2026-11.md の 4 章)。

ゲームの geometry には頂点を動かす表情が無く、動かせるのはボーンの回転・移動・大きさだけ。そこで:

1. 踊りの間に使う頂点モーフ (グループモーフは中へ広げる) を、動かす頂点の場所で「顔の場所」(目・口など) に分ける。
2. 顔の場所ごとに、曲の間のモーフの重みを、数個 (既定 6) の代表の状態にまとめる (k 平均。重み 0 の状態をいつも含む)。
3. 代表の状態ごとに、初期姿勢の頂点にモーフを足して、顔の場所の周りの三角形だけをボクセルにし直す。これが差し替えの板。
   部品 (頭など) からは、顔の場所の箱の中の表面のボクセルを抜く。
4. アニメーションでは、差し替えのボーン (e<場所>s<状態>) の大きさを、使う状態だけ 1、ほかは 0 にする。変わる時刻に、
   前と後の値を分けたキー (pre / post) を置き、補間しないで切り替える (image_mode.rig_anim.build_animation)。

ボーンモーフ (口をボーンで動かすモデル) は、姿勢の計算 (mvcodec.mmd.Poser.pose の bone_offsets) に入るので、ここでは扱わない。
"""
from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np

from image_mode import rig as rigmod
from mvcodec import mmd

DEFAULT_STATES = 6
# 踊りの間にこれ以上の重みになる頂点モーフだけを使う
MIN_WEIGHT = 0.05
# 顔の場所の箱を広げる量 (ボクセル)。口を開けた唇などが箱の外に出ないように
MARGIN = 1
KMEANS_ROUNDS = 12


@dataclass
class FacePatch:
    """顔の場所 1 つの差し替え。bone は載せる部品のボーン (PMX の番号。Rig.bones の 1 つ)。states は状態ごとの
    (cells (M, 3) 格子の位置, rgb (M, 3), fine (細かい表面の色か None))。weights は状態ごとのモーフの重み (S, m)。
    labels はアニメーションのコマごとの状態の番号。removed は部品から抜いた初期姿勢のボクセルの番号。"""
    bone: int
    morphs: list[str]
    weights: np.ndarray
    states: list
    labels: np.ndarray
    removed: np.ndarray
    box: tuple


def merged_morph_tracks(*paths) -> dict:
    """VMD (ダンスと、表情・口の VMD) の表情のキーをまとめる。同じモーフのキーは全部を並べ、同じフレームは後のファイルを使う。"""
    merged = {}
    for path in paths:
        if not path:
            continue
        for name, keys in mmd.read_vmd_morphs(path).items():
            table = dict(merged.get(name, []))
            table.update(dict(keys))
            merged[name] = sorted(table.items())
    return merged


def frame_weights(morph_tracks: dict, frames) -> list[dict]:
    """フレームごとの {モーフ名: 重み} のリスト (表情のキーの直線補間)。"""
    return [{name: mmd.sample_morph(keys, float(frame)) for name, keys in morph_tracks.items()} for frame in frames]


def vertex_morph_weights(model, weights_per_frame: list[dict]) -> tuple[list[int], np.ndarray]:
    """フレームごとの、頂点モーフの重み (F, M) (グループモーフを中へ広げた後)。使われる頂点モーフ (MIN_WEIGHT 以上に
    なるもの) の番号 (M,) と一緒に返す。"""
    if not weights_per_frame:
        return [], np.zeros((0, 0))
    names = sorted({name for weights in weights_per_frame for name in weights})
    columns = {}
    for name in names:
        for morph, ratio in mmd._expand_morphs(model, {name: 1.0}).items():
            if model.morphs[morph]["kind"] == mmd.MORPH_VERTEX and len(model.morphs[morph]["vertices"]):
                columns.setdefault(morph, {})[name] = ratio
    used = sorted(columns)
    table = np.zeros((len(weights_per_frame), len(used)))
    for f, weights in enumerate(weights_per_frame):
        for j, morph in enumerate(used):
            table[f, j] = sum(weights.get(name, 0.0) * ratio for name, ratio in columns[morph].items())
    keep = np.abs(table).max(axis=0) >= MIN_WEIGHT if len(used) else np.zeros(0, bool)
    return [m for m, k in zip(used, keep) if k], table[:, keep]


def _morph_box(model, grid: rigmod.RestGrid, morph: int):
    """頂点モーフが動かす頂点の、動かす前と後の格子の箱 (最小, 最大。両端を含む)。"""
    m = model.morphs[morph]
    rest = np.asarray(model.positions, np.float64)[m["vertices"]]
    points = np.concatenate([rest, rest + m["offsets"]])
    cells = np.floor(grid.to_voxel(points)).astype(np.int64)
    return cells.min(axis=0), cells.max(axis=0)


def face_regions(model, grid: rigmod.RestGrid, morphs: list[int]) -> list[list[int]]:
    """頂点モーフを、動かす場所 (箱が MARGIN ボクセル以内で重なるもの) ごとにまとめる。[[モーフの番号…]…]"""
    boxes = [_morph_box(model, grid, m) for m in morphs]
    parent = list(range(len(morphs)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(morphs)):
        for j in range(i + 1, len(morphs)):
            (alo, ahi), (blo, bhi) = boxes[i], boxes[j]
            if np.all(alo - MARGIN <= bhi) and np.all(blo - MARGIN <= ahi):
                parent[find(i)] = find(j)
    groups = {}
    for i in range(len(morphs)):
        groups.setdefault(find(i), []).append(i)
    return [sorted(members) for members in groups.values()]


def quantize(weights: np.ndarray, states: int) -> tuple[np.ndarray, np.ndarray]:
    """重み (F, m) を、states 個以下の代表 (S, m) にまとめる (k 平均)。代表 0 はいつも重み 0 (表情なし)。
    (代表, フレームごとの代表の番号 (F,)) を返す。同じ入力なら同じ結果。"""
    if len(weights) == 0:
        return np.zeros((1, weights.shape[1] if weights.ndim == 2 else 0)), np.zeros(0, np.int64)
    distinct = np.unique(np.round(weights, 4), axis=0)
    count = max(1, min(states, len(distinct) + 1))
    centres = [np.zeros(weights.shape[1])]
    while len(centres) < count:
        distance = np.min([np.sum((weights - c) ** 2, axis=1) for c in centres], axis=0)
        far = int(np.argmax(distance))
        if distance[far] <= 1e-12:
            break
        centres.append(weights[far].copy())
    centres = np.array(centres)
    labels = np.zeros(len(weights), np.int64)
    for _ in range(KMEANS_ROUNDS):
        distance = ((weights[:, None, :] - centres[None]) ** 2).sum(axis=2)
        labels = np.argmin(distance, axis=1)
        for s in range(1, len(centres)):   # 代表 0 (表情なし) は動かさない
            chosen = labels == s
            if chosen.any():
                centres[s] = weights[chosen].mean(axis=0)
    return centres, labels


def _region_faces(model, grid: rigmod.RestGrid, morphs: list[int], box) -> np.ndarray:
    """顔の場所の三角形: モーフが動かす頂点を含む三角形と、全部の頂点が箱の中にある三角形 (口の中など)。"""
    moved = np.zeros(len(model.positions), bool)
    for m in morphs:
        moved[model.morphs[m]["vertices"]] = True
    cells = np.floor(grid.to_voxel(np.asarray(model.positions, np.float64))).astype(np.int64)
    lo, hi = box
    inside = ((cells >= lo) & (cells <= hi)).all(axis=1)
    faces = np.asarray(model.faces)
    return moved[faces].any(axis=1) | inside[faces].all(axis=1)


def _cells_in(cells: np.ndarray, box) -> np.ndarray:
    lo, hi = box
    return ((cells >= lo) & (cells <= hi)).all(axis=1)


def build_patches(built: rigmod.Rig, model, materials, morph_tracks: dict, frames, *,
                  states: int = DEFAULT_STATES, log=print) -> rigmod.Rig:
    """表情の差し替えを作り、部品から差し替える所を抜いた Rig を返す (Rig.patches)。表情のキーが無い・使われる頂点
    モーフが無いときは、built をそのまま返す。frames はアニメーションのコマの MMD のフレーム番号。"""
    if not morph_tracks:
        return built
    weights_per_frame = frame_weights(morph_tracks, frames)
    morphs, table = vertex_morph_weights(model, weights_per_frame)
    if not morphs:
        return built
    grid = built.grid
    rest = built.rest
    texels = built.fine.texels if built.fine is not None else 1
    sx, sy, sz = grid.size
    rest_keys = (rest.cells[:, 0] * sy + rest.cells[:, 1]) * sz + rest.cells[:, 2]
    rest_order = np.argsort(rest_keys)
    patches = []
    removed_all = []
    for group in face_regions(model, grid, morphs):
        names = [model.morphs[morphs[i]]["name"] for i in group]
        boxes = [_morph_box(model, grid, morphs[i]) for i in group]
        box = (np.min([b[0] for b in boxes], axis=0) - MARGIN, np.max([b[1] for b in boxes], axis=0) + MARGIN)
        centres, labels = quantize(table[:, group], states)
        face_mask = _region_faces(model, grid, [morphs[i] for i in group], box)
        state_data = []
        union = []
        for centre in centres:
            vertices = np.asarray(model.positions, np.float64).copy()
            for j, i in enumerate(group):
                m = model.morphs[morphs[i]]
                np.add.at(vertices, m["vertices"], m["offsets"] * centre[j])
            try:
                surface = rigmod.surface_voxels(model, materials, grid, vertices=vertices, face_mask=face_mask)
            except ValueError:
                state_data.append((np.zeros((0, 3), np.int64), np.zeros((0, 3)), None))
                continue
            keep = _cells_in(surface.cells, box)
            cells, rgb = surface.cells[keep], surface.rgb[keep]
            fine = rigmod.fine_surface(model, materials, grid, texels, vertices=vertices, face_mask=face_mask)
            state_data.append((cells, rgb, fine))
            union.append(cells)
        if not union:
            continue
        cells = np.unique(np.concatenate(union), axis=0)
        keys = (cells[:, 0] * sy + cells[:, 1]) * sz + cells[:, 2]
        at = np.clip(np.searchsorted(rest_keys[rest_order], keys), 0, len(rest_keys) - 1)
        hit = rest_keys[rest_order][at] == keys
        removed = rest_order[at[hit]]
        removed = removed[rest.surface[removed]]
        if len(removed) == 0:
            continue
        # 載せる部品: 抜いたボクセルの部品のボーンの多数決 (受け持つボーンへ)
        owners = np.array([built.target.get(int(b), int(b)) for b in rest.bone[removed]])
        values, counts = np.unique(owners, return_counts=True)
        bone = int(values[np.argmax(counts)])
        if bone not in built.parents:
            bone = built.bones[0]
        patches.append(FacePatch(bone, names, centres, state_data, labels, removed,
                                 (tuple(int(v) for v in box[0]), tuple(int(v) for v in box[1]))))
        removed_all.append(removed)
        log(f"表情: 顔の場所 {len(patches)} ({', '.join(names[:6])}{' など' if len(names) > 6 else ''}) を "
            f"{len(centres)} 通りの差し替えにしました")
    if not patches:
        return built
    drop = np.zeros(len(rest.cells), bool)
    drop[np.concatenate(removed_all)] = True
    keep = ~drop[built.members_v]
    return replace(built, members_v=built.members_v[keep], members_p=built.members_p[keep], patches=tuple(patches))


def patch_bone_name(region: int, state: int) -> str:
    """差し替えのボーンの名前 (geometry と animation で同じ)。"""
    return f"e{region}s{state}"
