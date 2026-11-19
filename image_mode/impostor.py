"""画像モードの「角度で絵を切り替える立体」(インポスター、試作): .vox の立体アニメを、まわりの views 方向から描いた絵にする。

ブロックモードのだまし絵 (`--view anamorphic`) は、見ている人の目の位置から見たとおりに、スクリプトが毎回ブロックを
置き直す。画像モードは前もって作った絵を切り替えるだけなので、同じことはできない。代わりに、立体を views 方向
(既定 8。45 度ごと) から描いておき、クライアントの Molang が「スクリーンから見たカメラの向き」
(`query.rotation_to_camera`) に一番近い向きの絵を選び、板をカメラへ向ける (rp.py)。見る人ごとに自分の角度の絵が
出るので、マルチプレイでも全員に立って見える。限界: 向きの境目で絵がぱっと切り替わる。真上・真下からは崩れる。
テクスチャは向きの数だけ増える。

マス (スロット) の並び: コマ f・向き k の絵はスロット f * views + k。

向き k の絵は、立体の正面 (vox の y が小さい側。make_demo_dancer.py の顔の側) から、上から見て反時計回りに
k * 360 / views 度まわった所から見た絵。左右は見ている人から見たとおり (鏡写しにしない)。
光は立体に固定 (Minecraft のブロックの面と同じく、上 1.0・下 0.5・横 0.8 / 0.6) なので、まわっても光の向きは変わらない。
"""
from __future__ import annotations

import math
from typing import Iterable

import numpy as np

DEFAULT_VIEWS = 8
# 面の明るさ (Minecraft のブロックの面の陰と同じ割合)。+z 上・-z 下・±x 東西・±y 南北
FACE_SHADE = {(0, 0, 1): 1.0, (0, 0, -1): 0.5, (1, 0, 0): 0.6, (-1, 0, 0): 0.6, (0, 1, 0): 0.8, (0, -1, 0): 0.8}
SUPERSAMPLE = 2


def dense_frames(frames: list, palettes: list) -> tuple[list[np.ndarray], list[np.ndarray], tuple[int, int, int]]:
    """mvcodec.volume.load_vox_frames_with_palettes の戻り値を、(x, y, z) の色番号の配列 (0 は空) にそろえる。"""
    sx = max(size[0] for size, _ in frames)
    sy = max(size[1] for size, _ in frames)
    sz = max(size[2] for size, _ in frames)
    volumes = []
    for _size, voxels in frames:
        volume = np.zeros((sx, sy, sz), np.uint8)
        if len(voxels):
            volume[voxels[:, 0], voxels[:, 1], voxels[:, 2]] = voxels[:, 3]
        volumes.append(volume)
    rgbs = [np.asarray(palette, np.uint8)[:, :3] for palette in palettes]
    return volumes, rgbs, (sx, sy, sz)


def cell_size(shape: tuple[int, int, int], cell_height: int) -> tuple[int, int, float]:
    """1 マスの (幅, 高さ, 1 ボクセルの画素数)。幅は、どの向きでも足元の対角線が入るように。"""
    sx, sy, sz = shape
    scale = cell_height / sz
    diagonal = math.hypot(sx, sy)
    width = max(2, int(math.ceil(diagonal * scale)))
    width += width % 2
    return width, cell_height, scale


def _surface_faces(volume: np.ndarray) -> list[tuple[tuple[int, int, int], np.ndarray, np.ndarray]]:
    """外 (空) に面したボクセルの面: [(法線, 位置 (N, 3) の整数, 色番号 (N,))]。"""
    solid = volume > 0
    padded = np.pad(solid, 1)
    faces = []
    for normal in FACE_SHADE:
        dx, dy, dz = normal
        neighbour = padded[1 + dx:padded.shape[0] - 1 + dx, 1 + dy:padded.shape[1] - 1 + dy,
                           1 + dz:padded.shape[2] - 1 + dz]
        exposed = solid & ~neighbour
        positions = np.argwhere(exposed)
        if len(positions):
            faces.append((normal, positions, volume[exposed]))
    return faces


def _face_samples(normal: tuple[int, int, int], steps: int) -> np.ndarray:
    """1 つの面の上の steps×steps の点 (ボクセルの左下手前の角からの位置)。"""
    t = (np.arange(steps) + 0.5) / steps
    a, b = np.meshgrid(t, t, indexing="ij")
    a, b = a.ravel(), b.ravel()
    axis = [i for i, n in enumerate(normal) if n][0]
    others = [i for i in range(3) if i != axis]
    points = np.zeros((len(a), 3), np.float32)
    points[:, axis] = 1.0 if normal[axis] > 0 else 0.0
    points[:, others[0]] = a
    points[:, others[1]] = b
    return points


def render_view(volume: np.ndarray, rgb: np.ndarray, shape: tuple[int, int, int], angle_deg: float,
                width: int, height: int, scale: float) -> np.ndarray:
    """立体を angle_deg の向きから正射影で描いた RGBA (height, width, 4)。背景は透明 (アルファ 0)。"""
    ss = SUPERSAMPLE
    big_w, big_h = width * ss, height * ss
    sx, sy, sz = shape
    phi = math.radians(angle_deg)
    # 立体から見る人への向き d と、見る人の右 r (上から見て反時計回りが正。正面は -y)
    d = np.array([math.sin(phi), -math.cos(phi), 0.0], np.float32)
    r = np.array([math.cos(phi), math.sin(phi), 0.0], np.float32)
    centre = np.array([sx / 2, sy / 2, 0.0], np.float32)
    pixels = scale * ss
    steps = max(2, int(math.ceil(pixels * 1.5)))
    image = np.zeros((big_h, big_w, 4), np.float32)
    all_index, all_depth, all_colour = [], [], []
    for normal, positions, colours in _surface_faces(volume):
        if float(np.dot(np.array(normal, np.float32), d)) <= 1e-6:
            continue  # 裏を向いた面
        samples = _face_samples(normal, steps)
        points = (positions[:, None, :].astype(np.float32) + samples[None, :, :] - centre).reshape(-1, 3)
        u = points @ r
        depth = points @ d
        col = np.floor(u * pixels + big_w / 2).astype(np.int64)
        row = np.floor((sz - points[:, 2]) * pixels).astype(np.int64)
        keep = (col >= 0) & (col < big_w) & (row >= 0) & (row < big_h)
        shade = FACE_SHADE[normal]
        base = rgb[np.clip(colours.astype(np.int64) - 1, 0, 255)].astype(np.float32) * shade
        all_index.append((row * big_w + col)[keep])
        all_depth.append(depth[keep])
        all_colour.append(np.repeat(base, len(samples), axis=0)[keep])
    if all_index:
        index = np.concatenate(all_index)
        depth = np.concatenate(all_depth)
        colour = np.concatenate(all_colour)
        # 画素ごとに一番手前 (depth が最大) の点を残す
        order = np.lexsort((-depth, index))
        index, colour = index[order], colour[order]
        first = np.ones(len(index), bool)
        first[1:] = index[1:] != index[:-1]
        flat = image.reshape(-1, 4)
        flat[index[first], :3] = colour[first]
        flat[index[first], 3] = 255.0
    # 縮めて縁をなめらかに (色はアルファで重み付けして平均)
    image = image.reshape(height, ss, width, ss, 4)
    alpha = image[..., 3].mean(axis=(1, 3))
    weight = image[..., 3:4] / 255.0
    colour_sum = (image[..., :3] * weight).sum(axis=(1, 3))
    covered = weight.sum(axis=(1, 3))
    colour = np.where(covered > 0, colour_sum / np.maximum(covered, 1e-6), 0)
    out = np.zeros((height, width, 4), np.uint8)
    out[..., :3] = np.clip(np.rint(colour), 0, 255).astype(np.uint8)
    # アルファテストの素材は半分で切るので、縁を太らせすぎないよう 0 / 255 にする
    out[..., 3] = np.where(alpha >= 127.5, 255, 0).astype(np.uint8)
    return out


def view_angles(views: int) -> list[float]:
    return [k * 360.0 / views for k in range(views)]


def render_cells(volumes: list[np.ndarray], rgbs: list[np.ndarray], shape: tuple[int, int, int], views: int,
                 width: int, height: int, scale: float) -> Iterable[np.ndarray]:
    """スロットの順 (コマ f・向き k → f * views + k) に RGBA の絵を出す。"""
    angles = view_angles(views)
    for volume, rgb in zip(volumes, rgbs):
        for angle in angles:
            yield render_view(volume, rgb, shape, angle, width, height, scale)


def screen_blocks(width: int, height: int, figure_blocks: float) -> list:
    """板の [幅, 高さ] (ブロック)。高さは立体の高さ figure_blocks、幅は 1 マスの縦横比から。"""
    def num(value: float):
        value = round(value, 4)
        return int(value) if value == int(value) else value

    return [num(figure_blocks * width / height), num(figure_blocks)]
